#!/usr/bin/env python3
"""Prepare, validate and analyse human annotations of the fixed IPCC sample.

The sampling unit is paper_id, with source occurrence rows nested within ID.
Probability strata are fixed; targeted supplements have no sampling weight.
Raw source CSVs and their key fields remain immutable. Missing decisions yield
a pending result rather than an accuracy estimate. No database edits are made.
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
import shutil
import sys

ROOT = Path(__file__).resolve().parent
SEED = 20261011
PROBABILITY = "stratified_hash_sample"
TARGETED = "targeted_supplement_not_probability_sample"
SOURCE_NAMES = {
    "papers": "revision_identity_audit_sample_papers.csv",
    "occurrences": "revision_identity_audit_sample_entries.csv",
    "strata": "revision_identity_audit_strata.csv",
    "summary": "revision_identity_summary.json",
}
ID_FIELDS = {
    "annotator": None,
    "review_complete": ["yes", "no"],
    "identity_label": ["correct_merge", "false_merge", "unresolved"],
    "shared_link_label": ["same_work", "false_shared", "not_shared", "unresolved"],
    "missed_merge_search_status": ["not_started", "incomplete", "complete"],
    "missed_merge_result": ["none_found", "missed_merge_found", "unresolved"],
    "missed_merge_search_methods": None,
    "missed_merge_candidate_ids": None,
    "missed_merge_search_notes": None,
    "evidence_urls": None,
    "review_notes": None,
}
OCCURRENCE_FIELDS = {
    "annotator": None,
    "work_groups": None,
    "metadata_judgment": ["correct", "metadata_error", "unresolved"],
    "metadata_error_fields": None,
    "evidence_urls": None,
    "review_notes": None,
}
WEIGHT_FIELDS = ("population_ids", "sampled_ids", "inclusion_probability", "design_weight")
SEARCH_METHODS = {"raw_reference", "raw_doi", "title", "author_year", "candidate_raw_entries"}
METADATA_FIELDS = {"doi", "title", "year", "journal", "authors", "other"}
MATCHED_STATUS_SETS = {"doi_only", "title_only", "doi_and_title"}


def is_ai_annotator(value):
    """Identify explicit tool provenance, without scanning reviewers' notes."""
    return bool(re.search(r"\bcodex\b|\bai\b|\bchatgpt\b", str(value or ""), re.I))


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_csv(path):
    with Path(path).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n", quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")


def split_values(value):
    return {v.strip() for v in re.split(r"[|\n]", str(value or "")) if v.strip()}


def is_shared(row):
    return all(row[k] in MATCHED_STATUS_SETS for k in ("AR5_status_set", "AR6_status_set"))


def load_bundle(root=ROOT):
    root = Path(root)
    manifest = json.loads((root/"input_manifest.json").read_text(encoding="utf-8"))
    for kind, item in manifest["sources"].items():
        if digest(root/"inputs"/item["filename"]) != item["sha256"]:
            raise ValueError(f"Immutable source digest changed: {kind}")
    papers = read_csv(root/"inputs"/SOURCE_NAMES["papers"])
    occurrences = read_csv(root/"inputs"/SOURCE_NAMES["occurrences"])
    strata = read_csv(root/"inputs"/SOURCE_NAMES["strata"])
    summary = json.loads((root/"inputs"/SOURCE_NAMES["summary"]).read_text(encoding="utf-8"))
    return manifest, papers, occurrences, strata, summary


def canonical_templates(papers, occurrences, strata):
    strata_by_name = {r["stratum"]: r for r in strata}
    if len(strata_by_name) != len(strata):
        raise ValueError("Duplicate sampling strata")
    paper_weights = {}
    for row in papers:
        if row["selection"] == PROBABILITY:
            s = strata_by_name[row["stratum"]]
            n, N = int(s["sampled_ids"]), int(s["population_ids"])
            if not 0 < n <= N:
                raise ValueError("Invalid stratum finite population")
            if not math.isclose(float(s["inclusion_probability"]), n/N, rel_tol=1e-12):
                raise ValueError("Stratum inclusion probability is inconsistent")
            weight = dict(zip(WEIGHT_FIELDS, (str(N), str(n), format(n/N, ".17g"), format(N/n, ".17g"))))
        elif row["selection"] == TARGETED:
            weight = dict.fromkeys(WEIGHT_FIELDS, "")
        else:
            raise ValueError("Unknown source sample selection")
        paper_weights[row["paper_id"]] = weight
    ids = [{**row, **paper_weights[row["paper_id"]], **dict.fromkeys(ID_FIELDS, "")} for row in papers]
    entries = [{**row, **paper_weights[row["paper_id"]], **dict.fromkeys(OCCURRENCE_FIELDS, "")} for row in occurrences]
    return ids, entries


