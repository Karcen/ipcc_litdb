#!/usr/bin/env python3
"""Offline pilot cost and runtime planning from verified response archives.

No requests are sent. Actual usage is charged at the archived peak-rate upper
bound; it is not a provider bill. A 1.5 planning multiplier is a chosen margin,
not a confidence interval or a guarantee that the whole frame fits the budget.
"""
from __future__ import annotations

import argparse
import collections
import datetime
from decimal import Decimal
import json
from pathlib import Path

from aggregate import COMPLETE_STATUSES, MODEL_IDS, load_run, sha256
from budget import usage_upper

TOKENS = ("prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
PLANNING_MULTIPLIER = Decimal("1.5")
QC_THRESHOLD = Decimal("0.95")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def money(value):
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError("Accounting amounts must be finite and nonnegative")
    return result


def ledger_totals(ledger):
    """Read-only ledger arithmetic; unknown charges remain charged upper bounds."""
    limit = money(ledger["authorized_limit_cny"])
    if not 0 < limit <= 100:
        raise ValueError("Ledger limit exceeds the authorized CNY100 scope")
    charged, reserved = Decimal(0), Decimal(0)
    states = collections.Counter()
    for entry in ledger["entries"].values():
        state = entry["state"]
        if state not in ("reserved", "settled", "unknown_charge"):
            raise ValueError("Unknown ledger state")
        states[state] += 1
        if entry.get("charged_upper_cny") is not None:
            charged += money(entry["charged_upper_cny"])
        if state == "reserved":
            reserved += money(entry["reservation_upper_cny"])
    return {"authorized_limit_cny": str(limit), "charged_upper_cny": str(charged),
            "reserved_upper_cny": str(reserved), "remaining_upper_cny": str(limit - charged - reserved),
            "entry_state_counts": dict(states), "accounting": "peak-rate upper accounting, not a provider invoice"}


def _model(job):
    return job.get("model_id") or MODEL_IDS[job["configuration_id"]]


def schedule_exposures(schedule):
    """Count initial model calls and record reviews in an actual prepared scope."""
    if schedule.get("stage") not in ("full", "candidates") or set(schedule["arms"]) != {"blind"}:
        raise ValueError("Forecast target must be a prepared blind full or candidate schedule")
    rows = {}
    seen = set()
    for job in schedule["jobs"]:
        identifier = job.get("call_id", job.get("job_id"))
        if identifier in seen:
            raise ValueError("Duplicate target schedule job")
        seen.add(identifier)
        if job["arm"] != "blind":
            raise ValueError("Nonblind target job")
        model = _model(job)
        row = rows.setdefault(model, {"initial_calls": 0, "record_reviews": 0})
        row["initial_calls"] += 1
        row["record_reviews"] += job["record_count"]
    if schedule["initial_call_count"] != len(schedule["jobs"]):
        raise ValueError("Target schedule initial call count differs")
    return rows


def forecast_scope(schedule, rates, remaining, *, workers=3):
    exposures = schedule_exposures(schedule)
    rows = []
    total_cost, total_seconds = Decimal(0), Decimal(0)
    for model, exposure in sorted(exposures.items()):
        rate = rates[model]
        records = Decimal(exposure["record_reviews"])
        cost = rate["cost_upper_cny_per_record_review"] * records
        seconds = rate["elapsed_seconds_per_record_review"] * records
        total_cost += cost
        total_seconds += seconds
        rows.append({"model_id": model, **exposure,
                     "base_cost_upper_cny_estimate": str(cost),
                     "planning_cost_upper_cny_estimate": str(cost * PLANNING_MULTIPLIER),
                     "base_token_estimates": {name: str(rate[name + "_per_record_review"] * records) for name in TOKENS},
                     "base_serial_worker_seconds_estimate": str(seconds),
                     "attempts_estimate_from_initial_call_retry_rate": str(Decimal(exposure["initial_calls"]) * rate["attempts_per_initial_call"])})
    planned = total_cost * PLANNING_MULTIPLIER
    return {"scope": schedule.get("scope", schedule["stage"]),
            "distinct_cases": schedule["case_count"], "distinct_records": schedule["record_count"],
            "initial_call_count": schedule["initial_call_count"], "arms": ["blind"],
            "models": rows, "base_cost_upper_cny_estimate": str(total_cost),
            "planning_multiplier": str(PLANNING_MULTIPLIER), "planning_cost_upper_cny_estimate": str(planned),
            "fits_remaining_upper_budget_at_planning_multiplier": planned <= remaining,
            "base_serial_worker_seconds_estimate": str(total_seconds),
            "planning_serial_worker_seconds_estimate": str(total_seconds * PLANNING_MULTIPLIER),
            "idealized_parallel_workers": workers,
            "idealized_planning_wall_seconds_estimate": str(total_seconds * PLANNING_MULTIPLIER / workers),
            "runtime_interpretation": "elapsed job time scaled per record review including retries; ideal division by workers omits queueing, provider latency changes, and model imbalance",
            "status": "planning_only_no_calls_started_by_forecaster"}


def _empty_rate():
    return {"initial_calls": 0, "record_reviews": 0, "attempts": 0,
            "cost_upper_cny": Decimal(0), "elapsed_seconds": Decimal(0),
            **{name: 0 for name in TOKENS}}


def _rates(raw):
    result = {}
    for model, item in raw.items():
        records = item["record_reviews"]
        if records <= 0 or item["initial_calls"] <= 0:
            raise ValueError("No complete blind pilot exposure for a model")
        result[model] = {"cost_upper_cny_per_record_review": item["cost_upper_cny"] / records,
                         "elapsed_seconds_per_record_review": item["elapsed_seconds"] / records,
                         "attempts_per_initial_call": Decimal(item["attempts"]) / item["initial_calls"],
                         **{name + "_per_record_review": Decimal(item[name]) / records for name in TOKENS}}
    return result


def _public(raw):
    return {model: {key: str(value) if isinstance(value, Decimal) else value for key, value in row.items()} for model, row in raw.items()}


def create_forecast(root, run_name, *, ledger_path=None, full_schedule=None, candidate_schedule=None,
                    validator=None, workers=3, output_path=None):
    """Create a reviewable record. QC failure never authorizes full execution."""
    if type(workers) is not int or workers < 1:
        raise ValueError("workers must be a positive integer")
    root = Path(root).resolve()
    work = root / "work"
    run = work / "runs" / run_name
    ledger_path = Path(ledger_path) if ledger_path else work / "budget_20261025.json"
    # Hash and parse the same ledger bytes, even if a separate runner updates it.
    ledger_bytes = ledger_path.read_bytes()
    import hashlib
    ledger_hash = hashlib.sha256(ledger_bytes).hexdigest()
    ledger = json.loads(ledger_bytes)
    totals = ledger_totals(ledger)
    errors = []
    data = None
    try:
        data = load_run(root, run_name, validator)
    except (ValueError, KeyError, TypeError, OSError) as error:
        errors.append("archive_validation: " + str(error))
    schedule = read(run / "schedule.json")
    if schedule.get("stage") != "pilot":
        raise ValueError("Cost forecast requires a pilot schedule")
    paths = {run / "schedule.json", ledger_path, Path(__file__).resolve()}
    results = []
    all_usage, blind_usage = {}, {}
    earliest, latest = [], []
    for job in schedule["jobs"]:
        identifier = job.get("call_id", job.get("job_id"))
        directory = run / "calls" / identifier
        result_path = directory / "result.json"
        if not result_path.exists():
            results.append({"status": "missing"})
            continue
        result = read(result_path)
        results.append(result)
        paths.add(result_path)
        model = _model(job)
        rows = [all_usage.setdefault(model, _empty_rate())]
        if job["arm"] == "blind":
            rows.append(blind_usage.setdefault(model, _empty_rate()))
        for row in rows:
            row["initial_calls"] += 1
            row["record_reviews"] += job["record_count"]
            row["elapsed_seconds"] += money(result["elapsed_seconds"])
        latest.append(result["utc_finished"])
        for number, attempt in enumerate(result["attempts"], 1):
            usage = attempt.get("usage") or {}
            try:
                cost = money(attempt["cost_upper_cny"])
                if cost != usage_upper(model, usage):
                    errors.append("usage_cost_mismatch: " + identifier)
                entry = ledger["entries"][run_name + "/" + identifier + "/" + str(number)]
                if entry["state"] != "settled" or entry.get("reservation_exceeded") or entry["model"] != model or money(entry["charged_upper_cny"]) != cost or entry["usage"] != usage:
                    errors.append("ledger_provenance_mismatch: " + identifier)
                request_path = directory / f"attempt_{number}.request.json"
                if sha256(request_path) != attempt["raw_request_sha256"]:
                    errors.append("raw_request_hash_mismatch: " + identifier)
                paths.add(request_path)
                if attempt.get("http_status") != 200 or attempt.get("status") not in ("valid", "parse_or_schema_failure"):
                    errors.append("transport_or_execution_error: " + identifier)
                if attempt.get("returned_model") != model:
                    errors.append("returned_model_mismatch: " + identifier)
            except (KeyError, ValueError, TypeError, OSError) as error:
                errors.append("attempt_accounting: " + identifier + ": " + str(error))
                continue
            earliest.append(attempt["requested_utc"])
            for row in rows:
                row["attempts"] += 1
                row["cost_upper_cny"] += cost
                for name in TOKENS:
                    row[name] += usage.get(name, 0)
    complete = len(results) == len(schedule["jobs"]) and all(r["status"] in COMPLETE_STATUSES for r in results)
    valid = sum(r["status"] == "valid" and r.get("model_decision_available") is True and not r.get("code_generated_abstention") for r in results)
    fraction = Decimal(valid) / len(schedule["jobs"])
    software_path = run / "software.json"
    if software_path.exists():
        software = read(software_path)
        paths.add(software_path)
        if software.get("schedule_sha256") != sha256(run / "schedule.json"):
            errors.append("software_schedule_hash_mismatch")
    else:
        errors.append("software_provenance_missing")
    qc = complete and fraction >= QC_THRESHOLD and not errors
    record = {"record_type": "pilot_cost_forecast", "analysis_package": "1.5.0", "sampling_seed": 20261011,
              "pilot_run_name": run_name, "pilot_complete": complete, "pilot_qc_passed": qc,
              "valid_model_decision_jobs": valid, "scheduled_jobs": len(schedule["jobs"]),
              "valid_job_fraction": str(fraction), "required_valid_job_fraction": str(QC_THRESHOLD),
              "code_abstention_jobs": sum(r["status"] == "unresolvable_after_two_invalid_outputs" for r in results),
              "transport_or_provenance_errors": sorted(set(errors)), "use_for_inference": qc,
              "actual_usage_all_arms_by_model": _public(all_usage),
              "actual_usage_blind_arm_by_model": _public(blind_usage),
              "actual_pilot_cost_upper_cny": str(sum((row["cost_upper_cny"] for row in all_usage.values()), Decimal(0))),
              "shared_budget_snapshot": totals, "budget_ledger_sha256": ledger_hash,
              "planning_multiplier": str(PLANNING_MULTIPLIER),
              "cost_interpretation": "actual token usage valued at archived peak-rate upper estimates; not a provider invoice",
              "forecast_interpretation": "model-specific blind pilot cost and tokens per record review, including schema retries; 1.5 is a chosen planning margin, not a confidence interval or guarantee",
              "scope_limitations": "fixed pilot is not a probability sample of runtime or cost; raw string length, record mix, cache behavior, retry frequency and batching may differ in full/candidate scopes",
              "automatic_calls_authorized_by_this_record": False}
    if earliest and latest:
        record["observed_pilot_wall_seconds"] = (datetime.datetime.fromisoformat(max(latest)) - datetime.datetime.fromisoformat(min(earliest))).total_seconds()
    if complete and not errors:
        rates = _rates(blind_usage)
        record["blind_arm_rates"] = _public(rates)
        scopes = []
        for name, supplied in (("primary_full_forecast", full_schedule), ("candidate_extra_forecast", candidate_schedule)):
            path = Path(supplied) if supplied else work / "schedules" / ("full.json" if name.startswith("primary") else "candidates.json")
            target = read(path)
            paths.add(path)
            record[name] = forecast_scope(target, rates, Decimal(totals["remaining_upper_cny"]), workers=workers)
            scopes.append(record[name])
        combined = sum((Decimal(scope["planning_cost_upper_cny_estimate"]) for scope in scopes), Decimal(0))
        record["full_plus_candidate_planning_cost_upper_cny_estimate"] = str(combined)
        record["full_plus_candidate_fits_remaining_upper_budget"] = combined <= Decimal(totals["remaining_upper_cny"])
        record["primary_full_planning_budget_gate_passed"] = qc and record["primary_full_forecast"]["fits_remaining_upper_budget_at_planning_multiplier"]
    else:
        record["forecasts_withheld_reason"] = "pilot incomplete or archive/accounting provenance failed"
        record["primary_full_planning_budget_gate_passed"] = False
    if data:
        paths.update(data["input_paths"])
    record["input_sha256"] = {str(path.relative_to(root)) if path.is_relative_to(root) else "external/" + path.name: ledger_hash if path == ledger_path else sha256(path) for path in sorted(paths)}
    if output_path is None:
        output_path = root / "results" / run_name / "pilot_cost_forecast.json"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return record


def ensure_target_schedules(root):
    """Create missing planning schedules offline; never modify pilot inputs."""
    from prepare import prepare_stage
    work = Path(root) / "work"
    for stage in ("full", "candidates"):
        if not (work / "schedules" / (stage + ".json")).exists():
            prepare_stage(stage, work_dir=work)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    ensure_target_schedules(args.root)
    record = create_forecast(args.root, args.run_name, workers=args.workers)
    print(json.dumps({key: record[key] for key in ("pilot_complete", "pilot_qc_passed", "actual_pilot_cost_upper_cny", "primary_full_planning_budget_gate_passed")}))


if __name__ == "__main__":
    main()
