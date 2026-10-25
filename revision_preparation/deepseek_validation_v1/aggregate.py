#!/usr/bin/env python3
"""Offline summaries of archived DeepSeek review configurations.

Written for analysis 1.5.0; no transport, credentials, or model calls are used.
The three reviewers are configurations of two DeepSeek models. Agreement is
not accuracy, and code-generated abstentions are never model decisions.
"""
from __future__ import annotations

import argparse
import collections
from decimal import Decimal
import csv
import hashlib
import json
import pathlib
import re
from typing import Callable

CONFIGURATIONS = ("flash_A", "flash_B", "pro_C")
CASE_FIELDS = ("identity", "cross_report_recurrence")
RECORD_FIELDS = ("cited_year", "outlet", "source_type", "contains_multiple_citations")
COMPLETE_STATUSES = {"valid", "unresolvable_after_two_invalid_outputs"}
MODEL_IDS = {"flash_A": "deepseek-flash", "flash_B": "deepseek-flash", "pro_C": "deepseek-v4-pro"}
INTERPRETATION = "agreement between three configurations of two DeepSeek models; not accuracy or independent-model reliability"


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def _dump(path, value):
    pathlib.Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _csv(path, rows, fields):
    with pathlib.Path(path).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def canon(value, field):
    """Canonical comparison values; preserve each raw response in its archive."""
    if value is None:
        return "unresolvable"
    if field == "outlet":
        return re.sub(r"[^\w]+", "", value.casefold()) or "unresolvable"
    return str(value)


def majority(votes):
    """Two matching available votes suffice; None denotes no model decision.

    Availability is reported separately by the caller. A genuine model label
    'unresolvable' is a vote; a code-generated abstention is represented by None.
    """
    if len(votes) != 3:
        raise ValueError("the fixed panel requires exactly three configuration slots")
    counts = collections.Counter(value for value in votes if value is not None)
    if not counts:
        return "unresolvable", "no_majority"
    value, count = counts.most_common(1)[0]
    if count < 2:
        return "unresolvable", "no_majority"
    return value, "unanimous" if count == 3 else "two_of_three"


def fleiss_kappa(votes):
    """Descriptive Fleiss kappa on complete three-configuration items only."""
    if not votes:
        return None
    if any(len(row) != 3 or any(value is None for value in row) for row in votes):
        raise ValueError("Fleiss kappa requires three available configuration labels per item")
    counts = collections.Counter(value for row in votes for value in row)
    n_items = len(votes)
    expected = sum((count / (n_items * 3)) ** 2 for count in counts.values())
    observed = sum(sum(count * (count - 1) for count in collections.Counter(row).values()) / 6 for row in votes) / n_items
    return (observed - expected) / (1 - expected) if expected < 1 else None


def archived_rule_year(raw):
    """Exact frozen R3 regex and group(1) behavior, including the 300-char limit."""
    match = re.search(r"\b(1[6-9]\d{2}|20\d{2})[a-z]?\b", str(raw or "")[:300])
    return match.group(1) if match else "unresolvable"


def _safe_path(base, relative):
    path = (base / relative).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError("archive path escapes its declared root")
    return path


def _validator(root):
    # Bind validation to this run's local schema, never to a different checkout.
    from validate import validate_response
    schema = root / "work" / "inputs" / "response_schema.json"
    if not schema.exists():
        schema = root / "source_panel" / "response_schema.json"
    return lambda response, packet: validate_response(response, packet, schema)