def prepare(source_results, root=ROOT):
    """Freeze current originals once, then create separate empty templates."""
    source_results, root = Path(source_results), Path(root)
    if (root/"input_manifest.json").exists() or (root/"inputs").exists():
        raise ValueError("Inputs already exist; choose a new destination instead of overwriting them")
    papers = read_csv(source_results/SOURCE_NAMES["papers"])
    occurrences = read_csv(source_results/SOURCE_NAMES["occurrences"])
    strata = read_csv(source_results/SOURCE_NAMES["strata"])
    summary = json.loads((source_results/SOURCE_NAMES["summary"]).read_text(encoding="utf-8"))
    if len({r["paper_id"] for r in papers}) != len(papers):
        raise ValueError("Original sample paper IDs are not unique")
    if len({r["occurrence_id"] for r in occurrences}) != len(occurrences):
        raise ValueError("Original occurrence IDs are not unique")
    ids = {r["paper_id"] for r in papers}
    if {r["paper_id"] for r in occurrences} != ids:
        raise ValueError("Original occurrences and sampled IDs do not agree")
    counts = Counter(r["paper_id"] for r in occurrences)
    if any(counts[r["paper_id"]] != int(r["occurrences_reviewed"]) for r in papers):
        raise ValueError("Original paper occurrence counts do not agree")
    prob = [r for r in papers if r["selection"] == PROBABILITY]
    paper_map = {r["paper_id"]:r for r in papers}
    if any(r["selection"] != paper_map[r["paper_id"]]["selection"] or
           r["sample_stratum"] != paper_map[r["paper_id"]]["stratum"] for r in occurrences):
        raise ValueError("Original occurrence selections and strata do not agree with IDs")
    observed_strata = Counter(r["stratum"] for r in prob)
    if observed_strata != Counter({r["stratum"]: int(r["sampled_ids"]) for r in strata}):
        raise ValueError("Sample stratum counts do not agree")
    if summary["sample"]["seed"] != SEED or len(prob) != summary["sample"]["probability_sample_ids"]:
        raise ValueError("Source sampling seed or count differs")
    expected_summary = {"all_sample_ids":len(papers), "targeted_supplement_ids":len(papers)-len(prob),
        "raw_entries_exported":len(occurrences),"frame_size":sum(int(r["population_ids"]) for r in strata)}
    if any(k in summary["sample"] and summary["sample"][k] != v for k,v in expected_summary.items()):
        raise ValueError("Source summary and original sample counts do not agree")
    if any(r["human_label"] != "unreviewed" for r in papers) or any(r["human_identity_label"] != "unreviewed" for r in occurrences):
        raise ValueError("This preparation requires the untouched unreviewed source sample")
    id_rows, occurrence_rows = canonical_templates(papers, occurrences, strata)
    (root/"inputs").mkdir(parents=True)
    (root/"templates").mkdir()
    sources = {}
    for kind, name in SOURCE_NAMES.items():
        shutil.copyfile(source_results/name, root/"inputs"/name)
        sources[kind] = {"filename": name, "sha256": digest(root/"inputs"/name)}
    manifest = {
        "schema_version": 1, "sampling_seed": SEED,
        "database_sha256": summary["provenance"]["database_sha256"],
        "sources": sources,
        "counts": {"sampled_ids": len(papers), "probability_ids": len(prob),
            "targeted_ids": len(papers)-len(prob), "occurrences": len(occurrences),
            "strata": len(strata), "frame_ids": sum(int(r["population_ids"]) for r in strata)},
        "sampling": "SHA256(seed|stratum|paper_id) rank; up to 12 IDs per mutually exclusive stratum. Targeted supplements remain separate.",
        "inference_design": "Treat seeded hash ranking as pseudo-random simple random sampling without replacement within fixed strata. Intervals condition on this sampling design and the adjudicated labels.",
        "annotation_status": "No manual labels supplied; empty annotation templates only",
    }
    write_json(root/"input_manifest.json", manifest)
    write_json(root/"annotation_schema.json", {"id_fields": ID_FIELDS, "occurrence_fields": OCCURRENCE_FIELDS,
        "search_methods": sorted(SEARCH_METHODS), "metadata_fields": sorted(METADATA_FIELDS),
        "blank_is_allowed_for_work_in_progress": True, "immutable_fields": "Every source and sampling-weight field"})
    write_csv(root/"templates"/"id_annotations.csv", id_rows)
    write_csv(root/"templates"/"occurrence_annotations.csv", occurrence_rows)
    report, _, _ = validate(root, root/"templates"/"id_annotations.csv", root/"templates"/"occurrence_annotations.csv")
    if report["errors"] or report["missing_files"]:
        raise ValueError("Generated empty templates failed immutable-field validation")
    write_json(root/"initial_integrity_report.json", report)
    write_json(root/"pending_registry.json", {
        "status": "pending_complete_human_annotation", "registered_outputs": [],
        "baseline_reference_tables_modified": False,
        "planned_outputs": ["annotation_integrity.json", "annotation_estimates.json", "annotation_strata.csv", "annotation_rates.csv", "annotation_recurrence_stress.csv"],
        "registration_condition": "Valid complete human labels and explicit search completion for the relevant estimands. Register only actual generated outputs with their hashes after validation.",
    })
    return manifest


