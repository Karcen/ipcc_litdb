"""Candidate roll-up tests using explicitly synthetic judgements only."""
import copy
from pathlib import Path
import tempfile
import unittest

import candidate_results
import candidate_totals
import validation_statistics as stats


def synthetic_tables():
    packets, keys, cases, geometry = {}, [], [], []
    for i in range(2):
        cid = f"SYNTHETIC-TOTAL-{i}"
        packets[cid] = {"case_id": cid, "task": "identity", "records": [{"record_id": "R001", "report_group": "earlier", "raw_reference": "Synthetic first citation."}, {"record_id": "R002", "report_group": "later", "raw_reference": "Synthetic second citation."}]}
        keys.append({"case_id": cid, "origin": "synthetic:totals", "occurrence_ids": []})
        row = {"arm": "blind", "case_id": cid, "task": "identity", "available_configurations": 3}
        for config in stats.CONFIGS:
            row["available_" + config] = 1
            row["identity_" + config] = "different_studies" if i == 1 and config == "pro_C" else "same_study"
            row["cross_report_recurrence_" + config] = "non_recurrent" if i == 1 and config == "pro_C" else "recurrent"
        for field in stats.CASE_FIELDS:
            row[field + "_consensus"], row[field + "_decision"] = stats._eligible_majority([row[field + "_" + config] for config in stats.CONFIGS])
        cases.append(row)
        geometry.append({"case_id": cid, "geometry": "opposite_report_only_matched_ids", "AR5_paper_id": f"SYNTHETIC-A{i}", "AR6_paper_id": f"SYNTHETIC-B{i}", "AR5_occurrence_id": f"SYNTHETIC-OA{i}", "AR6_occurrence_id": f"SYNTHETIC-OB{i}", "component_id": f"SYNTHETIC-G{i}", "left_candidate_degree": 1, "right_candidate_degree": 1})
    inputs = {"cases": packets, "case_key": keys, "key": {r["case_id"]: r for r in keys}}
    return candidate_results.classification_tables(inputs, cases, geometry)


class CandidateTotalsTests(unittest.TestCase):
    def test_sums_unanimous_and_two_of_three_and_emits_zero_cells(self):
        source = synthetic_tables()
        result = candidate_totals.roll_up(source["candidate_geometry_classifications"], source["candidate_classification_detail"], source["candidate_review_coverage"], 2)
        rows = result["candidate_label_totals"]
        selected = [r for r in rows if r["geometry"] == "all" and r["reviewer"] == "consensus" and r["field"] == "identity" and r["classification_basis"] == "qc_derived"]
        self.assertEqual(len(selected), 6)
        self.assertEqual(next(r for r in selected if r["model_label"] == "same_study")["candidate_pair_rows"], 2)
        self.assertEqual(next(r for r in selected if r["model_label"] == "different_studies")["candidate_pair_rows"], 0)
        self.assertEqual(sum(r["candidate_pair_rows"] for r in selected), 2)
        self.assertEqual({r["source_classification_basis"] for r in selected}, {"postclassification_qc"})

    def test_detail_label_crosscheck_rejects_wrong_aggregate_counts(self):
        source = synthetic_tables()
        source["candidate_geometry_classifications"][0]["candidate_pair_rows"] += 1
        with self.assertRaisesRegex(ValueError, "detail labels"):
            candidate_totals.roll_up(source["candidate_geometry_classifications"], source["candidate_classification_detail"], source["candidate_review_coverage"], 2)

    def test_denominator_coverage_is_checked(self):
        source = synthetic_tables()
        with self.assertRaisesRegex(ValueError, "expected total"):
            candidate_totals.roll_up(source["candidate_geometry_classifications"], source["candidate_classification_detail"], source["candidate_review_coverage"], 3)

    def test_new_artifact_keeps_original_outputs_unchanged_and_hashes_registry(self):
        tables = synthetic_tables()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); directory = root / "synthetic-candidate-source"; directory.mkdir()
            names = ["candidate_geometry_classifications", "candidate_classification_detail", "candidate_review_coverage"]
            for name in names:
                stats.write_csv(directory / (name + ".csv"), tables[name])
            stats.dump_json(directory / "manifest.json", {"status": "complete_conditional_candidate_classifications", "run_name": "synthetic-only", "scheduled_candidate_cases": 2, "full_candidate_frame_cases": 2,
                            "output_sha256": {name + ".csv": stats.sha(directory / (name + ".csv")) for name in names}})
            before = {p: stats.sha(p) for p in directory.iterdir()}
            result = candidate_totals.write_totals(root, directory)
            self.assertTrue(all(stats.sha(p) == digest for p, digest in before.items()))
            output = directory / "candidate_totals"
            self.assertTrue(all(stats.sha(output / name) == digest for name, digest in result["output_sha256"].items()))
            self.assertTrue(result["no_summed_pair_deltas"])
            registry = stats.read_csv(output / "validation_summary.csv")
            self.assertTrue(registry)
            self.assertTrue(all(set(r) == set(stats.REGISTRY_FIELDS) for r in registry))


if __name__ == "__main__":
    unittest.main()
