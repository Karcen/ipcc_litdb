#!/usr/bin/env python3
"""Offline replay of a restored completed validation archive.

Run this against a disposable restored copy, never the production staging tree.
Expected CSV SHA-256 values are captured before any recomputation. Comparisons
default to exact bytes, with one logged historical empty-CSV serialization
adapter. Optional versioned portability verification is explicitly scoped to
frozen hashes and computed float cells; it never rewrites numerical values.
The JSON check report must be written outside the restored root. A fresh restore
is required for an independent repeat; this replay rewrites derived results only.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import csv
from decimal import Decimal, localcontext
import hashlib
import http.client
import io
import json
import math
from pathlib import Path
import re
import socket
import struct
import sys
import time
import urllib.request
from unittest.mock import patch

import aggregate
import audit_spending
import candidate_results
import candidate_totals
import paired_codex_comparison
import paired_identity_record_counts
import portable_replay
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
DIAGNOSTIC_EXAMPLE_LIMIT = 20
DIAGNOSTIC_VALUE_LIMIT = 256


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
    if hashlib.sha256(LEGACY_EMPTY_BYTES).hexdigest() != LEGACY_EXPECTED_SHA:
        raise ValueError("Legacy empty serialization lock is inconsistent")
    path.write_bytes(LEGACY_EMPTY_BYTES)
    return [{"path": LEGACY_PATH, "original_expected_sha256": LEGACY_EXPECTED_SHA,
             "unadapted_recomputed_sha256": LEGACY_RECOMPUTED_SHA, "adapted_sha256": validation_statistics.sha(path),
             "data_rows": 0, "labels_changed": False, "numeric_cells_changed": False,
             "reason": "Historical zero-row pilot CSV used an empty CRLF; current writer emits the declared header. Exact path/header/SHA-locked restored-output format compatibility only."}]


def _display(value):
    """Bound report size while preserving lengths and hashes of long values."""
    if value is None:
        return {"value": None, "characters": 0, "truncated": False}
    result = {"value": value[:DIAGNOSTIC_VALUE_LIMIT], "characters": len(value), "truncated": len(value) > DIAGNOSTIC_VALUE_LIMIT}
    if result["truncated"]:
        result["sha256_utf8"] = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return result


def _numeric(value):
    if value is None:
        return "missing", None
    if value == "":
        return "empty", None
    if re.fullmatch(r"[+-]?\d+", value):
        return "integer", int(value)
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return "string", None
    return ("finite_float", number) if math.isfinite(number) else ("nonfinite_numeric", None)


def binary64_ulp_distance(a, b):
    """Ordered IEEE-754 binary64 steps; signed zero is the same numeric point."""
    if not math.isfinite(a) or not math.isfinite(b):
        raise ValueError("ULP diagnostics require finite binary64 values")
    def ordered(value):
        bits = struct.unpack(">Q", struct.pack(">d", value))[0]
        magnitude = bits & ((1 << 63) - 1)
        return (1 << 63) - magnitude if bits >> 63 else (1 << 63) + magnitude
    return abs(ordered(a) - ordered(b))


def _cell_difference(expected, actual, *, header=False):
    expected_type, enumber = _numeric(expected)
    actual_type, anumber = _numeric(actual)
    result = {"expected": _display(expected), "actual": _display(actual), "expected_type": expected_type, "actual_type": actual_type}
    numeric = {"integer", "finite_float"}
    if header:
        result["difference_kind"] = "header_change"
    elif expected is None:
        result["difference_kind"] = "cell_added"
    elif actual is None:
        result["difference_kind"] = "cell_removed"
    elif expected_type == actual_type == "integer":
        result.update(difference_kind="integer_value_change" if enumber != anumber else "numeric_representation_change",
                      integer_delta=anumber - enumber, absolute_integer_delta=abs(anumber - enumber))
    elif expected_type in numeric and actual_type in numeric:
        try:
            a, b = float(enumber), float(anumber)
        except OverflowError:
            a = b = None
        result["difference_kind"] = "numeric_representation_change" if a is not None and a == b else "float_value_change"
        if a is not None and math.isfinite(a) and math.isfinite(b):
            delta = abs(b - a)
            result.update(expected_binary64_hex=a.hex(), actual_binary64_hex=b.hex(), ulp_distance=binary64_ulp_distance(a, b),
                          absolute_delta=delta if math.isfinite(delta) else None, absolute_delta_overflow=not math.isfinite(delta))
            # Decimal text difference remains visible when distinct CSV strings
            # round to the same binary64 value. This is diagnostic, not tolerance.
            with localcontext() as context:
                context.prec = max(50, len(expected) + len(actual) + 10)
                result["absolute_decimal_text_delta"] = str(abs(Decimal(actual) - Decimal(expected)))
    else:
        result["difference_kind"] = "string_or_type_change"
    return result


def _count_column(name, row):
    return bool(name and (name in validation_statistics.COUNT_FIELDS or name in {"n", "N"} or name.endswith(("_n", "_N"))
                          or name == "value" and row.get("unit") == "count"))


def csv_mismatch_diagnostics(expected_bytes, actual_bytes, *, example_limit=DIAGNOSTIC_EXAMPLE_LIMIT):
    """Describe every changed positional cell without normalizing comparison.

    Counts cover all cells, including headers and absent cells in added/removed
    rows or changed row widths. Only examples are capped. Parsing, numeric
    conversion and ULP calculations never affect the strict SHA verdict.
    """
    if type(example_limit) is not int or example_limit < 0:
        raise ValueError("diagnostic example limit must be a nonnegative integer")
    result = {"expected_byte_count": len(expected_bytes), "actual_byte_count": len(actual_bytes),
              "policy": "Positional CSV diagnostics only; original bytes/labels/counts and strict comparison verdict are unchanged.",
              "example_limit": example_limit, "value_character_limit": DIAGNOSTIC_VALUE_LIMIT}
    parsed = []
    for side, data in (("expected", expected_bytes), ("actual", actual_bytes)):
        try:
            parsed.append(list(csv.reader(io.StringIO(data.decode("utf-8"), newline=""), strict=True)))
        except (UnicodeError, csv.Error) as error:
            result.update(csv_parsed=False, parse_error_side=side, parse_error=str(error)[:DIAGNOSTIC_VALUE_LIMIT])
            return result
    left, right = parsed
    eh, ah = left[0] if left else [], right[0] if right else []
    expected_data_rows, actual_data_rows = max(0, len(left) - 1), max(0, len(right) - 1)
    result.update(csv_parsed=True, header_equal=eh == ah, expected_header_columns=len(eh), actual_header_columns=len(ah),
                  expected_data_rows=expected_data_rows, actual_data_rows=actual_data_rows,
                  added_data_rows=max(0, actual_data_rows - expected_data_rows), removed_data_rows=max(0, expected_data_rows - actual_data_rows))
    examples, kinds, columns = [], {}, {}
    changed = header_changed = data_changed = changed_rows = widths = 0
    context_names = ("source_table", "arm", "reviewer", "population", "stratum", "geometry", "field", "metric", "category", "case_id", "record_id", "outcome", "reference_label", "unit")
    for row_index in range(max(len(left), len(right))):
        erow = left[row_index] if row_index < len(left) else []
        arow = right[row_index] if row_index < len(right) else []
        if len(erow) != len(arow):
            widths += 1
        row_changed = False
        emap, amap = dict(zip(eh, erow)), dict(zip(ah, arow))
        for column_index in range(max(len(erow), len(arow))):
            e = erow[column_index] if column_index < len(erow) else None
            a = arow[column_index] if column_index < len(arow) else None
            if e == a:
                continue
            changed += 1; row_changed = True
            if row_index == 0:
                header_changed += 1
            else:
                data_changed += 1
            delta = _cell_difference(e, a, header=row_index == 0)
            kind = delta["difference_kind"]
            kinds[kind] = kinds.get(kind, 0) + 1
            ec = eh[column_index] if column_index < len(eh) else None
            ac = ah[column_index] if column_index < len(ah) else None
            declared_count = _count_column(ec, emap) or _count_column(ac, amap)
            column = columns.setdefault(column_index, {"column_index": column_index + 1, "expected_column": _display(ec), "actual_column": _display(ac),
                                                      "changed_cell_count": 0, "declared_count_cell_changes": 0, "difference_kinds": {},
                                                      "maximum_ulp_distance": None, "maximum_absolute_delta": None})
            column["changed_cell_count"] += 1
            column["declared_count_cell_changes"] += int(declared_count and row_index > 0)
            column["difference_kinds"][kind] = column["difference_kinds"].get(kind, 0) + 1
            for key, destination in (("ulp_distance", "maximum_ulp_distance"), ("absolute_delta", "maximum_absolute_delta")):
                if delta.get(key) is not None:
                    column[destination] = delta[key] if column[destination] is None else max(column[destination], delta[key])
            if len(examples) < example_limit:
                example = {"csv_row": row_index + 1, "data_row": row_index if row_index else None, "column_index": column_index + 1,
                           "expected_column": _display(ec), "actual_column": _display(ac), "declared_count_cell": bool(declared_count and row_index > 0), **delta}
                if row_index:
                    example["expected_row_context"] = {name: _display(emap[name]) for name in context_names if name in emap}
                    example["actual_row_context"] = {name: _display(amap[name]) for name in context_names if name in amap}
                examples.append(example)
        if row_changed and row_index:
            changed_rows += 1
    result.update(changed_cell_count=changed, header_changed_cell_count=header_changed, data_changed_cell_count=data_changed,
                  changed_data_row_count=changed_rows, row_width_difference_count=widths, difference_kind_counts=kinds,
                  changed_columns=[columns[index] for index in sorted(columns)], examples=examples,
                  omitted_changed_cell_examples=changed - len(examples), parsed_cells_equal=changed == 0,
                  serialization_only_difference=expected_bytes != actual_bytes and changed == 0)
    return result


def replay(root, check_output, *, portable_policy=None):
    root, check_output = Path(root).resolve(), Path(check_output).resolve()
    if root == Path(__file__).resolve().parent:
        raise ValueError("Refuse production/source-root replay; use a separate disposable restored root")
    if check_output.is_relative_to(root):
        raise ValueError("Replay check report must be outside the restored root")
    if not (root / "work").is_dir() or not (root / "results").is_dir():
        raise ValueError("Replay root must contain restored work and results directories")
    if (root / "work/.active_runner.lock").exists():
        raise ValueError("Refuse replay while a runner is active")
    expected_bytes = {str(path.relative_to(root)): path.read_bytes() for path in sorted((root / "results").rglob("*.csv"))}
    expected = {path: hashlib.sha256(data).hexdigest() for path, data in expected_bytes.items()}
    if not expected:
        raise ValueError("Restored root has no expected result CSVs")
    report = {"status": "running", "root": str(root), "csv_comparison_policy": "SHA-256 byte equality; one explicitly logged path/header/SHA-locked historical zero-row serialization adapter; no generic normalization, float tolerance, row sorting, or numeric coercion",
              "expected_snapshot_taken_before_recomputation": True, "expected_csv_count": len(expected), "expected_csv_sha256": expected,
              "expected_csv_bytes_retained_in_memory": True, "expected_csv_snapshot_bytes": sum(len(data) for data in expected_bytes.values()),
              "diagnostic_runtime": {"python_version": sys.version, "platform": sys.platform, "float_mantissa_bits": sys.float_info.mant_dig},
              "network_disabled": True, "model_calls": 0, "credential_reads": 0,
              "historical_pilots": {PILOTS[0]: {"replayed": True, "use_for_inference": False}, PILOTS[1]: {"replayed": True, "use_for_inference": False}, PILOTS[2]: {"replayed": True, "use_for_inference": True}},
              "old_29_table_baseline": "verified separately; not recomputed by this wrapper", "actions": []}
    started = time.monotonic()
    try:
        with network_disabled():
            policy = portable_replay.read_policy(portable_policy) if portable_policy else None
            if policy:
                for path, descriptor in policy["files"].items():
                    if expected.get(path) != descriptor["expected_sha256"]:
                        raise ValueError("Portable policy requires its frozen expected CSV hash: " + path)
                report["portable_policy"] = {"version": policy["policy_version"], "sha256": validation_statistics.sha(portable_policy), "opt_in": True}
                report["csv_comparison_policy"] += "; explicit portable-policy opt-in separately verifies only its frozen-hash/column-scoped computed floats and unchanged claim formatting; numeric-compared CSVs are not byte-exact"
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
            report["byte_mismatched_csvs"] = list(report["mismatched_csvs"])
            report["csv_mismatch_diagnostics"] = {item["path"]: csv_mismatch_diagnostics(expected_bytes[item["path"]], (root / item["path"]).read_bytes())
                                                  for item in report["mismatched_csvs"]}
            report["exactly_reproduced_csvs"] = report["direct_byte_exact_csvs"]
            report["documented_legacy_serialization_adapted_csvs"] = len(report["serialization_adapters"])
            report["byte_verified_after_documented_adapter_csvs"] = sum(expected[path] == actual.get(path) for path in expected)
            report["portable_numeric_compared_csvs"] = 0
            report["portable_numeric_accepted_cells"] = 0
            if policy:
                actual_bytes = {path: (root / path).read_bytes() for path in policy["files"] if (root / path).is_file()}
                comparisons = [portable_replay.compare_csv(item["path"], expected_bytes[item["path"]], (root / item["path"]).read_bytes(), policy)
                               for item in report["mismatched_csvs"]]
                for comparison in comparisons:
                    if comparison["accepted"]:
                        portable_replay.verify_registry_mirrors(comparison, expected_bytes, actual_bytes, policy)
                report["portable_numeric_comparisons"] = comparisons
                report["claim_format_verification"] = portable_replay.verify_claim_formats(policy, Path(portable_policy).resolve().parent, root, expected_bytes, actual_bytes)
                accepted = {item["path"] for item in comparisons if item["accepted"]}
                if report["claim_format_verification"]["status"] != "passed":
                    accepted = set()
                report["portable_numeric_compared_csvs"] = len(accepted)
                report["portable_numeric_accepted_cells"] = sum(len(item["accepted_cells"]) for item in comparisons if item["path"] in accepted)
                report["mismatched_csvs"] = [item for item in report["mismatched_csvs"] if item["path"] not in accepted]
            passed = not any(report[key] for key in ("missing_csvs", "unexpected_csvs", "mismatched_csvs"))
            passed = passed and (not policy or report["claim_format_verification"]["status"] == "passed")
            report["status"] = ("passed_with_portable_numeric_verification" if report["portable_numeric_compared_csvs"] else
                                "passed_with_documented_legacy_serialization_adapter" if report["serialization_adapters"] else "passed") if passed else "failed_csv_comparison"
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
    parser.add_argument("--portable-policy", type=Path, help="Explicit versioned, frozen-SHA/column-scoped numeric verification policy; default remains exact bytes")
    args = parser.parse_args()
    report = replay(args.root, args.check_output, portable_policy=args.portable_policy)
    print(json.dumps({key: report.get(key) for key in ("status", "expected_csv_count", "actual_csv_count", "direct_byte_exact_csvs", "documented_legacy_serialization_adapted_csvs", "byte_verified_after_documented_adapter_csvs", "portable_numeric_compared_csvs", "portable_numeric_accepted_cells", "error")}))
    raise SystemExit(0 if report["status"] in {"passed", "passed_with_documented_legacy_serialization_adapter", "passed_with_portable_numeric_verification"} else 1)


if __name__ == "__main__":
    main()