def _label_rows(path, expected_rows, key, label_fields, errors, report):
    if path is None or not Path(path).is_file():
        report["missing_files"].append(str(path) if path else key+" label file not provided")
        return {}
    expected = {r[key]: r for r in expected_rows}
    selected = {}
    duplicates = consistent_duplicates = 0
    rows = read_csv(path)
    for number, row in enumerate(rows, 2):
        k = row.get(key, "")
        if k not in expected:
            errors.append(f"{key} row {number}: missing or unexpected key {k!r}")
            continue
        for field, value in expected[k].items():
            if field in label_fields:
                continue
            actual = row.get(field)
            if field in WEIGHT_FIELDS and value and actual is not None:
                try:
                    same = math.isclose(float(actual), float(value), rel_tol=1e-12, abs_tol=0)
                except ValueError:
                    same = False
            else:
                same = actual == value
            if not same:
                errors.append(f"{key} {k}: immutable field differs: {field}")
        for field, allowed in label_fields.items():
            if field not in row:
                errors.append(f"{key} {k}: annotation column missing: {field}")
            elif allowed is not None and row[field] and row[field] not in allowed:
                errors.append(f"{key} {k}: invalid {field}: {row[field]!r}")
        unknown = set(row)-set(expected[k])-set(label_fields)
        if unknown:
            errors.append(f"{key} {k}: unknown columns: {sorted(unknown)}")
        if k in selected:
            duplicates += 1
            if row != selected[k]:
                errors.append(f"{key} {k}: conflicting duplicate rows")
            else:
                consistent_duplicates += 1
        else:
            selected[k] = row
    missing = set(expected)-set(selected)
    if missing:
        errors.append(f"{key}: missing {len(missing)} expected keys")
    report["observed"][key] = {"rows": len(rows), "unique_keys": len(selected),
        "duplicate_rows": duplicates,"consistent_duplicate_rows": consistent_duplicates,
        "blank_fields": {f: sum(not r.get(f) for r in selected.values()) for f in label_fields}}
    return selected


