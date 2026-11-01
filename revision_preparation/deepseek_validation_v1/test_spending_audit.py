"""Financial reconciliation tests use synthetic archived bytes only."""
import json
from pathlib import Path
import tempfile
import unittest
import audit_spending as audit
from budget import Ledger


class SpendingAuditTests(unittest.TestCase):
    def fixture(self, root):
        work = root / "work"; work.mkdir()
        directory = work / "runs/r/calls/c"; directory.mkdir(parents=True)
        (directory / "request.json").write_text(json.dumps({"call_id": "c"}))
        request = directory / "attempt_1.request.json"; request.write_bytes(b'{"mock":true}')
        ledger = Ledger(work / "budget_20261025.json", 100)
        ledger.reserve("r/c/1", "deepseek-flash", 100, 100)
        ledger.uncertain("r/c/1", "TimeoutError")
        result = {"status": "transport_error", "attempts": [{"attempt": 1, "request_sent": True,
                  "raw_request_sha256": audit.sha(request)}]}
        (directory / "result.json").write_text(json.dumps(result))
        return work, directory

    def test_unknown_request_is_retained_and_duplicate_archive_not_double_charged(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); work, directory = self.fixture(root)
            shutil.copytree(directory, work / "runs/r/interrupted_calls/c/generation_001")
            report = audit.reconcile(work, root / "out")
            self.assertEqual(report["unique_paid_attempts"], 1)
            self.assertEqual(report["unknown_charge_attempts"], 1)
            self.assertEqual(report["charged_upper_cny"], "0.001")

    def test_missing_or_changed_evidence_blocks_reconciliation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); work, directory = self.fixture(root)
            (directory / "attempt_1.request.json").write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, "bytes changed"):
                audit.reconcile(work, root / "out")

    def test_active_runner_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); work, _ = self.fixture(root)
            (work / ".active_runner.lock").write_text("123")
            with self.assertRaisesRegex(ValueError, "runner is active"):
                audit.reconcile(work, root / "out")


if __name__ == "__main__":
    unittest.main()
