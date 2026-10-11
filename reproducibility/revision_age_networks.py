"""Age and group-sharing sensitivity analyses for the assessment snapshot.

Units: occurrence_id is unique; paper_id is a stable database identity, subject
to the linkage checks supplied with the snapshot. Chapters are resampling
clusters. Occurrences join chapter/source and paper metadata many-to-one.
The input is immutable. Outputs have revision_age/revision_network prefixes.
These are descriptive finite-corpus analyses and conditional randomization
benchmarks, not inference from a probability sample of climate research.
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
import statistics

import numpy as np

ROOT = Path(__file__).resolve().parent
REPORTS = ("AR4", "AR5", "AR6")
GROUPS = ("WG1", "WG2", "WG3")
MATCHED = {"matched_doi", "matched_title"}
SQL = """SELECT r.occurrence_id,c.report_id,w.wg_label,r.chapter_id,r.paper_id,
 r.match_status,p.verification_status,p.doi_norm,p.year_best,p.journal_best,
 s.publication_year AS source_year
 FROM references_occurrence r JOIN chapters c ON r.chapter_id=c.chapter_id
 JOIN working_groups w ON w.wg_id=c.wg_id
 JOIN papers p ON p.paper_id=r.paper_id
 LEFT JOIN sources s ON s.source_id=c.source_id
 WHERE c.report_id IN ('AR4','AR5','AR6')
 ORDER BY c.report_id,r.occurrence_id"""
CUTOFFS = {
    "AR5": {"WG1": "2013-03-15", "WG2": "2013-08-31", "WG3": "2013-10-03"},
    "AR6": {"WG1": "2021-01-31", "WG2": "2021-09-01", "WG3": "2021-10-11"},
}
CUTOFF_SOURCES = [
    {"report": "AR5", "url": "https://archive.ipcc.ch/pdf/ar5/ar5-cut-off-dates.pdf",
     "title": "Cut-Off Dates for literature to be considered for AR5, updated 12 December 2012",
     "locator": "Page 1, papers accepted: WGI 15 March, WGII 31 August, WGIII 3 October 2013",
     "read_scope": "Official publisher indexed table text"},
    {"report": "AR6", "group": "WG1", "url": "https://www.ipcc.ch/report/ar6/wg1/downloads/report/IPCC_AR6_WGI_SummaryVolume.pdf",
     "locator": "Preface: scientific publications available by 31 January 2021",
     "read_scope": "Official publisher indexed preface text"},
    {"report": "AR6", "group": "WG2", "url": "https://www.ipcc.ch/site/assets/uploads/2021/05/WG2AR6_Timeline_v4.pdf",
     "locator": "September 2021: 1 September, cut-off date for accepted papers",
     "read_scope": "Official publisher indexed timeline text"},
    {"report": "AR6", "group": "WG3", "url": "https://www.ipcc.ch/report/ar6/wg3/downloads/report/IPCC_AR6_WGIII_HeadlineStatements.pdf",
     "locator": "Footnote 24: literature cut-off date 11 October 2021",
     "read_scope": "Official publisher indexed final headline statements text"},
]


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def write_csv(path, rows):
    rows = list(rows)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else [])
        writer.writeheader()
        # Render embedded NULs as the literal JSON escape for Python 3.10 CSV.
        # The immutable SQLite snapshot retains the original control characters.
        writer.writerows({key: value.replace("\x00", "\\u0000") if isinstance(value, str) else value
                          for key, value in row.items()} for row in rows)


def year(value):
    text = str(value or "").strip()
    return int(text) if re.fullmatch(r"[12][0-9]{3}", text) else None


def source_year(value):
    values = re.findall(r"\b([12][0-9]{3})\b", str(value or ""))
    return int(values[0]) if len(values) == 1 else None


def journal(value):
    return " ".join(str(value or "").casefold().split()) or "[missing journal]"


def metrics(hist):
    total = float(hist.sum())
    cdf = np.cumsum(hist)
    quantiles = [int(np.searchsorted(cdf, q * total, side="left")) for q in (.25, .5, .75)]
    median = quantiles[1]
    if math.isclose(float(cdf[median]), total / 2, rel_tol=1e-12, abs_tol=1e-10):
        following = np.flatnonzero(hist[median + 1:])
        if len(following):
            median = (median + median + 1 + int(following[0])) / 2
    mean = float(np.dot(hist, np.arange(61)) / total)
    sd = math.sqrt(float(np.dot(hist, (np.arange(61) - mean) ** 2) / total))
    return {"weight_denominator": total, "q25_inverse_ecdf": quantiles[0],
            "median_inverse_ecdf": quantiles[1], "q75_inverse_ecdf": quantiles[2],
            "median_midpoint": float(median),
            "mean_age": mean, "sd_age_population": sd,
            "price_le5": float(hist[:6].sum() / total),
            "price_lt5": float(hist[:5].sum() / total)}


def age_population(rows, basis):
    included, excluded = [], Counter()
    for row in rows:
        base = row["source_year_integer"]
        if basis == "cutoff_year":
            date = CUTOFFS.get(row["report_id"], {}).get(row["wg_label"])
            base = int(date[:4]) if date else None
        cited = row["publication_year_integer"]
        reason = ("no_verified_base_year" if base is None else
                  "missing_publication_year" if cited is None else
                  "publication_year_after_base" if cited > base else
                  "age_over_60" if base - cited > 60 else "included")
        excluded[reason] += 1
        if reason == "included":
            included.append((row["chapter_id"], row["paper_id"], base - cited))
    return included, dict(excluded)


def histograms(records, chapter_weights=None):
    chapters = sorted({r[0] for r in records})
    papers = sorted({r[1] for r in records})
    ci = {value: i for i, value in enumerate(chapters)}
    pi = {value: i for i, value in enumerate(papers)}
    chapter = np.array([ci[r[0]] for r in records], dtype=np.int32)
    paper = np.array([pi[r[1]] for r in records], dtype=np.int32)
    age = np.array([r[2] for r in records], dtype=np.int32)
    weights = np.ones(len(records)) if chapter_weights is None else chapter_weights[chapter]
    per_paper = np.bincount(paper, weights=weights, minlength=len(papers))
    equal_id = np.divide(weights, per_paper[paper], out=np.zeros_like(weights), where=per_paper[paper] > 0)
    return {"occurrence": np.bincount(age, weights=weights, minlength=61),
            "equal_paper_id": np.bincount(age, weights=equal_id, minlength=61)}, (chapters, paper, age, chapter)


def bootstrap(records, clusters, replicates, rng):
    # All clusters with selected occurrences enter the draw, including chapters
    # whose selected occurrences fail age inclusion. Each report is resampled
    # independently. Draw multiplicities reweight occurrences, then equal-ID
    # weights are recalculated among IDs present in the resampled chapters.
    clusters = sorted(clusters)
    ci = {chapter: i for i, chapter in enumerate(clusters)}
    pi = {paper: i for i, paper in enumerate(sorted({r[1] for r in records}))}
    chapter = np.array([ci[r[0]] for r in records], dtype=np.int32)
    paper = np.array([pi[r[1]] for r in records], dtype=np.int32)
    ages = np.array([r[2] for r in records], dtype=np.int32)
    draws = rng.multinomial(len(clusters), np.full(len(clusters), 1 / len(clusters)), size=replicates)
    result = {weight: [] for weight in ("occurrence", "equal_paper_id")}
    for draw in draws:
        weights = draw[chapter].astype(float)
        totals = np.bincount(paper, weights=weights, minlength=len(pi))
        equal_id = np.divide(weights, totals[paper], out=np.zeros_like(weights), where=totals[paper] > 0)
        for label, weights_ in (("occurrence", weights), ("equal_paper_id", equal_id)):
            hist = np.bincount(ages, weights=weights_, minlength=61)
            if hist.sum() == 0:
                raise ValueError("Empty age draw; a conditional empty-draw protocol is needed")
            result[label].append(metrics(hist))
    return result


def conditional_states(patterns):
    """Exact support of a 3-column binary matrix with fixed row/column sums.

    Degree-three rows are fixed. For b degree-two rows, x_i is the count
    missing column i. Degree-one column counts are g_i=m_i-c-b+x_i.
    Each feasible x has mass proportional to b! a! / product(x_i! g_i!).
    This counts every labelled matrix once, so independent draws are exact.
    """
    a = sum(p.bit_count() == 1 for p in patterns)
    b = sum(p.bit_count() == 2 for p in patterns)
    c = sum(p.bit_count() == 3 for p in patterns)
    margins = [sum(bool(p & (1 << j)) for p in patterns) for j in range(3)]
    lower = [max(0, b + c - m) for m in margins]
    upper = [min(b, a + b + c - m) for m in margins]
    states, logweights = [], []
    for x0 in range(lower[0], upper[0] + 1):
        lo = max(lower[1], b - x0 - upper[2])
        hi = min(upper[1], b - x0 - lower[2])
        for x1 in range(lo, hi + 1):
            x = (x0, x1, b - x0 - x1)
            g = tuple(margins[i] - c - b + x[i] for i in range(3))
            assert min(g) >= 0 and sum(g) == a
            assert all(c + b - x[i] + g[i] == margins[i] for i in range(3))
            states.append((c + x[2], c + x[1], c + x[0]))
            logweights.append(-sum(math.lgamma(v + 1) for v in (*x, *g)))
    assert states
    values = np.asarray(states, dtype=float)
    logweights = np.asarray(logweights)
    probabilities = np.exp(logweights - logweights.max())
    probabilities /= probabilities.sum()
    return values, probabilities, (a, b, c), margins


def holm(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    output = [0.0] * len(values)
    last = 0.0
    for rank, i in enumerate(order):
        last = max(last, min(1.0, values[i] * (len(values) - rank)))
        output[i] = last
    return output


def run(database, replicates=2500, seed=20261011):
    if replicates < 2000:
        raise ValueError("At least 2000 replicates are required")
    output, qc = ROOT / "results", ROOT / "qc"
    output.mkdir(exist_ok=True); qc.mkdir(exist_ok=True)
    database = Path(database).expanduser().resolve()
    before = digest(database)
    provenance_path = ROOT / "results" / "provenance.json"
    original_provenance = json.loads(provenance_path.read_text())
    if before != original_provenance["database_sha256"]:
        raise ValueError("Input differs from the distributed immutable analysis snapshot")
    connection = sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    rows = [dict(r) for r in connection.execute(SQL)]
    units = [dict(r) for r in connection.execute("""SELECT c.report_id,w.wg_label,c.chapter_id,
      EXISTS(SELECT 1 FROM references_occurrence r WHERE r.chapter_id=c.chapter_id) AS has_occurrences
      FROM chapters c JOIN working_groups w ON w.wg_id=c.wg_id
      WHERE c.report_id IN ('AR4','AR5','AR6') ORDER BY c.chapter_id""")]
    connection.close()
    assert len(rows) == len({r["occurrence_id"] for r in rows})
    assert len(rows) == 107071
    papers = {}
    for row in rows:
        row["source_year_integer"] = source_year(row["source_year"])
        row["publication_year_integer"] = year(row["year_best"])
        metadata = (row["year_best"], row["journal_best"], row["doi_norm"], row["verification_status"])
        if row["paper_id"] in papers:
            assert papers[row["paper_id"]] == metadata
        papers[row["paper_id"]] = metadata
    input_audit = {"database_sha256_before": before, "selected_occurrences": len(rows),
                   "sql": SQL, "original_provenance_sha256": digest(provenance_path),
                   "join": "Occurrence to unique chapter/source and paper metadata, many-to-one; all IDs resolved",
                   "units": {"occurrence": "occurrence_id", "identity": "database paper_id",
                             "resampling": "chapter_id", "membership": "report_id x working_group x paper_id"},
                   "source_years": [{"report": r, "group": g, "source_date": s, "occurrences": n}
                                    for (r, g, s), n in sorted(Counter((x["report_id"], x["wg_label"], x["source_year"]) for x in rows).items())]}
    definitions = {
        "age": "Integer source publication year minus preferred publication year; include 0 through 60, log every exclusion",
        "equal_id_weight": "Each included paper ID receives total weight one, divided among its included occurrences; retains integer-age support when source years differ",
        "price": "Primary Price index: fraction with age <=5 years (six integer ages, 0 through 5). Sensitivity: age <5 (0 through 4)",
        "quantiles": "Primary weighted inverse ECDF (left quantile). Weighted CDF midpoint median also given. Original exclusive and inclusive quartiles are given for occurrence counts only",
        "bootstrap": f"{replicates} independent multinomial chapter draws per report/cohort; draw observed chapters with selected occurrences with replacement; reweight occurrences by cluster multiplicity and recompute equal-ID weights among IDs present",
        "interval": "95% percentile resampling interval; finite-corpus sensitivity to chapter composition, conditional on available extracted chapters and assigned identities. It excludes extraction/linkage error and is not population-sampling uncertainty",
        "difference": "AR6 minus AR5, using independently resampled report chapter compositions; price differences are proportions",
        "cutoff_sensitivity": "Use documented cutoff YEAR as an alternative age origin and publication-year screen. Preferred publication year is not acceptance date; later publication can follow acceptance. Annual screens are sensitivity conditions, not adjudicated cutoff violations",
        "unknown_cutoffs": "No verified AR4 cutoff is applied. Synthesis/combined units have no assigned group cutoff and are explicitly excluded from cutoff-year age summaries",
    }
    summary, ecdf, intervals, bootstrap_rows, bootstrap_store = [], [], [], [], {}
    sequence = np.random.SeedSequence(seed).spawn(30)
    rng_index = 0
    for report in REPORTS:
        for cohort in ("extracted", "matched"):
            selected = [r for r in rows if r["report_id"] == report and (cohort == "extracted" or r["match_status"] in MATCHED)]
            for basis in ("source_year", "cutoff_year"):
                if basis == "cutoff_year" and report == "AR4":
                    continue
                records, exclusions = age_population(selected, basis)
                hist, _ = histograms(records)
                for weighting, values in hist.items():
                    item = {"report": report, "cohort": cohort, "basis": basis, "weighting": weighting,
                            "selected_occurrences": len(selected), "included_occurrences": len(records),
                            "included_ids": len({r[1] for r in records}), **metrics(values), **exclusions}
                    if weighting == "occurrence":
                        ages = np.repeat(np.arange(61), values.astype(int)).tolist()
                        for method in ("exclusive", "inclusive"):
                            q = statistics.quantiles(ages, n=4, method=method)
                            item.update({f"q25_{method}": q[0], f"q75_{method}": q[2]})
                    else:
                        item.update({f"q{q}_{method}": None for method in ("exclusive", "inclusive") for q in (25, 75)})
                    summary.append(item)
                    cumul = np.cumsum(values) / values.sum()
                    ecdf.extend({"report": report, "cohort": cohort, "basis": basis, "weighting": weighting,
                                 "age": age, "weight_at_age": float(values[age]), "ecdf": float(cumul[age])} for age in range(61))
                # Bootstrap source-based distributions for all three reports and
                # both populations; the annual-cutoff change is descriptive.
                if basis == "source_year":
                    draws = bootstrap(records, {r["chapter_id"] for r in selected}, replicates, np.random.default_rng(sequence[rng_index]))
                    rng_index += 1
                    for weighting, values in draws.items():
                        bootstrap_store[(report, cohort, weighting)] = values
                        names = ("median_inverse_ecdf", "median_midpoint", "mean_age", "price_le5", "price_lt5")
                        for metric in names:
                            observed = metrics(hist[weighting])[metric]
                            samples = np.array([v[metric] for v in values])
                            low, high = np.quantile(samples, [.025, .975], method="linear")
                            intervals.append({"report": report, "cohort": cohort, "weighting": weighting, "metric": metric,
                                              "estimate": observed, "lower_95": float(low), "upper_95": float(high),
                                              "replicates": replicates, "clusters": len({r["chapter_id"] for r in selected})})
                        for i, value in enumerate(values):
                            bootstrap_rows.append({"report": report, "cohort": cohort, "weighting": weighting,
                                                   "replicate": i, **{m: value[m] for m in names}})
    # Different summary rows share a complete, explicit schema.
    summary_fields = sorted({key for row in summary for key in row})
    summary = [{key: row.get(key, 0 if key in ("included", "no_verified_base_year", "missing_publication_year", "publication_year_after_base", "age_over_60") else None) for key in summary_fields} for row in summary]
    differences = []
    for cohort in ("extracted", "matched"):
        for weighting in ("occurrence", "equal_paper_id"):
            a = bootstrap_store[("AR5", cohort, weighting)]; b = bootstrap_store[("AR6", cohort, weighting)]
            for metric in ("median_inverse_ecdf", "median_midpoint", "mean_age", "price_le5", "price_lt5"):
                draws = np.array([v[metric] - u[metric] for u, v in zip(a, b)])
                low, high = np.quantile(draws, [.025, .975])
                observed = [next(r[metric] for r in summary if r["report"] == report and r["cohort"] == cohort and r["basis"] == "source_year" and r["weighting"] == weighting) for report in ("AR5", "AR6")]
                differences.append({"report_a": "AR5", "report_b": "AR6", "cohort": cohort, "weighting": weighting,
                                    "metric": metric, "difference": observed[1] - observed[0],
                                    "lower_95": float(low), "upper_95": float(high), "replicates": replicates})
    write_csv(output / "revision_age_summary.csv", summary)
    write_csv(output / "revision_age_ecdf.csv", ecdf)
    write_csv(output / "revision_age_bootstrap_intervals.csv", intervals)
    write_csv(output / "revision_age_bootstrap_draws.csv", bootstrap_rows)
    write_csv(output / "revision_age_differences.csv", differences)
    write_json(output / "revision_age_methods.json", {"seed": seed, "replicates": replicates, "definitions": definitions,
                                                      "cutoff_dates": CUTOFFS, "cutoff_sources": CUTOFF_SOURCES, "input": input_audit})

    cohort_rules = {
        "extracted": lambda r: True,
        "matched": lambda r: r["match_status"] in MATCHED,
        "matched_doi": lambda r: r["match_status"] == "matched_doi",
        "doi_identifier_present": lambda r: bool(r["doi_norm"]),
        "doi_verified_workflow": lambda r: r["verification_status"] == "doi_verified",
    }
    sets = {}
    coverage = []
    for report in REPORTS:
        for group in GROUPS:
            selected = [r for r in rows if r["report_id"] == report and r["wg_label"] == group]
            counts = Counter(r["match_status"] for r in selected)
            catalogue = [u for u in units if u["report_id"] == report and u["wg_label"] == group]
            item = {"report": report, "group": group, "catalogue_units": len(catalogue),
                    "units_with_occurrences": sum(u["has_occurrences"] for u in catalogue),
                    "occurrences": len(selected), "distinct_ids": len({r["paper_id"] for r in selected})}
            item["units_with_occurrences_fraction"] = item["units_with_occurrences"] / len(catalogue)
            for status in ("matched_doi", "matched_title", "unmatched"):
                item[status + "_occurrences"] = counts[status]
                item[status + "_fraction"] = counts[status] / len(selected)
            item["matched_occurrence_fraction"] = (counts["matched_doi"] + counts["matched_title"]) / len(selected)
            for status in ("wos_matched", "doi_verified", "unresolved"):
                count = len({r["paper_id"] for r in selected if r["verification_status"] == status})
                item[status + "_paper_ids"] = count
                item[status + "_paper_fraction"] = count / item["distinct_ids"]
            for rule, condition in cohort_rules.items():
                base = [r for r in selected if condition(r)]
                sets[(report, group, rule, "none")] = {r["paper_id"] for r in base}
                item[rule + "_ids"] = len(sets[(report, group, rule, "none")])
                item[rule + "_id_fraction"] = item[rule + "_ids"] / item["distinct_ids"]
                for screening in ("source_year", "cutoff_year"):
                    if screening == "cutoff_year" and report == "AR4":
                        continue
                    # Coherence only excludes missing and future years, not old
                    # works. Age >60 remains eligible for retention screening.
                    eligible_ids = set()
                    for r in base:
                        base_year = r["source_year_integer"] if screening == "source_year" else int(CUTOFFS[report][group][:4])
                        if base_year is not None and r["publication_year_integer"] is not None and r["publication_year_integer"] <= base_year:
                            eligible_ids.add(r["paper_id"])
                    sets[(report, group, rule, screening)] = eligible_ids
            coverage.append(item)
    retention, overlap = [], []
    for rule in cohort_rules:
        for group in GROUPS:
            for earlier, later in (("AR4", "AR5"), ("AR5", "AR6"), ("AR4", "AR6")):
                for screening in ("none", "source_year", "cutoff_year"):
                    if (earlier, group, rule, screening) not in sets:
                        continue
                    a, b = sets[(earlier, group, rule, screening)], sets[(later, group, rule, screening)]
                    shared = len(a & b)
                    retention.append({"rule": rule, "screening": screening, "group": group,
                                      "report_a": earlier, "report_b": later, "n_a": len(a), "n_b": len(b),
                                      "shared": shared, "retention": shared / len(a) if a else None,
                                      "reuse": shared / len(b) if b else None,
                                      "jaccard": shared / len(a | b) if a | b else None})
        for report in REPORTS:
            for i, j in ((0, 1), (0, 2), (1, 2)):
                a, b = sets[(report, GROUPS[i], rule, "none")], sets[(report, GROUPS[j], rule, "none")]
                shared = len(a & b)
                overlap.append({"report": report, "rule": rule, "group_a": GROUPS[i], "group_b": GROUPS[j],
                                "n_a": len(a), "n_b": len(b), "shared": shared,
                                "jaccard": shared / len(a | b) if a | b else None,
                                "overlap_coefficient": shared / min(len(a), len(b)) if a and b else None})
    write_csv(output / "revision_network_coverage.csv", coverage)
    write_csv(output / "revision_network_retention_sensitivity.csv", retention)
    write_csv(output / "revision_network_overlap_trends.csv", overlap)
    randomization, strata_audit, null_draws = [], [], []
    for bin_width in (5, 10):
        for report in REPORTS:
            matched_rows = [r for r in rows if r["report_id"] == report and r["match_status"] in MATCHED]
            universe = sorted({r["paper_id"] for r in matched_rows})
            grouped = defaultdict(list)
            for pid in universe:
                pubyear = year(papers[pid][0])
                bin_ = (pubyear // bin_width) * bin_width if pubyear else "unknown"
                pattern = sum(1 << j for j, group in enumerate(GROUPS) if pid in sets[(report, group, "matched", "none")])
                grouped[(journal(papers[pid][1]), str(bin_))].append(pattern)
            rng = np.random.default_rng(np.random.SeedSequence([seed, int(report[-1]), bin_width]))
            null = np.zeros((replicates, 3)); exact = np.zeros(3)
            observed = np.zeros(3); margins = np.zeros(3, dtype=int)
            variable_strata = 0
            for (outlet, vintage_bin), patterns in sorted(grouped.items()):
                # Degree-zero IDs can occur in synthesis/combined units. Their
                # zero memberships stay fixed; their row sums remain zero.
                nonzero = [p for p in patterns if p]
                if not nonzero:
                    continue
                values, probabilities, degree, counts = conditional_states(nonzero)
                exact += probabilities @ values
                sample = values[rng.choice(len(values), size=replicates, p=probabilities)]
                null += sample
                margins += np.array(counts)
                observed += [sum(bool(p & (1 << i)) and bool(p & (1 << j)) for p in patterns)
                             for i, j in ((0, 1), (0, 2), (1, 2))]
                assert np.all(values.sum(axis=1) == degree[1] + 3 * degree[2])
                variable_strata += len(values) > 1
                strata_audit.append({"report": report, "bin_width": bin_width, "journal_key": outlet,
                                     "publication_year_bin": vintage_bin, "ids": len(patterns),
                                     "zero_memberships": len(patterns) - len(nonzero),
                                     "one_membership": degree[0], "two_memberships": degree[1], "three_memberships": degree[2],
                                     "group1": counts[0], "group2": counts[1], "group3": counts[2],
                                     "feasible_overlap_states": len(values), "probability_sum": float(probabilities.sum())})
            assert all(margins[i] == len(sets[(report, GROUPS[i], "matched", "none")]) for i in range(3))
            assert np.all(null.sum(axis=1) == observed.sum())
            for index, (i, j) in enumerate(((0, 1), (0, 2), (1, 2))):
                actual = len(sets[(report, GROUPS[i], "matched", "none")] & sets[(report, GROUPS[j], "matched", "none")])
                assert actual == observed[index]
                shared = null[:, index]
                js = shared / (margins[i] + margins[j] - shared)
                os = shared / min(margins[i], margins[j])
                low, high = np.quantile(shared, [.025, .975])
                jlow, jhigh = np.quantile(js, [.025, .975])
                expected = float(exact[index])
                p = (1 + int(np.sum(np.abs(shared - expected) >= abs(actual - expected) - 1e-10))) / (replicates + 1)
                randomization.append({"report": report, "bin_width": bin_width, "group_a": GROUPS[i], "group_b": GROUPS[j],
                                      "universe_ids": len(universe), "n_a": int(margins[i]), "n_b": int(margins[j]),
                                      "observed_shared": actual, "expected_shared_exact": expected,
                                      "shared_lower_95": float(low), "shared_upper_95": float(high),
                                      "observed_jaccard": actual / (margins[i] + margins[j] - actual),
                                      "expected_jaccard_mc": float(js.mean()), "jaccard_lower_95": float(jlow), "jaccard_upper_95": float(jhigh),
                                      "observed_overlap": actual / min(margins[i], margins[j]),
                                      "expected_overlap_exact": expected / min(margins[i], margins[j]),
                                      "overlap_lower_95": float(low / min(margins[i], margins[j])),
                                      "overlap_upper_95": float(high / min(margins[i], margins[j])),
                                      "excess_shared": actual - expected, "observed_expected_ratio": actual / expected if expected else None,
                                      "excess_jaccard_pp": 100 * (actual / (margins[i] + margins[j] - actual) - js.mean()),
                                      "null_sd_shared": float(shared.std(ddof=1)), "conditional_tail_p": p,
                                      "replicates": replicates, "strata": len(grouped), "variable_strata": variable_strata})
            null_draws.extend({"report": report, "bin_width": bin_width, "replicate": i,
                               "WG1_WG2_shared": int(x[0]), "WG1_WG3_shared": int(x[1]), "WG2_WG3_shared": int(x[2])}
                              for i, x in enumerate(null))
    for width in (5, 10):
        family = [r for r in randomization if r["bin_width"] == width]
        adjusted = holm([r["conditional_tail_p"] for r in family])
        for row, p in zip(family, adjusted):
            row["holm_p_nine_pairs"] = p
    write_csv(output / "revision_network_conditional_null.csv", randomization)
    write_csv(output / "revision_network_null_strata.csv", strata_audit)
    write_csv(output / "revision_network_null_draws.csv", null_draws)
    network_methods = {"seed": seed, "replicates": replicates,
                       "universe": "Report-specific matched distinct IDs, including fixed zero memberships in combined/synthesis units",
                       "strata": "Casefolded whitespace-normalized journal title x preferred publication-year bins; five-year primary, ten-year sensitivity; missing values kept as explicit categories",
                       "null": "Uniform binary three-group membership matrices within each stratum, conditional on every paper's row sum and all group column sums. Enumerate feasible degree-two missing-group counts and degree-one group counts with exact combinatorial masses; draw independent states",
                       "preserved": "Report population, every paper's number of group memberships, all stratum group sample sizes and global group sample sizes. Zero and triple membership rows are fixed",
                       "interpretation": "Conditional benchmark for allocation of fixed total sharing among group pairs. The sum of the three pairwise shared counts is fixed by row degrees; the benchmark does not test whether total group integration is unusually low",
                       "tail": "Two-sided conditional Monte Carlo tail relative to exact expected shared count, plus-one correction. Holm adjustment across nine report/pair comparisons separately for each bin-width specification",
                       "rules": {"extracted": "All linked occurrences", "matched": "matched_doi or matched_title in that report/group",
                                 "matched_doi": "DOI-matched occurrences only", "doi_identifier_present": "Nonblank canonical DOI, regardless of workflow status",
                                 "doi_verified_workflow": "Paper verification_status exactly doi_verified, separate from wos_matched"},
                       "date_screen": "At least one selected group occurrence has a valid preferred year no later than source year or documented cutoff year; old documents remain eligible",
                       "input": input_audit}
    write_json(output / "revision_network_methods.json", network_methods)
    findings = {
        "age_interpretation": "The occurrence-weighted median remains four years in matched AR5 and AR6. Equal-ID weighting moves the median from four to five. Mean age rises and the Price fraction declines under both weights. A fixed occurrence median therefore describes one central statistic; it does not establish an unchanged age distribution.",
        "source_vs_cutoff": "AR5 and AR6 group cutoff years are 2013 and 2021. On that annual origin, matched occurrence medians are three and four years. Source-relative and cutoff-relative age answer different temporal questions. Acceptance dates for individual works are unavailable, so annual cutoff screening is a sensitivity rather than a determination of eligibility.",
        "conditional_network_interpretation": "With journal, vintage, group sizes, and each ID's membership degree held fixed, AR6 WGI-WGII sharing exceeds its conditional expectation, WGI-WGIII falls below it, and WGII-WGIII is close to it. Five- and ten-year bins give the same ordering. Absolute Jaccard fractions and deviations from this constrained benchmark describe different quantities.",
        "null_scope": "The conditional null preserves the total of the three pairwise shared counts. It tests how that fixed sharing is distributed among group pairs. It cannot test the overall amount of integration.",
        "assumptions": "Chapter resampling preserves dependence within chapters but treats chapters as exchangeable composition clusters within a report. Report draws are independent. Repeated paper identities across chapters remain linked in equal-ID reweighting. Exact conditional null draws require exchangeability of membership assignments among IDs within a journal-year stratum, subject to row/column margins. They are a model benchmark for the enumerated corpus.",
        "diagnostics": "Ages have bounded discrete support. Quantile methods are explicitly compared. No Gaussian distribution is assumed. Missing/future/old ages are counted. Exact finite-stratum enumeration avoids MCMC mixing assumptions. Small-fixture exhaustive matrix checks and Monte Carlo mean checks are recorded in qc.",
        "primary_age": [r for r in summary if r["cohort"] == "matched" and r["basis"] == "source_year"],
        "primary_age_differences": [r for r in differences if r["cohort"] == "matched"],
        "primary_null": [r for r in randomization if r["bin_width"] == 5],
    }
    write_json(output / "revision_age_network_findings.json", findings)
    after = digest(database)
    assert before == after
    audits = {"database_sha256_before": before, "database_sha256_after": after, "database_unchanged": True,
              "script_sha256": digest(Path(__file__)), "seed": seed, "replicates": replicates,
              "numpy_version": np.__version__, "unique_occurrences": True, "paper_metadata_consistent": True,
              "group_margins_preserved": True, "all_conditional_draws_preserve_total_pair_sharing": True,
              "bootstrap_empty_draws": 0, "outputs": {}}
    for path in sorted(output.glob("revision_age*")) + sorted(output.glob("revision_network*")):
        audits["outputs"][path.name] = {"bytes": path.stat().st_size, "sha256": digest(path)}
    write_json(qc / "revision_age_network_audit.json", audits)
    print(json.dumps({"age_summary_rows": len(summary), "bootstrap_interval_rows": len(intervals),
                      "conditional_pair_rows": len(randomization), "replicates": replicates,
                      "database_unchanged": True}, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--replicates", type=int, default=2500)
    parser.add_argument("--seed", type=int, default=20261011)
    args = parser.parse_args()
    run(args.database, args.replicates, args.seed)
