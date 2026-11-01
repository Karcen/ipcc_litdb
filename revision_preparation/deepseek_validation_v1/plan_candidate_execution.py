"""Archive peak and conditional off-peak planning scenarios; send no requests."""
from __future__ import annotations
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from forecast import create_forecast

HERE = Path(__file__).resolve().parent


def plan(root, pilot, candidate_schedule):
    root = root.resolve()
    output = root / "results/candidate_execution_plan_v2"
    output.mkdir(parents=True, exist_ok=True)
    budget = root / "work/budget_20261025.json"
    snapshot = output / "budget_at_forecast.json"
    if snapshot.exists():
        raise ValueError("Execution-plan snapshot exists; refuse to replace it")
    snapshot.write_bytes(budget.read_bytes())
    path = output / "pilot_cost_forecast.json"
    result = create_forecast(root, pilot, ledger_path=snapshot,
                             candidate_schedule=candidate_schedule, workers=8, output_path=path)
    if not result.get("pilot_complete") or not result.get("pilot_qc_passed"):
        raise ValueError("Pilot gate failed")
    # This is a planning scenario, not a prepaid discount or an invoice claim.
    forecast = result["candidate_extra_forecast"]
    peak = Decimal(forecast["planning_cost_upper_cny_estimate"])
    ledger = json.loads(snapshot.read_text())
    charged = sum(Decimal(x["charged_upper_cny"] or 0) for x in ledger["entries"].values())
    reserved = sum(Decimal(x["reservation_upper_cny"]) for x in ledger["entries"].values() if x["state"] == "reserved")
    remaining = Decimal(ledger["authorized_limit_cny"]) - charged - reserved
    record = {"status": "planning_scenarios_only", "candidate_schedule_sha256": hashlib.sha256(candidate_schedule.read_bytes()).hexdigest(),
              "budget_snapshot_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
              "peak_planning_upper_cny": str(peak), "conditional_offpeak_planning_upper_cny": str(peak * Decimal('0.5')),
              "remaining_upper_cny": str(remaining), "offpeak_scenario_fits": peak * Decimal('0.5') <= remaining,
              "peak_reservation_before_every_request": True,
              "settlement_condition": "A 0.5 factor is applied only to actual responses with verified off-peak request/response timing. Otherwise peak cost applies. The CNY100 ledger cap always governs.",
              "scenario_is_not_a_cost_guarantee": True}
    (output / "execution_plan.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=HERE)
    parser.add_argument('--pilot-run-name', default='deepseek_pilot_20261025_v3')
    parser.add_argument('--schedule', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(plan(args.root, args.pilot_run_name, args.schedule.resolve()), indent=2))