def load_run(root, run_name, validator: Callable | None = None):
    """Read and verify runner archives. Missing calls stay explicitly missing.

    packet_path is relative to root/work. No archive is synthesized or repaired.
    Returns a dict used by summarise_cases and summarise_records.
    """
    root = pathlib.Path(root).resolve()
    work = root / "work"
    run_dir = _safe_path(work / "runs", run_name)
    schedule_path = run_dir / "schedule.json"
    schedule = _read(schedule_path)
    jobs = schedule["jobs"]
    if not jobs:
        raise ValueError("schedule has no jobs")
    if schedule.get("initial_call_count", len(jobs)) != len(jobs):
        raise ValueError("scheduled initial call count differs from jobs")
    validator = validator or _validator(root)
    seen_calls, coverage, cases, decisions = set(), set(), {}, {}
    calls, usage, paths = [], [], {schedule_path}
    input_manifest = work / "input_manifest.json"
    if schedule.get("input_manifest_sha256"):
        if not input_manifest.exists() or sha256(input_manifest) != schedule["input_manifest_sha256"]:
            raise ValueError("prepared input manifest differs from the frozen schedule")
        paths.add(input_manifest)
    for job in jobs:
        call_id = job.get("call_id", job.get("job_id"))
        if not call_id or call_id in seen_calls:
            raise ValueError("duplicate or missing scheduled call_id")
        seen_calls.add(call_id)
        arm, config = job["arm"], job["configuration_id"]
        if arm not in ("blind", "metadata") or config not in CONFIGURATIONS:
            raise ValueError("unexpected arm or configuration")
        if job["model_id"] != MODEL_IDS[config]:
            raise ValueError("configuration model differs from the fixed panel")
        packet_path = _safe_path(work, job["packet_path"])
        packet = _read(packet_path)
        if job.get("packet_sha256") and job["packet_sha256"] != sha256(packet_path):
            raise ValueError(f"scheduled packet hash mismatch in {call_id}")
        paths.add(packet_path)
        if job.get("schema_path"):
            schema_path = _safe_path(work, job["schema_path"])
            if job.get("schema_sha256") != sha256(schema_path):
                raise ValueError(f"scheduled schema hash mismatch in {call_id}")
            paths.add(schema_path)
        if job.get("case_count", len(packet["cases"])) != len(packet["cases"]):
            raise ValueError(f"scheduled case count differs from packet in {call_id}")
        if job.get("record_count", sum(len(c["records"]) for c in packet["cases"])) != sum(len(c["records"]) for c in packet["cases"]):
            raise ValueError(f"scheduled record count differs from packet in {call_id}")
        arm_cases = cases.setdefault(arm, {})
        for case in packet["cases"]:
            cid = case["case_id"]
            key = (arm, config, cid)
            if key in coverage:
                raise ValueError("case scheduled twice for one configuration and arm")
            coverage.add(key)
            if cid in arm_cases and arm_cases[cid] != case:
                raise ValueError("case packet differs between configurations")
            arm_cases[cid] = case
        directory = _safe_path(run_dir / "calls", call_id)
        result_path = directory / "result.json"
        call = {"call_id": call_id, "arm": arm, "configuration_id": config,
                "model_id": job["model_id"], "batch_id": job["batch_id"],
                "status": "missing", "model_decision_available": False,
                "case_count": len(packet["cases"]), "attempt_count": 0}
        if not result_path.exists():
            calls.append(call)
            continue
        result = _read(result_path)
        paths.add(result_path)
        call.update(status=result["status"], attempt_count=len(result.get("attempts", [])))
        for i, attempt in enumerate(result.get("attempts", []), 1):
            token = attempt.get("usage") or {}
            if not isinstance(token, dict):
                raise ValueError("DeepSeek attempt usage must be an object")
            usage.append({"call_id": call_id, "arm": arm, "configuration_id": config,
                          "model_id": job["model_id"], "attempt": i, "status": attempt.get("status", ""),
                          **{name: token.get(name, 0) for name in ("prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")},
                          "cost_upper_cny": attempt.get("cost_upper_cny")})
        # Hash every archived call file, including raw API attempts and errors.
        paths.update(path for path in directory.rglob("*") if path.is_file())
        if result["status"] not in COMPLETE_STATUSES:
            calls.append(call)
            continue
        request_path, response_path = directory / "request.json", directory / "response.json"
        request, response = _read(request_path), _read(response_path)
        if request.get("packet_sha256") != sha256(packet_path):
            raise ValueError(f"packet hash mismatch in {call_id}")
        if result.get("response_sha256") != sha256(response_path):
            raise ValueError(f"response hash mismatch in {call_id}")
        prompt_path = directory / "prompt.txt"
        if not prompt_path.exists():
            raise ValueError(f"missing archived prompt in {call_id}")
        if request.get("prompt_sha256") and request["prompt_sha256"] != sha256(prompt_path):
            raise ValueError(f"prompt hash mismatch in {call_id}")
        if request.get("schema_sha256") and job.get("schema_sha256") and request["schema_sha256"] != job["schema_sha256"]:
            raise ValueError(f"request/schedule schema hash mismatch in {call_id}")
        for key in ("arm", "configuration_id", "model_id", "prompt_variant", "batch_id"):
            if key in request and request[key] != job[key]:
                raise ValueError(f"request/schedule {key} mismatch in {call_id}")
        validator(response, packet)
        attempts = result.get("attempts", [])
        if not 1 <= len(attempts) <= 2:
            raise ValueError(f"completed call requires one or two archived attempts in {call_id}")
        raw_attempts = []
        for number, attempt in enumerate(attempts, 1):
            if attempt.get("attempt", number) != number:
                raise ValueError(f"attempt sequence differs in {call_id}")
            raw_path = directory / f"attempt_{number}.response.json"
            if not raw_path.exists() or attempt.get("raw_response_sha256") != sha256(raw_path):
                raise ValueError(f"raw attempt archive missing or hash mismatch in {call_id}")
            raw_attempts.append(_read(raw_path))
        fallback = result["status"] == "unresolvable_after_two_invalid_outputs"
        if not fallback and (result.get("code_generated_abstention") is True or result.get("model_decision_available") is False):
            raise ValueError(f"valid result has contradictory abstention flags in {call_id}")
        if fallback:
            if len(attempts) != 2:
                raise ValueError(f"code abstention requires two archived attempts in {call_id}")
            if result.get("model_decision_available") is True:
                raise ValueError(f"code abstention claims a model decision in {call_id}")
            for case in response["cases"]:
                if case["identity"] not in ("unresolvable", "not_applicable") or case["cross_report_recurrence"] not in ("unresolvable", "not_applicable"):
                    raise ValueError(f"code abstention contains a substantive case decision in {call_id}")
                for record in case["records"]:
                    if record["cited_year"] is not None or record["outlet"] is not None or record["source_type"] != "unresolvable" or record["contains_multiple_citations"] != "unresolvable" or record["evidence_quote"]:
                        raise ValueError(f"code abstention contains a substantive record decision in {call_id}")
        else:
            try:
                parsed_raw = json.loads(raw_attempts[-1]["choices"][0]["message"]["content"])
            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
                raise ValueError(f"valid raw API response has no parsed model JSON in {call_id}") from error
            if parsed_raw != response:
                raise ValueError(f"parsed response differs from archived raw model content in {call_id}")
        call["model_decision_available"] = not fallback
        for case in response["cases"]:
            decisions[(arm, config, case["case_id"])] = {
                "response": case, "available": not fallback,
                "status": result["status"], "call_id": call_id}
        calls.append(call)
    # Verify all scheduled arms have every configuration exactly once per case.
    if "arms" in schedule and set(schedule["arms"]) != set(cases):
        raise ValueError("scheduled arm list differs from packet coverage")
    for arm, arm_cases in cases.items():
        if schedule.get("case_count", len(arm_cases)) != len(arm_cases):
            raise ValueError("scheduled distinct case count differs from packet coverage")
        record_count = sum(len(case["records"]) for case in arm_cases.values())
        if schedule.get("record_count", record_count) != record_count:
            raise ValueError("scheduled distinct record count differs from packet coverage")
        for cid in arm_cases:
            if any((arm, config, cid) not in coverage for config in CONFIGURATIONS):
                raise ValueError("schedule omits a configuration for a case")
    if set(cases) == {"blind", "metadata"}:
        if set(cases["blind"]) != set(cases["metadata"]):
            raise ValueError("arm case sets differ")
        for cid, case in cases["blind"].items():
            other = {k: v for k, v in cases["metadata"][cid].items() if k != "pipeline_metadata"}
            if case != other:
                raise ValueError("blind and metadata raw packets differ")
    return {"root": root, "run_name": run_name, "schedule": schedule, "cases": cases,
            "decisions": decisions, "calls": calls, "usage": usage, "input_paths": paths}


