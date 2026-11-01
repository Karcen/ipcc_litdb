#!/usr/bin/env python3
"""Offline paired comparison of saved blinded Codex and DeepSeek judgements.

Each input is a model judgement about the same frozen citation, never a truth
label. Comparisons use only the blind DeepSeek arm and preserve missingness,
model abstentions, QC exclusions, and shared/resolved denominators separately.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
import statistics

import validation_statistics as stats
from validate import validate_response


LIMIT = ("Paired model agreement on identical frozen bibliographic evidence; neither reviewer is a truth reference. "
         "No human accuracy, error-rate, causal anchoring effect, or model-winner inference.")
UNKNOWN = {"unresolvable", "unavailable", "no_majority", "qc_excluded"}
FIELDS = (*stats.CASE_FIELDS, "cited_year", "outlet_literal", "outlet_canonical", "source_type", "contains_multiple_citations")
DETAIL_FIELDS = ["arm", "reviewer", "population", "case_id", "record_id", "field", "codex_label", "deepseek_label", "codex_available", "deepseek_available", "shared_available", "both_resolved", "exact_agreement", "absolute_year_difference", "deepseek_unavailability_reason", "divergence_kind", "interpretation"]
CODEX_QC_FIELDS = ["reviewer", "case_id", "raw_identity_label", "raw_recurrence_label", "qc_excluded", "record_extractions_retained", "policy_version"]


def verify_reviewer(packet_path, response_path, provenance_path, protocol_path, schema_path):
    provenance = stats.read_json(provenance_path)
    if provenance.get("status") != "complete":
        raise ValueError("Codex reviewer artifact is not saved as complete")
    if not isinstance(provenance.get("reviewer"), str) or not provenance["reviewer"].strip():
        raise ValueError("saved reviewer provenance requires a reviewer name")
    if provenance.get("deepseek_outputs_visible_to_reviewer") is not False or provenance.get("human_adjudication") is not False:
        raise ValueError("comparison requires explicitly blinded model-review provenance")
    for key, path in (("packet_sha256", packet_path), ("response_sha256", response_path), ("protocol_sha256", protocol_path), ("schema_sha256", schema_path)):
        if provenance.get(key) != stats.sha(path):
            raise ValueError("Codex reviewer provenance hash differs: " + key)
    protocol = stats.read_json(protocol_path)
    if protocol.get("deepseek_outputs_visible_to_reviewer") is not False or protocol.get("human_adjudication") is not False:
        raise ValueError("comparison protocol must declare blinded model review")
    packet, response = stats.read_json(packet_path), stats.read_json(response_path)
    if not packet.get("cases"):
        raise ValueError("paired reviewer packet must contain at least one frozen case")
    validate_response(response, packet, schema_path)
    return packet, response, provenance


def load_deepseek(root, run_name, packet):
    """Read complete aggregation and hash-bound raw outlets; no transport calls."""
    root = Path(root).resolve()
    directory = root / "results" / run_name
    manifest, summary = stats.read_json(directory / "manifest.json"), stats.read_json(directory / "summary.json")
    if manifest.get("status") != "complete" or manifest.get("all_scheduled_calls_complete") is not True or summary.get("status") != "complete" or summary.get("incomplete_call_ids"):
        raise ValueError("paired comparison requires a complete DeepSeek aggregation")
    for name in ("case_consensus.csv", "record_consensus.csv", "summary.json"):
        if stats.sha(directory / name) != manifest["output_sha256"][name]:
            raise ValueError("DeepSeek aggregate output hash differs")
    schedule_path = root / "work/runs" / run_name / "schedule.json"
    if stats.sha(schedule_path) != manifest["input_sha256"][str(schedule_path.relative_to(root))]:
        raise ValueError("DeepSeek archived schedule hash differs")
    schedule = stats.read_json(schedule_path)
    if "blind" not in schedule["arms"] or "candidates" in schedule.get("scope", ""):
        raise ValueError("paired comparison requires the full or pilot blind bibliographic-review arm")
    requested = {c["case_id"]: c for c in packet["cases"]}
    scheduled, coverage, outlets, paths = {}, set(), {}, {schedule_path}
    for job in schedule["jobs"]:
        if job["arm"] != "blind":
            continue
        path = (root / "work" / job["packet_path"]).resolve()
        if not path.is_relative_to(root / "work") or stats.sha(path) != job["packet_sha256"] or manifest["input_sha256"].get(str(path.relative_to(root))) != stats.sha(path):
            raise ValueError("DeepSeek blind packet hash differs")
        source = stats.read_json(path)
        config = job["configuration_id"]
        if config not in stats.CONFIGS:
            raise ValueError("unexpected DeepSeek configuration")
        relevant = []
        for case in source["cases"]:
            cid = case["case_id"]
            if cid in scheduled and scheduled[cid] != case:
                raise ValueError("DeepSeek blind evidence varies between configurations")
            scheduled[cid] = case
            if cid not in requested:
                continue
            if requested[cid] != case:
                raise ValueError("Codex and DeepSeek packet strings/records/frozen evidence differ")
            if (config, cid) in coverage:
                raise ValueError("duplicate scheduled comparison configuration/case")
            coverage.add((config, cid)); relevant.append(cid)
        if not relevant:
            continue
        paths.add(path)
        call_id = job.get("call_id", job.get("job_id"))
        response_path = root / "work/runs" / run_name / "calls" / call_id / "response.json"
        if not response_path.exists() or manifest["input_sha256"].get(str(response_path.relative_to(root))) != stats.sha(response_path):
            raise ValueError("raw DeepSeek response needed for literal outlets is absent or changed")
        paths.add(response_path)
        for case in stats.read_json(response_path)["cases"]:
            if case["case_id"] in requested:
                for record in case["records"]:
                    outlets[(config, case["case_id"], record["record_id"])] = record["outlet"]
    if set(requested) - set(scheduled) or any((c, cid) not in coverage for c in stats.CONFIGS for cid in requested):
        raise ValueError("Codex frozen scope is not completely covered by the DeepSeek run")
    cases_all, records_all = stats.read_csv(directory / "case_consensus.csv"), stats.read_csv(directory / "record_consensus.csv")
    stats.unique(cases_all, ["arm", "case_id"]); stats.unique(records_all, ["arm", "case_id", "record_id"])
    cases = [r for r in cases_all if r["arm"] == "blind" and r["case_id"] in requested]
    records = [r for r in records_all if r["arm"] == "blind" and r["case_id"] in requested]
    if {r["case_id"] for r in cases} != set(requested):
        raise ValueError("paired DeepSeek case coverage differs")
    expected = {(cid, r["record_id"]) for cid, case in requested.items() for r in case["records"]}
    if {(r["case_id"], r["record_id"]) for r in records} != expected:
        raise ValueError("paired DeepSeek record coverage differs")
    paths |= {directory / name for name in ("manifest.json", "summary.json", "case_consensus.csv", "record_consensus.csv")}
    return cases, records, outlets, paths


def codex_qc_exclusions(packet, response):
    originals = {c["case_id"]: c for c in packet["cases"]}
    output = []
    for case in response["cases"]:
        records = originals[case["case_id"]]["records"]
        if len(records) == 2 and {r["report_group"] for r in records} == {"earlier", "later"} and case["identity"] == "different_studies" and case["cross_report_recurrence"] == "recurrent":
            output.append({"reviewer": "Codex", "case_id": case["case_id"], "raw_identity_label": case["identity"],
                           "raw_recurrence_label": case["cross_report_recurrence"], "qc_excluded": 1,
                           "record_extractions_retained": 1, "policy_version": stats.QC_POLICY_VERSION})
    return output


def paired_tables(packet, response, ds_cases, ds_records, raw_outlets, scope):
    packets = {c["case_id"]: c for c in packet["cases"]}
    key = [{"case_id": cid, "origin": "paired_frozen_packet:" + cid, "occurrence_ids": [], "task": c["task"]} for cid, c in packets.items()]
    inputs = {"cases": packets, "case_key": key, "key": {r["case_id"]: r for r in key}}
    derived, qc_tables = stats.derive_qc_cases(inputs, ds_cases)
    cases = {r["case_id"]: r for r in derived}
    records = {(r["case_id"], r["record_id"]): r for r in ds_records}
    excluded = codex_qc_exclusions(packet, response)
    excluded_ids = {r["case_id"] for r in excluded}
    details = []
    def append(cid, rid, field, codex_value, ds_value, reviewer, *, reason=""):
        c_available, d_available = codex_value not in {"unavailable", "no_majority", "qc_excluded"}, ds_value not in {"unavailable", "no_majority", "qc_excluded"}
        shared = c_available and d_available
        resolved = shared and codex_value not in UNKNOWN and ds_value not in UNKNOWN
        exact = int(codex_value == ds_value) if shared else None
        details.append({"arm": "blind", "reviewer": reviewer, "population": scope, "case_id": cid, "record_id": rid, "field": field,
                        "codex_label": codex_value, "deepseek_label": ds_value,
                        "codex_available": int(c_available), "deepseek_available": int(d_available), "shared_available": int(shared),
                        "both_resolved": int(resolved), "exact_agreement": exact,
                        "absolute_year_difference": abs(int(codex_value) - int(ds_value)) if field == "cited_year" and resolved else None,
                        "deepseek_unavailability_reason": reason if not d_available else "",
                        "divergence_kind": "not_shared_available" if not shared else ("unresolved_label" if not resolved else ("resolved_disagreement" if not exact else "resolved_agreement")),
                        "interpretation": "Literal/canonical outlet agreement is lexical string agreement, not semantic venue equivalence. " + LIMIT if field.startswith("outlet_") else LIMIT})
    for codex in response["cases"]:
        cid, case = codex["case_id"], packets[codex["case_id"]]
        ds_case = cases[cid]
        for reviewer in stats.REVIEWERS:
            if case["task"] == "identity":
                for field in stats.CASE_FIELDS:
                    if field == "cross_report_recurrence" and {r["report_group"] for r in case["records"]} != {"earlier", "later"}:
                        continue
                    cvalue = "qc_excluded" if cid in excluded_ids else codex[field]
                    append(cid, "", field, cvalue, stats.label(ds_case, field, reviewer), reviewer,
                           reason=stats.decision(ds_case, field) if reviewer == "consensus" else ds_case.get("status_" + reviewer, ""))
            for record in codex["records"]:
                rid, ds_record = record["record_id"], records[(cid, record["record_id"])]
                for field in ("cited_year", "source_type", "contains_multiple_citations", "outlet_canonical", "outlet_literal"):
                    if field == "outlet_literal":
                        cvalue = record["outlet"] if record["outlet"] is not None and record["outlet"].strip() else "unresolvable"
                        def raw_value(config):
                            if int(ds_record["available_" + config]) != 1:
                                return None
                            value = raw_outlets[(config, cid, rid)]
                            return value if value is not None and value.strip() else "unresolvable"
                        if reviewer == "consensus":
                            dvalue, ddecision = stats._eligible_majority([raw_value(c) for c in stats.CONFIGS])
                            if ddecision == "no_majority":
                                dvalue = "no_majority"
                        else:
                            dvalue = raw_value(reviewer) if int(ds_record["available_" + reviewer]) == 1 else "unavailable"
                    elif field == "outlet_canonical":
                        cvalue = stats.canon_outlet(record["outlet"]) or "unresolvable"
                        dvalue = stats.label(ds_record, "outlet", reviewer)
                    else:
                        cvalue = str(record[field]) if record[field] is not None else "unresolvable"
                        dvalue = stats.label(ds_record, field, reviewer)
                    append(cid, rid, field, cvalue, dvalue, reviewer,
                           reason="no_literal_majority" if field == "outlet_literal" and dvalue == "no_majority" else (ds_case.get("status_" + reviewer, "") if reviewer != "consensus" else "no_majority"))
    summary, confusion, year_counts, year_summary = [], [], [], []
    grouped = collections.defaultdict(list)
    for row in details:
        grouped[(row["reviewer"], row["field"])].append(row)
    for (reviewer, field), rows in sorted(grouped.items()):
        shared = [r for r in rows if r["shared_available"]]
        resolved = [r for r in rows if r["both_resolved"]]
        exact, rexact = sum(r["exact_agreement"] for r in shared), sum(r["exact_agreement"] for r in resolved)
        summary.append({"arm": "blind", "reviewer": reviewer, "population": scope, "field": field, "scheduled_items_n": len(rows),
                        "codex_available_n": sum(r["codex_available"] for r in rows), "deepseek_available_n": sum(r["deepseek_available"] for r in rows),
                        "shared_available_n": len(shared), "not_shared_available_n": len(rows) - len(shared),
                        "codex_unresolvable_n": sum(r["codex_label"] == "unresolvable" for r in rows),
                        "deepseek_unresolvable_n": sum(r["deepseek_label"] == "unresolvable" for r in rows),
                        "codex_qc_excluded_n": sum(r["codex_label"] == "qc_excluded" for r in rows),
                        "deepseek_qc_excluded_n": sum(r["deepseek_label"] == "qc_excluded" for r in rows),
                        "deepseek_no_majority_n": sum(r["deepseek_label"] == "no_majority" for r in rows),
                        "deepseek_unavailable_n": sum(r["deepseek_label"] == "unavailable" for r in rows),
                        "codex_missing_n": 0,
                        "deepseek_missing_n": sum(r["deepseek_unavailability_reason"] == "missing" for r in rows),
                        "deepseek_code_abstention_n": sum(r["deepseek_unavailability_reason"] == "unresolvable_after_two_invalid_outputs" for r in rows),
                        "both_resolved_n": len(resolved), "noncomparable_resolved_n": len(rows) - len(resolved),
                        "exact_agreement_shared_n": exact, "disagreement_shared_n": len(shared) - exact,
                        "exact_agreement_given_shared": exact / len(shared) if shared else None,
                        "exact_agreement_resolved_n": rexact, "disagreement_resolved_n": len(resolved) - rexact,
                        "exact_agreement_given_resolved": rexact / len(resolved) if resolved else None,
                        "interpretation": "Shared-availability agreement includes matching model abstentions; resolved-only agreement has a separate denominator. " + LIMIT})
        if field in {"identity", "cross_report_recurrence", "source_type", "contains_multiple_citations"}:
            counts = collections.Counter((r["codex_label"], r["deepseek_label"], r["both_resolved"]) for r in shared)
            for (cvalue, dvalue, both_resolved), count in sorted(counts.items()):
                confusion.append({"arm": "blind", "reviewer": reviewer, "population": scope, "field": field,
                                  "codex_label": cvalue, "deepseek_label": dvalue, "both_resolved": both_resolved,
                                  "paired_items_n": count, "interpretation": "Paired label cross-tabulation; neither axis is a reference truth. " + LIMIT})
        if field == "cited_year":
            differences = [r["absolute_year_difference"] for r in resolved]
            for difference, count in sorted(collections.Counter(differences).items()):
                year_counts.append({"arm": "blind", "reviewer": reviewer, "population": scope, "field": field,
                                    "absolute_year_difference": difference, "paired_items_n": count, "interpretation": LIMIT})
            year_summary.append({"arm": "blind", "reviewer": reviewer, "population": scope, "field": field,
                                 "both_resolved_n": len(differences), "mean_absolute_year_difference": statistics.mean(differences) if differences else None,
                                 "median_absolute_year_difference": statistics.median(differences) if differences else None,
                                 "maximum_absolute_year_difference": max(differences) if differences else None, "interpretation": LIMIT})
    return {"paired_agreement": summary, "paired_label_crosstabs": confusion, "paired_year_difference_counts": year_counts,
            "paired_year_difference_summary": year_summary, "paired_comparison_detail": details,
            "paired_divergences": [r for r in details if r["divergence_kind"] != "resolved_agreement"],
            "codex_qc_exclusions": excluded, **{"deepseek_" + k: v for k, v in qc_tables.items()}}


def weighted_id_tables(details, packet, frozen):
    """N/n expansion of paired model disagreement only for the full ID sample."""
    roles = frozen["sample_roles"]
    if {c["case_id"] for c in packet["cases"]} != {r["case_id"] for r in roles}:
        raise ValueError("weighted paired ID comparison requires all 319 probability IDs and 12 targeted supplements")
    design = {r["stratum"]: r for r in frozen["sample_strata"]}
    actual = collections.Counter(r["sample_stratum"] for r in roles if r["selection"] == stats.PROBABILITY)
    if set(actual) != set(design) or any(actual[s] != int(d["sampled_ids"]) for s, d in design.items()):
        raise ValueError("paired ID sample N/n contract differs")
    lookup = collections.defaultdict(list)
    for row in details:
        lookup[(row["case_id"], row["reviewer"], row["field"])].append(row)
    grouped, outcomes = collections.defaultdict(list), []
    for role in roles:
        group = "probability" if role["selection"] == stats.PROBABILITY else "targeted"
        if role["selection"] not in {stats.PROBABILITY, stats.TARGETED}:
            raise ValueError("unknown paired ID sample selection")
        for reviewer in stats.REVIEWERS:
            for field in FIELDS:
                if field == "cross_report_recurrence" and not role["sample_stratum"].startswith("shared_matched|"):
                    continue
                rows = lookup[(role["case_id"], reviewer, field)]
                if not rows:
                    raise ValueError("paired ID outcome has no comparison item")
                state = "positive" if any(r["both_resolved"] and r["exact_agreement"] == 0 for r in rows) else ("negative" if all(r["both_resolved"] and r["exact_agreement"] == 1 for r in rows) else "unresolved")
                outcome = field + ("_disagreement" if field in stats.CASE_FIELDS else "_disagreement_any_record")
                outcomes.append({"reviewer": reviewer, "sample_set": group, "stratum": role["sample_stratum"], "case_id": role["case_id"], "paper_id": role["paper_id"], "outcome": outcome, "state": state})
                grouped[(reviewer, group, role["sample_stratum"], outcome)].append(state)
    strata, weighted = [], []
    for (reviewer, group, stratum, outcome), states in sorted(grouped.items()):
        n = len(states); N = int(design[stratum]["population_ids"]) if group == "probability" else None
        strata.append({"arm": "blind", "reviewer": reviewer, "sample_set": group, "stratum": stratum, "outcome": outcome,
                       "population_N": N, "actual_sample_n": n, "N_over_n": N / n if N is not None else None,
                       **stats.count_states(states), "interpretation": "Paired model disagreement at assigned-ID unit; year/outlet/source outcomes mean any record disagrees. " + LIMIT})
    groups = collections.defaultdict(list)
    for row in strata:
        groups[(row["reviewer"], row["sample_set"], row["outcome"])].append(row)
    for (reviewer, group, outcome), rows in sorted(groups.items()):
        n = sum(r["actual_sample_n"] for r in rows)
        N = sum(r["population_N"] for r in rows) if group == "probability" else None
        total = N if N is not None else n
        expand = lambda key: sum(r[key] * (r["N_over_n"] if N is not None else 1) for r in rows)
        positive, unresolved = expand("positive_n"), expand("unresolved_n")
        weighted.append({"arm": "blind", "reviewer": reviewer, "sample_set": group, "outcome": outcome, "population_N": N,
                         "actual_sample_n": n, "observed_positive_n": sum(r["positive_n"] for r in rows),
                         "observed_unresolved_n": sum(r["unresolved_n"] for r in rows),
                         "weighted_disagreement_count": positive if N is not None else None,
                         "weighted_unresolved_count": unresolved if N is not None else None,
                         "lower_share": positive / total, "upper_share": (positive + unresolved) / total,
                         "interpretation": "Unresolved-label scenario bounds for paired model disagreement, not population identity error; targeted supplements are descriptive only. " + LIMIT})
    return {"paired_id_outcomes": outcomes, "paired_id_stratum_diagnostics": strata, "paired_id_weighted_diagnostics": weighted}


def run_comparison(root, run_name, packet_path, response_path, provenance_path, protocol_path, schema_path, *, output=None, scope=None, weight_id_sample=False):
    root = Path(root).resolve()
    paths = [Path(p).resolve() for p in (packet_path, response_path, provenance_path, protocol_path, schema_path)]
    packet_path, response_path, provenance_path, protocol_path, schema_path = paths
    packet, response, provenance = verify_reviewer(*paths)
    ds_cases, ds_records, raw_outlets, ds_paths = load_deepseek(root, run_name, packet)
    policy_path = stats.register_qc_policy(root, stats.read_json(protocol_path).get("seed", 20261011))
    scope = scope or packet_path.stem
    tables = paired_tables(packet, response, ds_cases, ds_records, raw_outlets, scope)
    extra_paths = []
    if weight_id_sample:
        frozen = stats.load_inputs(root)
        tables.update(weighted_id_tables(tables["paired_comparison_detail"], packet, frozen))
        extra_paths = frozen["input_paths"]
    count_keys = {"scheduled_items_n", "codex_available_n", "deepseek_available_n", "shared_available_n", "not_shared_available_n", "codex_unresolvable_n", "deepseek_unresolvable_n", "codex_qc_excluded_n", "deepseek_qc_excluded_n", "deepseek_no_majority_n", "deepseek_unavailable_n", "both_resolved_n", "noncomparable_resolved_n", "exact_agreement_shared_n", "disagreement_shared_n", "exact_agreement_resolved_n", "disagreement_resolved_n", "paired_items_n", "both_resolved"}
    count_keys |= {"codex_missing_n", "deepseek_missing_n", "deepseek_code_abstention_n"}
    stats.COUNT_FIELDS |= count_keys
    summary_tables = {k: v for k, v in tables.items() if k not in {"paired_divergences", "codex_qc_exclusions", "paired_id_outcomes"}}
    tables["validation_summary"] = stats.numeric_registry(summary_tables)
    output = Path(output).resolve() if output else root / "results" / run_name / ("codex_paired_" + response_path.stem)
    if output in (root, root / "results" / run_name):
        raise ValueError("paired comparison requires a separate output directory")
    output.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        fields = stats.REGISTRY_FIELDS if name == "validation_summary" else None
        if name in {"paired_comparison_detail", "paired_divergences"}:
            fields = DETAIL_FIELDS
        elif name == "codex_qc_exclusions":
            fields = CODEX_QC_FIELDS
        if name.endswith("qc_configuration_exclusions"):
            fields = stats.QC_EXCLUSION_FIELDS
        elif name.endswith("qc_consensus_changes"):
            fields = stats.QC_CHANGE_FIELDS
        stats.write_csv(output / (name + ".csv"), rows, fields)
    result = {"status": "complete_paired_model_comparison", "deepseek_run_name": run_name, "scope": scope,
              "case_count": len(packet["cases"]), "record_count": sum(len(c["records"]) for c in packet["cases"]),
              "codex_reviewer": provenance["reviewer"], "codex_model_id": provenance.get("model_id"),
              "reviewer_blinding": "asserted in saved reviewer provenance; no labels sent to reviewer by this consumer",
              "comparison_arm": "blind", "deepseek_reviewers": list(stats.REVIEWERS),
              "weighted_id_sample": weight_id_sample, "postclassification_qc_policy_sha256": stats.sha(policy_path),
              "input_sha256": {str(p.relative_to(root)) if p.is_relative_to(root) else str(p): stats.sha(p) for p in set(paths + list(ds_paths) + extra_paths + [policy_path])},
              "code_sha256": {p.name: stats.sha(p) for p in (Path(__file__).resolve(), Path(stats.__file__).resolve(), root / "validate.py", root / "test_paired_codex_comparison.py") if p.exists()},
              "output_sha256": {name + ".csv": stats.sha(output / (name + ".csv")) for name in tables},
              "row_counts": {name: len(rows) for name, rows in tables.items()}, "no_model_calls": True,
              "no_credential_reads": True, "no_human_adjudication": True, "interpretation": LIMIT}
    stats.dump_json(output / "manifest.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--deepseek-run-name", required=True)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--reviewer-provenance", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--scope")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--weight-id-sample", action="store_true")
    args = parser.parse_args()
    result = run_comparison(args.root, args.deepseek_run_name, args.packet, args.response, args.reviewer_provenance, args.protocol, args.schema,
                            output=args.output, scope=args.scope, weight_id_sample=args.weight_id_sample)
    print(json.dumps({key: result[key] for key in ("status", "scope", "case_count", "record_count", "row_counts")}))


if __name__ == "__main__":
    main()
