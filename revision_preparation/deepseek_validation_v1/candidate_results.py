#!/usr/bin/env python3
"""Offline classifications of a completed, explicitly scheduled candidate frame.

No candidate labels are computed or summarized before every scheduled call has
a complete verified aggregate. Pair counts are candidate-frame descriptions;
there is no population false-split estimator or sum of hypothetical pair deltas.
"""
from __future__ import annotations

import argparse
import collections
from pathlib import Path
import shutil

import validation_statistics as stats


INTERPRETATION = ("Conditional model classification of scheduled candidate-pair rows only; candidates are not a probability sample. "
                  "Overlapping endpoints prevent additive pair corrections. No population false-split estimate or human-accuracy claim.")


def _safe_work_path(root, relative):
    work = (root / "work").resolve()
    path = (work / relative).resolve()
    if not path.is_relative_to(work):
        raise ValueError("candidate packet path escapes work directory")
    return path


def scheduled_candidate_cases(root, schedule, aggregate_manifest, canonical):
    """Verify what the models actually saw, including report provenance."""
    root = Path(root).resolve()
    seen, cases, paths = set(), {}, set()
    for job in schedule["jobs"]:
        path = _safe_work_path(root, job["packet_path"])
        digest = stats.sha(path)
        if digest != job["packet_sha256"] or aggregate_manifest["input_sha256"].get(str(path.relative_to(root))) != digest:
            raise ValueError("scheduled candidate packet hash differs from completed aggregate")
        paths.add(path)
        packet = stats.read_json(path)
        arm, config = job["arm"], job["configuration_id"]
        if config not in stats.CONFIGS:
            raise ValueError("unknown candidate reviewer configuration")
        for case in packet["cases"]:
            cid = case["case_id"]
            key = (arm, config, cid)
            if key in seen:
                raise ValueError("candidate scheduled twice for one arm/configuration")
            seen.add(key)
            if cid not in canonical or case["task"] != "identity" or len(case["records"]) != 2:
                raise ValueError("scheduled case is outside the frozen two-record candidate frame")
            original = stats.unique(canonical[cid]["records"], ["record_id"])
            records = stats.unique(case["records"], ["record_id"])
            if set(records) != {("R001",), ("R002",)} or set(records) != set(original):
                raise ValueError("candidate record IDs differ from the frozen frame")
            for rid, record in records.items():
                if record["raw_reference"] != original[rid]["raw_reference"]:
                    raise ValueError("candidate raw citation differs from canonical frame")
                expected = "earlier" if rid == ("R001",) else "later"
                if record.get("report_group") != expected:
                    raise ValueError("candidate models must have received explicit earlier/later report_group provenance")
            if cid in cases and cases[cid] != case:
                raise ValueError("candidate packet varies across configurations/arms")
            cases[cid] = case
    for cid in cases:
        for arm in schedule["arms"]:
            if any((arm, config, cid) not in seen for config in stats.CONFIGS):
                raise ValueError("candidate schedule omits an arm/configuration slot")
    if len(cases) != int(schedule["case_count"]) or sum(len(c["records"]) for c in cases.values()) != int(schedule["record_count"]):
        raise ValueError("candidate schedule case/record counts differ")
    return cases, paths


def verify_derivation_provenance(root, schedule, canonical_path, packet_paths):
    """Bind the explicit provenance-only packet derivation to its schedule."""
    root = Path(root).resolve()
    relative = schedule.get("derived_provenance_manifest_path")
    digest = schedule.get("derived_provenance_manifest_sha256")
    if not relative or not digest:
        raise ValueError("candidate schedule lacks its derived provenance manifest binding")
    path = _safe_work_path(root, relative)
    if stats.sha(path) != digest:
        raise ValueError("derived candidate provenance manifest hash differs")
    manifest = stats.read_json(path)
    if manifest["canonical_candidate_input_sha256"] != stats.sha(canonical_path):
        raise ValueError("candidate derivation used a different canonical candidate frame")
    if manifest.get("raw_string_changes") != 0:
        raise ValueError("candidate provenance derivation declares raw citation changes")
    for relative, expected in manifest["source_input_sha256"].items():
        if stats.sha(_safe_work_path(root, relative)) != expected:
            raise ValueError("candidate derivation source input hash differs")
    for packet_path in packet_paths:
        packet_path = Path(packet_path).resolve()
        relative = str(packet_path.relative_to(root / "work"))
        expected = stats.sha(packet_path)
        if manifest["packet_sha256"].get(relative) != expected or manifest["output_sha256"].get(relative) != expected:
            raise ValueError("candidate derivation does not cover each scheduled packet hash")
    return path


