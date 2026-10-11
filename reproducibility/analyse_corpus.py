"""Reproduce report-cohort, occurrence-age, and journal analyses from SQLite."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import statistics

ROOT = Path(__file__).resolve().parent
REPORTS = [f"AR{i}" for i in range(1, 7)]
FALLBACK_YEARS = {"AR1": 1990, "AR2": 1995, "AR3": 2001, "AR4": 2007, "AR5": 2014, "AR6": 2021}
WG_LABELS = ("WG1", "WG2", "WG3")
SQL = """
SELECT r.occurrence_id, c.report_id, wg.wg_label, r.chapter_id, r.paper_id,
       r.match_status, r.parsed_year, r.raw_reference, p.doi_norm, p.verification_status, p.year_best,
       p.journal_best, p.title_best, p.n_reports, s.publication_year AS source_year,
       r.source_id, c.source_id AS chapter_source_id
FROM references_occurrence r
JOIN chapters c ON c.chapter_id = r.chapter_id
LEFT JOIN working_groups wg ON wg.wg_id = c.wg_id
LEFT JOIN papers p ON p.paper_id = r.paper_id
LEFT JOIN sources s ON s.source_id = c.source_id
WHERE c.report_id IN ('AR1','AR2','AR3','AR4','AR5','AR6')
ORDER BY c.report_id, r.occurrence_id
"""


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def write_csv(path, rows, fields=None):
    rows = list(rows)
    fields = fields or (list(rows[0]) if rows else [])
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        # Render embedded NULs as the literal JSON escape for Python 3.10 CSV.
        # The immutable SQLite snapshot retains the original control characters.
        writer.writerows({key: value.replace("\x00", "\\u0000") if isinstance(value, str) else value
                          for key, value in row.items()} for row in rows)


def compare(a, b):
    shared = len(a & b)
    return {"papers_a": len(a), "papers_b": len(b), "shared": shared,
            "only_a": len(a - b), "only_b": len(b - a),
            "retention": shared / len(a) if a else None,
            "reuse": shared / len(b) if b else None,
            "jaccard": shared / len(a | b) if a | b else None}


def journal_key(value):
    return " ".join(str(value or "").casefold().split())


def run(database, output=ROOT):
    output = Path(output)
    results, sources = output / "results", output / "source_data"
    results.mkdir(parents=True, exist_ok=True)
    sources.mkdir(parents=True, exist_ok=True)
    database = Path(database).expanduser().resolve()
    original_hash = digest(database)
    manifest_path = database.parent / "snapshot.json"
    snapshot = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    if snapshot and original_hash != snapshot["uncompressed"]["sha256"]:
        raise ValueError("Working database differs from the packaged snapshot manifest")
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in connection.execute(SQL)]
        coverage_wg = [dict(row) for row in connection.execute("""
            WITH unit_counts AS (
                SELECT c.report_id, wg.wg_label, COUNT(DISTINCT c.chapter_id) AS catalogue_units
                FROM chapters c LEFT JOIN working_groups wg ON wg.wg_id=c.wg_id
                WHERE c.report_id IN ('AR1','AR2','AR3','AR4','AR5','AR6')
                GROUP BY c.report_id,wg.wg_label
            ), extracted AS (
                SELECT c.report_id,wg.wg_label,COUNT(DISTINCT c.chapter_id) AS units_with_occurrences,
                       COUNT(*) AS reference_occurrences,COUNT(DISTINCT r.paper_id) AS distinct_papers
                FROM references_occurrence r JOIN chapters c ON c.chapter_id=r.chapter_id
                LEFT JOIN working_groups wg ON wg.wg_id=c.wg_id
                WHERE c.report_id IN ('AR1','AR2','AR3','AR4','AR5','AR6')
                GROUP BY c.report_id,wg.wg_label
            ) SELECT u.*,COALESCE(e.units_with_occurrences,0) AS units_with_occurrences,
                     COALESCE(e.reference_occurrences,0) AS reference_occurrences,COALESCE(e.distinct_papers,0) AS distinct_papers
              FROM unit_counts u LEFT JOIN extracted e ON e.report_id=u.report_id AND e.wg_label=u.wg_label
              ORDER BY u.report_id,u.wg_label
        """)]
        catalogue = [dict(row) for row in connection.execute("""
            SELECT c.report_id, COUNT(*) AS catalogue_chapters,
                   SUM(CASE WHEN EXISTS (SELECT 1 FROM references_occurrence r WHERE r.chapter_id=c.chapter_id) THEN 1 ELSE 0 END) AS chapters_with_occurrences
            FROM chapters c WHERE c.report_id IN ('AR1','AR2','AR3','AR4','AR5','AR6')
            GROUP BY c.report_id ORDER BY c.report_id
        """)]
    finally:
        connection.close()
    write_csv(sources / "reference_occurrences.csv", rows)
    cohorts = {report: set() for report in REPORTS}
    matched = {report: set() for report in REPORTS}
    verified = {report: set() for report in REPORTS}
    doi_verified = {report: set() for report in REPORTS}
    main_wg = {report: set() for report in REPORTS}
    wg_cohorts = {(report, wg): set() for report in REPORTS for wg in WG_LABELS}
    matched_wg_cohorts = {(report, wg): set() for report in REPORTS for wg in WG_LABELS}
    paper_meta = {}
    occurrences = Counter()
    matched_occurrences = Counter()
    by_report = defaultdict(list)
    for row in rows:
        report, paper_id = row["report_id"], row["paper_id"]
        occurrences[report] += 1
        matched_occurrences[report] += row["match_status"] in {"matched_doi", "matched_title"}
        by_report[report].append(row)
        if paper_id is None:
            continue
        cohorts[report].add(paper_id)
        paper_meta[paper_id] = {key: row[key] for key in ("paper_id", "title_best", "year_best", "journal_best", "doi_norm", "verification_status", "n_reports")}
        if row["match_status"] in {"matched_doi", "matched_title"}:
            matched[report].add(paper_id)
        if row["verification_status"] in {"wos_matched", "doi_verified"}:
            verified[report].add(paper_id)
        if row["verification_status"] == "doi_verified":
            doi_verified[report].add(paper_id)
        if row["wg_label"] in WG_LABELS:
            main_wg[report].add(paper_id)
            wg_cohorts[(report, row["wg_label"])].add(paper_id)
            if row["match_status"] in {"matched_doi", "matched_title"}:
                matched_wg_cohorts[(report, row["wg_label"])].add(paper_id)
    write_csv(sources / "papers.csv", [paper_meta[paper_id] for paper_id in sorted(paper_meta)])
    write_csv(sources / "paper_report_membership.csv", [{"report_id": report, "paper_id": paper_id} for report in REPORTS for paper_id in sorted(cohorts[report])])

    coverage = []
    catalogue_lookup = {row["report_id"]: row for row in catalogue}
    for report in REPORTS:
        coverage.append({"report_id": report, **catalogue_lookup[report], "reference_occurrences": occurrences[report],
                         "distinct_papers": len(cohorts[report]), "matched_occurrences": matched_occurrences[report],
                         "matched_fraction": matched_occurrences[report] / occurrences[report],
                         "matched_distinct_papers": len(matched[report]), "verified_distinct_papers": len(verified[report])})
    write_csv(results / "coverage.csv", coverage)
    write_csv(results / "coverage_by_report_wg.csv", coverage_wg)
    chronological = {report: set() for report in REPORTS}
    matched_chronological = {report: set() for report in REPORTS}
    quality_candidates = []
    for row in rows:
        found = re.findall(r"\b([12][0-9]{3})\b", str(row["source_year"] or ""))
        base = int(found[0]) if len(found) == 1 else FALLBACK_YEARS[row["report_id"]]
        preferred_text = str(row["year_best"] or "").strip()
        parsed_text = str(row["parsed_year"] or "").strip()
        preferred = int(preferred_text) if re.fullmatch(r"[12][0-9]{3}", preferred_text) else None
        parsed = int(parsed_text) if re.fullmatch(r"[12][0-9]{3}", parsed_text) else None
        if row["paper_id"] is not None and preferred is not None and preferred <= base:
            chronological[row["report_id"]].add(row["paper_id"])
            if row["match_status"] in {"matched_doi", "matched_title"}:
                matched_chronological[row["report_id"]].add(row["paper_id"])
        if preferred is not None and (preferred > base or parsed is not None and preferred != parsed):
            quality_candidates.append({key: row[key] for key in ("occurrence_id", "report_id", "wg_label", "chapter_id", "paper_id", "match_status", "verification_status", "doi_norm", "title_best", "raw_reference", "parsed_year", "year_best", "source_year")} | {"source_base_year": base, "preferred_year_after_source": preferred > base, "parsed_preferred_year_mismatch": parsed is not None and preferred != parsed})
    write_csv(results / "identity_year_quality_candidates.csv", quality_candidates)
    variants = {"all_extracted": cohorts, "matched_occurrences_only": matched,
                "verified_metadata_only": verified, "doi_verified_only": doi_verified, "main_wg_only": main_wg, "chronological_coherence": chronological, "matched_chronological_coherence": matched_chronological}
    transitions = []
    pairs = [(REPORTS[i], REPORTS[i + 1]) for i in range(5)] + [("AR4", "AR6")]
    for variant, sets in variants.items():
        for a, b in pairs:
            transitions.append({"variant": variant, "report_a": a, "report_b": b, **compare(sets[a], sets[b])})
    write_csv(results / "transitions.csv", transitions)
    matrix = [{"report_a": a, "report_b": b, **compare(cohorts[a], cohorts[b])} for a in REPORTS for b in REPORTS]
    write_csv(results / "report_overlap_matrix.csv", matrix)
    wg_overlap = [{"report_id": report, "wg_a": a, "wg_b": b, **compare(wg_cohorts[(report, a)], wg_cohorts[(report, b)])}
                  for report in ("AR4", "AR5", "AR6") for a in WG_LABELS for b in WG_LABELS if a < b]
    write_csv(results / "working_group_overlap.csv", wg_overlap)
    matched_wg_overlap = [{"report_id": report, "wg_a": a, "wg_b": b, **compare(matched_wg_cohorts[(report, a)], matched_wg_cohorts[(report, b)])}
                         for report in ("AR4", "AR5", "AR6") for a in WG_LABELS for b in WG_LABELS if a < b]
    write_csv(results / "working_group_overlap_matched.csv", matched_wg_overlap)
    wg_occurrences = Counter((row["report_id"],row["wg_label"]) for row in rows)
    wg_matched_occurrences = Counter((row["report_id"],row["wg_label"]) for row in rows if row["match_status"] in {"matched_doi","matched_title"})
    wg_cohort_counts = [{"report_id":report,"working_group":wg,
        "all_distinct_papers":len(wg_cohorts[(report,wg)]),"matched_distinct_papers":len(matched_wg_cohorts[(report,wg)]),
        "all_occurrences":wg_occurrences[(report,wg)],"matched_occurrences":wg_matched_occurrences[(report,wg)]}
        for report in ("AR4","AR5","AR6") for wg in WG_LABELS]
    write_csv(results / "working_group_cohort_counts.csv",wg_cohort_counts)


    vintage = []
    vintage_counts = Counter()
    for paper_id in sorted(cohorts["AR6"]):
        row = paper_meta[paper_id]
        year_text = str(row["year_best"] or "").strip()
        year = int(year_text) if re.fullmatch(r"[12][0-9]{3}", year_text) else None
        group = "Reused from AR5" if paper_id in cohorts["AR5"] else "New to AR6"
        period = "Unknown year" if year is None else "Through 2013" if year <= 2013 else "After 2013"
        vintage_counts[(group, period)] += 1
        vintage.append({"paper_id": paper_id, "cohort": group, "publication_year": year, "vintage": period})
    write_csv(sources / "ar6_vintage_papers.csv", vintage)
    vintage_summary = [{"cohort": group, "vintage": period, "papers": vintage_counts[(group, period)]}
                       for group in ("Reused from AR5", "New to AR6") for period in ("Through 2013", "After 2013", "Unknown year")]
    write_csv(results / "ar6_vintage_summary.csv", vintage_summary)

    age_summary, age_rows = [], []
    for report in REPORTS:
        ages = []
        exclusions = Counter()
        source_year_count, fallback_count = 0, 0
        for row in by_report[report]:
            found = re.findall(r"\b([12][0-9]{3})\b", str(row["source_year"] or ""))
            source_year = int(found[0]) if len(found) == 1 else None
            base_year = source_year or FALLBACK_YEARS[report]
            source_year_count += source_year is not None
            fallback_count += source_year is None
            text = str(row["year_best"] or "").strip()
            cited_year = int(text) if re.fullmatch(r"[12][0-9]{3}", text) else None
            age = base_year - cited_year if cited_year is not None else None
            exclusion = "missing_publication_year" if age is None else "future_publication_year" if age < 0 else "older_than_60_years" if age > 60 else "included"
            exclusions[exclusion] += 1
            if exclusion == "included":
                ages.append(age)
            age_rows.append({"occurrence_id": row["occurrence_id"], "report_id": report, "paper_id": row["paper_id"],
                             "base_year": base_year, "base_year_source": "source" if source_year else "report_fallback",
                             "cited_year": cited_year, "age": age, "inclusion": exclusion})
        quartiles = statistics.quantiles(ages, n=4, method="exclusive") if len(ages) >= 4 else [None] * 3
        age_summary.append({"report_id": report, "reference_occurrences": occurrences[report], "dated_included": len(ages),
                            "source_year_count": source_year_count, "fallback_year_count": fallback_count,
                            "missing_year": exclusions["missing_publication_year"], "future_year": exclusions["future_publication_year"],
                            "over_60_years": exclusions["older_than_60_years"], "median_age": statistics.median(ages) if ages else None,
                            "p25": quartiles[0], "p75": quartiles[2]})
    write_csv(results / "citation_age.csv", age_summary)
    write_csv(sources / "citation_age_occurrences.csv", age_rows)

    display_names = {}
    for row in paper_meta.values():
        key = journal_key(row["journal_best"])
        if key:
            display_names.setdefault(key, " ".join(str(row["journal_best"]).split()))
    journal_counts = {}
    journal_rows, concentration = [], []
    for report in REPORTS:
        counts = Counter(journal_key(paper_meta[paper_id]["journal_best"]) for paper_id in cohorts[report])
        journal_counts[report] = counts
        known = sum(value for key, value in counts.items() if key)
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
            journal_rows.append({"report_id": report, "journal_key": key, "journal_label": display_names.get(key, "Missing journal"),
                                 "papers": count, "share_all_papers": count / len(cohorts[report]),
                                 "share_known_journal_papers": count / known if key and known else None})
        hhi_all = sum((count / len(cohorts[report])) ** 2 for count in counts.values())
        hhi_known = sum((count / known) ** 2 for key, count in counts.items() if key) if known else None
        concentration.append({"report_id": report, "distinct_papers": len(cohorts[report]), "known_journal_papers": known,
                              "missing_journal_papers": counts.get("", 0), "missing_journal_share": counts.get("", 0) / len(cohorts[report]),
                              "named_journals": sum(bool(key) for key in counts), "hhi_known_journals": hhi_known,
                              "hhi_all_including_missing_category": hhi_all, "effective_journals_known": 1 / hhi_known if hhi_known else None,
                              "top5_named_journal_share_all": sum(count for key, count in sorted(((k, n) for k, n in counts.items() if k), key=lambda item: (-item[1], item[0]))[:5]) / len(cohorts[report])})
    write_csv(results / "journal_mix.csv", journal_rows)
    write_csv(results / "journal_concentration.csv", concentration)
    persistent_counts = Counter(paper_id for report in REPORTS for paper_id in cohorts[report])
    core = [{**paper_meta[paper_id], "assessment_reports_present": n, "reports": ";".join(report for report in REPORTS if paper_id in cohorts[report])}
            for paper_id, n in sorted(persistent_counts.items(), key=lambda item: (-item[1], item[0])) if n >= 3]
    write_csv(results / "persistent_core_papers.csv", core)
    persistence = [{"assessment_reports_present": n, "distinct_papers": sum(value == n for value in persistent_counts.values())} for n in range(1, 7)]
    write_csv(results / "persistence_distribution.csv", persistence)
    matched_persistence_counts = Counter(paper_id for report in REPORTS for paper_id in matched[report])
    matched_core = [{**paper_meta[paper_id],"assessment_reports_present":n,
        "reports":";".join(report for report in REPORTS if paper_id in matched[report]),
        "chronologically_coherent_matched_reports":sum(paper_id in matched_chronological[report] for report in REPORTS)}
        for paper_id,n in sorted(matched_persistence_counts.items(),key=lambda item:(-item[1],item[0])) if n>=3]
    write_csv(results / "persistent_core_papers_matched.csv",matched_core)
    matched_persistence = [{"assessment_reports_present":n,"distinct_papers":sum(value==n for value in matched_persistence_counts.values())} for n in range(1,7)]
    write_csv(results / "persistence_distribution_matched.csv",matched_persistence)

    matched_age_rows = [age_row for age_row, raw in zip(age_rows, rows) if raw["match_status"] in {"matched_doi", "matched_title"}]
    # Both lists use report/occurrence ordering from the extraction SQL.
    assert all(age_row["occurrence_id"] == raw["occurrence_id"] for age_row, raw in zip(age_rows, rows))
    matched_age_summary = []
    for report in REPORTS:
        selected = [row for row in matched_age_rows if row["report_id"] == report]
        values = [row["age"] for row in selected if row["inclusion"] == "included"]
        counts = Counter(row["inclusion"] for row in selected)
        quartiles = statistics.quantiles(values, n=4, method="exclusive") if len(values) >= 4 else [None] * 3
        matched_age_summary.append({"report_id": report, "reference_occurrences": len(selected), "dated_included": len(values),
            "missing_year": counts["missing_publication_year"], "future_year": counts["future_publication_year"], "over_60_years": counts["older_than_60_years"],
            "median_age": statistics.median(values) if values else None, "p25": quartiles[0], "p75": quartiles[2]})
    write_csv(results / "citation_age_matched.csv", matched_age_summary)
    write_csv(sources / "citation_age_occurrences_matched.csv", matched_age_rows)
    matched_vintage_counts = Counter()
    matched_vintage_papers = []
    for paper_id in sorted(matched["AR6"]):
        text = str(paper_meta[paper_id]["year_best"] or "").strip()
        year = int(text) if re.fullmatch(r"[12][0-9]{3}", text) else None
        group = "Reused from AR5" if paper_id in matched["AR5"] else "New to AR6"
        period = "Unknown year" if year is None else "Through 2013" if year <= 2013 else "After 2013"
        matched_vintage_counts[(group, period)] += 1
        matched_vintage_papers.append({"paper_id": paper_id, "cohort": group, "publication_year": year, "vintage": period})
    matched_vintage_summary = [{"cohort": group, "vintage": period, "papers": matched_vintage_counts[(group, period)]}
        for group in ("Reused from AR5", "New to AR6") for period in ("Through 2013", "After 2013", "Unknown year")]
    write_csv(results / "ar6_vintage_summary_matched.csv", matched_vintage_summary)
    write_csv(sources / "ar6_vintage_papers_matched.csv", matched_vintage_papers)
    matched_journal_rows, matched_concentration = [], []
    for report in REPORTS:
        counts = Counter(journal_key(paper_meta[paper_id]["journal_best"]) for paper_id in matched[report])
        total = len(matched[report])
        known = sum(value for key,value in counts.items() if key)
        for key,count in sorted(counts.items(),key=lambda item:(-item[1],item[0])):
            matched_journal_rows.append({"report_id": report, "journal_key": key, "journal_label": display_names.get(key,"Missing journal"),
                "papers": count, "share_all_papers": count/total if total else None, "share_known_journal_papers": count/known if key and known else None})
        hhi = sum((count/known)**2 for key,count in counts.items() if key) if known else None
        matched_concentration.append({"report_id": report, "distinct_papers": total, "known_journal_papers": known, "missing_journal_papers": counts.get("",0),
            "missing_journal_share": counts.get("",0)/total if total else None, "named_journals": sum(bool(key) for key in counts),
            "hhi_known_journals": hhi, "hhi_all_including_missing_category":sum((count/total)**2 for count in counts.values()) if total else None,
            "effective_journals_known": 1/hhi if hhi else None})
    write_csv(results / "journal_mix_matched.csv",matched_journal_rows)
    write_csv(results / "journal_concentration_matched.csv",matched_concentration)
    final_hash = digest(database)
    assert final_hash == original_hash, "Read-only analysis changed the database"
    definitions = {
        "population": "AR1–AR6 reference occurrences linked through chapters.report_id; all report units included in the primary cohort.",
        "paper_cohort": "Set of non-null paper_id values cited by at least one extracted occurrence in each report. Repeated occurrences count once.",
        "transition": {"retention": "shared / earlier-report papers", "reuse": "shared / later-report papers", "jaccard": "shared / union"},
        "vintage": "New to AR6 means absent from the AR5 paper-ID cohort, not newly published. Through 2013 versus after 2013 is a descriptive year split, not a claim about source-specific search cutoffs.",
        "citation_age": "Occurrence-weighted source publication year minus linked paper.year_best; sources joined through chapter.source_id. Source year is used if exactly one four-digit year is present, otherwise the report fallback. Include ages 0–60; exclusions are counted separately.",
        "identity_quality": "Paper IDs and enriched metadata can merge unrelated references. Matched occurrence status is a quality-conditioned subset, not a guarantee of correct identity. Future preferred years and parsed/preferred-year mismatches are candidates for review, not automatic corrections.",
        "quartiles": "statistics.quantiles(method='exclusive'); the same rule as the toolkit analytics engine.",
        "journal": "Casefold and collapsed whitespace; no abbreviation expansion or title-family merger. Journal title is an outlet descriptor, not a formal discipline assignment.",
        "journal_hhi": "Sum of squared distinct-paper shares over named journal titles; missing journals excluded and reported separately. Alternative HHI includes missing as one category.",
        "sensitivities": {"matched_occurrences_only": "Paper IDs with at least one matched_doi or matched_title occurrence in the report.",
                          "verified_metadata_only": "Paper verification_status is wos_matched or doi_verified.", "doi_verified_only": "Paper verification_status is doi_verified (a workflow-status subset, excluding wos_matched).",
                          "chronological_coherence": "Paper IDs with at least one occurrence whose valid preferred publication year does not exceed its source year; missing preferred years are excluded.",
                          "matched_chronological_coherence": "Chronologically coherent occurrences restricted to matched_doi or matched_title.",
                          "main_wg_only": "Only occurrences in chapters with working-group labels exactly WG1, WG2 or WG3; excludes SYR and combined/other units."},
        "coverage_units": "Catalogue units include front matter and annexes; a unit without occurrences does not by itself establish extraction failure or missing scientific content.",
        "inference": "Descriptive enumeration of the extracted corpus; no hypothesis tests or uncertainty intervals that imply probability sampling.",
    }
    provenance = {"database_sha256": original_hash, "database_bytes": database.stat().st_size,
                  "snapshot_date": snapshot.get("snapshot_date") if snapshot else None,
                  "packaged_date": snapshot.get("packaged_date") if snapshot else None,
                  "read_only": True, "database_unchanged": True, "definitions": definitions, "sql": SQL.strip(),
                  "report_year_fallbacks": FALLBACK_YEARS, "reference_occurrences_analysed": len(rows)}
    (results / "provenance.json").write_text(json.dumps(provenance, indent=2, ensure_ascii=False) + "\n")
    (results / "analysis_query.sql").write_text(SQL.strip() + ";\n")
    summary = {"coverage": coverage, "coverage_by_report_wg": coverage_wg, "transitions": transitions, "ar6_vintage": vintage_summary,
               "citation_age": age_summary, "citation_age_matched": matched_age_summary, "ar6_vintage_matched": matched_vintage_summary,
               "journal_concentration_matched": matched_concentration, "identity_year_quality_candidate_count": len(quality_candidates), "working_group_overlap": wg_overlap, "working_group_overlap_matched": matched_wg_overlap, "journal_concentration": concentration,
               "persistence": persistence, "persistence_matched": matched_persistence, "working_group_cohort_counts": wg_cohort_counts, "provenance": provenance}
    (results / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    headline = next(row for row in transitions if row["variant"] == "all_extracted" and row["report_a"] == "AR5" and row["report_b"] == "AR6")
    print(json.dumps({"ar5_ar6": headline, "vintage": vintage_summary, "ages": age_summary, "journal_concentration": concentration}, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=ROOT.parent / "database/ipcc_litdb.sqlite")
    parser.add_argument("--output", type=Path, default=ROOT)
    arguments = parser.parse_args()
    run(arguments.database, arguments.output)