def _decision(data, arm, config, cid):
    item = data["decisions"].get((arm, config, cid))
    return item if item and item["available"] else None


def summarise_cases(data):
    rows = []
    for arm, cases in sorted(data["cases"].items()):
        for cid, case in sorted(cases.items()):
            row = {"arm": arm, "case_id": cid, "task": case["task"]}
            for config in CONFIGURATIONS:
                item = data["decisions"].get((arm, config, cid))
                row["available_" + config] = int(bool(item and item["available"]))
                row["status_" + config] = item["status"] if item else "missing"
            row["available_configurations"] = sum(row["available_" + config] for config in CONFIGURATIONS)
            for field in CASE_FIELDS:
                votes = []
                for config in CONFIGURATIONS:
                    item = _decision(data, arm, config, cid)
                    value = item["response"][field] if item else None
                    row[field + "_" + config] = value
                    votes.append(value)
                row[field + "_consensus"], row[field + "_decision"] = majority(votes)
            rows.append(row)
    return rows


def summarise_records(data):
    rows = []
    for arm, cases in sorted(data["cases"].items()):
        for cid, case in sorted(cases.items()):
            for record in case["records"]:
                rid = record["record_id"]
                row = {"arm": arm, "case_id": cid, "record_id": rid}
                for config in CONFIGURATIONS:
                    item = _decision(data, arm, config, cid)
                    row["available_" + config] = int(item is not None)
                row["available_configurations"] = sum(row["available_" + config] for config in CONFIGURATIONS)
                for field in RECORD_FIELDS:
                    votes = []
                    for config in CONFIGURATIONS:
                        item = _decision(data, arm, config, cid)
                        response_record = next((r for r in item["response"]["records"] if r["record_id"] == rid), None) if item else None
                        value = canon(response_record[field], field) if response_record else None
                        row[field + "_" + config] = value
                        votes.append(value)
                    row[field + "_consensus"], row[field + "_decision"] = majority(votes)
                rows.append(row)
    return rows


