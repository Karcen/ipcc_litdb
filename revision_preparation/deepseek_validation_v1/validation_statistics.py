#!/usr/bin/env python3
"""Offline, model-conditioned diagnostics for the frozen bibliographic panel.

Units are assigned paper IDs (census/sample), reference occurrences (parser and
source samples), and candidate ID pairs (geometry only). Joins use asserted
unique keys. Inputs are immutable; unavailable and unresolved outputs stay in
their fixed denominators. Three configurations use two models from one provider
family, with no human adjudication. No network, credentials, or model calls.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3

CONFIGS = ("flash_A", "flash_B", "pro_C")
REVIEWERS = (*CONFIGS, "consensus")
CASE_FIELDS = ("identity", "cross_report_recurrence")
QC_POLICY_VERSION = "postclassification_joint_qc_v1"
SOURCE_TYPES = ("journal", "book", "report", "conference", "other", "unresolvable", "unavailable", "no_majority")
MODEL_LIMIT = "Conditional on model outputs from three configurations of two models in one provider family; no human labels or accuracy inference."
PROBABILITY = "stratified_hash_sample"
TARGETED = "targeted_supplement_not_probability_sample"


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def read_csv(path):
    with Path(path).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows, fields=None):
    if fields is None:
        fields = list(dict.fromkeys(k for row in rows for k in row))
    with Path(path).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def unique(rows, keys, *, allow_empty=False):
    """Assert a declared ID contract and return a one-to-one lookup."""
    result = {}
    for row in rows:
        key = tuple(row[k] for k in keys)
        if (not allow_empty and any(v is None or v == "" for v in key)) or key in result:
            raise ValueError(f"nonunique/missing key {keys}: {key}")
        result[key] = row
    return result


def canon_outlet(value):
    return re.sub(r"[^\w]+", "", str(value or "").casefold())


def rule_year(raw):
    """Exact frozen R3 behavior: first regex year in the first 300 characters."""
    match = re.search(r"\b(1[6-9]\d{2}|20\d{2})[a-z]?\b", str(raw or "")[:300])
    return match.group(1) if match else "unresolvable"


def _binomial_cdf(k, n, p):
    if k < 0:
        return 0.0
    if k >= n or p == 0:
        return 1.0
    if p == 1:
        return 0.0
    logs = [math.lgamma(n + 1) - math.lgamma(j + 1) - math.lgamma(n - j + 1)
            + j * math.log(p) + (n - j) * math.log1p(-p) for j in range(k + 1)]
    maximum = max(logs)
    return min(1.0, math.exp(maximum) * math.fsum(math.exp(x - maximum) for x in logs))


def clopper_pearson(k, n, alpha=0.05):
    """Two-sided exact binomial interval, descriptive sampling uncertainty only.

    No interpretation as uncertainty about true labels, model correctness, or
    dependence between reviewer configurations is permitted.
    """
    if not isinstance(k, int) or not isinstance(n, int) or not 0 <= k <= n or not 0 < alpha < 1:
        raise ValueError("CP requires integer 0 <= k <= n and 0 < alpha < 1")
    if n == 0:
        return None, None
    def solve(cutoff, target):
        low, high = 0.0, 1.0
        for _ in range(65):
            mid = (low + high) / 2
            if _binomial_cdf(cutoff, n, mid) > target:
                low = mid
            else:
                high = mid
        return (low + high) / 2
    lower = 0.0 if k == 0 else solve(k - 1, 1 - alpha / 2)
    upper = 1.0 if k == n else solve(k, alpha / 2)
    return lower, upper


def label(row, field, reviewer):
    """Keep transport availability, model abstention, and no-majority distinct."""
    if field in CASE_FIELDS and row.get("qc_policy_version"):
        if reviewer != "consensus" and int(row["qc_excluded_" + reviewer]):
            return "qc_excluded"
        if reviewer == "consensus" and decision(row, field) == "no_majority":
            return "no_majority"
        if reviewer != "consensus" and int(row["available_" + reviewer]) != 1:
            return "unavailable"
        value = row["qc_" + field + "_" + reviewer]
        return value if value not in (None, "") else "unresolvable"
    if reviewer == "consensus":
        if row[field + "_decision"] == "no_majority":
            return "no_majority"
    elif int(row["available_" + reviewer]) != 1:
        return "unavailable"
    value = row[field + "_" + reviewer]
    return value if value not in (None, "") else "unresolvable"


def decision(row, field):
    return row.get("qc_" + field + "_decision", row[field + "_decision"])


def qc_policy(seed):
    """Explicit registration timing: this rule was not preregistered before calls."""
    return {"policy_version": QC_POLICY_VERSION, "sampling_seed": int(seed), "uses_randomization": False,
            "registered_date": "2026-10-25",
            "registration_stage": "After partial full-run classifications, before complete full-run aggregate statistics; mechanical consistency QC, not preregistered identity adjudication.",
            "rule": "For exactly two focal-citation records, one earlier and one later, identity=different_studies plus cross_report_recurrence=recurrent is logically inconsistent.",
            "exclusion_unit": "configuration by case, both identity and recurrence votes jointly",
            "excluded_vote_handling": "No eligible vote for either case-level field; retained in all fixed denominators as unresolved; distinct from model unresolvable and transport unavailable.",
            "consensus": "Recompute from the original three configuration slots after exclusion; require at least two matching eligible votes. No majority yields unresolved. Genuine model unresolvable remains an eligible vote.",
            "record_extractions": "Retain all record-level year, outlet, source type and multiple-citation extractions unchanged.",
            "multi_record_cases": "Do not flag different_studies plus recurrent when more than two records are supplied.",
            "raw_data": "Never alter raw call archives or aggregate outputs; publish a separate derived case view with original columns preserved.",
            "interpretation": "No claim of human accuracy, true identity, true error rate, or independent-model reliability."}


def register_qc_policy(root, seed):
    directory = Path(root) / "work/statistics_policy"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (QC_POLICY_VERSION + ".json")
    expected = qc_policy(seed)
    if path.exists():
        if read_json(path) != expected:
            raise ValueError("registered post-classification QC policy changed")
    else:
        dump_json(path, expected)
    return path


def _eligible_majority(votes):
    if len(votes) != 3:
        raise ValueError("QC consensus retains exactly three configuration slots")
    counts = collections.Counter(value for value in votes if value is not None)
    if not counts:
        return "unresolvable", "no_majority"
    value, count = counts.most_common(1)[0]
    if count < 2:
        return "unresolvable", "no_majority"
    return value, "unanimous" if count == 3 else "two_of_three"


def derive_qc_cases(inputs, cases):
    """A separate post-classification view; original columns remain untouched."""
    raw_qc = logical_consistency_tables(inputs, cases)
    raw_flags = {(r["arm"], r["reviewer"], r["case_id"]) for r in raw_qc["logical_consistency_flags"]}
    derived, exclusions, changes = [], [], []
    for source in cases:
        if source.get("qc_policy_version"):
            raise ValueError("refuse to apply post-classification QC twice")
        row = dict(source)
        row["qc_policy_version"] = QC_POLICY_VERSION
        for config in CONFIGS:
            excluded = (row["arm"], config, row["case_id"]) in raw_flags
            row["qc_excluded_" + config] = int(excluded)
            row["qc_eligible_" + config] = int(int(row["available_" + config]) == 1 and not excluded)
            if excluded:
                key = inputs["key"][row["case_id"]]
                exclusions.append({"arm": row["arm"], "reviewer": config, "case_id": row["case_id"], "origin": key["origin"],
                                   "record_ids": "|".join(r["record_id"] for r in inputs["cases"][row["case_id"]]["records"]),
                                   "occurrence_ids": "|".join(key["occurrence_ids"]),
                                   "raw_identity_label": label(source, "identity", config),
                                   "raw_recurrence_label": label(source, "cross_report_recurrence", config),
                                   "qc_eligible": 0, "qc_excluded": 1, "record_extractions_retained": 1,
                                   "policy_version": QC_POLICY_VERSION})
            for field in CASE_FIELDS:
                row["qc_" + field + "_" + config] = "qc_excluded" if excluded else source[field + "_" + config]
        row["qc_eligible_configurations"] = sum(row["qc_eligible_" + config] for config in CONFIGS)
        row["qc_excluded_configurations"] = sum(row["qc_excluded_" + config] for config in CONFIGS)
        for field in CASE_FIELDS:
            votes = [source[field + "_" + config] if row["qc_eligible_" + config] else None for config in CONFIGS]
            value, status = _eligible_majority(votes)
            row["qc_" + field + "_consensus"] = value
            row["qc_" + field + "_decision"] = status
            raw_label, derived_label = label(source, field, "consensus"), label(row, field, "consensus")
            if raw_label != derived_label or source[field + "_decision"] != status:
                changes.append({"arm": row["arm"], "case_id": row["case_id"], "field": field,
                                "raw_consensus_label": raw_label, "qc_consensus_label": derived_label,
                                "raw_consensus_decision": source[field + "_decision"], "qc_consensus_decision": status,
                                "label_changed": int(raw_label != derived_label),
                                "decision_changed": int(source[field + "_decision"] != status),
                                "qc_excluded_configurations": row["qc_excluded_configurations"],
                                "qc_eligible_configurations": row["qc_eligible_configurations"], "policy_version": QC_POLICY_VERSION})
        derived.append(row)
    post_qc = logical_consistency_tables(inputs, derived)
    if post_qc["logical_consistency_flags"]:
        raise ValueError("joint incoherence remains after registered QC derivation")
    derived_lookup = unique(derived, ["arm", "case_id"])
    for source in cases:
        if any(derived_lookup[(source["arm"], source["case_id"])][k] != v for k, v in source.items()):
            raise ValueError("QC derivation modified an original aggregate column")
    scopes = {"all_cases": set(inputs["cases"]),
              "identity_cases": {cid for cid, packet in inputs["cases"].items() if packet["task"] == "identity"},
              "shared_id_census": {r["case_id"] for r in inputs.get("case_key", []) if r["origin"].startswith("current_shared_id:")},
              "probability_id_sample": {r["case_id"] for r in inputs.get("sample_roles", []) if r["selection"] == PROBABILITY},
              "targeted_id_sample": {r["case_id"] for r in inputs.get("sample_roles", []) if r["selection"] == TARGETED},
              "provisional_controls": {r["case_id"] for r in inputs.get("control_key", [])}}
    coverage = []
    for arm in sorted({r["arm"] for r in derived}):
        for scope, ids in scopes.items():
            items = [r for r in derived if r["arm"] == arm and r["case_id"] in ids]
            for reviewer in REVIEWERS:
                consensus = reviewer == "consensus"
                supported = lambda r, field: decision(r, field) != "no_majority" if consensus else bool(r["qc_eligible_" + reviewer])
                coverage.append({"arm": arm, "reviewer": reviewer, "scope": scope, "scheduled_n": len(items),
                                 "transport_available_n": None if consensus else sum(int(r["available_" + reviewer]) == 1 for r in items),
                                 "raw_supported_joint_consensus_n": sum(r["identity_decision"] != "no_majority" and r["cross_report_recurrence_decision"] != "no_majority" for r in items) if consensus else None,
                                 "raw_joint_incoherence_n": sum((arm, reviewer, r["case_id"]) in raw_flags for r in items),
                                 "qc_excluded_n": 0 if consensus else sum(r["qc_excluded_" + reviewer] for r in items),
                                 "cases_with_any_qc_exclusion_n": sum(r["qc_excluded_configurations"] > 0 for r in items),
                                 "qc_eligible_identity_n": sum(supported(r, "identity") for r in items),
                                 "qc_eligible_recurrence_n": sum(supported(r, "cross_report_recurrence") for r in items),
                                 "model_unresolvable_identity_n": sum(label(r, "identity", reviewer) == "unresolvable" for r in items),
                                 "model_unresolvable_recurrence_n": sum(label(r, "cross_report_recurrence", reviewer) == "unresolvable" for r in items),
                                 "identity_no_majority_n": sum(label(r, "identity", reviewer) == "no_majority" for r in items),
                                 "recurrence_no_majority_n": sum(label(r, "cross_report_recurrence", reviewer) == "no_majority" for r in items),
                                 "interpretation": "Overlapping scopes are not additive. QC excludes both case-level votes, retains transport availability and record extractions, and does not infer corrected identities."})
    return derived, {"case_qc_derived": derived, "qc_configuration_exclusions": exclusions,
                     "qc_consensus_changes": changes, "qc_exclusion_coverage": coverage,
                     "post_qc_logical_consistency_counts": post_qc["logical_consistency_counts"]}


def binary_state(value, positive, negative):
    if value == positive:
        return "positive"
    if value in negative:
        return "negative"
    return "unresolved"


def count_states(states):
    counts = collections.Counter(states)
    if set(counts) - {"positive", "negative", "unresolved"}:
        raise ValueError("invalid binary outcome state")
    return {"positive_n": counts["positive"], "negative_n": counts["negative"],
            "resolved_n": counts["positive"] + counts["negative"], "unresolved_n": counts["unresolved"]}


def freeze_analysis_inputs(root, repository):
    """Copy needed local baseline files and export memberships from hashed DB.

    This is an explicitly invoked preparation step; normal statistics runs read
    only the private frozen copies. The source database is opened read-only and
    its hash is checked both before and after the export.
    """
    root, repository = Path(root).resolve(), Path(repository).resolve()
    destination = root / "work/analysis_inputs"
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("analysis_inputs is already frozen; refusing overwrite")
    destination.mkdir(parents=True, exist_ok=True)
    preparation = read_json(root / "work/inputs/preparation_manifest.json")
    relative = {
        "revision_identity_transitions.csv": "reproducibility/reference/revision_identity_transitions.csv",
        "revision_identity_audit_strata.csv": "revision_preparation/annotation/inputs/revision_identity_audit_strata.csv",
        "revision_identity_audit_sample_papers.csv": "revision_preparation/annotation/inputs/revision_identity_audit_sample_papers.csv",
    }
    manifest = {"status": "frozen_local_inputs", "source_repository": str(repository), "input_sources": {}, "output_sha256": {}}
    for name, rel in relative.items():
        source = repository / rel
        expected = preparation["inputs"].get(rel)
        digest = sha(source)
        if expected and digest != expected:
            raise ValueError(f"preparation hash differs: {rel}")
        shutil.copyfile(source, destination / name)
        manifest["input_sources"][name] = {"repository_path": rel, "source_sha256": digest,
                                             "original_preparation_input_sha256": expected}
    database = repository / "database/ipcc_litdb.sqlite"
    digest = sha(database)
    if digest != preparation["inputs"]["database/ipcc_litdb.sqlite"]:
        raise ValueError("frozen database hash differs")
    query = """SELECT o.paper_id,
      SUM(CASE WHEN c.report_id='AR5' AND o.match_status IN ('matched_doi','matched_title') THEN 1 ELSE 0 END) AS AR5_matched_occurrences,
      SUM(CASE WHEN c.report_id='AR6' AND o.match_status IN ('matched_doi','matched_title') THEN 1 ELSE 0 END) AS AR6_matched_occurrences
      FROM references_occurrence o JOIN chapters c USING(chapter_id)
      WHERE c.report_id IN ('AR5','AR6') AND o.paper_id IS NOT NULL
      GROUP BY o.paper_id ORDER BY o.paper_id"""
    with sqlite3.connect(f"file:{database}?mode=ro&immutable=1", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        members = [dict(row) for row in connection.execute(query)]
    if sha(database) != digest:
        raise ValueError("source database changed during read-only export")
    write_csv(destination / "matched_id_membership.csv", members)
    manifest["membership_export"] = {"database_sha256": digest, "query": query, "rows": len(members),
                                      "unit": "assigned paper ID with any AR5/AR6 occurrence", "database_unchanged": True}
    manifest["output_sha256"] = {p.name: sha(p) for p in sorted(destination.iterdir()) if p.is_file()}
    dump_json(destination / "manifest.json", manifest)
    return manifest


def load_inputs(root):
    root = Path(root).resolve()
    frames, analysis = root / "work/inputs/frames", root / "work/analysis_inputs"
    frozen_manifest = read_json(analysis / "manifest.json")
    for name, digest in frozen_manifest["output_sha256"].items():
        if sha(analysis / name) != digest:
            raise ValueError(f"frozen analysis input changed: {name}")
    preparation = read_json(root / "work/inputs/preparation_manifest.json")
    frame_names = ("case_key", "sample_roles", "source_roles", "sampling_design", "control_key", "blind_all")
    for name in frame_names:
        if sha(frames / (name + ".json")) != preparation["outputs"]["frames/" + name + ".json"]:
            raise ValueError(f"canonical prepared frame hash differs: {name}")
    if frozen_manifest["membership_export"]["database_sha256"] != preparation["inputs"]["database/ipcc_litdb.sqlite"]:
        raise ValueError("membership database differs from canonical preparation")
    result = {name: read_json(frames / (name + ".json")) for name in
              ("case_key", "sample_roles", "source_roles", "sampling_design", "control_key")}
    result["cases"] = {r["case_id"]: r for r in read_json(frames / "blind_all.json")["cases"]}
    unique(result["case_key"], ["case_id"])
    if set(result["cases"]) != {r["case_id"] for r in result["case_key"]}:
        raise ValueError("case-key coverage differs from packet")
    result["key"] = {r["case_id"]: r for r in result["case_key"]}
    result["sample_papers"] = read_csv(analysis / "revision_identity_audit_sample_papers.csv")
    result["sample_strata"] = read_csv(analysis / "revision_identity_audit_strata.csv")
    members = read_csv(analysis / "matched_id_membership.csv")
    unique(members, ["paper_id"])
    result["membership"] = {r["paper_id"]: r for r in members}
    result["baseline"] = [r for r in read_csv(analysis / "revision_identity_transitions.csv")
                           if r["earlier"] == "AR5" and r["later"] == "AR6" and r["definition"] == "matched"]
    if len(result["baseline"]) != 1:
        raise ValueError("expected one frozen matched AR5→AR6 transition")
    result["baseline"] = result["baseline"][0]
    baseline = result["baseline"]
    matched_sets = {report: {pid for pid, row in result["membership"].items() if int(row[report + "_matched_occurrences"]) > 0}
                    for report in ("AR5", "AR6")}
    shared = matched_sets["AR5"] & matched_sets["AR6"]
    if (len(matched_sets["AR5"]), len(matched_sets["AR6"]), len(shared)) != (int(baseline["papers_a"]), int(baseline["papers_b"]), int(baseline["shared"])):
        raise ValueError("exported matched membership differs from frozen transition denominators")
    census = {r["origin"].split(":", 1)[1] for r in result["case_key"] if r["origin"].startswith("current_shared_id:")}
    if census != shared:
        raise ValueError("census paper-ID membership differs from frozen matched shared set")
    input_manifest = read_json(root / "work/input_manifest.json")
    candidate_path = root / "work/inputs/candidates/candidate_pairs.csv"
    if sha(candidate_path) != input_manifest["copied_inputs"]["candidates/candidate_pairs.csv"]["sha256"]:
        raise ValueError("frozen candidate-pair file changed")
    result["candidate_pairs"] = read_csv(candidate_path)
    result["input_paths"] = [frames / (name + ".json") for name in
                              ("case_key", "sample_roles", "source_roles", "sampling_design", "control_key", "blind_all")]
    result["input_paths"] += [p for p in analysis.iterdir() if p.is_file()]
    result["input_paths"] += [candidate_path, root / "work/inputs/preparation_manifest.json", root / "work/input_manifest.json"]
    return result


def candidate_geometry(pairs, membership):
    """Candidate-frame geometry only; no inferred same-study labels."""
    unique(pairs, ["case_id"])
    rows, endpoints, graph = [], collections.Counter(), collections.defaultdict(set)
    for pair in pairs:
        left, right = pair["AR5_paper_id"], pair["AR6_paper_id"]
        if left == right or left not in membership or right not in membership:
            raise ValueError("candidate pair needs two distinct frozen assigned IDs")
        def presence(pid):
            return {report for report in ("AR5", "AR6") if int(membership[pid][report + "_matched_occurrences"]) > 0}
        a, b = presence(left), presence(right)
        shared_delta = int(len(a | b) == 2) - int(len(a) == 2) - int(len(b) == 2)
        if shared_delta == 1:
            geometry = "opposite_report_only_matched_ids"
        elif shared_delta == -1:
            geometry = "two_already_shared_matched_ids"
        elif len(a) == 2 or len(b) == 2:
            geometry = "already_shared_plus_other_id"
        elif not a or not b:
            geometry = "at_least_one_id_has_no_matched_presence"
        else:
            geometry = "same_report_only_matched_ids"
        row = {"case_id": pair["case_id"], "AR5_paper_id": left, "AR6_paper_id": right,
               "AR5_occurrence_id": pair["AR5_occurrence_id"], "AR6_occurrence_id": pair["AR6_occurrence_id"],
               "left_matched_reports": "|".join(sorted(a)), "right_matched_reports": "|".join(sorted(b)),
               "geometry": geometry, "shared_delta_if_only_pair_merged": shared_delta,
               "AR5_denominator_delta_if_merged": -int("AR5" in a and "AR5" in b),
               "AR6_denominator_delta_if_merged": -int("AR6" in a and "AR6" in b),
               "raw_doi_relation": pair["raw_doi_relation"], "blocking_methods": pair["blocking_methods"],
               "priority": "direct_shared_increase_candidate" if shared_delta == 1 else "other_geometry",
               "interpretation": "Geometry conditional on accepting this pair alone; no identity label and overlapping pairs cannot be summed."}
        rows.append(row)
        endpoints.update((left, right)); graph[left].add(right); graph[right].add(left)
    # Connected components expose dependence between candidate pairs.
    component, seen = {}, set()
    for start in sorted(graph):
        if start in seen:
            continue
        stack, nodes = [start], set()
        while stack:
            node = stack.pop()
            if node in nodes:
                continue
            nodes.add(node); stack.extend(graph[node] - nodes)
        seen |= nodes
        cid = "G" + hashlib.sha256("|".join(sorted(nodes)).encode()).hexdigest()[:12]
        for node in nodes:
            component[node] = (cid, len(nodes))
    for row in rows:
        cid, size = component[row["AR5_paper_id"]]
        row.update(component_id=cid, component_ids=size,
                   left_candidate_degree=endpoints[row["AR5_paper_id"]], right_candidate_degree=endpoints[row["AR6_paper_id"]])
    summary = []
    for geometry in sorted({r["geometry"] for r in rows}):
        group = [r for r in rows if r["geometry"] == geometry]
        summary.append({"geometry": geometry, "candidate_pair_rows": len(group),
                        "unique_unordered_id_pairs": len({tuple(sorted((r["AR5_paper_id"], r["AR6_paper_id"]))) for r in group}),
                        "distinct_assigned_ids": len({r[k] for r in group for k in ("AR5_paper_id", "AR6_paper_id")}),
                        "components": len({r["component_id"] for r in group}),
                        "pairs_with_reused_endpoint": sum(r["left_candidate_degree"] > 1 or r["right_candidate_degree"] > 1 for r in group),
                        "max_component_ids": max(r["component_ids"] for r in group),
                        "labels_available": 0, "interpretation": "Unlabelled candidate frame; pair deltas are not an estimated shared-count correction."})
    return rows, summary


LOGICAL_FLAG_FIELDS = ["arm", "reviewer", "case_id", "origin", "record_ids", "occurrence_ids", "identity_label", "recurrence_label", "joint_incoherence", "rule", "interpretation"]
QC_EXCLUSION_FIELDS = ["arm", "reviewer", "case_id", "origin", "record_ids", "occurrence_ids", "raw_identity_label", "raw_recurrence_label", "qc_eligible", "qc_excluded", "record_extractions_retained", "policy_version"]
QC_CHANGE_FIELDS = ["arm", "case_id", "field", "raw_consensus_label", "qc_consensus_label", "raw_consensus_decision", "qc_consensus_decision", "label_changed", "decision_changed", "qc_excluded_configurations", "qc_eligible_configurations", "policy_version"]


def logical_consistency_tables(inputs, cases):
    """Detect a joint-label contradiction, without rewriting either label.

    Exactly two focal citations, one from each report group, cannot be disjoint
    studies yet support a common study. With three or more citations the same
    pair of labels can be coherent and is deliberately not flagged.
    """
    flags, grouped = [], collections.defaultdict(list)
    for row in cases:
        packet = inputs["cases"][row["case_id"]]
        if packet["task"] != "identity":
            continue
        records = packet["records"]
        cross_report = {r["report_group"] for r in records} == {"earlier", "later"}
        if not cross_report:
            continue
        eligible = len(records) == 2
        for reviewer in REVIEWERS:
            identity = label(row, "identity", reviewer)
            recurrence = label(row, "cross_report_recurrence", reviewer)
            mixed_recurrent = identity == "different_studies" and recurrence == "recurrent"
            contradiction = eligible and mixed_recurrent
            resolved = identity in {"same_study", "different_studies"} and recurrence in {"recurrent", "non_recurrent"}
            grouped[(row["arm"], reviewer)].append((eligible, resolved, contradiction, len(records) > 2 and mixed_recurrent))
            if contradiction:
                key = inputs["key"][row["case_id"]]
                flags.append({"arm": row["arm"], "reviewer": reviewer, "case_id": row["case_id"], "origin": key["origin"],
                              "record_ids": "|".join(r["record_id"] for r in records), "occurrence_ids": "|".join(key["occurrence_ids"]),
                              "identity_label": identity, "recurrence_label": recurrence, "joint_incoherence": 1,
                              "rule": "two_records_one_per_report_different_studies_cannot_recur",
                              "interpretation": "Archived labels retained. Joint semantic contradiction is handled only in a separate registered post-classification QC view."})
    counts = []
    for (arm, reviewer), items in sorted(grouped.items()):
        eligible = [r for r in items if r[0]]
        counts.append({"arm": arm, "reviewer": reviewer, "cross_report_identity_cases_n": len(items),
                       "two_record_eligible_n": len(eligible), "two_record_joint_resolved_n": sum(r[1] for r in eligible),
                       "two_record_joint_unresolved_n": sum(not r[1] for r in eligible), "joint_incoherence_n": sum(r[2] for r in eligible),
                       "multi_record_mixed_recurrent_n": sum(r[3] for r in items),
                       "interpretation": "Two-record inverse only; multi-record mixed-study cases may support recurrence and are retained. No labels reclassified."})
    return {"logical_consistency_flags": flags, "logical_consistency_counts": counts}


def census_tables(inputs, cases):
    ids = {r["case_id"] for r in inputs["case_key"] if r["origin"].startswith("current_shared_id:")}
    expected = int(inputs["sampling_design"]["shared_id_census"])
    baseline = inputs["baseline"]
    if len(ids) != expected or expected != int(baseline["shared"]):
        raise ValueError("census and frozen baseline shared denominator differ")
    a, b = int(baseline["papers_a"]), int(baseline["papers_b"])
    counts, availability, scenarios = [], [], []
    for arm in sorted({r["arm"] for r in cases}):
        items = [r for r in cases if r["arm"] == arm and r["case_id"] in ids]
        if len(items) != expected:
            raise ValueError("incomplete shared-ID census")
        for field in ("identity", "cross_report_recurrence"):
            for available_configs in range(4):
                availability.append({"arm": arm, "field": field, "available_configurations": available_configs,
                                     "availability_basis": "transport", "consensus_status": "all", "paper_ids": sum(int(r["available_configurations"]) == available_configs for r in items), "census_N": expected})
                if any(r.get("qc_policy_version") for r in items):
                    availability.append({"arm": arm, "field": field, "available_configurations": available_configs,
                                         "availability_basis": "qc_eligible", "consensus_status": "all",
                                         "paper_ids": sum(r["qc_eligible_configurations"] == available_configs for r in items), "census_N": expected})
            for consensus_status in ("unanimous", "two_of_three", "no_majority"):
                availability.append({"arm": arm, "field": field, "available_configurations": "all",
                                     "availability_basis": "qc_consensus" if any(r.get("qc_policy_version") for r in items) else "raw_consensus",
                                     "consensus_status": consensus_status, "paper_ids": sum(decision(r, field) == consensus_status for r in items), "census_N": expected})
            for reviewer in REVIEWERS:
                values = collections.Counter(label(row, field, reviewer) for row in items)
                for value, count in sorted(values.items()):
                    counts.append({"arm": arm, "reviewer": reviewer, "field": field, "model_label": value,
                                   "paper_ids": count, "census_N": expected, "interpretation": "QC-excluded case-level votes remain distinct and unresolved in fixed-denominator scenarios. " + MODEL_LIMIT})
        for reviewer in REVIEWERS:
            states = count_states(binary_state(label(r, "cross_report_recurrence", reviewer), "recurrent", {"non_recurrent"}) for r in items)
            lower, upper = states["positive_n"], states["positive_n"] + states["unresolved_n"]
            scenarios.append({"arm": arm, "reviewer": reviewer, "census_N": expected, "AR5_fixed_denominator": a,
                              "AR6_fixed_denominator": b, "recurrent_n": lower, "non_recurrent_n": states["negative_n"],
                              "unresolved_n": states["unresolved_n"], "S_lower": lower, "S_upper": upper,
                              "retention_lower": lower / a, "retention_upper": upper / a,
                              "reuse_lower": lower / b, "reuse_upper": upper / b,
                              "jaccard_lower": lower / (a + b - lower), "jaccard_upper": upper / (a + b - upper),
                              "interval_kind": "unresolved_label_scenario_bounds",
                              "interpretation": "AR5/AR6 matched-ID denominators held fixed; no correction for unobserved missing links or changed ID units. " + MODEL_LIMIT})
    return {"shared_census_labels": counts, "shared_census_availability": availability, "recurrence_scenarios": scenarios}


def _any_discrepancy(records, field, reviewer, preferred):
    baseline = canon_outlet(preferred) if field == "outlet" else str(preferred or "")
    if not baseline or (field == "cited_year" and not re.fullmatch(r"\d{4}", baseline)):
        return "unresolved"
    values = [label(r, field, reviewer) for r in records]
    unknown = {"unresolvable", "unavailable", "no_majority"}
    if any(v not in unknown and v != baseline for v in values):
        return "positive"
    if not values or any(v in unknown for v in values):
        return "unresolved"
    return "negative"


def sample_tables(inputs, cases, records):
    roles, papers = inputs["sample_roles"], inputs["sample_papers"]
    unique(roles, ["case_id"]); unique(roles, ["paper_id"])
    paper_lookup = {r["paper_id"]: r for r in papers}
    if set(paper_lookup) != {r["paper_id"] for r in roles}:
        raise ValueError("sample paper-ID mapping differs")
    design = {r["stratum"]: r for r in inputs["sample_strata"]}
    case_lookup = unique(cases, ["arm", "case_id"])
    record_lookup = collections.defaultdict(list)
    for row in records:
        record_lookup[(row["arm"], row["case_id"])].append(row)
    actual = collections.Counter(r["sample_stratum"] for r in roles if r["selection"] == PROBABILITY)
    if set(actual) != set(design) or any(actual[s] != int(d["sampled_ids"]) for s, d in design.items()):
        raise ValueError("probability sample stratum N/n contract differs")
    outcomes = []
    for arm in sorted({r["arm"] for r in cases}):
        for role in roles:
            pid, cid = role["paper_id"], role["case_id"]
            paper = paper_lookup[pid]
            if (paper["stratum"], paper["selection"]) != (role["sample_stratum"], role["selection"]):
                raise ValueError("sample source-role mismatch")
            row = case_lookup[(arm, cid)]
            group = "probability" if role["selection"] == PROBABILITY else "targeted"
            if role["selection"] not in {PROBABILITY, TARGETED}:
                raise ValueError("unknown sample selection")
            for reviewer in REVIEWERS:
                states = {"mixed_study": binary_state(label(row, "identity", reviewer), "different_studies", {"same_study"}),
                          "year_discrepancy_any_record": _any_discrepancy(record_lookup[(arm, cid)], "cited_year", reviewer, paper["year_best"]),
                          "outlet_discrepancy_any_record": _any_discrepancy(record_lookup[(arm, cid)], "outlet", reviewer, paper["journal_best"])}
                # Eligibility is frozen report membership, never a model label.
                if role["sample_stratum"].split("|")[0] == "shared_matched":
                    states["nonrecurrence_shared_only"] = binary_state(label(row, "cross_report_recurrence", reviewer), "non_recurrent", {"recurrent"})
                for outcome, state in states.items():
                    outcomes.append({"arm": arm, "reviewer": reviewer, "sample_set": group, "stratum": role["sample_stratum"],
                                     "paper_id": pid, "case_id": cid, "outcome": outcome, "state": state})
    stratum_rows, projections = [], []
    grouped = collections.defaultdict(list)
    for row in outcomes:
        grouped[(row["arm"], row["reviewer"], row["sample_set"], row["stratum"], row["outcome"])].append(row["state"])
    for (arm, reviewer, group, stratum, outcome), states in sorted(grouped.items()):
        counts = count_states(states)
        n = len(states)
        N = int(design[stratum]["population_ids"]) if group == "probability" else None
        stratum_rows.append({"arm": arm, "reviewer": reviewer, "sample_set": group, "stratum": stratum, "outcome": outcome,
                             "population_N": N, "actual_sample_n": n, "N_over_n": N / n if N is not None else None,
                             **counts, "sample_lower_share": counts["positive_n"] / n,
                             "sample_upper_share": (counts["positive_n"] + counts["unresolved_n"]) / n,
                             "interpretation": "Any-record year/outlet discrepancy versus preferred paper metadata, after punctuation/case outlet normalization; not semantic outlet adjudication. " + MODEL_LIMIT})
    grouped = collections.defaultdict(list)
    for row in stratum_rows:
        grouped[(row["arm"], row["reviewer"], row["sample_set"], row["outcome"])].append(row)
    for (arm, reviewer, group, outcome), rows in sorted(grouped.items()):
        n = sum(r["actual_sample_n"] for r in rows)
        N = sum(r["population_N"] for r in rows) if group == "probability" else None
        weighted = lambda field: sum(r[field] * (r["N_over_n"] if group == "probability" else 1) for r in rows)
        positive, unresolved, resolved = weighted("positive_n"), weighted("unresolved_n"), weighted("resolved_n")
        denominator = N if N is not None else n
        projections.append({"arm": arm, "reviewer": reviewer, "sample_set": group, "outcome": outcome, "population_N": N,
                            "actual_sample_n": n, "observed_positive_n": sum(r["positive_n"] for r in rows),
                            "observed_resolved_n": sum(r["resolved_n"] for r in rows), "observed_unresolved_n": sum(r["unresolved_n"] for r in rows),
                            "weighted_positive_count": positive if N is not None else None,
                            "weighted_resolved_count": resolved if N is not None else None,
                            "weighted_unresolved_count": unresolved if N is not None else None,
                            "lower_share": positive / denominator, "upper_share": (positive + unresolved) / denominator,
                            "status": "design_weighted_model_label_scenario" if N is not None else "targeted_sample_description_only",
                            "interval_kind": "unresolved_label_scenario_bounds", "interpretation": "Stratum N/n expansion of model-defined paper-ID outcomes; targeted supplements are separate. " + MODEL_LIMIT})
    return {"sample_id_outcomes": outcomes, "sample_stratum_diagnostics": stratum_rows, "sample_weighted_diagnostics": projections}


def occurrence_tables(inputs, records):
    key, design = inputs["key"], inputs["sampling_design"]
    parser_roles = {r["case_id"]: r for r in inputs["case_key"] if r["origin"].startswith("parser:")}
    source_roles = {r["case_id"]: r for r in inputs["source_roles"]}
    parser_occ = {r["occurrence_ids"][0] for r in parser_roles.values()}
    source_occ = {r["occurrence_id"] for r in source_roles.values()}
    overlap = [{"sampling_frames": "parser|source", "parser_occurrences": len(parser_occ), "source_occurrences": len(source_occ),
                "overlapping_occurrences": len(parser_occ & source_occ), "unique_occurrences": len(parser_occ | source_occ),
                "interpretation": "Separate probability designs; overlapping source/parser occurrences are never pooled as independent observations."}]
    parser_detail, source_detail = [], []
    for row in records:
        cid = row["case_id"]
        for reviewer in REVIEWERS:
            if cid in parser_roles:
                role = parser_roles[cid]
                packet = inputs["cases"][cid]
                if len(packet["records"]) != 1:
                    raise ValueError("parser unit must be one occurrence")
                value, rule = label(row, "cited_year", reviewer), rule_year(packet["records"][0]["raw_reference"])
                state = "unresolved" if value in {"unresolvable", "unavailable", "no_majority"} else ("positive" if value != rule else "negative")
                parser_detail.append({"arm": row["arm"], "reviewer": reviewer, "stratum": role["origin"].split(":")[1],
                                      "case_id": cid, "occurrence_id": role["occurrence_ids"][0], "rule_year": rule, "model_year": value, "state": state})
            if cid in source_roles:
                role = source_roles[cid]
                value = label(row, "source_type", reviewer)
                if value not in SOURCE_TYPES:
                    raise ValueError("unexpected model source-type label")
                source_detail.append({"arm": row["arm"], "reviewer": reviewer, "stratum": role["stratum"],
                                      "case_id": cid, "occurrence_id": role["occurrence_id"], "source_type": value})
    parser_summary, parser_weighted, source_counts, source_weighted = [], [], [], []
    for arm in sorted({r["arm"] for r in records}):
        for reviewer in REVIEWERS:
            for stratum, dimensions in design["parser_strata"].items():
                items = [r for r in parser_detail if (r["arm"], r["reviewer"], r["stratum"]) == (arm, reviewer, stratum)]
                n, N = len(items), int(dimensions["population_occurrences"])
                if n != int(dimensions["sampled_occurrences"]):
                    raise ValueError("incomplete parser probability sample")
                states = count_states(r["state"] for r in items)
                low, high = clopper_pearson(states["positive_n"], states["resolved_n"])
                parser_summary.append({"arm": arm, "reviewer": reviewer, "stratum": stratum, "population_N": N,
                                       "actual_sample_n": n, "N_over_n": N / n, **states,
                                       "resolved_coverage": states["resolved_n"] / n,
                                       "disagreement_given_resolved": states["positive_n"] / states["resolved_n"] if states["resolved_n"] else None,
                                       "disagreement_lower_share": states["positive_n"] / n,
                                       "disagreement_upper_share": (states["positive_n"] + states["unresolved_n"]) / n,
                                       "CP95_lower_given_resolved": low, "CP95_upper_given_resolved": high,
                                       "interpretation": "Model versus frozen regex disagreement; CP95 describes conditional binomial sampling only and omits finite-population correction/model dependence. Not parser accuracy."})
            for stratum, dimensions in design["source_extraction_strata"].items():
                items = [r for r in source_detail if (r["arm"], r["reviewer"], r["stratum"]) == (arm, reviewer, stratum)]
                n, N = len(items), int(dimensions["population_occurrences"])
                if n != int(dimensions["sampled_occurrences"]):
                    raise ValueError("incomplete source probability sample")
                counts = collections.Counter(r["source_type"] for r in items)
                for value in SOURCE_TYPES:
                    source_counts.append({"arm": arm, "reviewer": reviewer, "stratum": stratum, "source_type": value,
                                          "population_N": N, "actual_sample_n": n, "N_over_n": N / n,
                                          "model_label_n": counts[value], "projected_occurrences": N * counts[value] / n,
                                          "interpretation": "Stratum-weighted model source-type labels; unresolved and unavailable are retained."})
            for scope in ("AR5", "AR6", "AR5|AR6"):
                items = [r for r in parser_summary if r["arm"] == arm and r["reviewer"] == reviewer and (scope == "AR5|AR6" or r["stratum"].startswith(scope + "|"))]
                N = sum(r["population_N"] for r in items)
                positive = sum(r["positive_n"] * r["N_over_n"] for r in items)
                unresolved = sum(r["unresolved_n"] * r["N_over_n"] for r in items)
                parser_weighted.append({"arm": arm, "reviewer": reviewer, "report_scope": scope, "population_N": N,
                                        "actual_sample_n": sum(r["actual_sample_n"] for r in items),
                                        "observed_resolved_n": sum(r["resolved_n"] for r in items), "observed_unresolved_n": sum(r["unresolved_n"] for r in items),
                                        "weighted_disagreement_count": positive, "weighted_unresolved_count": unresolved,
                                        "resolved_coverage": 1 - unresolved / N,
                                        "disagreement_lower_share": positive / N, "disagreement_upper_share": (positive + unresolved) / N,
                                        "interpretation": "N/n-weighted model/regex disagreement scenario; no pooled binomial interval and no true-error interpretation."})
                for value in SOURCE_TYPES:
                    items = [r for r in source_counts if r["arm"] == arm and r["reviewer"] == reviewer and r["source_type"] == value and (scope == "AR5|AR6" or r["stratum"].startswith(scope + "|"))]
                    N = sum(r["population_N"] for r in items)
                    projected = sum(r["projected_occurrences"] for r in items)
                    source_weighted.append({"arm": arm, "reviewer": reviewer, "report_scope": scope, "source_type": value,
                                            "population_N": N, "actual_sample_n": sum(r["actual_sample_n"] for r in items),
                                            "model_label_n": sum(r["model_label_n"] for r in items), "projected_occurrences": projected,
                                            "projected_share": projected / N, "interpretation": MODEL_LIMIT})
    return {"occurrence_frame_overlap": overlap, "parser_model_regex_detail": parser_detail,
            "parser_stratum_diagnostics": parser_summary, "parser_weighted_diagnostics": parser_weighted,
            "source_model_detail": source_detail, "source_stratum_counts": source_counts, "source_weighted_counts": source_weighted}


def control_tables(inputs, cases):
    keys = {r["case_id"]: r for r in inputs["control_key"]}
    groups = collections.defaultdict(list)
    for row in cases:
        if row["case_id"] not in keys:
            continue
        key = keys[row["case_id"]]
        provenance = key["reference_provenance"].split(":", 1)[0]
        for reviewer in REVIEWERS:
            groups[(row["arm"], reviewer, provenance, key["reference_label"])].append(label(row, "identity", reviewer))
    output = []
    for (arm, reviewer, group, reference), values in sorted(groups.items()):
        available = [v for v in values if v not in {"unavailable", "no_majority", "qc_excluded"}]
        matching = available.count(reference)
        low, high = clopper_pearson(matching, len(available))
        output.append({"arm": arm, "reviewer": reviewer, "control_set": group, "reference_label": reference,
                       "scheduled_n": len(values), "available_n": len(available),
                       "unavailable_n": values.count("unavailable") + values.count("no_majority"),
                       "ineligible_for_agreement_n": len(values) - len(available),
                       "qc_excluded_n": values.count("qc_excluded"),
                       "transport_available_n": sum(v != "unavailable" for v in values) if reviewer != "consensus" else None,
                       "model_unresolvable_n": available.count("unresolvable"), "matching_reference_n": matching,
                       "agreement_given_available": matching / len(available) if available else None,
                       "CP95_lower_given_available": low, "CP95_upper_given_available": high,
                       "human_reference_labels_archived": 0,
                       "interpretation": "Agreement conditional on available QC-eligible responses with provisional ledger reference or literal raw-DOI-equality heuristic. QC exclusions are separate from model abstentions. CP95 is conditional binomial sampling only; selected controls are not a population probability sample. No sensitivity/specificity."})
    return {"control_conditional_agreement": output}


def pilot_anchoring_tables(root, run_name, inputs):
    """Within-case metadata shifts in the fixed 50-case pilot only.

    This intentionally does not call any census or probability-sample estimator.
    """
    root = Path(root)
    directory = root / "results" / run_name
    manifest, summary = read_json(directory / "manifest.json"), read_json(directory / "summary.json")
    if manifest.get("status") != "complete" or manifest.get("all_scheduled_calls_complete") is not True or summary.get("status") != "complete":
        raise ValueError("pilot anchoring requires its own complete aggregation")
    for name in ("case_consensus.csv", "record_consensus.csv", "metadata_arm_comparison.csv", "summary.json"):
        if sha(directory / name) != manifest["output_sha256"][name]:
            raise ValueError(f"pilot aggregate output hash mismatch: {name}")
    pilot_path = root / "work/inputs/pilot_case_ids_20261021.json"
    pilot_ids = set(read_json(pilot_path))
    if len(pilot_ids) != 50:
        raise ValueError("anchoring pilot must have the frozen 50 distinct cases")
    cases, records = read_csv(directory / "case_consensus.csv"), read_csv(directory / "record_consensus.csv")
    unique(cases, ["arm", "case_id"]); unique(records, ["arm", "case_id", "record_id"])
    schedule_path = root / "work/runs" / run_name / "schedule.json"
    if sha(schedule_path) != manifest["input_sha256"][str(schedule_path.relative_to(root))]:
        raise ValueError("pilot schedule hash differs from archived aggregation")
    schedule = read_json(schedule_path)
    if sorted(schedule["arms"]) != ["blind", "metadata"] or int(schedule["case_count"]) != 50:
        raise ValueError("anchoring requires the fixed two-arm 50-case pilot schedule")
    expected_records = {(cid, record["record_id"]) for cid in pilot_ids for record in inputs["cases"][cid]["records"]}
    for arm in ("blind", "metadata"):
        if {r["case_id"] for r in cases if r["arm"] == arm} != pilot_ids:
            raise ValueError("pilot anchoring case coverage differs from frozen 50")
        if {(r["case_id"], r["record_id"]) for r in records if r["arm"] == arm} != expected_records:
            raise ValueError("pilot anchoring record coverage differs")
    derived_cases, derived_tables = derive_qc_cases(inputs, cases)
    derived_lookup = unique(derived_cases, ["arm", "case_id"])
    grouped = collections.defaultdict(list)
    for row in read_csv(directory / "metadata_arm_comparison.csv"):
        if row["case_id"] not in pilot_ids:
            raise ValueError("metadata shift contains a case outside the frozen pilot")
        packet = inputs["cases"][row["case_id"]]
        if row["field"] in {"identity", "cross_report_recurrence"}:
            if packet["task"] != "identity":
                continue
            if row["field"] == "cross_report_recurrence" and {r["report_group"] for r in packet["records"]} != {"earlier", "later"}:
                continue
            # Case-level shifts use the same QC view as substantive summaries.
            # The archived comparison CSV and every record extraction stay intact.
            row = dict(row)
            blind, metadata = derived_lookup[("blind", row["case_id"])], derived_lookup[("metadata", row["case_id"])]
            reviewer, field = row["reviewer"], row["field"]
            eligible = lambda item: decision(item, field) != "no_majority" if reviewer == "consensus" else bool(item["qc_eligible_" + reviewer])
            comparable = eligible(blind) and eligible(metadata)
            row["blind"], row["metadata"] = label(blind, field, reviewer), label(metadata, field, reviewer)
            row["comparable"] = int(comparable)
            row["changed"] = int(row["blind"] != row["metadata"]) if comparable else ""
            row["qc_incomparable"] = int(not comparable and (blind["qc_excluded_configurations"] > 0 or metadata["qc_excluded_configurations"] > 0))
        grouped[(row["reviewer"], row["field"])].append(row)
    output = []
    for (reviewer, field), rows in sorted(grouped.items()):
        comparable = [r for r in rows if int(r["comparable"])]
        changed = sum(int(r["changed"]) for r in comparable)
        unresolved = lambda value: value in {"", "unresolvable", "not_applicable", "qc_excluded", "no_majority", "unavailable"}
        output.append({"pilot_run_name": run_name, "reviewer": reviewer, "field": field,
                       "pilot_cases_N": 50, "eligible_items_n": len(rows), "comparable_items_n": len(comparable),
                       "incomparable_items_n": len(rows) - len(comparable), "changed_items_n": changed,
                       "qc_incomparable_items_n": sum(int(r.get("qc_incomparable", 0)) for r in rows),
                       "unchanged_items_n": len(comparable) - changed,
                       "blind_unresolved_label_n": sum(unresolved(r["blind"]) for r in comparable),
                       "metadata_unresolved_label_n": sum(unresolved(r["metadata"]) for r in comparable),
                       "changed_given_comparable": changed / len(comparable) if comparable else None,
                       "interpretation": "Fixed 50-case pilot within-item metadata-arm shift; case fields use post-classification QC, record fields are unchanged. Not full-sample anchoring prevalence, causal metadata effect, accuracy, or population projection."})
    pilot_qc = logical_consistency_tables(inputs, cases)
    tables = {"pilot_metadata_arm_shifts": output, "pilot_logical_consistency_counts": pilot_qc["logical_consistency_counts"],
              "pilot_logical_consistency_flags": pilot_qc["logical_consistency_flags"]}
    tables.update({"pilot_" + name: rows for name, rows in derived_tables.items()})
    paths = [pilot_path, schedule_path] + [directory / name for name in ("manifest.json", "summary.json", "case_consensus.csv", "record_consensus.csv", "metadata_arm_comparison.csv")]
    return tables, paths


REGISTRY_FIELDS = ["source_table", "analysis", "arm", "reviewer", "population", "stratum", "category", "metric", "value", "unit", "interpretation"]
COUNT_FIELDS = {"paper_ids", "census_N", "available_configurations", "AR5_fixed_denominator", "AR6_fixed_denominator",
                "recurrent_n", "non_recurrent_n", "unresolved_n", "S_lower", "S_upper", "population_N", "actual_sample_n",
                "positive_n", "negative_n", "resolved_n", "observed_positive_n", "observed_resolved_n", "observed_unresolved_n",
                "weighted_positive_count", "weighted_resolved_count", "weighted_unresolved_count", "model_label_n", "projected_occurrences",
                "weighted_disagreement_count", "scheduled_n", "available_n", "unavailable_n", "model_unresolvable_n", "matching_reference_n",
                "parser_occurrences", "source_occurrences", "overlapping_occurrences", "unique_occurrences", "candidate_pair_rows",
                "unique_unordered_id_pairs", "distinct_assigned_ids", "components", "pairs_with_reused_endpoint", "max_component_ids", "labels_available",
                "human_reference_labels_archived"}
COUNT_FIELDS |= {"cross_report_identity_cases_n", "two_record_eligible_n", "two_record_joint_resolved_n", "two_record_joint_unresolved_n", "joint_incoherence_n", "multi_record_mixed_recurrent_n"}
COUNT_FIELDS |= {"pilot_cases_N", "eligible_items_n", "comparable_items_n", "incomparable_items_n", "changed_items_n", "unchanged_items_n", "blind_unresolved_label_n", "metadata_unresolved_label_n"}
COUNT_FIELDS |= {"transport_available_n", "raw_joint_incoherence_n", "qc_excluded_n", "cases_with_any_qc_exclusion_n", "qc_eligible_identity_n", "qc_eligible_recurrence_n", "model_unresolvable_identity_n", "model_unresolvable_recurrence_n", "identity_no_majority_n", "recurrence_no_majority_n"}
COUNT_FIELDS.add("qc_incomparable_items_n")
COUNT_FIELDS |= {"raw_supported_joint_consensus_n", "ineligible_for_agreement_n"}


def numeric_registry(tables):
    """One numeric quantity per row; detailed item tables remain separate."""
    output = []
    for table, rows in tables.items():
        if table.endswith(("_detail", "_flags", "_qc_derived", "_configuration_exclusions", "_consensus_changes")) or table in {"sample_id_outcomes", "candidate_pair_geometry"}:
            continue
        for row in rows:
            category = "|".join(f"{k}={row[k]}" for k in ("field", "model_label", "source_type", "outcome", "consensus_status", "reference_label", "geometry", "available_configurations", "availability_basis", "classification_basis", "codex_label", "deepseek_label", "both_resolved", "absolute_year_difference") if k in row)
            for key, value in row.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    output.append({"source_table": table + ".csv", "analysis": table.split("_", 1)[0], "arm": row.get("arm", ""),
                                   "reviewer": row.get("reviewer", ""), "population": row.get("population", row.get("sample_set", row.get("report_scope", row.get("control_set", row.get("sampling_frames", row.get("scope", "")))))),
                                   "stratum": row.get("stratum", ""), "category": category, "metric": key, "value": value,
                                   "unit": "count" if key in COUNT_FIELDS else ("weight" if key == "N_over_n" else ("years" if key.endswith("year_difference") else "proportion")),
                                   "interpretation": row.get("interpretation", MODEL_LIMIT)})
    unique(output, ["source_table", "arm", "reviewer", "population", "stratum", "category", "metric"], allow_empty=True)
    return output


def run_statistics(root, run_name, output=None, *, qc_only=False, pilot_run_name=None):
    root = Path(root).resolve()
    aggregates = root / "results" / run_name
    manifest = read_json(aggregates / "manifest.json")
    if manifest.get("status") != "complete" or manifest.get("all_scheduled_calls_complete") is not True:
        raise ValueError("refuse diagnostic inference from incomplete full run")
    summary = read_json(aggregates / "summary.json")
    if summary.get("status") != "complete" or summary.get("incomplete_call_ids"):
        raise ValueError("aggregate summary is incomplete")
    for name in ("case_consensus.csv", "record_consensus.csv", "summary.json"):
        if sha(aggregates / name) != manifest["output_sha256"][name]:
            raise ValueError(f"aggregate output hash mismatch: {name}")
    # Bind the archived aggregate to the same prepared-input manifest.
    if "work/input_manifest.json" in manifest.get("input_sha256", {}) and sha(root / "work/input_manifest.json") != manifest["input_sha256"]["work/input_manifest.json"]:
        raise ValueError("aggregate was produced from a different prepared-input manifest")
    inputs = load_inputs(root)
    cases, records = read_csv(aggregates / "case_consensus.csv"), read_csv(aggregates / "record_consensus.csv")
    unique(cases, ["arm", "case_id"]); unique(records, ["arm", "case_id", "record_id"])
    arms = sorted({r["arm"] for r in cases})
    schedule_path = root / "work/runs" / run_name / "schedule.json"
    if sha(schedule_path) != manifest["input_sha256"][str(schedule_path.relative_to(root))]:
        raise ValueError("full schedule hash differs from archived aggregation")
    schedule = read_json(schedule_path)
    if arms != sorted(schedule["arms"]) or "blind" not in arms:
        raise ValueError("full diagnostic arms differ from the frozen schedule")
    if int(schedule["case_count"]) != len(inputs["cases"]) or int(schedule["record_count"]) != sum(len(c["records"]) for c in inputs["cases"].values()):
        raise ValueError("full diagnostics require the full frozen case/record schedule; pilot cannot support population statistics")
    for arm in arms:
        if {r["case_id"] for r in cases if r["arm"] == arm} != set(inputs["cases"]):
            raise ValueError("full frozen case coverage differs")
        actual = {(r["case_id"], r["record_id"]) for r in records if r["arm"] == arm}
        expected = {(cid, r["record_id"]) for cid, case in inputs["cases"].items() for r in case["records"]}
        if actual != expected:
            raise ValueError("full frozen record coverage differs")
    tables = logical_consistency_tables(inputs, cases)
    policy_path = register_qc_policy(root, inputs["sampling_design"].get("data_seed", 20261011))
    qc_output = aggregates / "logical_semantic_qc"
    qc_output.mkdir(parents=True, exist_ok=True)
    write_csv(qc_output / "logical_consistency_flags.csv", tables["logical_consistency_flags"], LOGICAL_FLAG_FIELDS)
    write_csv(qc_output / "logical_consistency_counts.csv", tables["logical_consistency_counts"])
    flags = tables["logical_consistency_flags"]
    qc_manifest = {"status": "raw_joint_incoherence_detected" if flags else "logical_qc_passed", "run_name": run_name,
                   "contradiction_rows": len(flags), "contradiction_distinct_cases": len({r["case_id"] for r in flags}),
                   "registered_policy_path": str(policy_path.relative_to(root)), "registered_policy_sha256": sha(policy_path),
                   "input_sha256": {str(p.relative_to(root)): sha(p) for p in inputs["input_paths"] + [aggregates / "manifest.json", aggregates / "case_consensus.csv"]},
                   "code_sha256": sha(Path(__file__)), "output_sha256": {p.name: sha(p) for p in sorted(qc_output.glob("*.csv"))},
                   "labels_unchanged": True, "substantive_inference_performed": False}
    dump_json(qc_output / "manifest.json", qc_manifest)
    if qc_only:
        return qc_manifest
    derived_cases, derived_tables = derive_qc_cases(inputs, cases)
    tables.update(derived_tables)
    tables.update(census_tables(inputs, derived_cases)); tables.update(sample_tables(inputs, derived_cases, records))
    tables.update(occurrence_tables(inputs, records)); tables.update(control_tables(inputs, derived_cases))
    geometry, geometry_counts = candidate_geometry(inputs["candidate_pairs"], inputs["membership"])
    tables.update(candidate_pair_geometry=geometry, candidate_geometry_counts=geometry_counts)
    pilot_paths = []
    if pilot_run_name:
        pilot_tables, pilot_paths = pilot_anchoring_tables(root, pilot_run_name, inputs)
        tables.update(pilot_tables)
    tables["validation_summary"] = numeric_registry(tables)
    output = Path(output).resolve() if output else aggregates / "scientific_diagnostics"
    if output == aggregates or output == root:
        raise ValueError("diagnostics require a separate output directory")
    output.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        fields = REGISTRY_FIELDS if name == "validation_summary" else (LOGICAL_FLAG_FIELDS if name.endswith("logical_consistency_flags") else None)
        if name.endswith("qc_configuration_exclusions"):
            fields = QC_EXCLUSION_FIELDS
        elif name.endswith("qc_consensus_changes"):
            fields = QC_CHANGE_FIELDS
        write_csv(output / (name + ".csv"), rows, fields)
    shutil.copyfile(policy_path, output / policy_path.name)
    paths = inputs["input_paths"] + [policy_path, schedule_path] + pilot_paths + [aggregates / name for name in ("manifest.json", "summary.json", "case_consensus.csv", "record_consensus.csv")]
    source_paths = [Path(__file__).resolve(), root / "test_statistics.py"]
    manifest_out = {"status": "complete_model_conditioned_diagnostics", "run_name": run_name,
                    "full_run_arms": arms, "metadata_pilot_run_name": pilot_run_name,
                    "metadata_scope": "separate fixed 50-case pilot only" if pilot_run_name else "no metadata-arm anchoring statistics requested",
                    "analysis_version": "1.5.0", "model_configurations": list(CONFIGS), "model_count": 2, "provider_family_count": 1,
                    "postclassification_qc_policy_version": QC_POLICY_VERSION, "postclassification_qc_policy_sha256": sha(policy_path),
                    "classification_view": "separate derived QC view; raw original columns retained",
                    "qc_excluded_configuration_case_pairs": len(derived_tables["qc_configuration_exclusions"]),
                    "no_human_adjudication": True, "no_model_calls": True, "no_credential_reads": True,
                    "input_sha256": {str(p.relative_to(root)): sha(p) for p in paths},
                    "code_sha256": {p.name: sha(p) for p in source_paths if p.exists()},
                    "output_sha256": {**{name + ".csv": sha(output / (name + ".csv")) for name in tables}, policy_path.name: sha(output / policy_path.name)},
                    "row_counts": {name: len(rows) for name, rows in tables.items()},
                    "denominator_policy": "Unavailable/unresolvable/QC-excluded/no-majority stay in fixed denominators; scenario upper bounds assign all unresolved positives. Probability strata use frozen N/actual n.",
                    "limitations": [MODEL_LIMIT, "ID-based recurrence scenarios hold AR5 and AR6 assigned-ID denominators fixed and exclude missing-link correction.",
                                    "CP95 intervals describe only conditional binomial sampling; selected controls are not a random accuracy sample.",
                                    "Source/parser sample overlap is reported, never pooled as independent.",
                                    "Candidate pair geometry is unlabelled; overlapping candidate pairs do not imply additive corrections.",
                                    "Outlet normalization compares punctuation/case-normalized strings, not semantic venue equivalence."]}
    dump_json(output / "manifest.json", manifest_out)
    return manifest_out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--run-name")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--freeze-inputs-from", type=Path)
    parser.add_argument("--geometry-only", action="store_true", help="Unlabelled input geometry; no run inference")
    parser.add_argument("--logical-qc-only", action="store_true", help="Publish joint-label logical QC only, before substantive inference")
    parser.add_argument("--pilot-run-name", help="Separate fixed-50 two-arm pilot for descriptive metadata anchoring only")
    args = parser.parse_args()
    if args.freeze_inputs_from:
        result = freeze_analysis_inputs(args.root, args.freeze_inputs_from)
    elif args.geometry_only:
        inputs = load_inputs(args.root)
        rows, counts = candidate_geometry(inputs["candidate_pairs"], inputs["membership"])
        out = args.output or args.root / "work/candidate_geometry"
        out.mkdir(parents=True, exist_ok=True)
        write_csv(out / "candidate_pair_geometry.csv", rows); write_csv(out / "candidate_geometry_counts.csv", counts)
        write_csv(out / "validation_summary.csv", numeric_registry({"candidate_geometry_counts": counts}), REGISTRY_FIELDS)
        result = {"status": "unlabelled_frame_geometry_only", "counts": counts,
                  "input_sha256": {str(p.relative_to(args.root)): sha(p) for p in inputs["input_paths"]},
                  "code_sha256": sha(Path(__file__)), "output_sha256": {p.name: sha(p) for p in sorted(out.glob("*.csv"))}}
        dump_json(out / "manifest.json", result)
    elif args.run_name:
        result = run_statistics(args.root, args.run_name, args.output, qc_only=args.logical_qc_only, pilot_run_name=args.pilot_run_name)
    else:
        parser.error("provide --run-name, --geometry-only, or --freeze-inputs-from")
    print(json.dumps({k: v for k, v in result.items() if k in {"status", "row_counts", "counts"}}))


if __name__ == "__main__":
    main()
