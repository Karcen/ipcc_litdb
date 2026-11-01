"""Prepare frozen inputs and schedules offline without sending model calls."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "source_panel"
WORK = HERE / "work"
ANALYSIS_PACKAGE = "1.5.0"
SEED = 20261011
CONFIGURATIONS = (
    {"configuration_id": "flash_A", "model_tier": "Flash", "prompt_variant": "A"},
    {"configuration_id": "flash_B", "model_tier": "Flash", "prompt_variant": "B"},
    {"configuration_id": "pro_C", "model_tier": "Pro", "prompt_variant": "C"},
)
CURRENT_CODE = ("prepare_panel.py", "run_panel.py", "run_full_panel.py", "aggregate_panel.py", "test_panel_protocol.py", "generate_candidates.py")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def write_frozen(path, data):
    path = Path(path)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("Prepared artifact differs; use a new work directory: " + str(path))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def verify_original_outputs(source_dir=SOURCE):
    source_dir = Path(source_dir)
    manifest = _load(source_dir / "preparation_manifest.json")
    if manifest.get("seed") != SEED:
        raise ValueError("Original preparation seed differs")
    verified = {}
    for relative, expected in manifest["outputs"].items():
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Unsafe original output path")
        if path.name.startswith("._"):
            continue
        actual = digest(source_dir / path)
        if actual != expected:
            raise ValueError("Frozen original output hash differs: " + relative)
        verified[relative] = actual
    code = {}
    for name in CURRENT_CODE:
        path = source_dir / name
        if not path.is_file():
            continue
        current = digest(path)
        original = next((value for key, value in manifest["inputs"].items() if key.endswith("/" + name)), None)
        code[name] = {"current_sha256": current, "original_preparation_input_sha256": original,
                      "matches_original_preparation_input": current == original if original is not None else None}
    return {"original_preparation_manifest_sha256": digest(source_dir / "preparation_manifest.json"),
            "verified_original_output_sha256": verified, "current_source_code": code,
            "apple_double_sidecars_excluded": True, "packet_regeneration_from_current_code_claimed": False}


def _unblind(case):
    return {key: value for key, value in case.items() if key != "pipeline_metadata"}


def _check_arms(blind, metadata):
    if blind != {"cases": [_unblind(case) for case in metadata["cases"]]}:
        raise ValueError("Blind and metadata arms differ in case order or raw evidence")
    for case in blind["cases"]:
        if set(case) != {"case_id", "task", "records", "frozen_evidence"}:
            raise ValueError("Unexpected field in blind case")
        for record in case["records"]:
            if set(record) != {"record_id", "report_group", "raw_reference"}:
                raise ValueError("Unexpected field in blind record")


def prepare_inputs(source_dir=SOURCE, work_dir=WORK):
    source_dir, work_dir = Path(source_dir), Path(work_dir)
    verification = verify_original_outputs(source_dir)
    names = list(verification["verified_original_output_sha256"])
    names += ["preparation_manifest.json", "pilot_v2_preflight.json", "pilot_case_ids_20261021.json", "easy_doi_controls_20261021.json"]
    names += ["candidates/" + name for name in ("blind_candidates.json", "candidate_pairs.csv", "hard_distinct_doi_candidates.csv", "summary.json", "manifest.json") if (source_dir / "candidates" / name).is_file()]
    copied = {}
    for relative in sorted(set(names)):
        destination = work_dir / "inputs" / relative
        write_frozen(destination, (source_dir / relative).read_bytes())
        copied[relative] = {"sha256": digest(destination), "bytes": destination.stat().st_size}
    for name in verification["current_source_code"]:
        write_frozen(work_dir / "inputs/provenance/current_code" / name, (source_dir / name).read_bytes())
    inputs = work_dir / "inputs"
    blind, metadata = _load(inputs / "frames/blind_all.json"), _load(inputs / "frames/metadata_all.json")
    _check_arms(blind, metadata)
    if len(blind["cases"]) != 5956 or sum(len(case["records"]) for case in blind["cases"]) != 9701:
        raise ValueError("Frozen canonical frame counts differ")
    pilot_blind, pilot_metadata = _load(inputs / "pilot/blind_50.json"), _load(inputs / "pilot/metadata_50.json")
    _check_arms(pilot_blind, pilot_metadata)
    ids = [case["case_id"] for case in pilot_blind["cases"]]
    if len(ids) != 50 or len(set(ids)) != 50 or ids != _load(inputs / "pilot_case_ids_20261021.json"):
        raise ValueError("Fixed pilot IDs differ")
    preflight = _load(inputs / "pilot_v2_preflight.json")
    for arm in ("blind", "metadata"):
        if digest(inputs / f"pilot/{arm}_50.json") != preflight[arm + "_packet_sha256"]:
            raise ValueError("Corrected pilot preflight hash differs")
    manifest = {"analysis_package": ANALYSIS_PACKAGE, "seed": SEED, "raw_inputs_private": True,
                "source": "revision_preparation/review_round3/panel", "copied_inputs": copied,
                "provenance_verification": verification, "canonical_case_count": 5956, "canonical_record_count": 9701,
                "pilot_distinct_cases": 50, "pilot_records_per_arm": sum(len(c["records"]) for c in pilot_blind["cases"]),
                "preparation_code_sha256": digest(Path(__file__)), "model_calls_made": 0}
    write_frozen(work_dir / "input_manifest.json", encoded(manifest))
    return manifest


def build_batches(packet, *, paired_packet=None, max_cases=10, max_records=20, max_bytes=40000):
    for value in (max_cases, max_records, max_bytes):
        if type(value) is not int or value < 1:
            raise ValueError("Batch limits must be positive integers")
    if paired_packet is not None:
        _check_arms(packet, paired_packet)
    cases = packet["cases"]
    ids = [case["case_id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate case IDs in batch frame")
    if any(not case["records"] for case in cases):
        raise ValueError("Empty case records cannot be batched")
    paired = paired_packet["cases"] if paired_packet is not None else cases
    batches, pending = [], []

    def limits_exceeded(indices):
        counts = sum(len(cases[index]["records"]) for index in indices)
        sizes = [len(encoded({"cases": [frame[index] for index in indices]})) for frame in (cases, paired)]
        return len(indices) > max_cases or counts > max_records or max(sizes) > max_bytes

    def append(indices):
        body = {"cases": [cases[index] for index in indices]}
        other = {"cases": [paired[index] for index in indices]}
        batches.append({"batch_id": f"batch_{len(batches) + 1:04d}", "packet": copy.deepcopy(body),
                        "paired_packet": copy.deepcopy(other) if paired_packet is not None else None,
                        "case_ids": [cases[index]["case_id"] for index in indices], "case_count": len(indices),
                        "record_count": sum(len(cases[index]["records"]) for index in indices),
                        "packet_bytes": len(encoded(body)), "paired_packet_bytes": len(encoded(other)) if paired_packet is not None else None,
                        "oversized_single_case": len(indices) == 1 and limits_exceeded(indices)})

    for index in range(len(cases)):
        if pending and limits_exceeded(pending + [index]):
            append(pending)
            pending = []
        pending.append(index)
    if pending:
        append(pending)
    return batches


def verify_prepared_inputs(work_dir=WORK):
    work_dir = Path(work_dir)
    manifest = _load(work_dir / "input_manifest.json")
    for relative, item in manifest["copied_inputs"].items():
        if digest(work_dir / "inputs" / relative) != item["sha256"]:
            raise ValueError("Prepared input hash differs: " + relative)
    return manifest


def prepare_stage(stage="pilot", *, work_dir=WORK, include_candidates=False, max_cases=10, max_records=20, max_bytes=40000):
    work_dir = Path(work_dir)
    if stage not in ("pilot", "full", "candidates"):
        raise ValueError("Unknown preparation stage")
    if include_candidates and stage != "full":
        raise ValueError("include_candidates is applicable only to the full stage")
    if not (work_dir / "input_manifest.json").is_file():
        raise ValueError("Prepare and verify frozen inputs before preparing a stage")
    verify_prepared_inputs(work_dir)
    inputs = work_dir / "inputs"
    if stage == "pilot":
        blind, metadata = _load(inputs / "pilot/blind_50.json"), _load(inputs / "pilot/metadata_50.json")
    else:
        relative = "candidates/blind_candidates.json" if stage == "candidates" else "frames/blind_all.json"
        blind, metadata = _load(inputs / relative), None
        if include_candidates:
            blind["cases"].extend(_load(inputs / "candidates/blind_candidates.json")["cases"])
    batches = build_batches(blind, paired_packet=metadata, max_cases=max_cases, max_records=max_records, max_bytes=max_bytes)
    scope = stage + ("_with_candidates" if include_candidates else "")
    jobs, summaries = [], []
    for batch in batches:
        summaries.append({key: value for key, value in batch.items() if key not in ("packet", "paired_packet")})
        for arm in (("blind", "metadata") if stage == "pilot" else ("blind",)):
            body = batch["packet"] if arm == "blind" else batch["paired_packet"]
            relative = Path("packets") / scope / arm / (batch["batch_id"] + ".json")
            path = work_dir / relative
            write_frozen(path, encoded(body))
            for configuration in CONFIGURATIONS:
                jobs.append({**configuration, "job_id": scope + "_" + batch["batch_id"] + "_" + arm + "_" + configuration["configuration_id"],
                             "arm": arm, "batch_id": batch["batch_id"], "packet_path": relative.as_posix(), "packet_sha256": digest(path),
                             "prompt_path": f"inputs/prompt_{configuration['prompt_variant']}.txt",
                             "prompt_sha256": digest(inputs / f"prompt_{configuration['prompt_variant']}.txt"),
                             "schema_path": "inputs/response_schema.json", "schema_sha256": digest(inputs / "response_schema.json"),
                             "case_count": batch["case_count"], "record_count": batch["record_count"], "packet_bytes": path.stat().st_size,
                             "oversized_single_case": batch["oversized_single_case"]})
    schedule = {"analysis_package": ANALYSIS_PACKAGE, "seed": SEED, "stage": stage, "scope": scope,
                "configurations": list(CONFIGURATIONS), "provider_families": 1, "model_tiers": 2, "configuration_count": 3,
                "independent_reviewers_claimed": False, "case_count": len(blind["cases"]),
                "record_count": sum(len(case["records"]) for case in blind["cases"]),
                "arms": ["blind", "metadata"] if stage == "pilot" else ["blind"],
                "batch_limits": {"max_cases": max_cases, "max_records": max_records, "max_utf8_bytes": max_bytes},
                "batch_count_per_arm": len(batches), "initial_call_count": len(jobs), "maximum_schema_attempts_per_job": 2,
                "batch_summaries": summaries, "jobs": jobs, "model_calls_made": 0, "automatic_execution_enabled": False,
                "execution_requires_runner_budget_gate": True, "full_execution_requires_completed_pilot": stage != "pilot",
                "input_manifest_sha256": digest(work_dir / "input_manifest.json")}
    write_frozen(work_dir / "schedules" / (scope + ".json"), encoded(schedule))
    return schedule


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("pilot", "full", "candidates"), default="pilot")
    parser.add_argument("--include-candidates", action="store_true")
    parser.add_argument("--source-dir", type=Path, default=SOURCE)
    parser.add_argument("--work-dir", type=Path, default=WORK)
    args = parser.parse_args()
    prepare_inputs(source_dir=args.source_dir, work_dir=args.work_dir)
    schedule = prepare_stage(args.stage, work_dir=args.work_dir, include_candidates=args.include_candidates)
    print(json.dumps({"stage": args.stage, "cases": schedule["case_count"], "batches_per_arm": schedule["batch_count_per_arm"],
                      "initial_calls_planned": schedule["initial_call_count"], "model_calls_made": 0}))


if __name__ == "__main__":
    main()