def agreement_table(data, case_rows, record_rows):
    rows = []
    for arm in sorted(data["cases"]):
        for field in (*CASE_FIELDS, *RECORD_FIELDS):
            items = [row for row in (case_rows if field in CASE_FIELDS else record_rows) if row["arm"] == arm]
            if field in CASE_FIELDS:
                items = [row for row in items if row["task"] == "identity"]
                if field == "cross_report_recurrence":
                    items = [row for row in items if len({r["report_group"] for r in data["cases"][arm][row["case_id"]]["records"]}) > 1]
            complete = [row for row in items if row["available_configurations"] == 3]
            votes = [[row[field + "_" + config] for config in CONFIGURATIONS] for row in complete]
            rows.append({"arm": arm, "field": field, "scheduled_items": len(items),
                         "complete_configuration_items": len(complete), "incomplete_configuration_items": len(items) - len(complete),
                         "unanimous_items": sum(len(set(vote)) == 1 for vote in votes),
                         "two_of_three_items": sum(collections.Counter(vote).most_common(1)[0][1] == 2 for vote in votes),
                         "no_majority_items": sum(len(set(vote)) == 3 for vote in votes),
                         "fleiss_kappa": fleiss_kappa(votes), "interpretation": INTERPRETATION})
    return rows


def metadata_arm_comparison(case_rows, record_rows):
    """Within-item shifts, separately for each configuration and the consensus."""
    rows = []
    for source, fields in ((case_rows, CASE_FIELDS), (record_rows, RECORD_FIELDS)):
        lookup = {(r["arm"], r["case_id"], r.get("record_id", "")): r for r in source}
        for arm, cid, rid in sorted(lookup):
            if arm != "blind" or ("metadata", cid, rid) not in lookup:
                continue
            blind, meta = lookup[(arm, cid, rid)], lookup[("metadata", cid, rid)]
            for reviewer in (*CONFIGURATIONS, "consensus"):
                for field in fields:
                    if reviewer == "consensus":
                        b_available = blind[field + "_decision"] != "no_majority"
                        m_available = meta[field + "_decision"] != "no_majority"
                    else:
                        b_available, m_available = blind["available_" + reviewer], meta["available_" + reviewer]
                    b, m = blind[field + "_" + reviewer], meta[field + "_" + reviewer]
                    comparable = bool(b_available and m_available)
                    rows.append({"case_id": cid, "record_id": rid, "reviewer": reviewer, "field": field,
                                 "blind": b, "metadata": m, "blind_available": int(bool(b_available)),
                                 "metadata_available": int(bool(m_available)), "comparable": int(comparable),
                                 "changed": int(b != m) if comparable else None,
                                 "interpretation": "within-item metadata-arm shift; configuration dependence and order effects are not identified"})
    return rows


