"""Reproduce working-group and AR6 cohort decompositions from saved source CSVs.

The input CSVs were extracted read-only by analyse_corpus.py. This extension
uses its paper-ID, matching, journal, age and quartile definitions unchanged.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import json
import math
from pathlib import Path
import re
import statistics

from analyse_corpus import FALLBACK_YEARS, WG_LABELS, compare, digest, journal_key, write_csv

ROOT = Path(__file__).resolve().parent
MATCHED = {"matched_doi", "matched_title"}
REUSED = "Reused from AR5"
NEW = "New to AR6"


def read_csv(path):
    with path.open(encoding="utf-8", newline="") as stream:
        yield from csv.DictReader(stream)


def year(value):
    text = str(value or "").strip()
    return int(text) if re.fullmatch(r"[12][0-9]{3}", text) else None


def age_record(row):
    found = re.findall(r"\b([12][0-9]{3})\b", str(row["source_year"] or ""))
    source_year = int(found[0]) if len(found) == 1 else None
    base = source_year or FALLBACK_YEARS[row["report_id"]]
    cited = year(row["year_best"])
    age = base - cited if cited is not None else None
    inclusion = ("missing_publication_year" if age is None else
                 "future_publication_year" if age < 0 else
                 "older_than_60_years" if age > 60 else "included")
    return {"base_year": base, "base_year_source": "source" if source_year else "report_fallback",
            "cited_year": cited, "age": age, "inclusion": inclusion}


def age_summary(rows):
    ages = [row["age"] for row in rows if row["inclusion"] == "included"]
    exclusions = Counter(row["inclusion"] for row in rows)
    quartiles = statistics.quantiles(ages, n=4, method="exclusive") if len(ages) >= 4 else [None] * 3
    return {"matched_occurrences": len(rows), "dated_included": len(ages),
            "source_year_count": sum(row["base_year_source"] == "source" for row in rows),
            "fallback_year_count": sum(row["base_year_source"] == "report_fallback" for row in rows),
            "missing_year": exclusions["missing_publication_year"],
            "future_year": exclusions["future_publication_year"],
            "over_60_years": exclusions["older_than_60_years"],
            "median_age": statistics.median(ages) if ages else None,
            "p25": quartiles[0], "p75": quartiles[2]}


def journal_summary(ids, papers, cohort):
    counts = Counter(journal_key(papers[paper_id]["journal_best"]) for paper_id in sorted(ids))
    total = len(ids)
    known = sum(count for key, count in counts.items() if key)
    named = sorted(((key, count) for key, count in counts.items() if key), key=lambda item: (-item[1], item[0]))
    hhi = sum((count / known) ** 2 for key, count in named) if known else None
    summary = {"cohort": cohort, "distinct_papers": total, "known_journal_papers": known,
               "missing_journal_papers": counts.get("", 0),
               "missing_journal_share": counts.get("", 0) / total if total else None,
               "named_journals": len(named), "hhi_known_journals": hhi,
               "effective_journals_known": 1 / hhi if hhi else None,
               "hhi_all_including_missing_category": sum((count / total) ** 2 for count in counts.values()) if total else None,
               "top5_named_journal_share_known": sum(count for _, count in named[:5]) / known if known else None,
               "top5_named_journal_share_all": sum(count for _, count in named[:5]) / total if total else None}
    return summary, counts


def run(source_root=ROOT, output_dir=None):
    source_root = Path(source_root).expanduser().resolve()
    sources, original_results = source_root / "source_data", source_root / "results"
    output = Path(output_dir).expanduser().resolve() if output_dir else original_results
    inputs = [sources / name for name in ("reference_occurrences.csv", "papers.csv",
              "citation_age_occurrences_matched.csv", "ar6_vintage_papers_matched.csv")]
    inputs += [original_results / name for name in ("summary.json", "provenance.json", "journal_mix_matched.csv")]
    input_hashes = {str(path.relative_to(source_root)): digest(path) for path in inputs}
    original = json.loads((original_results / "summary.json").read_text(encoding="utf-8"))
    provenance = json.loads((original_results / "provenance.json").read_text(encoding="utf-8"))
    papers = {int(row["paper_id"]): {"year_best": row["year_best"], "journal_best": row["journal_best"]}
              for row in read_csv(sources / "papers.csv")}
    cohorts, group_cohorts = defaultdict(set), defaultdict(set)
    matched_rows, source_counts = [], Counter()
    occurrence_ids = set()
    for row in read_csv(sources / "reference_occurrences.csv"):
        occurrence_id = row["occurrence_id"]
        if occurrence_id in occurrence_ids:
            raise ValueError(f"Repeated occurrence_id in saved source: {occurrence_id}")
        occurrence_ids.add(occurrence_id)
        source_counts[row["report_id"]] += 1
        if row["match_status"] not in MATCHED:
            continue
        paper_id = int(row["paper_id"]) if row["paper_id"] else None
        if paper_id is not None:
            if paper_id not in papers:
                raise ValueError(f"Missing paper metadata for {paper_id}")
            if any(row[key] != papers[paper_id][key] for key in ("year_best", "journal_best")):
                raise ValueError(f"Inconsistent saved paper metadata for {paper_id}")
            cohorts[row["report_id"]].add(paper_id)
            group_cohorts[(row["report_id"], row["wg_label"])].add(paper_id)
        matched_rows.append({"occurrence_id": occurrence_id, "report_id": row["report_id"],
                             "working_group": row["wg_label"], "paper_id": paper_id, **age_record(row)})
    saved_ages = {row["occurrence_id"]: row for row in read_csv(sources / "citation_age_occurrences_matched.csv")}
    saved_vintage = {int(row["paper_id"]): row for row in read_csv(sources / "ar6_vintage_papers_matched.csv")}
    checks = []

    def check(name, observed, expected):
        if isinstance(observed, float) or isinstance(expected, float):
            passed = observed is not None and expected is not None and math.isclose(observed, expected, rel_tol=1e-12, abs_tol=1e-12)
        else:
            passed = observed == expected
        checks.append({"check": name, "observed": observed, "expected": expected, "passed": passed})
        if not passed:
            raise ValueError(f"Aggregate consistency failed: {name}; observed={observed}, expected={expected}")

    check("unique source occurrences", len(occurrence_ids), provenance["reference_occurrences_analysed"])
    check("matched age source rows", len(saved_ages), len(matched_rows))
    disagreements = 0
    for row in matched_rows:
        saved = saved_ages.get(row["occurrence_id"])
        if saved is None or any(saved[key] != str(row[key] if row[key] is not None else "")
                                for key in ("report_id", "paper_id", "base_year", "base_year_source", "cited_year", "age", "inclusion")):
            disagreements += 1
    check("independently recomputed occurrence ages disagree with original source", disagreements, 0)
    for row in original["coverage"]:
        report = row["report_id"]
        check(f"{report} extracted occurrences", source_counts[report], row["reference_occurrences"])
        check(f"{report} matched paper cohort", len(cohorts[report]), row["matched_distinct_papers"])
        check(f"{report} matched occurrences", sum(item["report_id"] == report for item in matched_rows), row["matched_occurrences"])

    a, b = cohorts["AR5"], cohorts["AR6"]
    reused, new = a & b, b - a
    baseline = next(row for row in original["transitions"] if row["variant"] == "matched_occurrences_only"
                    and row["report_a"] == "AR5" and row["report_b"] == "AR6")
    transition = compare(a, b)
    for key, value in transition.items():
        check(f"AR5–AR6 matched transition {key}", value, baseline[key])
    check("AR6 reused and new IDs are disjoint", len(reused & new), 0)
    check("AR6 reused/new partition", len(reused) + len(new), len(b))
    check("saved AR6 vintage paper-ID population", set(saved_vintage) == b, True)
    check("saved AR6 vintage cohort assignments", sum(saved_vintage[i]["cohort"] != (REUSED if i in reused else NEW) for i in b), 0)
    check("saved AR6 vintage publication years", sum(year(saved_vintage[i]["publication_year"]) != year(papers[i]["year_best"]) for i in b), 0)

    within_groups, cross_groups = [], []
    for wg in WG_LABELS:
        earlier, later = group_cohorts[("AR5", wg)], group_cohorts[("AR6", wg)]
        within_groups.append({"working_group": wg, "report_a": "AR5", "report_b": "AR6", **compare(earlier, later),
                              "ar6_reused_from_any_ar5_unit": len(later & reused),
                              "ar6_reused_from_other_ar5_unit_only": len((later & reused) - earlier),
                              "ar6_new_to_report": len(later & new),
                              "report_reuse_fraction_in_ar6_wg": len(later & reused) / len(later) if later else None})
        check(f"{wg} AR6 same-WG/other-unit/new partition", len(earlier & later) + len((later & reused) - earlier) + len(later & new), len(later))
        for report in ("AR5", "AR6"):
            original_count = next(row["matched_distinct_papers"] for row in original["working_group_cohort_counts"]
                                  if row["report_id"] == report and row["working_group"] == wg)
            check(f"{report} {wg} matched paper cohort", len(group_cohorts[(report, wg)]), original_count)
    for earlier_wg in WG_LABELS:
        for later_wg in WG_LABELS:
            left, right = group_cohorts[("AR5", earlier_wg)], group_cohorts[("AR6", later_wg)]
            cross_groups.append({"ar5_working_group": earlier_wg, "ar6_working_group": later_wg, **compare(left, right)})
    same_wg_shared = set().union(*(group_cohorts[("AR5", wg)] & group_cohorts[("AR6", wg)] for wg in WG_LABELS))
    main5 = set().union(*(group_cohorts[("AR5", wg)] for wg in WG_LABELS))
    main6 = set().union(*(group_cohorts[("AR6", wg)] for wg in WG_LABELS))
    group_paths = {"report_shared_papers": len(reused), "shared_in_at_least_one_same_main_wg": len(same_wg_shared),
                   "shared_across_main_wgs_without_same_wg_overlap": len((main5 & main6) - same_wg_shared),
                   "shared_outside_either_main_wg_union": len(reused - (main5 & main6))}
    check("report-shared WG representation partition", sum(value for key, value in group_paths.items() if key != "report_shared_papers"), len(reused))
    ar6_wg_overlap = []
    for first in WG_LABELS:
        for second in WG_LABELS:
            if first >= second:
                continue
            left, right = group_cohorts[("AR6", first)], group_cohorts[("AR6", second)]
            shared = len(left & right)
            row = {"report_id": "AR6", "wg_a": first, "wg_b": second,
                   "papers_a": len(left), "papers_b": len(right), "shared": shared,
                   "union": len(left | right), "jaccard": shared / len(left | right) if left | right else None,
                   "overlap_coefficient": shared / min(len(left), len(right)) if left and right else None,
                   "share_of_wg_a": shared / len(left) if left else None,
                   "share_of_wg_b": shared / len(right) if right else None}
            ar6_wg_overlap.append(row)
            previous = next(item for item in original["working_group_overlap_matched"]
                            if item["report_id"] == "AR6" and item["wg_a"] == first and item["wg_b"] == second)
            for key in ("papers_a", "papers_b", "shared", "jaccard"):
                check(f"AR6 {first}–{second} overlap {key}", row[key], previous[key])

    cohort_sets = {"AR5 matched": a, "AR6 matched": b, REUSED: reused, NEW: new}
    journal_summaries, journal_rows, journal_counts = [], [], {}
    display_names = {}
    for row in read_csv(original_results / "journal_mix_matched.csv"):
        if row["journal_key"]:
            display_names.setdefault(row["journal_key"], row["journal_label"])
    for label, ids in cohort_sets.items():
        summary, counts = journal_summary(ids, papers, label)
        journal_summaries.append(summary)
        journal_counts[label] = counts
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
            journal_rows.append({"cohort": label, "journal_key": key,
                                 "journal_label": display_names.get(key, "Missing journal"), "papers": count,
                                 "share_all_papers": count / len(ids) if ids else None,
                                 "share_known_journal_papers": count / summary["known_journal_papers"] if key and summary["known_journal_papers"] else None})
        if label in {"AR5 matched", "AR6 matched"}:
            original_journal = next(row for row in original["journal_concentration_matched"] if row["report_id"] == label[:3])
            for key in ("distinct_papers", "known_journal_papers", "missing_journal_papers", "named_journals", "hhi_known_journals", "effective_journals_known"):
                check(f"{label} journal {key}", summary[key], original_journal[key])
    check("AR6 cohort journal counts reconstruct pooled counts", journal_counts[REUSED] + journal_counts[NEW] == journal_counts["AR6 matched"], True)
    reused_titles = {key for key in journal_counts[REUSED] if key}
    new_titles = {key for key in journal_counts[NEW] if key}
    title_overlap = {"reused_named_titles": len(reused_titles), "new_named_titles": len(new_titles),
                     "shared_named_titles": len(reused_titles & new_titles),
                     "reused_only_named_titles": len(reused_titles - new_titles),
                     "new_only_named_titles": len(new_titles - reused_titles),
                     "union_named_titles": len(reused_titles | new_titles),
                     "jaccard": len(reused_titles & new_titles) / len(reused_titles | new_titles),
                     "overlap_coefficient": len(reused_titles & new_titles) / min(len(reused_titles), len(new_titles))}
    check("AR6 named-title union matches pooled titles", title_overlap["union_named_titles"], len(journal_counts["AR6 matched"]) - bool(journal_counts["AR6 matched"].get("", 0)))
    check("AR6 named-title inclusion–exclusion identity", len(reused_titles) + len(new_titles) - len(reused_titles & new_titles), len(reused_titles | new_titles))
    prior_journals = {key for key in journal_counts["AR5 matched"] if key}
    outlet_presence = []
    for label in ("AR6 matched", REUSED, NEW):
        counts = journal_counts[label]
        known = sum(count for key, count in counts.items() if key)
        for status, keys in (("Present in matched AR5", {key for key in counts if key in prior_journals}),
                             ("Absent from matched AR5", {key for key in counts if key and key not in prior_journals})):
            number = sum(counts[key] for key in keys)
            outlet_presence.append({"cohort": label, "outlet_presence": status, "named_journal_titles": len(keys),
                                    "papers": number, "known_journal_denominator": known,
                                    "share_known_journal_papers": number / known if known else None})
        check(f"{label} outlet presence partition", sum(row["papers"] for row in outlet_presence if row["cohort"] == label), known)

    age_by_wg, age_by_cohort = [], []
    for report in ("AR4", "AR5", "AR6"):
        for wg in ("All report units", *WG_LABELS):
            selected = [row for row in matched_rows if row["report_id"] == report and (wg == "All report units" or row["working_group"] == wg)]
            row = {"report_id": report, "working_group": wg,
                   "distinct_matched_papers": len(cohorts[report] if wg == "All report units" else group_cohorts[(report, wg)]), **age_summary(selected)}
            age_by_wg.append(row)
            check(f"{report} {wg} age inclusion partition", row["dated_included"] + row["missing_year"] + row["future_year"] + row["over_60_years"], row["matched_occurrences"])
            if wg == "All report units":
                original_age = next(item for item in original["citation_age_matched"] if item["report_id"] == report)
                for key in ("dated_included", "missing_year", "future_year", "over_60_years", "median_age", "p25", "p75"):
                    check(f"{report} matched age {key}", row[key], original_age[key])
    ar6_rows = [row for row in matched_rows if row["report_id"] == "AR6"]
    unlinked = [row for row in ar6_rows if row["paper_id"] is None]
    check("AR6 matched occurrences without linked paper ID", len(unlinked), 0)
    for wg in ("All report units", *WG_LABELS):
        for label, ids in ((REUSED, reused), (NEW, new)):
            selected = [row for row in ar6_rows if row["paper_id"] in ids and (wg == "All report units" or row["working_group"] == wg)]
            age_by_cohort.append({"report_id": "AR6", "working_group": wg, "cohort": label,
                                  "distinct_matched_papers": len(ids if wg == "All report units" else ids & group_cohorts[("AR6", wg)]),
                                  **age_summary(selected)})
        combined = next(row for row in age_by_wg if row["report_id"] == "AR6" and row["working_group"] == wg)
        for key in ("matched_occurrences", "dated_included", "missing_year", "future_year", "over_60_years"):
            check(f"AR6 {wg} age cohorts reconstruct {key}", sum(row[key] for row in age_by_cohort if row["working_group"] == wg), combined[key])
    vintage = []
    for label, ids in ((REUSED, reused), (NEW, new)):
        counts = Counter("Unknown year" if year(papers[i]["year_best"]) is None else
                         "Through 2013" if year(papers[i]["year_best"]) <= 2013 else "After 2013" for i in ids)
        for period in ("Through 2013", "After 2013", "Unknown year"):
            expected = next(row["papers"] for row in original["ar6_vintage_matched"] if row["cohort"] == label and row["vintage"] == period)
            check(f"{label} vintage {period}", counts[period], expected)
            vintage.append({"cohort": label, "vintage": period, "papers": counts[period],
                            "cohort_paper_denominator": len(ids), "share_cohort_papers": counts[period] / len(ids) if ids else None})
    occurrence_intensity = []
    for label, ids in ((REUSED, reused), (NEW, new)):
        counts = Counter(row["paper_id"] for row in ar6_rows if row["paper_id"] in ids)
        values = [counts[i] for i in sorted(ids)]
        quartiles = statistics.quantiles(values, n=4, method="exclusive") if len(values) >= 4 else [None] * 3
        occurrence_intensity.append({"cohort": label, "distinct_papers": len(ids), "matched_occurrences": sum(values),
                                     "share_ar6_matched_papers": len(ids) / len(b), "share_ar6_matched_occurrences": sum(values) / len(ar6_rows),
                                     "mean_occurrences_per_paper": statistics.mean(values) if values else None,
                                     "median_occurrences_per_paper": statistics.median(values) if values else None,
                                     "p25_occurrences_per_paper": quartiles[0], "p75_occurrences_per_paper": quartiles[2]})
    for path in inputs:
        check(f"input unchanged: {path.relative_to(source_root)}", digest(path), input_hashes[str(path.relative_to(source_root))])
    profiles = []
    for label in ("AR6 matched", REUSED, NEW):
        journal = next(row for row in journal_summaries if row["cohort"] == label)
        age = (next(row for row in age_by_wg if row["report_id"] == "AR6" and row["working_group"] == "All report units")
               if label == "AR6 matched" else
               next(row for row in age_by_cohort if row["cohort"] == label and row["working_group"] == "All report units"))
        profiles.append({"cohort": label, **{key: journal[key] for key in ("distinct_papers", "known_journal_papers", "missing_journal_papers", "named_journals", "hhi_known_journals", "effective_journals_known")},
                         **{key: age[key] for key in ("matched_occurrences", "dated_included", "median_age", "p25", "p75", "missing_year", "future_year", "over_60_years")}})
    report = {"provenance": {"input_kind": "Saved source CSVs from the read-only original extraction",
                            "database_sha256": provenance["database_sha256"], "snapshot_date": provenance["snapshot_date"],
                            "input_sha256": input_hashes, "original_definitions": provenance["definitions"],
                            "original_analysis_script_sha256": digest(source_root / "analyse_corpus.py")},
              "extension_definitions": {
                  "within_wg": "Matched paper-ID sets are formed separately within the same WG1, WG2 or WG3 in AR5 and AR6. Retention=intersection/AR5 WG; reuse=intersection/AR6 WG; Jaccard=intersection/union.",
                  "report_reuse": "A matched AR6 paper is reused if its paper ID also appears in the matched AR5 cohort, across any report unit. A paper absent from the matched AR5 cohort is new to the matched AR6 cohort.",
                  "wg_representation": "A paper can occur in several working groups. WG rows and cross-WG intersections overlap and must not be summed as independent paper counts. Different WG representation is descriptive, not a measured transfer of research.",
                  "wg_overlap_coefficient": "Within AR6, shared/min(WG a papers, WG b papers); equivalently, the share of the smaller WG cohort also represented in the other group. Jaccard instead uses the union denominator.",
                  "journal_denominator": "Each distinct paper contributes once to its cohort's named-title distribution. Known-journal papers form the HHI and named-share denominator; missing metadata is counted separately. Effective titles=1/HHI. HHI is a descriptive concentration measure, not adjusted for cohort size.",
                  "outlet_presence": "A named title is present in AR5 when at least one matched AR5 paper has the identical casefolded, whitespace-collapsed title. An absent title is newly represented in this extracted cohort, not necessarily a newly established journal.",
                  "journal_title_overlap": "Intersection and differences of exact normalized named-title sets among the reused and new AR6 paper-ID cohorts. Title-set overlap is distinct from overlap of paper IDs, which are disjoint by definition.",
                  "age_by_cohort": "Only matched AR6 occurrences are partitioned by their linked paper's membership in the report-wide reused/new cohorts. Repeated occurrences remain repeated. Source year and age 0–60 rule are unchanged.",
                  "quartiles": "statistics.quantiles(method='exclusive'), exactly as in analyse_corpus.py; IQRs describe records, not uncertainty.",
                  "occurrence_intensity": "Counts only matched AR6 occurrences per cohort paper ID. Reused/new paper shares and occurrence shares use separate denominators.",
                  "interpretation": "Descriptive decompositions of the extracted, matched corpus. Matching and inherited paper identity remain subject to the original identity-quality qualification."},
              "report_transition": transition, "within_working_group_transitions": within_groups,
              "cross_working_group_intersections": cross_groups, "working_group_representation_paths": group_paths,
              "within_ar6_working_group_overlap": ar6_wg_overlap,
              "journal_concentration_by_cohort": journal_summaries, "outlet_presence_by_cohort": outlet_presence,
              "journal_title_overlap_between_ar6_cohorts": title_overlap, "ar6_cohort_profiles": profiles,
              "citation_age_by_working_group": age_by_wg, "citation_age_by_ar6_cohort": age_by_cohort,
              "ar6_vintage_by_cohort": vintage, "ar6_occurrence_intensity": occurrence_intensity,
              "quality_checks": {"all_passed": all(row["passed"] for row in checks), "checks": checks}}
    output.mkdir(parents=True, exist_ok=True)
    for name, rows in (("deepened_wg_transitions.csv", within_groups), ("deepened_cross_wg_intersections.csv", cross_groups),
                       ("deepened_journal_concentration.csv", journal_summaries), ("deepened_journal_mix.csv", journal_rows),
                       ("deepened_outlet_presence.csv", outlet_presence), ("deepened_age_by_wg.csv", age_by_wg),
                       ("deepened_age_by_ar6_cohort.csv", age_by_cohort), ("deepened_ar6_vintage.csv", vintage),
                       ("deepened_occurrence_intensity.csv", occurrence_intensity), ("deepened_ar6_wg_overlap.csv", ar6_wg_overlap),
                       ("deepened_ar6_cohort_profiles.csv", profiles)):
        write_csv(output / name, rows)
    (output / "deepened_analysis.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_tables(output, within_groups, profiles, age_by_wg)
    print(json.dumps({"within_wg": within_groups, "journal_cohorts": journal_summaries,
                      "ar6_cohort_ages": [row for row in age_by_cohort if row["working_group"] == "All report units"],
                      "wg_representation_paths": group_paths, "journal_title_overlap": title_overlap,
                      "ar6_wg_overlap": ar6_wg_overlap, "quality_checks_passed": len(checks)}, indent=2))
    return report


def write_tables(output, groups, profiles, group_ages):
    transition_rows = [f"{row['working_group']} & {row['papers_a']:,} & {row['papers_b']:,} & {row['shared']:,} & {row['retention']*100:.2f} & {row['reuse']*100:.2f} & {row['jaccard']*100:.2f} \\\\" for row in groups]
    (output / "deepened_wg_transition_table.tex").write_text("\n".join([
        r"\begin{table}[H]", r"\centering\small",
        r"\caption{Matched paper continuity within the same working group from AR5 to AR6. Retention uses the AR5 group denominator; reuse uses the AR6 group denominator. Working-group cohorts can overlap.}",
        r"\label{tab:wg_continuity}", r"\begin{tabular}{@{}lrrrrrr@{}}", r"\toprule",
        r"Group & AR5 papers & AR6 papers & Shared & Retention (\%) & Reuse (\%) & Jaccard (\%) \\",
        r"\midrule", *transition_rows, r"\bottomrule", r"\end{tabular}", r"\end{table}", ""]), encoding="utf-8")
    cohort_rows = []
    for row in profiles:
        display = "All matched AR6" if row["cohort"] == "AR6 matched" else row["cohort"]
        cohort_rows.append(f"{display} & {row['distinct_papers']:,} & {row['dated_included']:,} & {row['median_age']:g} ({row['p25']:g}--{row['p75']:g}) & {row['known_journal_papers']:,} & {row['hhi_known_journals']:.4f} & {row['effective_journals_known']:.1f} \\\\")
    missing_new = next(row["missing_journal_papers"] for row in profiles if row["cohort"] == NEW)
    (output / "deepened_cohort_profile_table.tex").write_text("\n".join([
        r"\begin{table}[htbp]", r"\centering\small",
        r"\caption{Outlet concentration and publication age of matched AR6 paper cohorts. HHI uses paper IDs with named outlet metadata; " + str(missing_new) + r" new papers lack it. Effective outlets equal the reciprocal of HHI. Ages are occurrence-weighted, restricted to 0--60 years, and reported as median (interquartile range).}",
        r"\label{tab:cohort_profiles}", r"\begin{tabular}{@{}lrrrrrr@{}}", r"\toprule",
        r"AR6 cohort & \shortstack{Paper\\IDs} & \shortstack{Dated\\occurrences} & \shortstack{Age\\(years)} & \shortstack{IDs with\\outlet} & HHI & \shortstack{Effective\\outlets} \\",
        r"\midrule", *cohort_rows, r"\bottomrule", r"\end{tabular}", r"\end{table}", ""]), encoding="utf-8")
    group_rows = []
    for wg in WG_LABELS:
        cells = []
        for report in ("AR4", "AR5", "AR6"):
            row = next(item for item in group_ages if item["report_id"] == report and item["working_group"] == wg)
            cells.append(f"{row['median_age']:g} ({row['p25']:g}--{row['p75']:g}); {row['dated_included']:,}")
        group_rows.append(f"{wg} & " + " & ".join(cells) + r" \\")
    (output / "deepened_wg_age_table.tex").write_text("\n".join([
        r"\begin{table}[H]", r"\centering\small",
        r"\caption{Matched citation age by working group: median (interquartile range) in years, followed by the dated occurrence denominator. Ages use each source volume's publication year and include values from 0 to 60 years.}",
        r"\label{tab:wg_ages}", r"\begin{tabular}{@{}lrrr@{}}", r"\toprule", r"Group & AR4 & AR5 & AR6 \\",
        r"\midrule", *group_rows, r"\bottomrule", r"\end{tabular}", r"\end{table}", ""]), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT, help="Directory containing the original source_data/ and results/.")
    parser.add_argument("--output-dir", type=Path, help="Output directory; defaults to results/ within --source-root.")
    args = parser.parse_args()
    run(args.source_root, args.output_dir)
