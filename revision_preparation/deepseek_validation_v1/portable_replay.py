"""Narrow opt-in portability verification; never rewrites numerical results."""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path
import re
import struct


VERSION = "portable_replay_v1"
RULES = {"weighted_ulp": {"category": "weighted_projection_or_share", "comparison": "ulp", "maximum_ulp_distance": 4},
         "cp95_absolute": {"category": "CP95", "comparison": "absolute", "maximum_absolute_delta": "1e-12"},
         "kappa_absolute": {"category": "fleiss_kappa", "comparison": "absolute", "maximum_absolute_delta": "1e-12"}}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_policy(path):
    policy = json.loads(Path(path).read_text())
    if policy.get("policy_version") != VERSION or policy.get("rules") != RULES:
        raise ValueError("Unsupported or altered portable verification policy/rule limits")
    return policy


def parse(data):
    return list(csv.reader(io.StringIO(data.decode("utf-8"), newline=""), strict=True))


def encode(rows):
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows(rows)
    return stream.getvalue().encode("utf-8")


def ulps(a, b):
    def position(value):
        bits = struct.unpack(">Q", struct.pack(">d", value))[0]
        magnitude = bits & ((1 << 63) - 1)
        return (1 << 63) - magnitude if bits >> 63 else (1 << 63) + magnitude
    return abs(position(a) - position(b))


def cell_rule(entry, column, row):
    if column == "value" and "registry_value_rules" in entry:
        matches = [r["rule"] for r in entry["registry_value_rules"] if (r["source_table"], r["metric"]) == (row.get("source_table"), row.get("metric"))]
        return matches[0] if len(matches) == 1 else None
    return entry.get("columns", {}).get(column)


def compare_csv(path, expected, actual, policy):
    """Return full accepted-cell evidence, or reject the entire changed file."""
    result = {"path": path, "policy_version": policy["policy_version"], "expected_sha256": sha(expected),
              "actual_sha256": sha(actual), "accepted": False, "accepted_cells": [], "rejections": []}
    entry = policy["files"].get(path)
    def reject(reason, **context):
        result["rejections"].append({"reason": reason, **context})
    if entry is None:
        reject("file_not_allowlisted"); return result
    if sha(expected) != entry["expected_sha256"]:
        reject("frozen_expected_hash_mismatch"); return result
    try:
        left, right = parse(expected), parse(actual)
    except (UnicodeError, csv.Error) as error:
        reject("csv_parse_error", detail=str(error)); return result
    if not left or not right or left[0] != right[0] or len(set(left[0])) != len(left[0]):
        reject("header_change_or_duplicate_header"); return result
    if len(left) != len(right) or any(len(a) != len(b) or len(a) != len(left[0]) for a, b in zip(left, right)):
        reject("row_count_or_width_change"); return result
    # Check serialization, rather than silently normalizing quotes or newlines.
    if encode(left) != expected or encode(right) != actual:
        reject("non_numeric_serialization_change"); return result
    header = left[0]
    restored_text = [row[:] for row in right]
    changed = 0
    for i, (erow, arow) in enumerate(zip(left[1:], right[1:]), 2):
        emap, amap = dict(zip(header, erow)), dict(zip(header, arow))
        for j, (e, a) in enumerate(zip(erow, arow)):
            if e == a:
                continue
            changed += 1
            column = header[j]
            context = {"csv_row": i, "column_index": j + 1, "column": column, "expected": e, "actual": a}
            rule_name = cell_rule(entry, column, emap)
            if rule_name not in RULES or cell_rule(entry, column, amap) != rule_name:
                reject("cell_not_allowlisted", **context); continue
            if re.fullmatch(r"[+-]?\d+", e) or re.fullmatch(r"[+-]?\d+", a):
                reject("integer_text_must_be_exact", **context); continue
            try:
                ev, av = float(e), float(a)
            except (ValueError, OverflowError):
                reject("not_finite_float_text", **context); continue
            if not math.isfinite(ev) or not math.isfinite(av):
                reject("not_finite_float_text", **context); continue
            if e != repr(ev) or a != repr(av):
                reject("noncanonical_float_text", **context); continue
            if math.copysign(1.0, ev) != math.copysign(1.0, av):
                reject("sign_change", **context); continue
            if (ev == 0.0) != (av == 0.0):
                reject("zero_nonzero_transition", **context); continue
            rule = RULES[rule_name]
            distance, delta = ulps(ev, av), abs(ev - av)
            accepted = distance <= 4 if rule["comparison"] == "ulp" else delta <= 1e-12
            if not accepted:
                reject("numeric_threshold_exceeded", rule=rule_name, ulp_distance=distance, absolute_delta=delta, **context); continue
            identifying = {k: emap[k] for k in ("source_table", "metric", "arm", "reviewer", "population", "stratum", "field", "category", "outcome", "report_scope", "source_type", "reference_label") if k in emap}
            result["accepted_cells"].append({**context, "rule": rule_name, "category": rule["category"], "ulp_distance": distance,
                                             "absolute_delta": delta, "expected_binary64_hex": ev.hex(), "actual_binary64_hex": av.hex(),
                                             "row_selector_context": identifying})
            restored_text[i - 1][j] = e
    if not changed:
        reject("no_changed_computed_cells")
    if not result["rejections"] and encode(restored_text) != expected:
        reject("bytes_outside_accepted_numeric_cells_changed")
    result["changed_cells"] = changed
    result["accepted"] = not result["rejections"]
    result["actual_source_values_unchanged"] = True
    return result