def diagnostic_tables(data, case_rows, record_rows, case_key=(), control_key=(), sampling_design=None):
    """Optional hidden-key diagnostics, with no human-gold or error-rate claims."""
    key = {row["case_id"]: row for row in case_key}
    controls = {row["case_id"]: row for row in control_key}
    parser, source, control = [], [], []
    for row in record_rows:
        origin = key.get(row["case_id"], {}).get("origin", "")
        if origin.startswith("parser:"):
            record = next(r for r in data["cases"][row["arm"]][row["case_id"]]["records"] if r["record_id"] == row["record_id"])
            panel, decision = row["cited_year_consensus"], row["cited_year_decision"]
            resolved = decision != "no_majority" and panel != "unresolvable"
            rule = archived_rule_year(record["raw_reference"])
            parser.append({"arm": row["arm"], "case_id": row["case_id"], "record_id": row["record_id"],
                           "stratum": origin.split(":", 2)[1], "rule_year": rule, "panel_year": panel,
                           "panel_resolved": int(resolved), "disagreement": int(panel != rule) if resolved else None,
                           "interpretation": "parser-panel disagreement against the frozen regex; not a human-validated error"})
        if origin.startswith("source:"):
            source.append({"arm": row["arm"], "case_id": row["case_id"], "record_id": row["record_id"],
                           "stratum": origin.split(":", 2)[1], "source_type": row["source_type_consensus"],
                           "decision": row["source_type_decision"], "available_configurations": row["available_configurations"]})
    grouped = collections.defaultdict(list)
    for row in case_rows:
        ref = controls.get(row["case_id"])
        if ref:
            group = ref["reference_provenance"].split(":", 1)[0]
            for reviewer in (*CONFIGURATIONS, "consensus"):
                available = row["identity_decision"] != "no_majority" if reviewer == "consensus" else bool(row["available_" + reviewer])
                grouped[(row["arm"], group, reviewer, ref["reference_label"])].append((available, row["identity_" + reviewer]))
    for (arm, group, reviewer, label), values in sorted(grouped.items()):
        available = [value for ok, value in values if ok]
        matching = available.count(label)
        control.append({"arm": arm, "control_set": group, "reviewer": reviewer, "reference_label": label,
                        "scheduled_items": len(values), "available_items": len(available),
                        "unavailable_items": len(values) - len(available), "matching_reference_labels": matching,
                        "agreement_available": matching / len(available) if available else None,
                        "human_reference_labels_archived": False,
                        "interpretation": "agreement conditional on provisional ledger labels" if group == "ledger" else "agreement with raw-DOI-equality heuristic; not sensitivity or specificity"})
    counts = collections.Counter((r["arm"], r["stratum"], r["source_type"], r["decision"]) for r in source)
    source_counts = [{"arm": arm, "stratum": stratum, "source_type": label, "decision": decision, "records": count}
                     for (arm, stratum, label, decision), count in sorted(counts.items())]
    return {"parser_disagreements": parser, "source_field_counts": source_counts,
            "control_reference_agreement": control, "weighted_ai_label_projections": weighted_ai_label_projections(source, sampling_design or {})}


def weighted_ai_label_projections(source_rows, sampling_design):
    """Design-weighted source-type counts only for a fully observed frozen sample.

    The pilot cannot support this projection. Abstention/no-majority rows also
    prevent extrapolation. This estimates AI labels, not true source-type totals.
    """
    strata = sampling_design.get("source_extraction_strata", {})
    output = []
    for arm in sorted({r["arm"] for r in source_rows}):
        records = [r for r in source_rows if r["arm"] == arm]
        complete = bool(strata) and all(d["sampled_occurrences"] > 0 and sum(r["stratum"] == s for r in records) == d["sampled_occurrences"] for s, d in strata.items())
        complete = complete and all(r["stratum"] in strata and r["decision"] != "no_majority" and r["available_configurations"] == 3 for r in records)
        if not complete:
            output.append({"arm": arm, "source_type": "", "projected_occurrences": None,
                           "status": "not_estimated_incomplete_probability_sample", "interpretation": "pilot or incomplete sample cannot support a full-frame weighted projection"})
            continue
        for label in sorted({r["source_type"] for r in records}):
            total = sum(d["population_occurrences"] * sum(r["source_type"] == label and r["stratum"] == s for r in records) / d["sampled_occurrences"] for s, d in strata.items() if d["sampled_occurrences"])
            output.append({"arm": arm, "source_type": label, "projected_occurrences": total,
                           "status": "design_weighted_ai_label_projection", "interpretation": "projection of AI labels in the frozen probability frame; not human-validated source counts or an accuracy correction"})
    return output


