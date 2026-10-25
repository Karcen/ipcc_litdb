"""Candidate-summary tests; every citation and label is synthetic test data."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import candidate_results as candidate
import validation_statistics as stats


def synthetic_candidate_run(root, *, provenance=True):
    run_name = "synthetic-candidates-only"
    canonical, pairs, case_rows, records = [], [], [], []
    membership = {}
    assignments = [("A", "B", {"AR5"}, {"AR6"}), ("C", "D", {"AR5", "AR6"}, {"AR6"}), ("E", "F", {"AR5"}, {"AR6"})]
    for i, (left, right, reports_a, reports_b) in enumerate(assignments):
        cid = f"SYNTHETIC-M{i}"
        canonical.append({"case_id": cid, "task": "identity", "records": [
            {"record_id": "R001", "raw_reference": f"Synthetic first candidate citation {i}."},
            {"record_id": "R002", "raw_reference": f"Synthetic second candidate citation {i}."}], "frozen_evidence": []})
        pairs.append({"case_id": cid, "AR5_paper_id": left, "AR6_paper_id": right,
                      "AR5_occurrence_id": f"SYNTHETIC-AR5-{i}", "AR6_occurrence_id": f"SYNTHETIC-AR6-{i}",
                      "blocking_methods": "synthetic", "raw_doi_relation": "unknown"})
        for pid, present in ((left, reports_a), (right, reports_b)):
            membership[pid] = {report + "_matched_occurrences": int(report in present) for report in ("AR5", "AR6")}
    selected = copy.deepcopy(canonical[:2])
    if provenance:
        for packet in selected:
            packet["records"][0]["report_group"] = "earlier"
            packet["records"][1]["report_group"] = "later"
    input_dir = root / "work/inputs/candidates"
    input_dir.mkdir(parents=True)
    candidate_path = input_dir / "blind_candidates.json"
    stats.dump_json(candidate_path, {"cases": canonical})
    input_manifest_path = root / "work/input_manifest.json"
    stats.dump_json(input_manifest_path, {"copied_inputs": {"candidates/blind_candidates.json": {"sha256": stats.sha(candidate_path)}}})
    packet_path = root / "work/packets/synthetic-provenance-v2/blind/batch_0001.json"
    packet_path.parent.mkdir(parents=True)
    stats.dump_json(packet_path, {"cases": selected})
    schedule = {"scope": "candidates_with_provenance_v2", "seed": 20261011, "arms": ["blind"], "case_count": 2, "record_count": 4,
                "jobs": [{"arm": "blind", "configuration_id": config, "packet_path": str(packet_path.relative_to(root / "work")), "packet_sha256": stats.sha(packet_path)} for config in stats.CONFIGS]}
    derivation_path = root / "work/derived/synthetic-candidates/derived_provenance_manifest.json"
    derivation_path.parent.mkdir(parents=True)
    relative_packet = str(packet_path.relative_to(root / "work"))
    stats.dump_json(derivation_path, {"canonical_candidate_input_sha256": stats.sha(candidate_path), "raw_string_changes": 0,
                    "source_input_sha256": {"inputs/candidates/blind_candidates.json": stats.sha(candidate_path)},
                    "packet_sha256": {relative_packet: stats.sha(packet_path)}, "output_sha256": {relative_packet: stats.sha(packet_path)}})
    schedule["derived_provenance_manifest_path"] = str(derivation_path.relative_to(root / "work"))
    schedule["derived_provenance_manifest_sha256"] = stats.sha(derivation_path)
    schedule_path = root / "work/runs" / run_name / "schedule.json"
    schedule_path.parent.mkdir(parents=True)
    stats.dump_json(schedule_path, schedule)
    for i in range(2):
        row = {"arm": "blind", "case_id": f"SYNTHETIC-M{i}", "task": "identity", "available_configurations": 3}
        for config in stats.CONFIGS:
            row["available_" + config] = 1
            row["identity_" + config] = "different_studies" if i == 0 else "same_study"
            row["cross_report_recurrence_" + config] = "non_recurrent" if i == 0 and config == "pro_C" else "recurrent"
        for field in stats.CASE_FIELDS:
            row[field + "_consensus"], row[field + "_decision"] = stats._eligible_majority([row[field + "_" + c] for c in stats.CONFIGS])
        case_rows.append(row)
        for rid in ("R001", "R002"):
            records.append({"arm": "blind", "case_id": row["case_id"], "record_id": rid})
    directory = root / "results" / run_name
    directory.mkdir(parents=True)
    stats.write_csv(directory / "case_consensus.csv", case_rows)
    stats.write_csv(directory / "record_consensus.csv", records)
    stats.dump_json(directory / "summary.json", {"status": "complete", "incomplete_call_ids": []})
    manifest = {"status": "complete", "all_scheduled_calls_complete": True,
                "input_sha256": {str(p.relative_to(root)): stats.sha(p) for p in (input_manifest_path, packet_path, schedule_path)},
                "output_sha256": {name: stats.sha(directory / name) for name in ("case_consensus.csv", "record_consensus.csv", "summary.json")}}
    stats.dump_json(directory / "manifest.json", manifest)
    frozen = {"candidate_pairs": pairs, "membership": membership, "input_paths": []}
    return run_name, frozen, schedule, manifest, canonical, directory


class CompletedCandidateTests(unittest.TestCase):
    def test_provenance_manifest_binding_and_packet_coverage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, schedule, _, _, _ = synthetic_candidate_run(root)
            packets = {root / "work" / job["packet_path"] for job in schedule["jobs"]}
            canonical = root / "work/inputs/candidates/blind_candidates.json"
            path = candidate.verify_derivation_provenance(root, schedule, canonical, packets)
            manifest = stats.read_json(path)
            manifest["packet_sha256"] = {}
            stats.dump_json(path, manifest)
            with self.assertRaisesRegex(ValueError, "manifest hash differs"):
                candidate.verify_derivation_provenance(root, schedule, canonical, packets)
            schedule["derived_provenance_manifest_sha256"] = stats.sha(path)
            with self.assertRaisesRegex(ValueError, "cover each scheduled packet"):
                candidate.verify_derivation_provenance(root, schedule, canonical, packets)

    def test_completed_subset_classifies_by_geometry_with_qc_and_no_additive_correction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_name, frozen, _, _, _, directory = synthetic_candidate_run(root)
            before = stats.sha(directory / "case_consensus.csv")
            with patch.object(stats, "load_inputs", return_value=frozen):
                result = candidate.run_candidate_statistics(root, run_name)
            self.assertEqual(result["scheduled_candidate_cases"], 2)
            self.assertEqual(result["full_candidate_frame_cases"], 3)
            self.assertEqual(result["qc_excluded_configuration_case_pairs"], 2)
            self.assertEqual(stats.sha(directory / "case_consensus.csv"), before)
            self.assertTrue(result["no_population_error_estimator"])
            self.assertTrue(result["no_summed_pair_deltas"])
            output = directory / "candidate_scientific_diagnostics"
            rows = stats.read_csv(output / "candidate_geometry_classifications.csv")
            raw = [r for r in rows if r["reviewer"] == "consensus" and r["geometry"] == "opposite_report_only_matched_ids" and r["field"] == "cross_report_recurrence" and r["classification_basis"] == "archived_raw"]
            qc = [r for r in rows if r["reviewer"] == "consensus" and r["geometry"] == "opposite_report_only_matched_ids" and r["field"] == "cross_report_recurrence" and r["classification_basis"] == "postclassification_qc"]
            self.assertEqual(raw[0]["model_label"], "recurrent")
            self.assertEqual(raw[0]["consensus_status"], "two_of_three")
            self.assertEqual(qc[0]["model_label"], "no_majority")
            coverage = stats.read_csv(output / "candidate_review_coverage.csv")
            self.assertEqual(sum(int(r["unscheduled_candidate_rows_n"]) for r in coverage), 1)
            registry = stats.read_csv(output / "validation_summary.csv")
            self.assertTrue(registry)
            self.assertTrue(all(set(r) == set(stats.REGISTRY_FIELDS) for r in registry))
            self.assertTrue(all(stats.sha(output / name) == digest for name, digest in result["output_sha256"].items()))

    def test_missing_provenance_is_not_silently_repaired(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_name, frozen, _, _, _, directory = synthetic_candidate_run(root, provenance=False)
            with patch.object(stats, "load_inputs", return_value=frozen):
                with self.assertRaisesRegex(ValueError, "received explicit"):
                    candidate.run_candidate_statistics(root, run_name)
            self.assertFalse((directory / "candidate_scientific_diagnostics").exists())

    def test_incomplete_candidate_run_never_produces_label_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_name, _, _, manifest, _, directory = synthetic_candidate_run(root)
            manifest["status"], manifest["all_scheduled_calls_complete"] = "partial", False
            stats.dump_json(directory / "manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "incomplete"):
                candidate.run_candidate_statistics(root, run_name)
            self.assertFalse((directory / "candidate_scientific_diagnostics").exists())

    def test_scheduled_raw_string_tamper_is_rejected_even_with_new_packet_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, schedule, manifest, canonical, _ = synthetic_candidate_run(root)
            path = root / "work" / schedule["jobs"][0]["packet_path"]
            packet = stats.read_json(path)
            packet["cases"][0]["records"][0]["raw_reference"] = "Tampered synthetic citation."
            stats.dump_json(path, packet)
            for job in schedule["jobs"]:
                job["packet_sha256"] = stats.sha(path)
            manifest["input_sha256"][str(path.relative_to(root))] = stats.sha(path)
            with self.assertRaisesRegex(ValueError, "raw citation differs"):
                candidate.scheduled_candidate_cases(root, schedule, manifest, {r["case_id"]: r for r in canonical})

    def test_single_configuration_cannot_be_summarized_as_completed_panel(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, schedule, manifest, canonical, _ = synthetic_candidate_run(root)
            schedule["jobs"] = schedule["jobs"][:1]
            with self.assertRaisesRegex(ValueError, "omits an arm/configuration"):
                candidate.scheduled_candidate_cases(root, schedule, manifest, {r["case_id"]: r for r in canonical})


if __name__ == "__main__":
    unittest.main()