def validate(root=ROOT, id_labels=None, occurrence_labels=None):
    manifest, papers, occurrences, strata, summary = load_bundle(root)
    ids_expected, entries_expected = canonical_templates(papers, occurrences, strata)
    report = {"schema_version": 1, "status": "pending_annotation", "source_unchanged": True,
        "expected": manifest["counts"], "observed": {}, "missing_files": [], "errors": [],
        "readiness": {}, "labels_repaired": False}
    errors = report["errors"]
    ids = _label_rows(id_labels, ids_expected, "paper_id", ID_FIELDS, errors, report)
    entries = _label_rows(occurrence_labels, entries_expected, "occurrence_id", OCCURRENCE_FIELDS, errors, report)
    by_pid = defaultdict(list)
    for row in entries.values():
        by_pid[row["paper_id"]].append(row)
        fields = split_values(row.get("metadata_error_fields"))
        if fields-METADATA_FIELDS:
            errors.append(f"occurrence {row['occurrence_id']}: invalid metadata fields")
        if row.get("metadata_judgment") == "correct" and fields:
            errors.append(f"occurrence {row['occurrence_id']}: correct metadata cannot have error fields")
        if row.get("metadata_judgment") == "metadata_error" and not fields:
            errors.append(f"occurrence {row['occurrence_id']}: specify the erroneous metadata fields")
        if row.get("metadata_judgment") == "metadata_error" and not (row.get("review_notes") or row.get("evidence_urls")):
            errors.append(f"occurrence {row['occurrence_id']}: metadata error needs notes or evidence")
    for pid, row in ids.items():
        rr = by_pid[pid]
        ai_review = is_ai_annotator(row.get("annotator")) or any(is_ai_annotator(r.get("annotator")) for r in rr)
        if ai_review and (row.get("review_complete") == "yes" or row.get("missed_merge_search_status") == "complete"):
            errors.append(f"paper {pid}: AI suggestions cannot complete a review or search; source-check the entries and use the actual reviewer's name or code")
        methods = split_values(row.get("missed_merge_search_methods"))
        if methods-SEARCH_METHODS:
            errors.append(f"paper {pid}: invalid missed-merge search methods")
        candidates = split_values(row.get("missed_merge_candidate_ids"))
        if any(not re.fullmatch(r"[1-9][0-9]*", c) or c == pid for c in candidates):
            errors.append(f"paper {pid}: candidate IDs must be other positive integer IDs")
        if row.get("missed_merge_search_status") == "complete":
            if row.get("missed_merge_result") not in {"none_found", "missed_merge_found"}:
                errors.append(f"paper {pid}: completed search requires an explicit result")
            if not (methods & {"raw_doi", "title"}) or not {"raw_reference", "author_year"} <= methods or not row.get("missed_merge_search_notes"):
                errors.append(f"paper {pid}: completed search needs original raw-reference, DOI/title, author-year and search notes")
            if row.get("missed_merge_result") == "missed_merge_found" and (not candidates or "candidate_raw_entries" not in methods):
                errors.append(f"paper {pid}: confirmed missed merge needs candidate IDs and candidate raw-entry checks")
            if row.get("missed_merge_result") == "none_found" and candidates:
                errors.append(f"paper {pid}: none_found conflicts with confirmed candidate IDs")
        if row.get("review_complete") != "yes":
            continue
        if not row.get("annotator") or not (row.get("review_notes") or row.get("evidence_urls")):
            errors.append(f"paper {pid}: completed review needs annotator and notes/evidence")
        if row.get("identity_label") not in {"correct_merge", "false_merge"}:
            errors.append(f"paper {pid}: completed review requires a resolved identity label")
        if any(not split_values(r.get("work_groups")) or r.get("metadata_judgment") not in {"correct", "metadata_error"} for r in rr):
            errors.append(f"paper {pid}: complete every occurrence's work group and metadata judgment")
        groups = set().union(*(split_values(r.get("work_groups")) for r in rr)) if rr else set()
        if row.get("identity_label") == "correct_merge" and len(groups) != 1:
            errors.append(f"paper {pid}: correct_merge requires one distinct work group")
        if row.get("identity_label") == "false_merge" and len(groups) < 2:
            errors.append(f"paper {pid}: false_merge requires at least two distinct work groups")
        if is_shared(row):
            group_sets = {report_id: set().union(*(split_values(r.get("work_groups")) for r in rr
                if r["report_id"] == report_id and r["match_status"] in {"matched_doi", "matched_title"})) for report_id in ("AR5", "AR6")}
            real_shared = bool(group_sets["AR5"] & group_sets["AR6"])
            expected_link = "same_work" if real_shared else "false_shared"
            if row.get("shared_link_label") != expected_link:
                errors.append(f"paper {pid}: shared-link label conflicts with occurrence work groups")
        elif row.get("shared_link_label") != "not_shared":
            errors.append(f"paper {pid}: this ID is outside the observed shared-matched set")
    probability = [r for r in papers if r["selection"] == PROBABILITY]
    prob_ids = {r["paper_id"] for r in probability}
    shared_prob = {r["paper_id"] for r in probability if is_shared(r)}
    valid = not errors and not report["missing_files"]
    ready_id = lambda pid: (ids.get(pid, {}).get("review_complete") == "yes"
                           and not is_ai_annotator(ids.get(pid, {}).get("annotator"))
                           and not any(is_ai_annotator(r.get("annotator")) for r in by_pid[pid]))
    report["readiness"] = {
        "probability_identity": valid and all(ready_id(pid) for pid in prob_ids),
        "probability_shared_link": valid and all(ready_id(pid) for pid in shared_prob),
        "probability_metadata": valid and all(ready_id(pid) for pid in prob_ids),
        "probability_missed_merge_search": valid and all(ready_id(pid) and ids[pid].get("missed_merge_search_status") == "complete" for pid in prob_ids),
        "targeted_descriptive": valid and all(ready_id(r["paper_id"]) for r in papers if r["selection"] == TARGETED),
        "targeted_missed_merge_search": valid and all(ready_id(r["paper_id"]) and ids[r["paper_id"]].get("missed_merge_search_status") == "complete" for r in papers if r["selection"] == TARGETED),
    }
    report["completed_ids"] = sum(ready_id(pid) for pid in ids)
    report["completed_probability_ids"] = sum(ready_id(pid) for pid in prob_ids)
    report["completed_searches"] = sum(r.get("missed_merge_search_status") == "complete"
        and not is_ai_annotator(r.get("annotator"))
        and not any(is_ai_annotator(o.get("annotator")) for o in by_pid[pid]) for pid, r in ids.items())
    report["status"] = "invalid" if errors else "ready_for_estimates" if all(report["readiness"].values()) else "pending_annotation"
    return report, ids, entries