def aggregate_run(root, run_name, *, allow_partial=False, validator=None, case_key=None, control_key=None, sampling_design=None, auxiliary_paths=()):
    """Publish complete-run results, or explicitly named partial outputs on opt-in.

    Complete means every scheduled call produced a validated decision or an
    archived code abstention after two invalid outputs. Fallback availability is
    disclosed and excluded from kappa. Missing/failed/budget-stopped calls prevent
    a full summary. A partial report never appears under a complete-summary name.
    """
    data = load_run(root, run_name, validator)
    # Only the frozen local copies created by prepare.py are considered.
    auxiliary_paths = list(auxiliary_paths)
    loaded_keys = {}
    for name, supplied, empty in (("case_key", case_key, []), ("control_key", control_key, []), ("sampling_design", sampling_design, {})):
        key_path = pathlib.Path(root) / "work" / "inputs" / "frames" / (name + ".json")
        if supplied is None and key_path.exists():
            loaded_keys[name] = _read(key_path)
            auxiliary_paths.append(key_path)
        else:
            loaded_keys[name] = supplied if supplied is not None else empty
    case_key, control_key, sampling_design = (loaded_keys[name] for name in ("case_key", "control_key", "sampling_design"))
    incomplete = [call for call in data["calls"] if call["status"] not in COMPLETE_STATUSES]
    if incomplete and not allow_partial:
        raise ValueError(f"refuse complete summary: {len(incomplete)} of {len(data['calls'])} scheduled calls are incomplete")
    out = pathlib.Path(root) / "results" / run_name
    out.mkdir(parents=True, exist_ok=True)
    prefix = "partial_" if incomplete else ""
    if incomplete and (out / "manifest.json").exists():
        raise ValueError("refuse partial publication beside an existing complete manifest")
    case_rows, record_rows = summarise_cases(data), summarise_records(data)
    agreement = agreement_table(data, case_rows, record_rows)
    shifts = metadata_arm_comparison(case_rows, record_rows)
    tables = {"case_consensus": case_rows, "record_consensus": record_rows,
              "agreement": agreement, "metadata_arm_comparison": shifts,
              "call_usage": data["usage"], "call_status": data["calls"]}
    tables.update(diagnostic_tables(data, case_rows, record_rows, case_key, control_key, sampling_design))
    fields = {
        "case_consensus": ["arm", "case_id", "task", *[x for c in CONFIGURATIONS for x in ("available_"+c, "status_"+c)], "available_configurations", *[x for f in CASE_FIELDS for x in (*[f+"_"+c for c in CONFIGURATIONS], f+"_consensus", f+"_decision")]],
        "record_consensus": ["arm", "case_id", "record_id", *["available_"+c for c in CONFIGURATIONS], "available_configurations", *[x for f in RECORD_FIELDS for x in (*[f+"_"+c for c in CONFIGURATIONS], f+"_consensus", f+"_decision")]],
        "agreement": ["arm", "field", "scheduled_items", "complete_configuration_items", "incomplete_configuration_items", "unanimous_items", "two_of_three_items", "no_majority_items", "fleiss_kappa", "interpretation"],
        "metadata_arm_comparison": ["case_id", "record_id", "reviewer", "field", "blind", "metadata", "blind_available", "metadata_available", "comparable", "changed", "interpretation"],
        "call_usage": ["call_id", "arm", "configuration_id", "model_id", "attempt", "status", "prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "cost_upper_cny"],
        "call_status": ["call_id", "arm", "configuration_id", "model_id", "batch_id", "status", "model_decision_available", "case_count", "attempt_count"],
        "parser_disagreements": ["arm", "case_id", "record_id", "stratum", "rule_year", "panel_year", "panel_resolved", "disagreement", "interpretation"],
        "source_field_counts": ["arm", "stratum", "source_type", "decision", "records"],
        "control_reference_agreement": ["arm", "control_set", "reviewer", "reference_label", "scheduled_items", "available_items", "unavailable_items", "matching_reference_labels", "agreement_available", "human_reference_labels_archived", "interpretation"],
        "weighted_ai_label_projections": ["arm", "source_type", "projected_occurrences", "status", "interpretation"],
    }
    output_paths = []
    for name, rows in tables.items():
        target = out / (prefix + name + ".csv")
        _csv(target, rows, fields[name])
        output_paths.append(target)
    usage = data["usage"]
    costs = [row["cost_upper_cny"] for row in usage]
    summary = {"analysis_package": "1.5.0", "sampling_seed": 20261011,
               "run_name": run_name, "status": "partial" if incomplete else "complete",
               "scheduled_calls": len(data["calls"]), "completed_calls": len(data["calls"]) - len(incomplete),
               "model_decision_calls": sum(call["model_decision_available"] for call in data["calls"]),
               "code_abstention_calls": sum(call["status"] == "unresolvable_after_two_invalid_outputs" for call in data["calls"]),
               "call_status_counts": dict(collections.Counter(call["status"] for call in data["calls"])),
               "distinct_cases": len({cid for cases in data["cases"].values() for cid in cases}),
               "case_count_by_arm": {arm: len(cases) for arm, cases in data["cases"].items()},
               "record_count_by_arm": {arm: sum(len(case["records"]) for case in cases.values()) for arm, cases in data["cases"].items()},
               "reviewer_configurations": list(CONFIGURATIONS), "configured_model_ids": sorted(set(MODEL_IDS.values())),
               "configuration_count": 3, "configured_model_count": 2, "independent_models": False,
               "tokens": {name: sum(row[name] for row in usage) for name in ("prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")},
               "cost_upper_cny_recorded": str(sum((Decimal(str(cost)) for cost in costs if cost is not None), Decimal(0))),
               "attempts_missing_cost": sum(cost is None for cost in costs),
               "metadata_arm_shifts": {field: {"comparable": sum(row["comparable"] for row in shifts if row["reviewer"] == "consensus" and row["field"] == field), "changed": sum(row["changed"] or 0 for row in shifts if row["reviewer"] == "consensus" and row["field"] == field)} for field in (*CASE_FIELDS, *RECORD_FIELDS)},
               "weighted_ai_label_projection_status": sorted({row["status"] for row in tables["weighted_ai_label_projections"]}) or ["not_estimated_no_source_sample_items"],
               "interpretation": INTERPRETATION, "no_human_adjudication": True,
               "control_interpretation": "agreement conditional on provisional reference labels; no sensitivity, specificity, or accuracy estimate",
               "incomplete_call_ids": [call["call_id"] for call in incomplete]}
    summary_path = out / (prefix + "summary.json")
    _dump(summary_path, summary)
    output_paths.append(summary_path)
    # Include explicit auxiliary manifests and keys only; never scan credentials.
    input_manifest = pathlib.Path(root) / "work" / "input_manifest.json"
    schema_path = pathlib.Path(root) / "work" / "inputs" / "response_schema.json"
    if not schema_path.exists():
        schema_path = pathlib.Path(root) / "source_panel" / "response_schema.json"
    source_manifest = pathlib.Path(root) / "work" / "inputs" / "preparation_manifest.json"
    for path in (input_manifest, schema_path, source_manifest, pathlib.Path(root) / "validate.py"):
        if path.exists():
            auxiliary_paths.append(path)
    data["input_paths"].update(pathlib.Path(p).resolve() for p in auxiliary_paths)
    source_path = pathlib.Path(__file__).resolve()
    data["input_paths"].add(source_path)
    manifest_path = out / (prefix + "manifest.json")
    manifest = {"analysis_package": "1.5.0", "sampling_seed": 20261011,
                "status": summary["status"], "all_scheduled_calls_complete": not incomplete,
                "code_provenance": "new independent offline implementation; frozen R3 regex behavior retained; no imported old run paths",
                "input_sha256": {str(path.relative_to(pathlib.Path(root).resolve())) if path.is_relative_to(pathlib.Path(root).resolve()) else "aggregator_source/" + path.name: sha256(path) for path in sorted(data["input_paths"])},
                "output_sha256": {path.name: sha256(path) for path in output_paths},
                "no_human_adjudication": True, "agreement_is_not_accuracy": True,
                "reviewer_unit": "configuration", "configuration_count": 3, "configured_model_count": 2}
    _dump(manifest_path, manifest)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parent)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    print(json.dumps(aggregate_run(args.root, args.run_name, allow_partial=args.allow_partial)))


if __name__ == "__main__":
    main()
