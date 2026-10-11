"""Reproduce outlet-label diversity checks across assessment reports.

The unit is one immutable paper ID per report cohort. Repeated reference
occurrences define membership and never increase a paper's outlet weight.
The design is descriptive finite-cohort enumeration and uniform subsampling
without replacement. Subsampling intervals do not estimate uncertainty in
the population of climate research. Input files are read without alteration.

Paper-to-WoS-record attachment is many-to-one on the retained record ID.
Missing attachments and source labels remain explicit exclusions. DOI-only
means at least one matched_doi occurrence within the selected report.
No personal, abstract, address or email fields are read from the exports.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import xml.etree.ElementTree as ET
import zipfile

import numpy as np

ROOT = Path(__file__).resolve().parent
REPORTS = ("AR4", "AR5", "AR6")
MATCHED = {"matched_doi", "matched_title"}
DEFAULT_DATABASE = ROOT.parent / "database/ipcc_litdb.sqlite"


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write_csv(path, rows):
    rows = list(rows)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else [])
        writer.writeheader()
        # Render embedded NULs as the literal JSON escape for Python 3.10 CSV.
        # The immutable SQLite snapshot retains the original control characters.
        writer.writerows({key: value.replace("\x00", "\\u0000") if isinstance(value, str) else value
                          for key, value in row.items()} for row in rows)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def basic_title(value):
    return " ".join(str(value or "").casefold().split())


def conservative_title(value):
    label = basic_title(value)
    # Merge identical word sequences separated by common title punctuation.
    # Preserve punctuation-only labels as their original categories.
    cleaned = " ".join(re.sub(r"[:.,;()\[\]‐‑–—-]", " ", label).split())
    return cleaned or label


def hill(counts):
    counts = np.asarray(list(counts), dtype=np.float64)
    counts = counts[counts > 0]
    n = float(counts.sum())
    if not n:
        return {"q0": None, "q1": None, "q2": None, "hhi": None}
    p = counts / n
    hhi = float(np.dot(p, p))
    return {"q0": int(len(counts)), "q1": float(np.exp(-np.dot(p, np.log(p)))),
            "q2": 1 / hhi, "hhi": hhi}


def cohort_data(database):
    cohorts = {(r, s): set() for r in REPORTS for s in ("raw", "matched", "doi_only")}
    coherent = defaultdict(set)
    fallbacks = {"AR4": 2007, "AR5": 2014, "AR6": 2021}
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        papers = {row["paper_id"]: dict(row) for row in connection.execute('''
            SELECT p.paper_id,p.journal_best,p.year_best,p.doi_norm,p.wos_record_id,
                   w."Source Title" AS wos_source_title,w."DOI" AS wos_doi
            FROM papers p LEFT JOIN wos_records w ON w.record_id=p.wos_record_id
            ORDER BY p.paper_id
        ''')}
        rows = connection.execute('''
            SELECT c.report_id,r.paper_id,r.match_status,s.publication_year
            FROM references_occurrence r JOIN chapters c USING(chapter_id)
            LEFT JOIN sources s ON s.source_id=c.source_id
            WHERE c.report_id IN ('AR4','AR5','AR6') ORDER BY r.occurrence_id
        ''')
        for row in rows:
            r, paper = row["report_id"], row["paper_id"]
            if paper is None:
                continue
            cohorts[(r, "raw")].add(paper)
            if row["match_status"] in MATCHED:
                cohorts[(r, "matched")].add(paper)
            if row["match_status"] == "matched_doi":
                cohorts[(r, "doi_only")].add(paper)
            years = re.findall(r"\b[12][0-9]{3}\b", str(row["publication_year"] or ""))
            source_year = int(years[0]) if len(years) == 1 else fallbacks[r]
            year_text = str(papers[paper]["year_best"] or "").strip()
            year = int(year_text) if re.fullmatch(r"[12][0-9]{3}", year_text) else None
            if year is not None and year <= source_year:
                coherent[(r, "raw")].add(paper)
                if row["match_status"] in MATCHED:
                    coherent[(r, "matched")].add(paper)
                if row["match_status"] == "matched_doi":
                    coherent[(r, "doi_only")].add(paper)
        schema = {table: [x[1] for x in connection.execute('PRAGMA table_info("' + table + '")')]
                  for table in ("papers", "wos_records")}
    assert len(papers) == 113591
    for report in REPORTS:
        assert cohorts[(report, "doi_only")] <= cohorts[(report, "matched")] <= cohorts[(report, "raw")]
    return papers, cohorts, coherent, schema


def audit_export(path):
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    fields = ("issn", "eissn", "journal abbreviation", "journal iso abbreviation", "wos categories", "research areas")
    result = {"file": "WoS_export_tables/" + path.parent.name + "/" + path.name, "sha256": digest(path), "rows": 0,
              **{field + "_nonempty": 0 for field in fields}}
    with zipfile.ZipFile(path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = ["".join(x.itertext()) for x in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
        columns = {}
        with archive.open("xl/worksheets/sheet1.xml") as stream:
            for _, element in ET.iterparse(stream, events=("end",)):
                if element.tag != "{" + ns["s"] + "}row":
                    continue
                cells = {}
                for cell in element.findall("s:c", ns):
                    ref = "".join(x for x in cell.attrib["r"] if x.isalpha())
                    node = cell.find("s:v", ns)
                    value = node.text if node is not None else ""
                    if cell.attrib.get("t") == "s" and value:
                        value = shared[int(value)]
                    elif cell.attrib.get("t") == "inlineStr":
                        node = cell.find("s:is", ns)
                        value = "".join(node.itertext()) if node is not None else ""
                    cells[ref] = value or ""
                if element.attrib.get("r") == "1":
                    columns = {value.casefold(): key for key, value in cells.items()}
                    result["issn_columns_present"] = all(field in columns for field in ("issn", "eissn"))
                else:
                    result["rows"] += 1
                    for field in fields:
                        result[field + "_nonempty"] += bool(cells.get(columns.get(field, ""), "").strip())
                element.clear()
    return result


def metadata_audit(upstream, exports, schema):
    result = {"packaged_issn_fields": [field for fields in schema.values() for field in fields if "issn" in field.casefold()],
              "upstream_available": upstream is not None and upstream.is_file(), "export_inventory": []}
    if upstream is not None and upstream.is_file():
        with sqlite3.connect(upstream.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            row = connection.execute('''SELECT COUNT(*),
                SUM(CASE WHEN COALESCE(ISSN,'')<>'' THEN 1 ELSE 0 END),
                SUM(CASE WHEN COALESCE(eISSN,'')<>'' THEN 1 ELSE 0 END),
                SUM(CASE WHEN COALESCE("WoS Categories",'')<>'' THEN 1 ELSE 0 END),
                SUM(CASE WHEN COALESCE("Research Areas",'')<>'' THEN 1 ELSE 0 END) FROM record''').fetchone()
            records = {row[0] for row in connection.execute("SELECT record_id FROM record")}
            linked = {row[0] for row in connection.execute("SELECT DISTINCT record_id FROM occurrence_record")}
        result.update({"upstream_sha256": digest(upstream), "upstream_records": row[0],
                       "upstream_issn_nonempty": row[1], "upstream_eissn_nonempty": row[2],
                       "upstream_wos_categories_nonempty": row[3], "upstream_research_areas_nonempty": row[4],
                       "upstream_records_without_ipcc_reference_link": len(records - linked)})
    if exports is not None:
        for report in REPORTS:
            for path in sorted((exports / report).glob("*.xlsx")):
                if not path.name.startswith("._"):
                    result["export_inventory"].append({"report": report, **audit_export(path)})
    result["export_files_checked"] = len(result["export_inventory"])
    result["export_rows_checked"] = sum(x["rows"] for x in result["export_inventory"])
    result["export_issn_nonempty"] = sum(x["issn_nonempty"] for x in result["export_inventory"])
    result["export_eissn_nonempty"] = sum(x["eissn_nonempty"] for x in result["export_inventory"])
    result["true_issn_normalization_available"] = False
    result["independent_climate_supply_baseline_available"] = False
    result["baseline_assessment"] = "All retained WoS records are selected through IPCC citation occurrences. These records and their chapter exports cannot measure the supply of climate research outside the IPCC corpus."
    result["normalization_assessment"] = "No local ISSN/eISSN values are available. Conservative title-token aliases are a sensitivity analysis, not ISSN or ISSN-L serial normalization."
    return result


def selected_ids(papers, cohorts, coherent, report, cohort, restriction):
    ids = cohorts[(report, cohort)]
    if restriction == "date_coherent":
        return ids & coherent[(report, cohort)]
    if restriction == "publication_2000_2013":
        return {paper for paper in ids if re.fullmatch(r"[12][0-9]{3}", str(papers[paper]["year_best"] or ""))
                and 2000 <= int(papers[paper]["year_best"]) <= 2013}
    return ids


def outlet_counts(papers, ids, field, normalizer):
    counts = Counter(normalizer(papers[paper][field]) for paper in ids)
    missing = counts.pop("", 0)
    return counts, missing


def draw_metrics(counts, size, reps, seed):
    abundance = np.asarray(list(counts.values()), dtype=np.int64)
    population = int(abundance.sum())
    assert 0 < size <= population
    rng = np.random.default_rng(seed)
    if size == population:
        draws = np.repeat(abundance[None, :], reps, axis=0)
    else:
        draws = rng.multivariate_hypergeometric(abundance, size, size=reps, method="marginals")
    assert np.all(draws.sum(axis=1) == size)
    assert np.all(draws <= abundance[None, :])
    p = draws.astype(np.float64) / size
    logged = np.zeros_like(p)
    np.log(p, out=logged, where=p > 0)
    hhi = np.sum(p * p, axis=1)
    values = {"q0": np.sum(draws > 0, axis=1).astype(float), "q1": np.exp(-np.sum(p * logged, axis=1)),
              "q2": 1 / hhi, "hhi": hhi}
    full = hill(abundance)
    lchoose = lambda n, k: math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
    base = lchoose(population, size)
    expected_q0 = sum(1 if population - f < size else -math.expm1(lchoose(population - int(f), size) - base) for f in abundance)
    expected_hhi = full["hhi"] if population == 1 else full["hhi"] + (population - size) / (size * (population - 1)) * (1 - full["hhi"])
    exact = {"q0": float(expected_q0), "hhi": float(expected_hhi)}
    summaries = []
    for metric, vector in values.items():
        sd = float(np.std(vector, ddof=1))
        mcse = sd / math.sqrt(reps)
        analytical = exact.get(metric)
        diagnostic = analytical is None or abs(float(vector.mean()) - analytical) <= max(5 * mcse, 1e-7)
        assert diagnostic, (metric, vector.mean(), analytical, mcse)
        summaries.append({"metric": metric, "population_ids": population, "sample_ids": size, "replicates": reps,
                          "mean": float(vector.mean()), "sd": sd, "monte_carlo_se": mcse,
                          "p025": float(np.quantile(vector, 0.025)), "p975": float(np.quantile(vector, 0.975)),
                          "analytical_expectation": analytical, "analytical_diagnostic_passed": diagnostic,
                          "mean_first_500": float(vector[:500].mean()), "mean_first_1000": float(vector[:1000].mean())})
    return values, summaries


def run(args):
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    qc = args.qc_output
    qc.mkdir(parents=True, exist_ok=True)
    scientific = list((ROOT / "source_data").glob("*.csv")) + [f for f in (ROOT / "results").glob("*") if f.is_file() and not f.name.startswith(("._", "revision_"))]
    immutable = {str(f.relative_to(ROOT)): digest(f) for f in scientific if not f.name.startswith("._")}
    input_hash = digest(args.database)
    upstream_hash = digest(args.upstream) if args.upstream is not None and args.upstream.is_file() else None
    papers, cohorts, coherent, schema = cohort_data(args.database)
    meta = {"packaged_issn_fields": [field for columns in schema.values() for field in columns if "issn" in field.casefold()],
            "true_issn_normalization_available": False,
            "independent_climate_supply_baseline_available": False,
            "normalization_assessment": "The distributed snapshot has no ISSN/eISSN fields. Conservative title-token aliases are a sensitivity analysis, not ISSN or ISSN-L serial normalization.",
            "baseline_assessment": "The distributed bibliographic snapshot contains works selected through IPCC citations. It has no independent climate-literature supply denominator."}
    if args.upstream is not None or args.exports is not None:
        supplementary = metadata_audit(args.upstream, args.exports, schema)
        write_json(qc / "revision_outlet_source_metadata.json", supplementary)
    expected = {"AR4": (13635, 4597, 4303), "AR5": (21770, 13510, 13150), "AR6": (47636, 34873, 34340)}
    for r, sizes in expected.items():
        assert tuple(len(cohorts[(r, s)]) for s in ("raw", "matched", "doi_only")) == sizes
    normalizers = {"casefold_whitespace": basic_title, "conservative_title_tokens": conservative_title}
    fields = {"preferred_label": "journal_best", "registered_wos_source": "wos_source_title"}
    labels = Counter(basic_title(papers[p]["journal_best"]) for p in set.union(*(cohorts[(r, "raw")] for r in REPORTS)))
    aliases = defaultdict(list)
    for label, n in sorted(labels.items()):
        if label:
            aliases[conservative_title(label)].append((label, n))
    variant_rows = [{"canonical_title_tokens": canonical, "source_label": label, "papers_in_ar4_ar6_union": n,
                     "labels_in_cluster": len(group), "rule": "casefold, collapsed whitespace and common punctuation separators only"}
                    for canonical, group in sorted(aliases.items()) if len(group) > 1 for label, n in group]
    write_csv(output / "revision_outlet_title_variants.csv", variant_rows)
    metrics = []
    for report in REPORTS:
        for cohort in ("raw", "matched", "doi_only"):
            for restriction in ("all", "date_coherent", "publication_2000_2013"):
                ids = selected_ids(papers, cohorts, coherent, report, cohort, restriction)
                for field_name, field in fields.items():
                    for name, normalize in normalizers.items():
                        counts, missing = outlet_counts(papers, ids, field, normalize)
                        values = hill(counts.values())
                        if values["q0"]:
                            assert values["q2"] <= values["q1"] + 1e-8 <= values["q0"] + 1e-8
                        metrics.append({"report": report, "cohort": cohort, "restriction": restriction, "source_field": field_name,
                                        "normalization": name, "cohort_ids_before_restriction": len(cohorts[(report, cohort)]),
                                        "ids_after_restriction": len(ids), "known_outlet_ids": sum(counts.values()),
                                        "missing_outlet_ids": missing, **values})
    write_csv(output / "revision_outlet_hill_numbers.csv", metrics)
    selection, selection_summaries = [], []
    for report in REPORTS:
        for name, normalize in normalizers.items():
            full, _ = outlet_counts(papers, cohorts[(report, "raw")], "journal_best", normalize)
            matched, _ = outlet_counts(papers, cohorts[(report, "matched")], "journal_best", normalize)
            full_n, matched_n = sum(full.values()), sum(matched.values())
            diffs = []
            for label in sorted(set(full) | set(matched)):
                a, b = full[label] / full_n, matched[label] / matched_n
                diffs.append(abs(b - a))
                selection.append({"report": report, "normalization": name, "outlet": label, "full_ids": full[label],
                                  "matched_ids": matched[label], "full_known_share": a, "matched_known_share": b,
                                  "matched_minus_full_percentage_points": 100 * (b - a)})
            selection_summaries.append({"report": report, "normalization": name,
                                        "full_known_ids": full_n, "matched_known_ids": matched_n,
                                        "total_variation_distance": sum(diffs) / 2})
    write_csv(output / "revision_outlet_coverage_selection.csv", selection)
    write_csv(output / "revision_outlet_coverage_summary.csv", selection_summaries)
    configs = [(s, "all", "preferred_label", n) for s in ("raw", "matched", "doi_only") for n in normalizers]
    configs += [("matched", r, "registered_wos_source", "conservative_title_tokens") for r in ("all", "publication_2000_2013")]
    configs += [("matched", "publication_2000_2013", "preferred_label", "conservative_title_tokens"),
                ("matched", "common_outlet_support", "preferred_label", "conservative_title_tokens")]
    rare, replicates = [], []
    for index, (cohort, restriction, field_name, normalization) in enumerate(configs):
        normalize, field = normalizers[normalization], fields[field_name]
        a_ids = selected_ids(papers, cohorts, coherent, "AR5", cohort, restriction)
        b_ids = selected_ids(papers, cohorts, coherent, "AR6", cohort, restriction)
        a, a_missing = outlet_counts(papers, a_ids, field, normalize)
        b, b_missing = outlet_counts(papers, b_ids, field, normalize)
        if restriction == "common_outlet_support":
            common = set(a) & set(b)
            a, b = Counter({k: a[k] for k in sorted(common)}), Counter({k: b[k] for k in sorted(common)})
        size = min(sum(a.values()), sum(b.values()))
        pair_name = "|".join((cohort, restriction, field_name, normalization))
        vectors = {}
        for r, counts in (("AR5", a), ("AR6", b)):
            ordered = {k: counts[k] for k in sorted(counts)}
            seed = np.random.SeedSequence([args.seed, index, int(r[-1])])
            vector, summary = draw_metrics(ordered, size, args.replicates, seed)
            vectors[r] = vector
            rare.extend({"comparison": pair_name, "report": r, "seed_base": args.seed, **row} for row in summary)
        for i in range(args.replicates):
            replicates.append({"comparison": pair_name, "replicate": i + 1, "sample_ids_per_report": size,
                               **{r.lower() + "_" + metric: float(vector[metric][i]) for r, vector in vectors.items() for metric in vector},
                               **{"difference_ar6_minus_ar5_" + metric: float(vectors["AR6"][metric][i] - vectors["AR5"][metric][i]) for metric in vectors["AR5"]}})
    write_csv(output / "revision_outlet_rarefaction_summary.csv", rare)
    write_csv(output / "revision_outlet_rarefaction_replicates.csv", replicates)
    difference_rows = []
    for config in sorted({row["comparison"] for row in replicates}):
        selected = [row for row in replicates if row["comparison"] == config]
        for metric in ("q0", "q1", "q2", "hhi"):
            values = np.asarray([row["difference_ar6_minus_ar5_" + metric] for row in selected])
            difference_rows.append({"comparison": config, "metric": metric, "replicates": len(values), "sample_ids_per_report": selected[0]["sample_ids_per_report"],
                                    "mean_difference_ar6_minus_ar5": float(values.mean()), "p025": float(np.quantile(values, 0.025)),
                                    "p975": float(np.quantile(values, 0.975)), "monte_carlo_se": float(values.std(ddof=1) / math.sqrt(len(values)))})
    write_csv(output / "revision_outlet_rarefaction_differences.csv", difference_rows)
    record = {"database_sha256": input_hash, "replicates_per_comparison": args.replicates, "seed_base": args.seed,
              "numpy_version": np.__version__, "metadata_audit": meta,
              "title_variant_clusters": len({row["canonical_title_tokens"] for row in variant_rows}),
              "title_labels_in_variant_clusters": len(variant_rows),
              "union_paper_ids_in_variant_clusters": sum(row["papers_in_ar4_ar6_union"] for row in variant_rows),
              "scope": {"unit": "One paper ID per report cohort; reference repeats only define membership.",
                        "doi_only": "At least one matched_doi occurrence within the report, not the separate doi_verified preferred-metadata status.",
                        "q0": "Number of nonempty observed outlet labels.", "q1": "exp(-sum p*log(p)), known-outlet IDs as denominator.",
                        "q2": "1/sum(p*p), known-outlet IDs as denominator.", "hhi": "sum(p*p), known-outlet IDs as denominator.",
                        "normalization": "Casefold/whitespace baseline and conservative common-punctuation title-token aliases. No lexical abbreviation expansion, leading-article deletion or inferred journal-family merger.",
                        "registered_wos_source": "Retained WoS Source Title joined many-to-one through paper.wos_record_id. It describes source outlets, including proceedings; it is not a verified journal-only classification.",
                        "publication_2000_2013": "The same preferred publication-year window in both assessments; defined before metric computation.",
                        "date_coherent": "At least one selected occurrence with valid preferred year not later than the source publication year.",
                        "common_outlet_support": "Both reports restricted to labels present in their matched known-outlet sets; within-support concentration comparison.",
                        "rarefaction": "Independent uniform samples without replacement from the two finite known-outlet paper-ID cohorts. Sample size is their smaller size; a complete smaller cohort is deterministic.",
                        "intervals": "2.5th and 97.5th simulation quantiles measure conditional subsampling variation. They are not confidence intervals for climate research or causal effects.",
                        "coverage_selection": "Total variation compares matched and full normalized known-outlet share distributions within the same extracted report.",
                        "discipline": "Outlet labels are not formal disciplinary classifications."},
              "coverage_selection": selection_summaries, "rarefaction_differences": difference_rows}
    write_json(output / "revision_outlet_analysis.json", record)
    unchanged = all(digest(ROOT / f) == value for f, value in immutable.items())
    assert unchanged
    assert digest(args.database) == input_hash
    if upstream_hash:
        assert digest(args.upstream) == upstream_hash
    quality = {"all_checks_passed": True, "immutable_existing_science_files": len(immutable),
               "existing_science_files_unchanged": unchanged, "database_unchanged": True, "upstream_database_unchanged": True,
               "rarefaction_comparisons": len(configs), "replicates_each": args.replicates,
               "finite_population_draw_sizes_and_bounds_passed": True,
               "hill_number_ordering_passed": True, "analytical_expected_richness_and_hhi_diagnostics_passed": True,
               "source_sha256": digest(Path(__file__)), "outputs": [f.name for f in sorted(output.glob("revision_outlet*"))],
               "privacy": "The analysis reads bibliographic source and identifier fields from the supplied local snapshot.",
               "methods": "working-with-data; statistical-analysis. Sampling diagnostics match the finite enumeration design; no parametric group tests were applied."}
    write_json(qc / "revision_outlet_quality.json", quality)
    print(json.dumps({"metrics_rows": len(metrics), "title_variant_clusters": record["title_variant_clusters"],
                      "rarefaction_comparisons": len(configs), "replicates_each": args.replicates,
                      "issn_fields_in_snapshot": len(meta["packaged_issn_fields"]),
                      "all_checks_passed": True}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--upstream", type=Path, help="Optional private upstream SQLite metadata audit; not required for the numerical analysis")
    parser.add_argument("--exports", type=Path, help="Optional private WoS Excel export audit; not required for the numerical analysis")
    parser.add_argument("--output", type=Path, default=ROOT / "results")
    parser.add_argument("--qc-output", type=Path, default=ROOT / "qc")
    parser.add_argument("--replicates", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20261011)
    arguments = parser.parse_args()
    if arguments.replicates < 2000:
        parser.error("Use at least 2000 replicates")
    run(arguments)
