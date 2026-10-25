"""Offline replay safety and exactness tests with synthetic temporary CSVs."""
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import replay_validation as replay
import validation_statistics as stats


class ReplayTests(unittest.TestCase):
    def make_root(self, parent):
        root = parent / "synthetic-restored"
        (root / "work").mkdir(parents=True)
        (root / "results").mkdir()
        (root / "results/synthetic.csv").write_text("synthetic_n,value\n1,0.25\n")
        return root, parent / "replay_check.json"

    def test_expected_hash_snapshot_precedes_recompute_and_exact_bytes_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, output = self.make_root(Path(temporary))
            before = stats.sha(root / "results/synthetic.csv")
            def same_csv():
                (root / "results/synthetic.csv").write_text("synthetic_n,value\n1,0.25\n")
                return {"status": "synthetic_complete"}
            with patch.object(replay, "source_preflight", return_value={"synthetic": True}), patch.object(replay, "build_actions", return_value=[("synthetic", same_csv)]):
                result = replay.replay(root, output)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["expected_csv_sha256"]["results/synthetic.csv"], before)
            self.assertEqual(result["exactly_reproduced_csvs"], 1)
            self.assertTrue(output.exists())

    def test_different_float_bytes_are_not_silently_tolerated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, output = self.make_root(Path(temporary))
            def changed_csv():
                (root / "results/synthetic.csv").write_text("synthetic_n,value\n1,0.25000000000000001\n")
            with patch.object(replay, "source_preflight", return_value={}), patch.object(replay, "build_actions", return_value=[("synthetic", changed_csv)]):
                result = replay.replay(root, output)
            self.assertEqual(result["status"], "failed_csv_comparison")
            self.assertEqual(result["mismatched_csvs"][0]["path"], "results/synthetic.csv")
            self.assertEqual(result["exactly_reproduced_csvs"], 0)

    def test_network_attempt_fails_closed_and_report_is_external(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, output = self.make_root(Path(temporary))
            def forbidden_network():
                socket.create_connection(("synthetic.invalid", 443))
            with patch.object(replay, "source_preflight", return_value={}), patch.object(replay, "build_actions", return_value=[("synthetic_network", forbidden_network)]):
                result = replay.replay(root, output)
            self.assertEqual(result["status"], "failed_replay")
            self.assertIn("Network access is disabled", result["error"])
            self.assertEqual(result["actions"][0]["status"], "failed")
            self.assertTrue(output.exists())

    def test_rejects_production_root_internal_report_and_active_runner(self):
        with self.assertRaisesRegex(ValueError, "production/source-root"):
            replay.replay(Path(replay.__file__).parent, Path(replay.__file__).parent.parent / "synthetic-replay.json")
        with tempfile.TemporaryDirectory() as temporary:
            root, output = self.make_root(Path(temporary))
            with self.assertRaisesRegex(ValueError, "outside"):
                replay.replay(root, root / "results/replay.json")
            (root / "work/.active_runner.lock").write_text("synthetic lock")
            with self.assertRaisesRegex(ValueError, "runner is active"):
                replay.replay(root, output)

    def test_missing_or_extra_csv_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, output = self.make_root(Path(temporary))
            def replacement():
                (root / "results/synthetic.csv").unlink()
                (root / "results/unexpected.csv").write_text("synthetic_only\n1\n")
            with patch.object(replay, "source_preflight", return_value={}), patch.object(replay, "build_actions", return_value=[("synthetic", replacement)]):
                result = replay.replay(root, output)
            self.assertEqual(result["missing_csvs"], ["results/synthetic.csv"])
            self.assertEqual(result["unexpected_csvs"], ["results/unexpected.csv"])

    def test_known_legacy_zero_row_serialization_is_logged_separately(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, output = self.make_root(Path(temporary))
            legacy = root / replay.LEGACY_PATH; legacy.parent.mkdir(parents=True)
            legacy.write_bytes(replay.LEGACY_EMPTY_BYTES)
            def header_only():
                legacy.write_bytes(replay.LEGACY_HEADER_BYTES)
            with patch.object(replay, "source_preflight", return_value={}), patch.object(replay, "build_actions", return_value=[("synthetic_legacy_writer", header_only)]):
                result = replay.replay(root, output)
            self.assertEqual(result["status"], "passed_with_documented_legacy_serialization_adapter")
            self.assertEqual(result["direct_byte_exact_csvs"], 1)
            self.assertEqual(result["documented_legacy_serialization_adapted_csvs"], 1)
            self.assertEqual(result["byte_verified_after_documented_adapter_csvs"], 2)
            self.assertEqual(result["serialization_adapters"][0]["unadapted_recomputed_sha256"], replay.LEGACY_RECOMPUTED_SHA)
            self.assertFalse(result["serialization_adapters"][0]["labels_changed"])
            self.assertFalse(result["serialization_adapters"][0]["numeric_cells_changed"])
            self.assertEqual(legacy.read_bytes(), replay.LEGACY_EMPTY_BYTES)

    def test_adapter_rejects_nonempty_rows_or_mismatched_header_or_line_endings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _ = self.make_root(Path(temporary))
            legacy = root / replay.LEGACY_PATH; legacy.parent.mkdir(parents=True)
            expected = {replay.LEGACY_PATH: replay.LEGACY_EXPECTED_SHA}
            for data in (replay.LEGACY_HEADER_BYTES + b"SYNTHETIC,case,label,label,1,1,policy\r\n",
                         replay.LEGACY_HEADER_BYTES.replace(b"reviewer", b"wrong_header"),
                         replay.LEGACY_HEADER_BYTES.replace(b"\r\n", b"\n")):
                legacy.write_bytes(data)
                with self.assertRaisesRegex(ValueError, "exact declared header|locked serialization"):
                    replay.apply_locked_legacy_adapter(root, expected)
                self.assertEqual(legacy.read_bytes(), data)

    def test_adapter_does_not_touch_other_paths_or_unlocked_expected_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _ = self.make_root(Path(temporary))
            other = root / "results/other_pilot/codex_qc_exclusions.csv"; other.parent.mkdir(parents=True)
            other.write_bytes(replay.LEGACY_HEADER_BYTES)
            self.assertEqual(replay.apply_locked_legacy_adapter(root, {str(other.relative_to(root)): replay.LEGACY_EXPECTED_SHA}), [])
            self.assertEqual(other.read_bytes(), replay.LEGACY_HEADER_BYTES)
            legacy = root / replay.LEGACY_PATH; legacy.parent.mkdir(parents=True)
            legacy.write_bytes(replay.LEGACY_HEADER_BYTES)
            self.assertEqual(replay.apply_locked_legacy_adapter(root, {replay.LEGACY_PATH: "0" * 64}), [])
            self.assertEqual(legacy.read_bytes(), replay.LEGACY_HEADER_BYTES)


if __name__ == "__main__":
    unittest.main()