def verify_claim_formats(policy, policy_directory, root, expected, actual):
    """Use hash-pinned public claim selectors/formatter without any paper files."""
    descriptor = policy["claim_formatting"]
    base, root = Path(policy_directory).resolve(), Path(root).resolve()
    spec_path, formatter_path = base / descriptor["spec_relative_path"], base / descriptor["formatter_relative_path"]
    if sha(spec_path.read_bytes()) != descriptor["spec_sha256"] or sha(formatter_path.read_bytes()) != descriptor["formatter_sha256"]:
        raise ValueError("Portable claim spec/formatter hashes differ")
    spec = json.loads(spec_path.read_text())
    module_spec = importlib.util.spec_from_file_location("portable_claim_formatter", formatter_path)
    formatter = importlib.util.module_from_spec(module_spec); module_spec.loader.exec_module(formatter)
    ecache, acache, checks = {}, {}, []
    for item in spec["claims"]:
        source = item.get("source", {})
        public_path = source.get("path", "")
        prefix = descriptor["report_source_prefix"]
        if not public_path.startswith(prefix):
            continue
        relative = "results/" + public_path[len(prefix):]
        if relative.endswith(".gz"):
            relative = relative[:-3]
        if relative not in policy["files"] or relative not in expected:
            continue
        if relative not in actual:
            raise ValueError("Claim source is missing from replay: " + relative)
        for cache, data in ((ecache, expected[relative]), (acache, actual[relative])):
            if relative not in cache:
                cache[relative] = {"raw_sha256": sha(data), "content_sha256": sha(data), "document": list(csv.DictReader(io.StringIO(data.decode("utf-8"), newline=""))), "kind": "csv", "bytes": len(data)}
        mapped = {**source, "path": relative}
        ev, etrace = formatter.source_value(mapped, root, ecache)
        av, atrace = formatter.source_value(mapped, root, acache)
        ef, _ = formatter.format_value(ev, item["format"])
        af, _ = formatter.format_value(av, item["format"])
        checks.append({"claim_id": item["id"], "source": relative, "column": source["column"], "filters": source.get("filters", {}),
                       "format": item["format"], "expected_raw": ev, "actual_raw": av, "expected_formatted": ef, "actual_formatted": af,
                       "unchanged": ef == af})
    if not checks:
        raise ValueError("No affected manuscript claim selectors were verified")
    return {"status": "passed" if all(c["unchanged"] for c in checks) else "failed", "spec_sha256": descriptor["spec_sha256"],
            "formatter_sha256": descriptor["formatter_sha256"], "claims_checked": len(checks), "checks": checks, "paper_files_read": False}


def verify_registry_mirrors(comparison, expected, actual, policy):
    """Changed registry values must exactly mirror their selected source cells."""
    path = comparison["path"]
    if "registry_value_rules" not in policy["files"][path]:
        return
    def identity(row):
        category_keys = ("field", "model_label", "source_type", "outcome", "consensus_status", "reference_label", "geometry", "available_configurations", "availability_basis", "classification_basis", "codex_label", "deepseek_label", "both_resolved", "absolute_year_difference")
        category = "|".join(f"{key}={row[key]}" for key in category_keys if key in row)
        population = row.get("population", row.get("sample_set", row.get("report_scope", row.get("control_set", row.get("sampling_frames", row.get("scope", ""))))))
        return row.get("arm", ""), row.get("reviewer", ""), population, row.get("stratum", ""), category
    def documents(data):
        rows = parse(data)
        return [dict(zip(rows[0], row)) for row in rows[1:]]
    registry = documents(expected[path])
    for cell in comparison["accepted_cells"]:
        row = registry[cell["csv_row"] - 2]
        source = str(Path(path).parent / row["source_table"])
        metric = row["metric"]
        reason = None
        if (Path(row["source_table"]).name != row["source_table"] or source not in policy["files"]
                or policy["files"][source].get("columns", {}).get(metric) != cell["rule"]):
            reason = "registry_source_not_allowlisted"
        else:
            key = row.get("arm", ""), row.get("reviewer", ""), row.get("population", ""), row.get("stratum", ""), row.get("category", "")
            for side, store, value in (("expected", expected, cell["expected"]), ("actual", actual, cell["actual"])):
                matches = [r for r in documents(store[source]) if identity(r) == key]
                if len(matches) != 1 or matches[0].get(metric) != value:
                    reason = "registry_value_not_exact_source_mirror_" + side
                    break
        if reason:
            comparison["rejections"].append({"reason": reason, "csv_row": cell["csv_row"], "source_table": row["source_table"], "metric": metric})
        else:
            cell["exact_source_mirror_verified"] = True
    comparison["accepted"] = not comparison["rejections"]
