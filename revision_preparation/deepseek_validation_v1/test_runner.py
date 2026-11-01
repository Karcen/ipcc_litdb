"""Mock-only financial and archive tests; no real credentials or network calls."""
from __future__ import annotations

import concurrent.futures
import contextlib
import copy
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import sys
from unittest.mock import patch
import urllib.error

import aggregate
import budget
import prepare
import runner
from validate import validate_response

FAKE_KEY = "unit-test-placeholder-not-a-credential"
SOURCE = Path(os.environ.get("IPCC_VALIDATION_SOURCE_DIR", str(prepare.SOURCE)))


class FakeResponse:
    status = 200
    headers = {"content-type": "application/json", "x-request-id": "mock-request"}

    def __init__(self, envelope):
        self.body = json.dumps(envelope).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "budget.json"

    def tearDown(self):
        self.temporary.cleanup()

    def test_peak_cache_arithmetic_and_conservative_missing_cache(self):
        usage = {"prompt_tokens": 100, "completion_tokens": 10, "prompt_cache_hit_tokens": 40, "prompt_cache_miss_tokens": 60}
        self.assertEqual(budget.usage_upper("deepseek-flash", usage), Decimal("0.0002016"))
        self.assertEqual(budget.usage_upper("deepseek-v4-pro", usage), Decimal("0.000822"))
        self.assertEqual(budget.usage_upper("deepseek-flash", {"prompt_tokens": 100, "completion_tokens": 10}), Decimal("0.00028"))
        self.assertEqual(budget.usage_upper("deepseek-flash", {"prompt_tokens": 100, "completion_tokens": 10, "prompt_cache_hit_tokens": 40}), Decimal("0.00028"))
        for invalid in ({**usage, "prompt_cache_miss_tokens": 59}, {**usage, "completion_tokens": True}, {**usage, "prompt_tokens": -1}):
            with self.assertRaises(ValueError):
                budget.usage_upper("deepseek-flash", invalid)

    def test_budget_is_finite_and_at_most_100(self):
        for value in (0, -1, 101, "Infinity", "NaN"):
            with self.assertRaises(ValueError):
                budget.Ledger(self.path, value)

    def test_reservations_reject_negative_fractional_and_boolean_tokens(self):
        ledger = budget.Ledger(self.path, 100)
        for tokens in (-1, 1.5, True):
            with self.assertRaises(ValueError):
                ledger.reserve("invalid", "deepseek-flash", tokens, 1)
        self.assertFalse(ledger.data["entries"])

    def test_concurrent_reservations_never_cross_authorized_limit(self):
        ledger = budget.Ledger(self.path, 100)

        def reserve(index):
            try:
                ledger.reserve(str(index), "deepseek-flash", 5000000, 0)
                return True
            except budget.BudgetStop:
                return False

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            accepted = list(pool.map(reserve, range(24)))
        self.assertEqual(sum(accepted), 10)
        self.assertEqual(Decimal(ledger.totals()["reserved_upper_cny"]), 100)
        self.assertEqual(Decimal(ledger.totals()["remaining_upper_cny"]), 0)

    def test_unknown_charges_persist_and_cannot_be_resent(self):
        ledger = budget.Ledger(self.path, 100)
        ledger.reserve("attempt", "deepseek-flash", 10000000, 0)
        ledger.uncertain("attempt", "TimeoutError")
        restored = budget.Ledger(self.path, 100)
        self.assertEqual(restored.data["entries"]["attempt"]["state"], "unknown_charge")
        self.assertEqual(Decimal(restored.totals()["charged_upper_cny"]), 20)
        self.assertEqual(Decimal(restored.totals()["remaining_upper_cny"]), 80)
        with self.assertRaises(budget.BudgetStop):
            restored.reserve("attempt", "deepseek-flash", 1, 1)
        with self.assertRaises(ValueError):
            budget.Ledger(self.path, 99)

    def test_settlement_releases_unused_reservation_and_excess_stops_future_requests(self):
        ledger = budget.Ledger(self.path, 100)
        ledger.reserve("valid", "deepseek-flash", 1000, 1000)
        ledger.settle("valid", {"prompt_tokens": 100, "completion_tokens": 10})
        self.assertEqual(Decimal(ledger.totals()["charged_upper_cny"]), Decimal("0.00028"))
        self.assertEqual(Decimal(ledger.totals()["reserved_upper_cny"]), 0)
        ledger.reserve("excess", "deepseek-flash", 1, 0)
        with self.assertRaises(budget.BudgetStop):
            ledger.settle("excess", {"prompt_tokens": 100, "completion_tokens": 10})
        restored = budget.Ledger(self.path, 100)
        with self.assertRaises(budget.BudgetStop):
            restored.reserve("later", "deepseek-flash", 1, 0)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.work = self.root / "work"
        self.run = self.work / "runs/mock"
        self.work.mkdir()
        self.schema = json.loads((SOURCE / "response_schema.json").read_text())
        self.packet = {"cases": [{"case_id": "C1", "task": "identity", "records": [
            {"record_id": "R1", "report_group": "earlier", "raw_reference": "Smith, 2001. Example study. Nature."},
            {"record_id": "R2", "report_group": "later", "raw_reference": "Smith, 2001a. Example study. Nature."}], "frozen_evidence": []}]}
        self.response = {"cases": [{"case_id": "C1", "identity": "same_study", "cross_report_recurrence": "recurrent", "records": [
            {"record_id": r["record_id"], "cited_year": 2001, "outlet": "Nature", "source_type": "journal", "contains_multiple_citations": "no", "evidence_quote": r["raw_reference"]}
            for r in self.packet["cases"][0]["records"]], "reason": "The supplied citations agree."}]}
        prepare.write_frozen(self.work / "packet.json", prepare.encoded(self.packet))
        prepare.write_frozen(self.work / "schema.json", prepare.encoded(self.schema))
        prepare.write_frozen(self.work / "prompt.txt", b"Review supplied references only.\n")
        self.job = {"job_id": "pilot_batch_0001_blind_flash_A", "configuration_id": "flash_A", "model_tier": "Flash", "prompt_variant": "A", "arm": "blind", "batch_id": "batch_0001",
                    "packet_path": "packet.json", "packet_sha256": prepare.digest(self.work / "packet.json"),
                    "schema_path": "schema.json", "schema_sha256": prepare.digest(self.work / "schema.json"),
                    "prompt_path": "prompt.txt", "prompt_sha256": prepare.digest(self.work / "prompt.txt"), "case_count": 1, "record_count": 2}
        self.ledger = budget.Ledger(self.work / "budget.json", 100)
        self.halt = threading.Event()

    def tearDown(self):
        self.temporary.cleanup()

    def envelope(self, response=None):
        return {"id": "mock-response", "model": "deepseek-flash", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(self.response if response is None else response)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "prompt_cache_hit_tokens": 20, "prompt_cache_miss_tokens": 80}}

    def call(self, job=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return runner.call_job(self.job if job is None else job, self.work, self.run, self.ledger, FAKE_KEY, self.halt, 1)

    def call_dir(self):
        return self.run / "calls" / self.job["job_id"]

    def test_mock_call_records_aggregate_contract_and_hashes(self):
        with patch.object(runner.urllib.request, "urlopen", return_value=FakeResponse(self.envelope())) as opened:
            result = self.call()
        self.assertEqual(result["status"], "valid")
        self.assertEqual(opened.call_count, 1)
        directory = self.call_dir()
        request = json.loads((directory / "request.json").read_text())
        self.assertEqual(request["job_id"], self.job["job_id"])
        self.assertEqual(request["call_id"], self.job["job_id"])
        self.assertEqual(request["packet_sha256"], self.job["packet_sha256"])
        self.assertEqual(request["prompt_sha256"], prepare.digest(directory / "prompt.txt"))
        self.assertEqual(result["attempts"][0]["raw_response_sha256"], prepare.digest(directory / "attempt_1.response.json"))
        self.assertEqual(result["attempts"][0]["raw_request_sha256"], prepare.digest(directory / "attempt_1.request.json"))
        self.assertNotIn(FAKE_KEY, (directory / "attempt_1.request.json").read_text())
        validate_response(json.loads((directory / "response.json").read_text()), self.packet, self.schema)
        jobs = [{**self.job, "call_id": self.job["job_id"], "model_id": "deepseek-flash"}]
        for configuration, tier, variant in (("flash_B", "Flash", "B"), ("pro_C", "Pro", "C")):
            job = {**self.job, "job_id": "pilot_batch_0001_blind_" + configuration,
                   "configuration_id": configuration, "model_tier": tier, "prompt_variant": variant}
            envelope = self.envelope()
            envelope["model"] = runner.MODELS[tier]
            with patch.object(runner.urllib.request, "urlopen", return_value=FakeResponse(envelope)):
                self.call(job)
            jobs.append({**job, "call_id": job["job_id"], "model_id": runner.MODELS[tier]})
        budget.write_json(self.run / "schedule.json", {"stage": "pilot", "jobs": jobs, "initial_call_count": len(jobs)})
        loaded = aggregate.load_run(self.root, "mock", validator=lambda response, packet: validate_response(response, packet, self.schema))
        self.assertEqual([call["status"] for call in loaded["calls"]], ["valid"] * 3)

    def test_one_schema_retry_then_valid(self):
        with patch.object(runner.urllib.request, "urlopen", side_effect=[FakeResponse(self.envelope({"cases": []})), FakeResponse(self.envelope())]) as opened:
            result = self.call()
        self.assertEqual(result["status"], "valid")
        self.assertEqual(opened.call_count, 2)
        self.assertEqual(len(self.ledger.data["entries"]), 2)

    def test_two_schema_failures_produce_unavailable_abstentions(self):
        with patch.object(runner.urllib.request, "urlopen", side_effect=[FakeResponse(self.envelope({"cases": []})), FakeResponse(self.envelope({"cases": []}))]) as opened:
            result = self.call()
        self.assertEqual(opened.call_count, 2)
        self.assertEqual(result["status"], "unresolvable_after_two_invalid_outputs")
        self.assertTrue(result["code_generated_abstention"])
        self.assertFalse(result["model_decision_available"])

    def test_http_error_stops_without_retry_and_retains_unknown_charge(self):
        error = urllib.error.HTTPError(runner.ENDPOINT, 429, "Mock limit", {}, io.BytesIO(b'{"error":"mock limit"}'))
        with patch.object(runner.urllib.request, "urlopen", side_effect=error) as opened:
            result = self.call()
        self.assertEqual(result["status"], "transport_error")
        self.assertEqual(opened.call_count, 1)
        self.assertTrue(self.halt.is_set())
        self.assertEqual(next(iter(self.ledger.data["entries"].values()))["state"], "unknown_charge")
        with patch.object(runner.urllib.request, "urlopen") as opened:
            with self.assertRaises(runner.RunStop):
                self.call()
            opened.assert_not_called()

    def test_budget_denial_sends_nothing(self):
        self.ledger = budget.Ledger(self.work / "tiny_budget.json", "0.000001")
        with patch.object(runner.urllib.request, "urlopen") as opened:
            result = self.call()
            opened.assert_not_called()
        self.assertEqual(result["status"], "budget_stop")

    def test_resume_never_resends_and_rejects_changed_input(self):
        with patch.object(runner.urllib.request, "urlopen", return_value=FakeResponse(self.envelope())):
            self.call()
        with patch.object(runner.urllib.request, "urlopen") as opened:
            result = self.call()
            self.assertEqual(result["status"], "valid")
            opened.assert_not_called()
        (self.work / "prompt.txt").write_text("Changed source prompt")
        with patch.object(runner.urllib.request, "urlopen") as opened:
            with self.assertRaises(runner.RunStop):
                self.call()
            opened.assert_not_called()

    def test_resume_rejects_changed_archived_request_or_raw_response(self):
        with patch.object(runner.urllib.request, "urlopen", return_value=FakeResponse(self.envelope())):
            self.call()
        path = self.call_dir() / "request.json"
        saved = path.read_bytes()
        request = json.loads(saved)
        request["packet_sha256"] = "incorrect"
        budget.write_json(path, request)
        with patch.object(runner.urllib.request, "urlopen") as opened:
            with self.assertRaises(runner.RunStop):
                self.call()
            opened.assert_not_called()
        path.write_bytes(saved)
        (self.call_dir() / "attempt_1.response.json").write_text("Changed raw response")
        with patch.object(runner.urllib.request, "urlopen") as opened:
            with self.assertRaises(runner.RunStop):
                self.call()
            opened.assert_not_called()

    def test_preexisting_unfinished_archive_cannot_send_again(self):
        self.call_dir().mkdir(parents=True)
        (self.call_dir() / "request.json").write_text("{}")
        with patch.object(runner.urllib.request, "urlopen") as opened:
            with self.assertRaises(runner.RunStop):
                self.call()
            opened.assert_not_called()

    def test_rejected_resume_preserves_original_authorization_and_software(self):
        manifest = {"copied_inputs": {}}
        budget.write_json(self.work / "input_manifest.json", manifest)
        budget.write_json(self.work / "schedules/pilot.json", {"stage": "pilot", "jobs": [self.job], "initial_call_count": 1})
        authorization = self.root / "mock_authorization.json"
        document = {"provider": "DeepSeek", "paid_calls_authorized": True, "budget_limit_cny": 100, "scope": "mock unit test"}
        budget.write_json(authorization, document)
        arguments = ["runner.py", "--stage", "pilot", "--run-name", "mock", "--work-dir", str(self.work),
                     "--authorization", str(authorization), "--workers", "1"]
        with patch.object(sys, "argv", arguments), patch.object(runner, "load_key", return_value=FAKE_KEY), \
                patch.object(runner.urllib.request, "urlopen", return_value=FakeResponse(self.envelope())), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(), 0)
        saved_authorization = (self.run / "authorization.json").read_bytes()
        saved_software = (self.run / "software.json").read_bytes()
        budget.write_json(authorization, {**document, "scope": "changed unit test"})
        with patch.object(sys, "argv", arguments + ["--resume"]), patch.object(runner, "load_key", return_value=FAKE_KEY), \
                patch.object(runner.urllib.request, "urlopen") as opened, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(runner.RunStop):
                runner.main()
            opened.assert_not_called()
        self.assertEqual((self.run / "authorization.json").read_bytes(), saved_authorization)
        self.assertEqual((self.run / "software.json").read_bytes(), saved_software)


if __name__ == "__main__":
    unittest.main()
