"""Structural identity supplements; fixtures contain synthetic labels only."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import paired_codex_comparison as paired
import paired_identity_record_counts as supplement
import validation_statistics as stats
from test_paired_codex_comparison import synthetic_pair, save_reviewer_fixture


class StructuralIdentityTests(unittest.TestCase):
    def setUp(self):
        self.packet, response, cases, records, outlets = synthetic_pair()
        self.tables = paired.paired_tables(self.packet, response, cases, records, outlets, "synthetic_scope")

    def test_record_count_strata_preserve_all_identity_counts(self):
        details = self.tables["paired_comparison_detail"]
        before = copy.deepcopy(details)
        result = supplement.identity_record_count_tables(self.packet, details, "synthetic_scope")
        self.assertEqual(details, before)
        counts = result["identity_record_count_population"]
        self.assertEqual({r["stratum"]: r["assigned_ids_n"] for r in counts}, {"singleton": 2, "multiple_records": 1})
        for reviewer in stats.REVIEWERS:
            original = next(r for r in self.tables["paired_agreement"] if r["field"] == "identity" and r["reviewer"] == reviewer)
            parts = [r for r in result["identity_record_count_agreement"] if r["reviewer"] == reviewer]
            for metric in ("scheduled_items_n", "shared_available_n", "both_resolved_n", "exact_agreement_shared_n", "exact_agreement_resolved_n", "deepseek_unresolvable_n", "deepseek_no_majority_n"):
                self.assertEqual(sum(r[metric] for r in parts), original[metric])

    def test_singleton_abstentions_are_not_merge_consistency_agreement(self):
        details = copy.deepcopy(self.tables["paired_comparison_detail"])
        counts = {c["case_id"]: len(c["records"]) for c in self.packet["cases"]}
        for row in details:
            if row["field"] == "identity" and counts[row["case_id"]] == 1:
                row.update(codex_label="same_study", deepseek_label="unresolvable", shared_available=1, both_resolved=0, exact_agreement=0)
        result = supplement.identity_record_count_tables(self.packet, details, "synthetic_scope")
        row = next(r for r in result["identity_record_count_agreement"] if r["reviewer"] == "consensus" and r["stratum"] == "singleton")
        self.assertEqual((row["scheduled_items_n"], row["shared_available_n"], row["both_resolved_n"]), (2, 2, 0))
        self.assertEqual(row["disagreement_shared_n"], 2)
        self.assertIsNone(row["exact_agreement_given_resolved"])

    def test_missing_reviewer_case_is_rejected(self):
        details = [r for r in self.tables["paired_comparison_detail"] if not (r["field"] == "identity" and r["reviewer"] == "consensus" and r["case_id"] == self.packet["cases"][0]["case_id"])]
        with self.assertRaisesRegex(ValueError, "every reviewer"):
            supplement.identity_record_count_tables(self.packet, details, "synthetic_scope")

    def test_supplement_has_own_manifest_and_does_not_modify_base_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths, (_, _, cases, records, outlets) = save_reviewer_fixture(root)
            with patch.object(paired, "load_deepseek", return_value=(cases, records, outlets, set())):
                paired.run_comparison(root, "synthetic-complete", *paths, scope="synthetic_scope")
            directory = root / "results/synthetic-complete/codex_paired_response"
            original = {p: stats.sha(p) for p in directory.iterdir() if p.is_file()}
            result = supplement.write_supplement(root, directory, paths[0])
            self.assertTrue(result["original_results_unchanged"])
            self.assertTrue(all(stats.sha(p) == h for p, h in original.items()))
            out = directory / "identity_record_count_supplement"
            self.assertTrue(all(stats.sha(out / name) == digest for name, digest in result["output_sha256"].items()))
            registry = stats.read_csv(out / "validation_summary.csv")
            self.assertEqual({r["stratum"] for r in registry}, {"singleton", "multiple_records"})
            self.assertTrue(all(set(r) == set(stats.REGISTRY_FIELDS) for r in registry))
            (directory / "paired_comparison_detail.csv").write_text("changed synthetic artifact")
            with self.assertRaisesRegex(ValueError, "hash differs"):
                supplement.write_supplement(root, directory, paths[0])


if __name__ == "__main__":
    unittest.main()