def hypergeom_tail(N, K, n, k, upper):
    lo, hi = max(0, n-(N-K)), min(n, K)
    values = range(max(lo, k), hi+1) if upper else range(lo, min(hi, k)+1)
    denominator = math.comb(N, n)
    return math.fsum(math.comb(K, x)*math.comb(N-K, n-x)/denominator for x in values)


def finite_binary_interval(N, n, k, alpha):
    """Invert both hypergeometric tails for unknown finite-population successes."""
    if not 0 <= k <= n <= N or n == 0 or not 0 < alpha < 1:
        raise ValueError("Invalid finite-population binary interval parameters")
    if n == N:
        return k, k
    threshold = alpha/2
    lo, hi = 0, N
    while lo < hi:
        mid = (lo+hi)//2
        if hypergeom_tail(N, mid, n, k, True) >= threshold:
            hi = mid
        else:
            lo = mid+1
    lower = lo
    lo, hi = 0, N
    while lo < hi:
        mid = (lo+hi+1)//2
        if hypergeom_tail(N, mid, n, k, False) >= threshold:
            lo = mid
        else:
            hi = mid-1
    return lower, lo


def stratified_binary(strata, sample, outcome, *, metric, domain_prefix=None, alpha=.05):
    """HT ID-total point estimate with conservative simultaneous exact bounds."""
    selected_strata = [s for s in strata if domain_prefix is None or s["stratum"].startswith(domain_prefix)]
    by_stratum = defaultdict(list)
    for row in sample:
        by_stratum[row["stratum"]].append(row)
    uncertain = sum(int(s["sampled_ids"]) < int(s["population_ids"]) for s in selected_strata)
    total_N = total_n = 0
    total = variance = 0.0
    lower = upper = 0
    details = []
    for s in selected_strata:
        N, n = int(s["population_ids"]), int(s["sampled_ids"])
        rr = by_stratum[s["stratum"]]
        if len(rr) != n or len({r["paper_id"] for r in rr}) != n:
            raise ValueError("Probability sample IDs do not match a complete stratum")
        values = [outcome(r) for r in rr]
        if any(v not in (0, 1) for v in values):
            raise ValueError("Binary outcomes must be resolved 0/1 labels")
        k = sum(values)
        estimate = N*k/n
        lb, ub = finite_binary_interval(N, n, k, alpha/max(1, uncertain))
        total_N += N; total_n += n; total += estimate; lower += lb; upper += ub
        if n < N:
            if n > 1 and variance is not None:
                sample_var = (k-k*k/n)/(n-1)
                variance += N*N*(1-n/N)*sample_var/n
            else:
                variance = None
        details.append({"metric": metric, "stratum": s["stratum"], "population_ids": N,
            "sampled_ids": n, "positive_sample_ids": k, "design_weight": N/n,
            "estimated_positive_ids": estimate, "lower_positive_ids": lb, "upper_positive_ids": ub,
            "finite_population_fraction": n/N})
    if not total_N:
        raise ValueError("The estimand has no population strata")
    rate = {"metric": metric, "unit": "paper_id", "denominator_ids": total_N,
        "within_ID_audit_scope":"Reviewed AR5/AR6 sample occurrences only; other-report occurrences of these IDs are outside this within-ID audit",
        "probability_sample_ids": total_n, "estimated_positive_ids": total,
        "rate": total/total_N, "lower_95": lower/total_N, "upper_95": upper/total_N,
        "lower_positive_ids": lower, "upper_positive_ids": upper,
        "design_standard_error": math.sqrt(variance)/total_N if variance is not None else None,
        "interval": "Finite-population hypergeometric inversion; Bonferroni simultaneous noncensus-stratum coverage; weighted stratum totals aggregated",
        "confidence_level": 1-alpha, "uncertain_strata": uncertain, "sampling_seed": SEED}
    return rate, details


