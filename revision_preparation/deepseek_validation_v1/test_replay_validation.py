"""Offline replay safety and exactness tests with synthetic temporary CSVs."""
from pathlib import Path
import json
import math
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
            diagnostic = result["csv_mismatch_diagnostics"]["results/synthetic.csv"]
            self.assertEqual(diagnostic["changed_cell_count"], 1)
            example = diagnostic["examples"][0]
            self.assertEqual(example["expected"]["value"], "0.25")
            self.assertEqual(example["actual"]["value"], "0.25000000000000001")
            self.assertEqual(example["difference_kind"], "numeric_representation_change")
            self.assertEqual(example["ulp_distance"], 0)
            self.assertEqual(example["absolute_decimal_text_delta"], "1E-17")

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


class CsvDiagnosticTests(unittest.TestCase):
    def test_count_float_and_label_changes_are_distinct_and_exactly_counted(self):
        expected = b"scheduled_items_n,rate,label\r\n331,0.1,same_study\r\n"
        actual = b"scheduled_items_n,rate,label\r\n332,0.10000000000000002,different_studies\r\n"
        result = replay.csv_mismatch_diagnostics(expected, actual)
        self.assertEqual(result["changed_cell_count"], 3)
        self.assertEqual(result["header_changed_cell_count"], 0)
        self.assertEqual(result["changed_data_row_count"], 1)
        self.assertEqual(result["difference_kind_counts"], {"integer_value_change": 1, "float_value_change": 1, "string_or_type_change": 1})
        count, floating, label = result["examples"]
        self.assertTrue(count["declared_count_cell"])
        self.assertEqual(count["integer_delta"], 1)
        self.assertEqual(count["csv_row"], 2)
        self.assertEqual(floating["expected_column"]["value"], "rate")
        self.assertEqual(floating["ulp_distance"], 1)
        self.assertAlmostEqual(floating["absolute_delta"], math.nextafter(0.1, math.inf) - 0.1)
        self.assertFalse(floating["declared_count_cell"])
        self.assertEqual(label["actual"]["value"], "different_studies")

    def test_examples_are_capped_but_all_changed_cells_are_counted(self):
        expected = ("value\n" + "0.1\n" * 47).encode()
        actual = ("value\n" + "0.10000000000000002\n" * 47).encode()
        result = replay.csv_mismatch_diagnostics(expected, actual)
        self.assertEqual(result["changed_cell_count"], 47)
        self.assertEqual(len(result["examples"]), 20)
        self.assertEqual(result["omitted_changed_cell_examples"], 27)
        self.assertEqual(result["changed_columns"][0]["changed_cell_count"], 47)
        self.assertEqual(result["changed_columns"][0]["maximum_ulp_distance"], 1)

    def test_headers_row_addition_and_cell_absence_are_reported(self):
        expected = b"n,label\n1,old\n"
        actual = b"count,label,extra\n1,new,x\n2,added,y\n"
        result = replay.csv_mismatch_diagnostics(expected, actual)
        self.assertFalse(result["header_equal"])
        self.assertEqual(result["header_changed_cell_count"], 2)
        self.assertEqual(result["added_data_rows"], 1)
        self.assertEqual(result["removed_data_rows"], 0)
        self.assertEqual(result["changed_cell_count"], 7)
        self.assertEqual(result["data_changed_cell_count"], 5)
        self.assertEqual(result["difference_kind_counts"]["cell_added"], 4)
        reverse = replay.csv_mismatch_diagnostics(actual, expected)
        self.assertEqual(reverse["removed_data_rows"], 1)
        self.assertEqual(reverse["difference_kind_counts"]["cell_removed"], 4)

    def test_registry_context_and_declared_count_units_remain_visible(self):
        expected = b"source_table,reviewer,metric,value,unit\nsynthetic.csv,consensus,synthetic_n,331,count\n"
        actual = expected.replace(b"331,count", b"332,count")
        result = replay.csv_mismatch_diagnostics(expected, actual)
        example = result["examples"][0]
        self.assertTrue(example["declared_count_cell"])
        self.assertEqual(example["expected_row_context"]["metric"]["value"], "synthetic_n")
        self.assertEqual(example["expected_row_context"]["source_table"]["value"], "synthetic.csv")

    def test_serialization_only_difference_does_not_invent_changed_cells(self):
        result = replay.csv_mismatch_diagnostics(b"n,label\r\n1,same\r\n", b"n,label\n1,same\n")
        self.assertTrue(result["serialization_only_difference"])
        self.assertTrue(result["parsed_cells_equal"])
        self.assertEqual(result["changed_cell_count"], 0)
        self.assertEqual(result["examples"], [])

    def test_header_addition_is_not_counted_as_a_data_row_and_integer_format_is_distinct(self):
        header = replay.csv_mismatch_diagnostics(b"", b"n\n")
        self.assertEqual(header["header_changed_cell_count"], 1)
        self.assertEqual(header["added_data_rows"], 0)
        self.assertEqual(header["data_changed_cell_count"], 0)
        formatting = replay.csv_mismatch_diagnostics(b"n\n001\n", b"n\n1\n")
        self.assertEqual(formatting["examples"][0]["difference_kind"], "numeric_representation_change")
        self.assertEqual(formatting["examples"][0]["integer_delta"], 0)

    def test_nonfinite_and_long_strings_remain_json_safe_and_bounded(self):
        expected = b"value,label\nnan," + b"a" * 400 + b"\n"
        actual = b"value,label\ninf," + b"b" * 400 + b"\n"
        result = replay.csv_mismatch_diagnostics(expected, actual)
        self.assertEqual(result["changed_cell_count"], 2)
        self.assertEqual(result["examples"][0]["expected_type"], "nonfinite_numeric")
        self.assertTrue(result["examples"][1]["expected"]["truncated"])
        self.assertEqual(len(result["examples"][1]["expected"]["value"]), 256)
        self.assertEqual(result["examples"][1]["expected"]["characters"], 400)
        json.dumps(result, allow_nan=False)

    def test_ulp_distance_handles_signed_zero_and_negative_adjacent_values(self):
        self.assertEqual(replay.binary64_ulp_distance(-0.0, 0.0), 0)
        self.assertEqual(replay.binary64_ulp_distance(-0.1, math.nextafter(-0.1, -math.inf)), 1)
        with self.assertRaisesRegex(ValueError, "finite"):
            replay.binary64_ulp_distance(math.inf, 0.1)


if __name__ == "__main__":
    unittest.main()
