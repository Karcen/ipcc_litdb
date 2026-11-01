"""Portable verification tests; synthetic tables are not research outputs."""
import copy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import portable_replay as portable
import replay_validation as replay


def fixture(column="weighted_unresolved_count", value="0.25", rule="weighted_ulp"):
    data = portable.encode([["arm", "reviewer", "label", "raw_n", column], ["blind", "consensus", "synthetic", "319", value]])
    path = "results/synthetic/summary.csv"
    policy = {"policy_version": portable.VERSION, "rules": copy.deepcopy(portable.RULES),
              "files": {path: {"expected_sha256": portable.sha(data), "columns": {column: rule}}}}
    return path, data, policy


def change(data, row, column, value):
    rows = portable.parse(data); rows[row][column] = value
    return portable.encode(rows)


class PortableCellTests(unittest.TestCase):
    def test_weighted_four_ulps_pass_and_five_fail_without_source_change(self):
        path, expected, policy = fixture()
        value = 0.25
        for _ in range(4):
            value = math.nextafter(value, math.inf)
        actual = change(expected, 1, 4, repr(value))
        result = portable.compare_csv(path, expected, actual, policy)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["accepted_cells"][0]["ulp_distance"], 4)
        self.assertEqual(result["accepted_cells"][0]["category"], "weighted_projection_or_share")
        self.assertEqual(actual, change(expected, 1, 4, repr(value)))
        value = math.nextafter(value, math.inf)
        result = portable.compare_csv(path, expected, change(expected, 1, 4, repr(value)), policy)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["rejections"][0]["reason"], "numeric_threshold_exceeded")

    def test_cp_and_kappa_absolute_limits_and_large_weighted_ulp_are_distinct(self):
        for column, rule in (("CP95_lower_given_resolved", "cp95_absolute"), ("fleiss_kappa", "kappa_absolute")):
            path, expected, policy = fixture(column=column, rule=rule)
            self.assertTrue(portable.compare_csv(path, expected, change(expected, 1, 4, "0.2500000000005"), policy)["accepted"])
            self.assertFalse(portable.compare_csv(path, expected, change(expected, 1, 4, "0.250000000002"), policy)["accepted"])
        path, expected, policy = fixture()
        self.assertFalse(portable.compare_csv(path, expected, change(expected, 1, 4, "0.2500000000005"), policy)["accepted"])

    def test_counts_labels_structure_unknown_fields_and_unknown_paths_fail(self):
        path, expected, policy = fixture()
        for row, column, value in ((1, 3, "320"), (1, 2, "changed_label"), (0, 4, "changed_header"), (1, 3, "319.00000000000006")):
            self.assertFalse(portable.compare_csv(path, expected, change(expected, row, column, value), policy)["accepted"])
        self.assertFalse(portable.compare_csv("results/unknown.csv", expected, change(expected, 1, 4, "0.25000000000000006"), policy)["accepted"])
        self.assertFalse(portable.compare_csv(path, expected, expected + b"blind,consensus,synthetic,319,0.25\r\n", policy)["accepted"])
        self.assertFalse(portable.compare_csv(path, expected, expected.replace(b"\r\n", b"\n"), policy)["accepted"])

    def test_wrong_hash_integer_text_nonfinite_sign_and_zero_changes_fail(self):
        path, expected, policy = fixture()
        wrong = copy.deepcopy(policy); wrong["files"][path]["expected_sha256"] = "0" * 64
        self.assertFalse(portable.compare_csv(path, expected, change(expected, 1, 4, "0.25000000000000006"), wrong)["accepted"])
        for value in ("nan", "inf", "-inf", "-0.25", "1", " 0.25 "):
            self.assertFalse(portable.compare_csv(path, expected, change(expected, 1, 4, value), policy)["accepted"])
        for e, a in (("0.0", "5e-324"), ("0.0", "-0.0"), ("5e-324", "0.0")):
            path, expected, policy = fixture(column="CP95_lower_given_resolved", value=e, rule="cp95_absolute")
            self.assertFalse(portable.compare_csv(path, expected, change(expected, 1, 4, a), policy)["accepted"])

    def test_registry_requires_exact_source_metric_and_exact_source_value_mirror(self):
        source_path, source, policy = fixture()
        registry_path = "results/synthetic/validation_summary.csv"
        registry = portable.encode([["source_table", "arm", "reviewer", "population", "stratum", "category", "metric", "value", "unit"],
                                    ["summary.csv", "blind", "consensus", "", "", "", "weighted_unresolved_count", "0.25", "count"]])
        policy["files"][registry_path] = {"expected_sha256": portable.sha(registry), "registry_value_rules": [{"source_table": "summary.csv", "metric": "weighted_unresolved_count", "rule": "weighted_ulp"}]}
        actual_registry = change(registry, 1, 7, "0.25000000000000006")
        result = portable.compare_csv(registry_path, registry, actual_registry, policy)
        self.assertTrue(result["accepted"])
        expected = {source_path: source, registry_path: registry}
        actual = {source_path: change(source, 1, 4, "0.25000000000000006"), registry_path: actual_registry}
        portable.verify_registry_mirrors(result, expected, actual, policy)
        self.assertTrue(result["accepted"])
        self.assertTrue(result["accepted_cells"][0]["exact_source_mirror_verified"])
        bad = portable.compare_csv(registry_path, registry, actual_registry, policy)
        portable.verify_registry_mirrors(bad, expected, {**actual, source_path: source}, policy)
        self.assertFalse(bad["accepted"])
        unknown = change(registry, 1, 6, "raw_n")
        self.assertFalse(portable.compare_csv(registry_path, registry, unknown, policy)["accepted"])

    def test_all_accepted_cells_are_logged_without_diagnostic_cap(self):
        path, expected, policy = fixture()
        rows = portable.parse(expected); rows += [rows[1][:] for _ in range(34)]
        expected = portable.encode(rows); policy["files"][path]["expected_sha256"] = portable.sha(expected)
        actual = portable.encode([rows[0]] + [row[:-1] + ["0.25000000000000006"] for row in rows[1:]])
        result = portable.compare_csv(path, expected, actual, policy)
        self.assertTrue(result["accepted"])
        self.assertEqual(len(result["accepted_cells"]), 35)

    def test_policy_rule_limits_cannot_be_widened(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, _, policy = fixture(); policy["rules"]["weighted_ulp"]["maximum_ulp_distance"] = 5
            path = Path(temporary) / "policy.json"; path.write_text(json.dumps(policy))
            with self.assertRaisesRegex(ValueError, "altered"):
                portable.read_policy(path)

    def test_claim_selectors_and_formatter_are_hash_pinned_and_rounding_must_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, expected, policy = fixture(column="upper_share")
            formatter = root / "formatter.py"
            formatter.write_text('from decimal import Decimal\n'
                                 'def source_value(source, root, cache):\n'
                                 ' rows=cache[source["path"]]["document"]\n'
                                 ' matches=[r for r in rows if all(r[k]==str(v) for k,v in source["filters"].items())]\n'
                                 ' if len(matches)!=1: raise ValueError("selector mismatch")\n'
                                 ' return matches[0][source["column"]],{}\n'
                                 'def format_value(value, fmt):\n'
                                 ' return format(Decimal(value)*100,".2f"),"synthetic percent formatter"\n')
            spec = root / "spec.json"
            spec.write_text(json.dumps({"claims": [{"id": "synthetic_share", "format": "percent2", "source": {"path": "module/reports/synthetic/summary.csv", "column": "upper_share", "filters": {"reviewer": "consensus"}}}]}))
            policy["claim_formatting"] = {"spec_relative_path": "spec.json", "spec_sha256": portable.sha(spec.read_bytes()), "formatter_relative_path": "formatter.py", "formatter_sha256": portable.sha(formatter.read_bytes()), "report_source_prefix": "module/reports/"}
            actual = change(expected, 1, 4, "0.25000000000000006")
            result = portable.verify_claim_formats(policy, root, root, {path: expected}, {path: actual})
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["checks"][0]["actual_formatted"], "25.00")
            result = portable.verify_claim_formats(policy, root, root, {path: expected}, {path: change(expected, 1, 4, "0.26")})
            self.assertEqual(result["status"], "failed")
            spec.write_text('{}')
            with self.assertRaisesRegex(ValueError, "hashes differ"):
                portable.verify_claim_formats(policy, root, root, {path: expected}, {path: actual})