def estimate(root=ROOT, id_labels=None, occurrence_labels=None, output=None):
    report, ids, entries = validate(root, id_labels, occurrence_labels)
    manifest, papers, _, strata, summary = load_bundle(root)
    result = {"status": "pending_annotation", "sampling_seed": SEED,
        "database_sha256": manifest["database_sha256"], "integrity": report,
        "rates": [], "strata": [], "recurrence_stress": [], "targeted_descriptive": None,
        "scope": "Weighted ID-level results for the union of matched AR5/AR6 IDs, using only their exported AR5/AR6 occurrences for within-ID and metadata adjudication. Other-report occurrences of those IDs are not audited here. Targeted supplements are descriptive. Missed-merge searches examine counterparts anywhere in the saved database and measure audited-ID search findings, not global pair recall."}
    if report["errors"] or report["missing_files"]:
        result["status"] = report["status"]
    elif all(report["readiness"].values()):
        sample = [ids[r["paper_id"]] for r in papers if r["selection"] == PROBABILITY]
        by_pid = defaultdict(list)
        for row in entries.values():
            by_pid[row["paper_id"]].append(row)
        specs = []
        if report["readiness"]["probability_identity"]:
            specs.extend([
                ("within_ID_identity_precision", lambda r: int(r["identity_label"] == "correct_merge"), None),
                ("ID_false_merge_rate", lambda r: int(r["identity_label"] == "false_merge"), None),
                ("ID_any_metadata_error_rate", lambda r: int(any(x["metadata_judgment"] == "metadata_error" for x in by_pid[r["paper_id"]])), None),
                ("shared_ID_false_merge_rate", lambda r: int(r["identity_label"] == "false_merge"), "shared_matched|"),
                ("shared_ID_false_shared_rate", lambda r: int(r["shared_link_label"] == "false_shared"), "shared_matched|"),
            ])
        if report["readiness"]["probability_missed_merge_search"]:
            specs.append(("audited_ID_confirmed_missed_merge_rate", lambda r: int(r["missed_merge_result"] == "missed_merge_found"), None))
        for metric, outcome, domain in specs:
            rate, detail = stratified_binary(strata, sample, outcome, metric=metric, domain_prefix=domain)
            result["rates"].append(rate); result["strata"].extend(detail)
        counts = summary["main_counts"]
        shared, earlier, later = counts["shared_matched"], counts["matched_AR5"], counts["matched_AR6"]
        for rate in result["rates"]:
            if rate["metric"] not in {"shared_ID_false_merge_rate", "shared_ID_false_shared_rate"}:
                continue
            if rate["denominator_ids"] != shared:
                raise ValueError("Shared-stratum population does not match recurrence denominator")
            assumed_loss = rate["estimated_positive_ids"]
            result["recurrence_stress"].append({"error_mechanism": rate["metric"],
                "assumption": "Every affected shared ID loses its shared-numerator contribution; other identity changes are held outside this calculation",
                "observed_shared_ids": shared, "AR5_denominator_fixed": earlier, "AR6_denominator_fixed": later,
                "estimated_affected_shared_ids": assumed_loss,
                "retention_stress": (shared-assumed_loss)/earlier,
                "retention_lower_95_stress": (shared-rate["upper_positive_ids"])/earlier,
                "retention_upper_95_stress": (shared-rate["lower_positive_ids"])/earlier,
                "reuse_stress": (shared-assumed_loss)/later,
                "reuse_lower_95_stress": (shared-rate["upper_positive_ids"])/later,
                "reuse_upper_95_stress": (shared-rate["lower_positive_ids"])/later,
                "interpretation": "Weighted error-count sensitivity; no corrected database or corrected unique-work count is constructed"})
        if report["readiness"]["targeted_descriptive"]:
            targeted = [ids[r["paper_id"]] for r in papers if r["selection"] == TARGETED]
            result["targeted_descriptive"] = {"IDs": len(targeted),
                "identity_labels": dict(Counter(r["identity_label"] for r in targeted)),
                "shared_link_labels": dict(Counter(r["shared_link_label"] for r in targeted)),
                "missed_merge_results":dict(Counter(r["missed_merge_result"] for r in targeted)),
                "IDs_with_any_metadata_error":sum(any(e["metadata_judgment"]=="metadata_error" for e in by_pid[r["paper_id"]]) for r in targeted),
                "weighting": "No population weight; purposively selected cases only"}
        if result["rates"]:
            result["status"] = "all_planned_estimates_ready"
    if output:
        output = Path(output)
        if output.exists():
            raise ValueError("Choose a new estimate output directory; existing outputs are preserved")
        output.mkdir(parents=True)
        write_json(output/"annotation_integrity.json", report)
        if not result["rates"]:
            return result
        write_json(output/"annotation_estimates.json", result)
        for name, rows in [("annotation_rates.csv", result["rates"]), ("annotation_strata.csv", result["strata"]),
                           ("annotation_recurrence_stress.csv", result["recurrence_stress"])]:
            if rows:
                write_csv(output/name, rows)
        write_json(output/"output_manifest.json", {"status": result["status"], "database_sha256": manifest["database_sha256"],
            "files": [{"filename": p.name, "sha256": digest(p)} for p in sorted(output.iterdir()) if p.is_file() and not p.name.startswith("._")],
            "numeric_estimates_generated": bool(result["rates"])})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--source-results", type=Path, required=True)
    prep.add_argument("--root", type=Path, default=ROOT)
    for name in ("validate", "estimate"):
        command = sub.add_parser(name)
        command.add_argument("--root", type=Path, default=ROOT)
        command.add_argument("--id-labels", type=Path)
        command.add_argument("--occurrence-labels", type=Path)
        if name == "estimate":
            command.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.source_results, args.root)
        elif args.command == "validate":
            result = validate(args.root, args.id_labels, args.occurrence_labels)[0]
        else:
            result = estimate(args.root, args.id_labels, args.occurrence_labels, args.output)
        print(json.dumps(result["integrity"] if args.command == "estimate" and not result["rates"] else result, indent=2, ensure_ascii=False))
        return 0 if args.command == "prepare" or result["status"] in {"ready_for_estimates", "all_planned_estimates_ready"} else 2
    except (OSError, ValueError, KeyError) as exc:
        print(f"Annotation check failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