def classification_tables(inputs, raw_cases, geometry):
    derived, qc_tables = stats.derive_qc_cases(inputs, raw_cases)
    geometry_lookup = {r["case_id"]: r for r in geometry}
    reviewed_ids = {r["case_id"] for r in raw_cases}
    if reviewed_ids - set(geometry_lookup):
        raise ValueError("candidate classification missing frozen geometry")
    counts, coverage = [], []
    for arm in sorted({r["arm"] for r in derived}):
        for kind in sorted({r["geometry"] for r in geometry}):
            frame = [r for r in geometry if r["geometry"] == kind]
            group = [r for r in derived if r["arm"] == arm and geometry_lookup[r["case_id"]]["geometry"] == kind]
            selected = [r for r in frame if r["case_id"] in reviewed_ids]
            coverage.append({"arm": arm, "geometry": kind, "frame_candidate_rows_n": len(frame),
                             "scheduled_candidate_rows_n": len(group), "unscheduled_candidate_rows_n": len(frame) - len(group),
                             "reviewed_unique_unordered_id_pairs_n": len({tuple(sorted((r["AR5_paper_id"], r["AR6_paper_id"]))) for r in selected}),
                             "reviewed_pairs_with_reused_endpoint_n": sum(r["left_candidate_degree"] > 1 or r["right_candidate_degree"] > 1 for r in selected),
                             "interpretation": INTERPRETATION})
            for field in stats.CASE_FIELDS:
                for reviewer in stats.REVIEWERS:
                    for basis, source in (("archived_raw", raw_cases), ("postclassification_qc", derived)):
                        rows = [r for r in source if r["arm"] == arm and geometry_lookup[r["case_id"]]["geometry"] == kind]
                        values = collections.Counter((stats.label(r, field, reviewer), stats.decision(r, field) if reviewer == "consensus" else "configuration_vote") for r in rows)
                        for (value, consensus_status), count in sorted(values.items()):
                            counts.append({"arm": arm, "reviewer": reviewer, "geometry": kind, "field": field,
                                           "classification_basis": basis, "model_label": value, "consensus_status": consensus_status,
                                           "candidate_pair_rows": count, "scheduled_geometry_N": len(group),
                                           "full_geometry_frame_N": len(frame), "interpretation": INTERPRETATION})
    details = []
    for row in derived:
        geo = geometry_lookup[row["case_id"]]
        details.append({**row, **{key: geo[key] for key in ("geometry", "AR5_paper_id", "AR6_paper_id", "AR5_occurrence_id", "AR6_occurrence_id", "component_id", "left_candidate_degree", "right_candidate_degree")}})
    tables = {"candidate_geometry_classifications": counts, "candidate_review_coverage": coverage,
              "candidate_classification_detail": details, **stats.logical_consistency_tables(inputs, raw_cases)}
    tables.update(qc_tables)
    return tables