class PortableReplayIntegrationTests(unittest.TestCase):
    def test_opt_in_distinguishes_numeric_comparison_from_byte_equality(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary); root = parent / "restored"; (root / "work").mkdir(parents=True)
            (root / "results/synthetic").mkdir(parents=True)
            path, expected, policy = fixture(); (root / path).write_bytes(expected)
            policy_path = parent / "policy.json"; policy_path.write_text(json.dumps(policy))
            def action():
                (root / path).write_bytes(change(expected, 1, 4, "0.25000000000000006"))
            with patch.object(replay, "source_preflight", return_value={}), patch.object(replay, "build_actions", return_value=[("synthetic", action)]), patch.object(portable, "verify_claim_formats", return_value={"status": "passed", "claims_checked": 1}):
                result = replay.replay(root, parent / "report.json", portable_policy=policy_path)
            self.assertEqual(result["status"], "passed_with_portable_numeric_verification")
            self.assertEqual(result["direct_byte_exact_csvs"], 0)
            self.assertEqual(result["byte_verified_after_documented_adapter_csvs"], 0)
            self.assertEqual(result["portable_numeric_compared_csvs"], 1)
            self.assertEqual(result["portable_numeric_accepted_cells"], 1)
            self.assertEqual(len(result["byte_mismatched_csvs"]), 1)
            self.assertEqual(result["mismatched_csvs"], [])
            self.assertEqual((root / path).read_bytes(), change(expected, 1, 4, "0.25000000000000006"))

    def test_formatted_claim_change_prevents_portable_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary); root = parent / "restored"; (root / "work").mkdir(parents=True); (root / "results/synthetic").mkdir(parents=True)
            path, expected, policy = fixture(); (root / path).write_bytes(expected)
            policy_path = parent / "policy.json"; policy_path.write_text(json.dumps(policy))
            def action():
                (root / path).write_bytes(change(expected, 1, 4, "0.25000000000000006"))
            with patch.object(replay, "source_preflight", return_value={}), patch.object(replay, "build_actions", return_value=[("synthetic", action)]), patch.object(portable, "verify_claim_formats", return_value={"status": "failed", "claims_checked": 1}):
                result = replay.replay(root, parent / "report.json", portable_policy=policy_path)
            self.assertEqual(result["status"], "failed_csv_comparison")
            self.assertEqual(result["portable_numeric_compared_csvs"], 0)


if __name__ == "__main__":
    unittest.main()
