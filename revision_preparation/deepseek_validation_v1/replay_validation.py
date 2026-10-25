#!/usr/bin/env python3
"""Offline replay of a restored completed validation archive.

Run this against a disposable restored copy, never the production staging tree.
Expected CSV SHA-256 values are captured before any recomputation. Comparisons
are byte-exact, with one logged, path/header/SHA-locked historical empty-CSV
serialization adapter; there is no floating-point tolerance or row resorting.
The JSON check report must be written outside the restored root. A fresh restore
is required for an independent repeat; this replay rewrites derived results only.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import csv
import http.client
import io
import json
from pathlib import Path
import socket
import time
import urllib.request
from unittest.mock import patch

import aggregate
import audit_spending
import candidate_results
import candidate_totals
import paired_codex_comparison
import paired_identity_record_counts
import prepare
import validate
import validation_statistics


PILOTS = ("deepseek_pilot_20261025_v1", "deepseek_pilot_20261025_v2", "deepseek_pilot_20261025_v3")
FULL = "deepseek_full_20261025_v1"
CANDIDATE = "deepseek_candidates_20261025_v2"
MODULES = (aggregate, audit_spending, candidate_results, candidate_totals,
           paired_codex_comparison, paired_identity_record_counts, prepare, validate, validation_statistics)
LEGACY_PATH = "results/codex_comparison_pilot_v1/codex_qc_exclusions.csv"
LEGACY_EXPECTED_SHA = "7eb70257593da06f682a3ddda54a9d260d4fc514f645237f5ca74b08f8da61a6"
LEGACY_RECOMPUTED_SHA = "8961acef9a65913137593f526a81b05fb323c6b4ca4fa316cf5e685548635c6e"
LEGACY_EMPTY_BYTES = b"\r\n"
LEGACY_HEADER_BYTES = b"reviewer,case_id,raw_identity_label,raw_recurrence_label,qc_excluded,record_extractions_retained,policy_version\r\n"


@contextmanager
def network_disabled():
    """Fail closed if any replay dependency unexpectedly attempts networking."""
    def blocked(*args, **kwargs):
        raise RuntimeError("Network access is disabled during offline validation replay")
    with ExitStack() as stack:
        for name in ("socket.socket", "socket.create_connection", "urllib.request.urlopen", "http.client.HTTPConnection.connect", "http.client.HTTPSConnection.connect"):
            stack.enter_context(patch(name, side_effect=blocked))
        yield


def source_preflight(root):
    """Verify replay implementations and the existing frozen-input contracts."""
    code = {}
    for module in MODULES:
        implementation = Path(module.__file__).resolve()
        restored = root / implementation.name
        if not restored.exists() or validation_statistics.sha(restored) != validation_statistics.sha(implementation):
            raise ValueError("Replay implementation differs from restored source: " + implementation.name)
        code[implementation.name] = validation_statistics.sha(implementation)
    prepared = prepare.verify_prepared_inputs(root / "work")
    analysis = validation_statistics.load_inputs(root)
    return {"code_sha256": code, "prepared_input_manifest_sha256": validation_statistics.sha(root / "work/input_manifest.json"),
            "prepared_case_count": prepared["canonical_case_count"],
            "frozen_analysis_case_count": len(analysis["cases"]), "frozen_analysis_inputs_verified": True}


def build_actions(root):
    """Dependency-ordered replay plan. No runner/forecast/transport imports."""
    actions = []
    for run in (*PILOTS, FULL, CANDIDATE):
        actions.append(("aggregate:" + run, lambda run=run: aggregate.aggregate_run(root, run)))
    actions.append(("scientific_diagnostics:full_blind_and_pilot_v3", lambda: validation_statistics.run_statistics(root, FULL, pilot_run_name=PILOTS[-1])))
    actions.append(("candidate_scientific_diagnostics", lambda: candidate_results.run_candidate_statistics(root, CANDIDATE)))
    candidate_directory = root / "results" / CANDIDATE / "candidate_scientific_diagnostics"
    actions.append(("candidate_label_totals", lambda: candidate_totals.write_totals(root, candidate_directory)))
    codex = root / "work/codex_comparison_v1"
    schema, protocol = codex / "inputs/response_schema.json", codex / "protocol.json"
    for name, run, packet, response, provenance, scope, weighted in (
        ("codex_comparison_pilot_v1", PILOTS[-1], "pilot_blind_50.json", "pilot_codex_response.json", "reviewer_report.json", "fixed_50_pilot", False),
        ("codex_comparison_sample_v1", FULL, "sample_blind_331.json", "sample_codex_response.json", "sample_reviewer_report.json", "probability_319_targeted_12", True),
    ):
        actions.append(("paired:" + name,
                        lambda name=name, run=run, packet=packet, response=response, provenance=provenance, scope=scope, weighted=weighted:
                        paired_codex_comparison.run_comparison(root, run, codex / "inputs" / packet, codex / response, codex / provenance, protocol, schema,
                                                               output=root / "results" / name, scope=scope, weight_id_sample=weighted)))
    actions.append(("paired_identity_record_count_supplement", lambda: paired_identity_record_counts.write_supplement(root, root / "results/codex_comparison_sample_v1", codex / "inputs/sample_blind_331.json")))
    actions.append(("spending_reconciliation", lambda: audit_spending.reconcile(root / "work", root / "results/spending")))
    return actions


def apply_locked_legacy_adapter(root, expected):
    """Reproduce exactly one historical zero-row serialization, with no data edit.

    The frozen pilot artifact predates the declared-header writer. This adapter
    accepts only its locked original SHA and the locked header-only recomputed
    bytes. No other path, empty file, header, data row, or numeric formatting is
    normalized. It is applied only in the disposable restored replay tree.
    """
    root = Path(root).resolve()
    if root == Path(__file__).resolve().parent:
        raise ValueError("Legacy adapter cannot modify the production/source root")
    if expected.get(LEGACY_PATH) != LEGACY_EXPECTED_SHA:
        return []
    path = root / LEGACY_PATH
    if not path.exists() or validation_statistics.sha(path) == LEGACY_EXPECTED_SHA:
        return []
    data = path.read_bytes()
    rows = list(csv.reader(io.StringIO(data.decode("utf-8"))))
    if rows != [paired_codex_comparison.CODEX_QC_FIELDS]:
        raise ValueError("Known legacy pilot adapter requires its exact declared header and zero data rows")
    if data != LEGACY_HEADER_BYTES or validation_statistics.sha(path) != LEGACY_RECOMPUTED_SHA:
        raise ValueError("Known legacy pilot adapter recomputed bytes/SHA differ from the locked serialization")
    import hashlib
    if hashlib.sha256(LEGACY_EMPTY_BYTES).hexdigest() != LEGACY_EXPECTED_SHA:
        raise ValueError("Legacy empty serialization lock is inconsistent")
    path.write_bytes(LEGACY_EMPTY_BYTES)
    return [{"path": LEGACY_PATH, "original_expected_sha256": LEGACY_EXPECTED_SHA,
             "unadapted_recomputed_sha256": LEGACY_RECOMPUTED_SHA, "adapted_sha256": validation_statistics.sha(path),
             "data_rows": 0, "labels_changed": False, "numeric_cells_changed": False,
             "reason": "Historical zero-row pilot CSV used an empty CRLF; current writer emits the declared header. Exact path/header/SHA-locked restored-output format compatibility only."}]


def replay(root, check_output):
    root, check_output = Path(root).resolve(), Path(check_output).resolve()
    if root == Path(__file__).resolve().parent:
        raise ValueError("Refuse production/source-root replay; use a separate disposable restored root")
    if check_output.is_relative_to(root):
        raise ValueError("Replay check report must be outside the restored root")
    if not (root / "work").is_dir() or not (root / "results").is_dir():
        raise ValueError("Replay root must contain restored work and results directories")
    if (root / "work/.active_runner.lock").exists():
        raise ValueError("Refuse replay while a runner is active")
    expected = {str(path.relative_to(root)): validation_statistics.sha(path) for path in sorted((root / "results").rglob("*.csv"))}
    if not expected:
        raise ValueError("Restored root has no expected result CSVs")
    report = {"status": "running", "root": str(root), "csv_comparison_policy": "SHA-256 byte equality; one explicitly logged path/header/SHA-locked historical zero-row serialization adapter; no generic normalization, float tolerance, row sorting, or numeric coercion",
              "expected_snapshot_taken_before_recomputation": True, "expected_csv_count": len(expected), "expected_csv_sha256": expected,
              "network_disabled": True, "model_calls": 0, "credential_reads": 0,
              "historical_pilots": {PILOTS[0]: {"replayed": True, "use_for_inference": False}, PILOTS[1]: {"replayed": True, "use_for_inference": False}, PILOTS[2]: {"replayed": True, "use_for_inference": True}},
              "old_29_table_baseline": "verified separately; not recomputed by this wrapper", "actions": []}
    started = time.monotonic()
    try:
        with network_disabled():
            report["preflight"] = source_preflight(root)
            for name, action in build_actions(root):
                step = {"name": name, "status": "running"}
                report["actions"].append(step)
                result = action()
                step["status"] = "passed"
                if isinstance(result, dict):
                    step["result_status"] = result.get("status", "completed")
                    for key in ("distinct_cases", "scheduled_candidate_cases", "case_count", "record_count", "unique_paid_attempts", "charged_upper_cny"):
                        if key in result:
                            step[key] = result[key]
            unadapted = {str(path.relative_to(root)): validation_statistics.sha(path) for path in sorted((root / "results").rglob("*.csv"))}
            report["unadapted_csv_sha256"] = unadapted
            report["direct_byte_exact_csvs"] = sum(expected[path] == unadapted.get(path) for path in expected)
            report["serialization_adapters"] = apply_locked_legacy_adapter(root, expected)
            actual = {str(path.relative_to(root)): validation_statistics.sha(path) for path in sorted((root / "results").rglob("*.csv"))}
            report["actual_csv_count"] = len(actual)
            report["actual_csv_sha256"] = actual
            report["missing_csvs"] = sorted(set(expected) - set(actual))
            report["unexpected_csvs"] = sorted(set(actual) - set(expected))
            report["mismatched_csvs"] = [{"path": path, "expected_sha256": expected[path], "actual_sha256": actual[path]} for path in sorted(set(expected) & set(actual)) if expected[path] != actual[path]]
            report["exactly_reproduced_csvs"] = report["direct_byte_exact_csvs"]
            report["documented_legacy_serialization_adapted_csvs"] = len(report["serialization_adapters"])
            report["byte_verified_after_documented_adapter_csvs"] = sum(expected[path] == actual.get(path) for path in expected)
            passed = not any(report[key] for key in ("missing_csvs", "unexpected_csvs", "mismatched_csvs"))
            report["status"] = ("passed_with_documented_legacy_serialization_adapter" if report["serialization_adapters"] else "passed") if passed else "failed_csv_comparison"
    except Exception as error:
        if report["actions"] and report["actions"][-1]["status"] == "running":
            report["actions"][-1]["status"] = "failed"
        report["status"] = "failed_replay"
        report["error_type"], report["error"] = type(error).__name__, str(error)
    report["elapsed_seconds"] = time.monotonic() - started
    check_output.parent.mkdir(parents=True, exist_ok=True)
    validation_statistics.dump_json(check_output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output-check", "--outputcheck", dest="check_output", type=Path, required=True)
    args = parser.parse_args()
    report = replay(args.root, args.check_output)
    print(json.dumps({key: report.get(key) for key in ("status", "expected_csv_count", "actual_csv_count", "direct_byte_exact_csvs", "documented_legacy_serialization_adapted_csvs", "byte_verified_after_documented_adapter_csvs", "error")}))
    raise SystemExit(0 if report["status"] in {"passed", "passed_with_documented_legacy_serialization_adapter"} else 1)


if __name__ == "__main__":
    main()