def run_candidate_statistics(root, run_name, output=None):
    root = Path(root).resolve()
    directory = root / "results" / run_name
    manifest = stats.read_json(directory / "manifest.json")
    if manifest.get("status") != "complete" or manifest.get("all_scheduled_calls_complete") is not True:
        raise ValueError("refuse candidate summaries from incomplete aggregate")
    summary = stats.read_json(directory / "summary.json")
    if summary.get("status") != "complete" or summary.get("incomplete_call_ids"):
        raise ValueError("candidate aggregate summary is incomplete")
    for name in ("case_consensus.csv", "record_consensus.csv", "summary.json"):
        if stats.sha(directory / name) != manifest["output_sha256"][name]:
            raise ValueError("candidate aggregate output hash mismatch")
    schedule_path = root / "work/runs" / run_name / "schedule.json"
    if stats.sha(schedule_path) != manifest["input_sha256"][str(schedule_path.relative_to(root))]:
        raise ValueError("candidate schedule differs from completed aggregate")
    schedule = stats.read_json(schedule_path)
    if "candidates" not in schedule.get("scope", ""):
        raise ValueError("a separately scoped candidate run is required")
    frozen = stats.load_inputs(root)
    candidate_path = root / "work/inputs/candidates/blind_candidates.json"
    input_manifest_path = root / "work/input_manifest.json"
    input_manifest = stats.read_json(input_manifest_path)
    if stats.sha(candidate_path) != input_manifest["copied_inputs"]["candidates/blind_candidates.json"]["sha256"]:
        raise ValueError("canonical frozen candidate frame changed")
    if stats.sha(input_manifest_path) != manifest["input_sha256"]["work/input_manifest.json"]:
        raise ValueError("candidate run used a different prepared-input manifest")
    canonical_rows = stats.read_json(candidate_path)["cases"]
    stats.unique(canonical_rows, ["case_id"])
    canonical = {r["case_id"]: r for r in canonical_rows}
    pairs = frozen["candidate_pairs"]
    if set(canonical) != {r["case_id"] for r in pairs}:
        raise ValueError("candidate frame/key ID coverage differs")
    packets, packet_paths = scheduled_candidate_cases(root, schedule, manifest, canonical)
    derivation_path = verify_derivation_provenance(root, schedule, candidate_path, packet_paths)
    cases, records = stats.read_csv(directory / "case_consensus.csv"), stats.read_csv(directory / "record_consensus.csv")
    stats.unique(cases, ["arm", "case_id"]); stats.unique(records, ["arm", "case_id", "record_id"])
    if sorted({r["arm"] for r in cases}) != sorted(schedule["arms"]):
        raise ValueError("candidate aggregate arm coverage differs")
    for arm in schedule["arms"]:
        if {r["case_id"] for r in cases if r["arm"] == arm} != set(packets):
            raise ValueError("candidate aggregate case coverage differs")
        expected = {(cid, r["record_id"]) for cid, packet in packets.items() for r in packet["records"]}
        if {(r["case_id"], r["record_id"]) for r in records if r["arm"] == arm} != expected:
            raise ValueError("candidate aggregate record coverage differs")
    selected_pairs = {r["case_id"]: r for r in pairs if r["case_id"] in packets}
    key = [{"case_id": cid, "origin": "candidate_id_pair:" + row["AR5_paper_id"] + "|" + row["AR6_paper_id"],
            "task": "identity", "occurrence_ids": [row["AR5_occurrence_id"], row["AR6_occurrence_id"]]} for cid, row in selected_pairs.items()]
    inputs = {"cases": packets, "case_key": key, "key": {r["case_id"]: r for r in key}, "sample_roles": [], "control_key": []}
    policy_path = stats.register_qc_policy(root, schedule.get("seed", 20261011))
    geometry, _ = stats.candidate_geometry(pairs, frozen["membership"])
    tables = classification_tables(inputs, cases, geometry)
    stats.COUNT_FIELDS |= {"frame_candidate_rows_n", "scheduled_candidate_rows_n", "unscheduled_candidate_rows_n", "reviewed_unique_unordered_id_pairs_n", "reviewed_pairs_with_reused_endpoint_n", "scheduled_geometry_N", "full_geometry_frame_N"}
    tables["validation_summary"] = stats.numeric_registry(tables)
    output = Path(output).resolve() if output else directory / "candidate_scientific_diagnostics"
    if output in (directory, root):
        raise ValueError("candidate summaries require a separate output directory")
    output.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        fields = stats.REGISTRY_FIELDS if name == "validation_summary" else None
        if name.endswith("logical_consistency_flags"):
            fields = stats.LOGICAL_FLAG_FIELDS
        elif name == "qc_configuration_exclusions":
            fields = stats.QC_EXCLUSION_FIELDS
        elif name == "qc_consensus_changes":
            fields = stats.QC_CHANGE_FIELDS
        stats.write_csv(output / (name + ".csv"), rows, fields)
    shutil.copyfile(policy_path, output / policy_path.name)
    inputs_used = frozen["input_paths"] + [candidate_path, derivation_path, schedule_path, policy_path] + sorted(packet_paths)
    inputs_used += [directory / name for name in ("manifest.json", "summary.json", "case_consensus.csv", "record_consensus.csv")]
    result = {"status": "complete_conditional_candidate_classifications", "run_name": run_name,
              "scheduled_candidate_cases": len(packets), "full_candidate_frame_cases": len(canonical), "arms": schedule["arms"],
              "postclassification_qc_policy_sha256": stats.sha(policy_path),
              "qc_excluded_configuration_case_pairs": len(tables["qc_configuration_exclusions"]),
              "input_sha256": {str(path.relative_to(root)): stats.sha(path) for path in inputs_used},
              "code_sha256": {path.name: stats.sha(path) for path in (Path(__file__).resolve(), Path(stats.__file__).resolve(), root / "test_candidate_results.py") if path.exists()},
              "output_sha256": {**{name + ".csv": stats.sha(output / (name + ".csv")) for name in tables}, policy_path.name: stats.sha(output / policy_path.name)},
              "row_counts": {name: len(rows) for name, rows in tables.items()}, "no_model_calls": True,
              "no_credential_reads": True, "no_human_adjudication": True, "no_population_error_estimator": True,
              "no_summed_pair_deltas": True, "interpretation": INTERPRETATION}
    stats.dump_json(output / "manifest.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_candidate_statistics(args.root, args.run_name, args.output)
    print({key: result[key] for key in ("status", "scheduled_candidate_cases", "qc_excluded_configuration_case_pairs", "row_counts")})


if __name__ == "__main__":
    main()
