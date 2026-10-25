"""Reconcile all paid attempts, including preserved connection interruptions.

The ledger records published-rate upper estimates, not a provider invoice.
This audit never contacts the service and never loads a credential.
"""
from __future__ import annotations
import argparse
import csv
from decimal import Decimal
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reconcile(work, destination):
    ledger_path = work / "budget_20261025.json"
    ledger = json.loads(ledger_path.read_text())
    entries = ledger["entries"]
    if any(e["state"] == "reserved" for e in entries.values()):
        raise ValueError("Unfinished reservations remain; spending audit requires stopped runners")
    if (work / ".active_runner.lock").exists():
        raise ValueError("A runner is active")
    evidence = {}
    for run in sorted((work / "runs").iterdir()):
        if not run.is_dir():
            continue
        for branch in ("calls", "interrupted_calls"):
            for result_path in sorted((run / branch).rglob("result.json")):
                directory = result_path.parent
                request = json.loads((directory / "request.json").read_text())
                result = json.loads(result_path.read_text())
                for attempt in result.get("attempts", []):
                    if not attempt.get("request_sent"):
                        continue
                    number = attempt["attempt"]
                    aid = attempt.get("ledger_attempt_id", run.name + "/" + request["call_id"] + "/" + str(number))
                    if aid not in entries:
                        raise ValueError("Archived paid attempt is absent from ledger: " + aid)
                    for suffix, field in (("request.json", "raw_request_sha256"), ("response.json", "raw_response_sha256")):
                        if field in attempt and sha(directory / ("attempt_%s.%s" % (number, suffix))) != attempt[field]:
                            raise ValueError("Paid attempt bytes changed: " + aid)
                    if attempt.get("cost_upper_cny") is not None and Decimal(attempt["cost_upper_cny"]) != Decimal(entries[aid]["charged_upper_cny"]):
                        raise ValueError("Attempt and ledger settled cost differ: " + aid)
                    signature = (attempt.get("raw_request_sha256"), attempt.get("raw_response_sha256"))
                    old = evidence.setdefault(aid, {"signature": signature, "locations": []})
                    if old["signature"] != signature:
                        raise ValueError("Conflicting evidence for a repeated ledger ID: " + aid)
                    old["locations"].append(result_path.relative_to(work).as_posix())
    if set(evidence) != set(entries):
        raise ValueError("Ledger attempts lack archived request evidence: " + str(sorted(set(entries) - set(evidence))))
    rows = []
    total = Decimal(0)
    for aid, entry in sorted(entries.items()):
        amount = Decimal(entry["charged_upper_cny"])
        total += amount
        rows.append({"ledger_attempt_id": aid, "state": entry["state"], "model": entry["model"],
                     "charged_upper_cny": str(amount), "settlement_factor": entry.get("settlement_factor", "1"),
                     "archive_locations": "|".join(evidence[aid]["locations"]),
                     "raw_request_sha256": evidence[aid]["signature"][0],
                     "raw_response_sha256": evidence[aid]["signature"][1] or ""})
    limit = Decimal(ledger["authorized_limit_cny"])
    if total > limit or limit > Decimal(100):
        raise ValueError("Recorded upper cost exceeds authorized limit")
    destination.mkdir(parents=True, exist_ok=True)
    with (destination / "paid_attempts.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else ["ledger_attempt_id"])
        writer.writeheader(); writer.writerows(rows)
    report = {"status": "passed", "ledger_sha256": sha(ledger_path), "unique_paid_attempts": len(rows),
              "unknown_charge_attempts": sum(r["state"] == "unknown_charge" for r in rows),
              "charged_upper_cny": str(total), "authorized_limit_cny": str(limit),
              "remaining_upper_cny": str(limit-total), "invoice_claimed": False,
              "accounting": "Uncertain requests retain peak reservations; interrupted copies are deduplicated by ledger attempt ID",
              "paid_attempts_sha256": sha(destination / "paid_attempts.csv")}
    (destination / "spending_audit.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, default=HERE / "work")
    parser.add_argument("--output-dir", type=Path, default=HERE / "results/spending")
    args = parser.parse_args()
    print(json.dumps(reconcile(args.work_dir.resolve(), args.output_dir.resolve()), indent=2))
