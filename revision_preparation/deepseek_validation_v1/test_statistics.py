"""Offline tests. Every label in a fixture is explicitly synthetic test data.

These fixtures are not research annotations and never enter actual results.
"""
import math
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import validation_statistics as stats


def synthetic_fixture():
    """Tiny invented frame with known weights and a deliberately unresolved ID."""
    key, cases = [], {}
    def case(cid, origin, task="identity", year="2001", group="earlier"):
        key.append({"case_id": cid, "origin": origin, "task": task, "occurrence_ids": ["SYNTHETIC-OCC-" + cid]})
        cases[cid] = {"case_id": cid, "task": task, "records": [{"record_id": "R001", "report_group": group,
                       "raw_reference": f"Synthetic author, {year}: This invented record exists only in a unit test."}]}
    case("SYNTHETIC-C1", "current_shared_id:1")
    case("SYNTHETIC-C2", "current_shared_id:2")
    case("SYNTHETIC-C3", "sampled_id:3")
    case("SYNTHETIC-C4", "sampled_id:4")
    parser_strata = {"AR5|matched": {"population_occurrences": 10, "sampled_occurrences": 1},
                     "AR5|unmatched": {"population_occurrences": 20, "sampled_occurrences": 1},
                     "AR6|matched": {"population_occurrences": 30, "sampled_occurrences": 1},
                     "AR6|unmatched": {"population_occurrences": 40, "sampled_occurrences": 1}}
    source_strata, source_roles = {}, []
    for i, stratum in enumerate(parser_strata):
        case(f"SYNTHETIC-P{i}", f"parser:{stratum}:SYNTHETIC-OCC-SYNTHETIC-P{i}", "extract_fields")
    for i, stratum in enumerate(("AR5|unmatched|raw_doi_present", "AR5|unmatched|raw_doi_absent", "AR6|unmatched|raw_doi_present", "AR6|unmatched|raw_doi_absent")):
        cid = f"SYNTHETIC-S{i}"
        case(cid, f"source:{stratum}:SYNTHETIC-OCC-{cid}", "extract_fields")
        source_strata[stratum] = {"population_occurrences": (i + 1) * 10, "sampled_occurrences": 1}
        source_roles.append({"case_id": cid, "stratum": stratum, "occurrence_id": "SYNTHETIC-OCC-" + cid})
    roles = [{"case_id": "SYNTHETIC-C1", "paper_id": "1", "sample_stratum": "shared_matched|synthetic", "selection": stats.PROBABILITY},
             {"case_id": "SYNTHETIC-C3", "paper_id": "3", "sample_stratum": "AR5_only_matched|synthetic", "selection": stats.PROBABILITY},
             {"case_id": "SYNTHETIC-C4", "paper_id": "4", "sample_stratum": "AR6_only_matched|synthetic", "selection": stats.TARGETED}]
    papers = [{"paper_id": r["paper_id"], "stratum": r["sample_stratum"], "selection": r["selection"],
               "year_best": "2000" if r["paper_id"] == "1" else "2001", "journal_best": "Synthetic Venue"} for r in roles]
    dimensions = [{"stratum": "shared_matched|synthetic", "population_ids": "4", "sampled_ids": "1"},
                  {"stratum": "AR5_only_matched|synthetic", "population_ids": "6", "sampled_ids": "1"}]
    inputs = {"case_key": key, "key": {r["case_id"]: r for r in key}, "cases": cases,
              "sampling_design": {"shared_id_census": 2, "parser_strata": parser_strata, "source_extraction_strata": source_strata},
              "sample_roles": roles, "sample_papers": papers, "sample_strata": dimensions, "source_roles": source_roles,
              "baseline": {"earlier": "AR5", "later": "AR6", "definition": "matched", "papers_a": "10", "papers_b": "20", "shared": "2"},
              "control_key": [{"case_id": "SYNTHETIC-C1", "reference_provenance": "ledger:synthetic", "reference_label": "same_study"}]}
    case_rows, record_rows = [], []
    for arm in ("blind", "metadata"):
        for cid, packet in cases.items():
            row = {"arm": arm, "case_id": cid, "task": packet["task"], "available_configurations": 3}
            identity = "different_studies" if cid == "SYNTHETIC-C1" else ("unresolvable" if cid == "SYNTHETIC-C2" else "same_study")
            recurrence = "recurrent" if cid == "SYNTHETIC-C1" else ("unresolvable" if cid == "SYNTHETIC-C2" else "not_applicable")
            if packet["task"] == "extract_fields":
                identity = recurrence = "not_applicable"
            for config in stats.CONFIGS:
                row["available_" + config] = 1; row["status_" + config] = "valid"
            for field, value in (("identity", identity), ("cross_report_recurrence", recurrence)):
                for reviewer in stats.REVIEWERS:
                    row[field + "_" + reviewer] = value
                row[field + "_decision"] = "unanimous"
            case_rows.append(row)
            record = {"arm": arm, "case_id": cid, "record_id": "R001", "available_configurations": 3}
            for config in stats.CONFIGS:
                record["available_" + config] = 1
            for field, value in (("cited_year", "2001"), ("outlet", "syntheticvenue"), ("source_type", "journal"), ("contains_multiple_citations", "no")):
                for reviewer in stats.REVIEWERS:
                    record[field + "_" + reviewer] = value
                record[field + "_decision"] = "unanimous"
            record_rows.append(record)
    return inputs, case_rows, record_rows


def write_synthetic_schedule(root, run_name, inputs, arms):
    path = root / "work/runs" / run_name / "schedule.json"
    path.parent.mkdir(parents=True)
    stats.dump_json(path, {"arms": arms, "case_count": len(inputs["cases"]),
                          "record_count": sum(len(c["records"]) for c in inputs["cases"].values())})
    return {str(path.relative_to(root)): stats.sha(path)}


class NumericTests(unittest.TestCase):
    def test_cp_known_value_and_boundaries(self):
        low, high = stats.clopper_pearson(10, 20)
        self.assertAlmostEqual(low, 0.27195784956, places=9)
        self.assertAlmostEqual(high, 0.72804215044, places=9)
        self.assertEqual(stats.clopper_pearson(0, 0), (None, None))
        low, high = stats.clopper_pearson(0, 10)
        self.assertEqual(low, 0)
        self.assertAlmostEqual(high, 1 - 0.025 ** 0.1, places=12)
        low, high = stats.clopper_pearson(10, 10)
        self.assertAlmostEqual(low, 0.025 ** 0.1, places=12)
        self.assertEqual(high, 1)

    def test_cp_symmetry_and_binomial_coverage(self):
        n = 12
        intervals = [stats.clopper_pearson(k, n) for k in range(n + 1)]
        for k, interval in enumerate(intervals):
            self.assertAlmostEqual(interval[0], 1 - intervals[n - k][1], places=11)
        for p in (0.01, 0.1, 0.25, 0.5, 0.8, 0.99):
            coverage = sum(math.comb(n, k) * p ** k * (1 - p) ** (n - k) for k, (lo, hi) in enumerate(intervals) if lo <= p <= hi)
            self.assertGreaterEqual(coverage, 0.95 - 1e-12)

    def test_cp_rejects_invalid_counts(self):
        for k, n in ((1, 0), (-1, 10), (1.5, 10), (2, -1)):
            with self.assertRaises(ValueError):
                stats.clopper_pearson(k, n)

    def test_exact_frozen_regex(self):
        self.assertEqual(stats.rule_year("SYNTHETIC 1999a then 2001"), "1999")
        self.assertEqual(stats.rule_year("x" * 300 + " 2001"), "unresolvable")
        self.assertEqual(stats.rule_year("year 1599 then 2100"), "unresolvable")

    def test_id_contract(self):
        with self.assertRaises(ValueError):
            stats.unique([{"id": "synthetic"}, {"id": "synthetic"}], ["id"])
        with self.assertRaises(ValueError):
            stats.unique([{"id": ""}], ["id"])


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.inputs, self.cases, self.records = synthetic_fixture()

    def test_identity_and_recurrence_are_separate(self):
        tables = stats.census_tables(self.inputs, self.cases)
        row = next(r for r in tables["recurrence_scenarios"] if r["arm"] == "blind" and r["reviewer"] == "consensus")
        self.assertEqual((row["S_lower"], row["S_upper"]), (1, 2))
        self.assertAlmostEqual(row["retention_lower"], 0.1)
        self.assertAlmostEqual(row["reuse_upper"], 0.1)
        labels = {(r["field"], r["model_label"]) for r in tables["shared_census_labels"]}
        self.assertIn(("identity", "different_studies"), labels)
        self.assertIn(("cross_report_recurrence", "recurrent"), labels)

    def test_two_record_joint_incoherence_does_not_flag_multi_record(self):
        packet = self.inputs["cases"]["SYNTHETIC-C1"]
        packet["records"].append({"record_id": "R002", "report_group": "later", "raw_reference": "A second invented unit-test citation."})
        tables = stats.logical_consistency_tables(self.inputs, self.cases)
        self.assertEqual(len(tables["logical_consistency_flags"]), 8)
        self.assertTrue(all(r["joint_incoherence"] == 1 for r in tables["logical_consistency_flags"]))
        self.assertTrue(all(r["two_record_eligible_n"] == 1 for r in tables["logical_consistency_counts"]))
        packet["records"].append({"record_id": "R003", "report_group": "earlier", "raw_reference": "Third invented unit-test citation."})
        tables = stats.logical_consistency_tables(self.inputs, self.cases)
        self.assertEqual(tables["logical_consistency_flags"], [])
        self.assertTrue(all(r["multi_record_mixed_recurrent_n"] == 1 for r in tables["logical_consistency_counts"]))

    def test_unavailable_and_no_majority_enter_unresolved_bound(self):
        for row in self.cases:
            if row["case_id"] == "SYNTHETIC-C1":
                row["available_flash_A"] = 0
                row["cross_report_recurrence_decision"] = "no_majority"
        table = stats.census_tables(self.inputs, self.cases)["recurrence_scenarios"]
        for row in table:
            if row["reviewer"] in ("flash_A", "consensus"):
                self.assertEqual((row["S_lower"], row["S_upper"]), (0, 2))

    def test_sample_weighted_any_record_outcome(self):
        tables = stats.sample_tables(self.inputs, self.cases, self.records)
        rows = [r for r in tables["sample_weighted_diagnostics"] if r["arm"] == "blind" and r["reviewer"] == "consensus"]
        weighted = {r["outcome"]: r for r in rows if r["sample_set"] == "probability"}
        self.assertAlmostEqual(weighted["mixed_study"]["lower_share"], 0.4)
        self.assertAlmostEqual(weighted["year_discrepancy_any_record"]["lower_share"], 0.4)
        self.assertEqual(weighted["year_discrepancy_any_record"]["population_N"], 10)
        self.assertEqual(weighted["nonrecurrence_shared_only"]["actual_sample_n"], 1)
        self.assertEqual(weighted["nonrecurrence_shared_only"]["population_N"], 4)
        targeted = next(r for r in rows if r["sample_set"] == "targeted")
        self.assertIsNone(targeted["population_N"])
        self.assertIsNone(targeted["weighted_positive_count"])

    def test_unresolved_sample_not_dropped_or_weight_redistributed(self):
        for row in self.records:
            if row["case_id"] == "SYNTHETIC-C3":
                row["cited_year_decision"] = "no_majority"
        tables = stats.sample_tables(self.inputs, self.cases, self.records)
        row = next(r for r in tables["sample_weighted_diagnostics"] if r["arm"] == "blind" and r["reviewer"] == "consensus" and r["sample_set"] == "probability" and r["outcome"] == "year_discrepancy_any_record")
        self.assertEqual(row["observed_unresolved_n"], 1)
        self.assertAlmostEqual(row["lower_share"], 0.4)
        self.assertAlmostEqual(row["upper_share"], 1.0)

    def test_sample_contract_rejects_unmatched_or_wrong_n(self):
        self.inputs["sample_strata"][0]["sampled_ids"] = "2"
        with self.assertRaises(ValueError):
            stats.sample_tables(self.inputs, self.cases, self.records)

    def test_occurrence_weighting_not_pooled_accuracy(self):
        for row in self.records:
            if row["case_id"] == "SYNTHETIC-P0":
                row["cited_year_flash_A"] = "2000"
            if row["case_id"] == "SYNTHETIC-P3":
                row["available_flash_A"] = 0
            if row["case_id"] == "SYNTHETIC-S3":
                row["source_type_flash_A"] = "unresolvable"
        tables = stats.occurrence_tables(self.inputs, self.records)
        pooled = next(r for r in tables["parser_weighted_diagnostics"] if r["arm"] == "blind" and r["reviewer"] == "flash_A" and r["report_scope"] == "AR5|AR6")
        self.assertEqual(pooled["population_N"], 100)
        self.assertAlmostEqual(pooled["disagreement_lower_share"], 0.1)
        self.assertAlmostEqual(pooled["disagreement_upper_share"], 0.5)
        source = [r for r in tables["source_weighted_counts"] if r["arm"] == "blind" and r["reviewer"] == "flash_A" and r["report_scope"] == "AR5|AR6"]
        self.assertAlmostEqual(sum(r["projected_occurrences"] for r in source), 100)
        self.assertEqual(next(r for r in source if r["source_type"] == "unresolvable")["projected_occurrences"], 40)

    def test_overlap_is_occurrence_based(self):
        self.inputs["source_roles"][0]["occurrence_id"] = "SYNTHETIC-OCC-SYNTHETIC-P0"
        table = stats.occurrence_tables(self.inputs, self.records)["occurrence_frame_overlap"][0]
        self.assertEqual(table["overlapping_occurrences"], 1)
        self.assertEqual(table["unique_occurrences"], 7)

    def test_control_is_provisional_agreement(self):
        tables = stats.control_tables(self.inputs, self.cases)
        row = tables["control_conditional_agreement"][0]
        self.assertEqual(row["matching_reference_n"], 0)
        self.assertEqual(row["human_reference_labels_archived"], 0)
        self.assertIn("No sensitivity/specificity", row["interpretation"])

    def test_registry_has_unique_long_numeric_quantities(self):
        tables = stats.census_tables(self.inputs, self.cases)
        tables.update(stats.sample_tables(self.inputs, self.cases, self.records))
        tables.update(stats.occurrence_tables(self.inputs, self.records))
        tables.update(stats.control_tables(self.inputs, self.cases))
        rows = stats.numeric_registry(tables)
        self.assertTrue(rows)
        self.assertTrue(all(isinstance(r["value"], (int, float)) for r in rows))
        self.assertTrue(all(set(r) == set(stats.REGISTRY_FIELDS) for r in rows))


class CandidateGeometryTests(unittest.TestCase):
    def test_pair_geometry_and_endpoint_dependence(self):
        membership = {"SYNTHETIC-A": {"AR5_matched_occurrences": 1, "AR6_matched_occurrences": 0},
                      "SYNTHETIC-B": {"AR5_matched_occurrences": 0, "AR6_matched_occurrences": 1},
                      "SYNTHETIC-C": {"AR5_matched_occurrences": 1, "AR6_matched_occurrences": 1},
                      "SYNTHETIC-D": {"AR5_matched_occurrences": 1, "AR6_matched_occurrences": 1},
                      "SYNTHETIC-E": {"AR5_matched_occurrences": 0, "AR6_matched_occurrences": 0}}
        def pair(i, left, right):
            return {"case_id": f"SYNTHETIC-M{i}", "AR5_paper_id": "SYNTHETIC-" + left, "AR6_paper_id": "SYNTHETIC-" + right,
                    "AR5_occurrence_id": f"SYNTHETIC-AR5-{i}", "AR6_occurrence_id": f"SYNTHETIC-AR6-{i}", "raw_doi_relation": "unknown", "blocking_methods": "synthetic"}
        rows, counts = stats.candidate_geometry([pair(1, "A", "B"), pair(2, "A", "C"), pair(3, "C", "D"), pair(4, "E", "B")], membership)
        self.assertEqual([r["shared_delta_if_only_pair_merged"] for r in rows], [1, 0, -1, 0])
        self.assertEqual(rows[0]["geometry"], "opposite_report_only_matched_ids")
        self.assertEqual(rows[2]["AR5_denominator_delta_if_merged"], -1)
        self.assertEqual(rows[0]["component_ids"], 5)
        self.assertEqual(rows[0]["left_candidate_degree"], 2)
        self.assertEqual(sum(r["candidate_pair_rows"] for r in counts), 4)
        self.assertTrue(all(r["labels_available"] == 0 for r in counts))


class PostclassificationQCTests(unittest.TestCase):
    def setUp(self):
        self.inputs, self.cases, self.records = synthetic_fixture()
        self.packet = self.inputs["cases"]["SYNTHETIC-C1"]
        self.packet["records"].append({"record_id": "R002", "report_group": "later", "raw_reference": "Synthetic QC fixture citation, never research data."})

    def _set_case_vote(self, config, identity, recurrence):
        for row in self.cases:
            if row["case_id"] == "SYNTHETIC-C1":
                row["identity_" + config], row["cross_report_recurrence_" + config] = identity, recurrence

    def _recompute_raw_majorities(self):
        for row in self.cases:
            for field in stats.CASE_FIELDS:
                votes = [row[field + "_" + c] if row["available_" + c] else None for c in stats.CONFIGS]
                row[field + "_consensus"], row[field + "_decision"] = stats._eligible_majority(votes)

    def test_exclude_both_votes_retain_raw_columns_and_transport_availability(self):
        self._set_case_vote("flash_B", "same_study", "recurrent")
        self._set_case_vote("pro_C", "same_study", "recurrent")
        self._recompute_raw_majorities()
        original = copy.deepcopy(self.cases)
        records = copy.deepcopy(self.records)
        derived, tables = stats.derive_qc_cases(self.inputs, self.cases)
        row = next(r for r in derived if r["arm"] == "blind" and r["case_id"] == "SYNTHETIC-C1")
        self.assertEqual(self.cases, original)
        self.assertEqual(self.records, records)
        self.assertEqual(row["available_configurations"], 3)
        self.assertEqual(row["qc_eligible_configurations"], 2)
        self.assertEqual(row["identity_flash_A"], "different_studies")
        self.assertEqual(row["cross_report_recurrence_flash_A"], "recurrent")
        self.assertEqual(stats.label(row, "identity", "flash_A"), "qc_excluded")
        self.assertEqual(stats.label(row, "cross_report_recurrence", "flash_A"), "qc_excluded")
        self.assertEqual(stats.label(row, "identity", "consensus"), "same_study")
        self.assertEqual(stats.label(row, "cross_report_recurrence", "consensus"), "recurrent")
        self.assertEqual(stats.decision(row, "cross_report_recurrence"), "two_of_three")
        self.assertEqual(len(tables["qc_configuration_exclusions"]), 2)
        coverage = next(r for r in tables["qc_exclusion_coverage"] if r["arm"] == "blind" and r["reviewer"] == "flash_A" and r["scope"] == "shared_id_census")
        self.assertEqual((coverage["transport_available_n"], coverage["qc_excluded_n"], coverage["qc_eligible_identity_n"]), (2, 1, 1))

    def test_one_remaining_vote_cannot_become_consensus(self):
        self._set_case_vote("pro_C", "different_studies", "non_recurrent")
        self._recompute_raw_majorities()
        derived, tables = stats.derive_qc_cases(self.inputs, self.cases)
        row = next(r for r in derived if r["arm"] == "blind" and r["case_id"] == "SYNTHETIC-C1")
        self.assertEqual(row["qc_eligible_configurations"], 1)
        self.assertEqual(stats.label(row, "identity", "consensus"), "no_majority")
        self.assertEqual(stats.label(row, "cross_report_recurrence", "consensus"), "no_majority")
        scenario = next(r for r in stats.census_tables(self.inputs, derived)["recurrence_scenarios"] if r["arm"] == "blind" and r["reviewer"] == "consensus")
        self.assertEqual((scenario["S_lower"], scenario["S_upper"], scenario["census_N"]), (0, 2, 2))
        self.assertTrue(any(r["raw_consensus_label"] == "recurrent" and r["qc_consensus_label"] == "no_majority" for r in tables["qc_consensus_changes"]))

    def test_model_abstention_remains_distinct_and_can_have_two_eligible_votes(self):
        self._set_case_vote("flash_B", "unresolvable", "unresolvable")
        self._set_case_vote("pro_C", "unresolvable", "unresolvable")
        self._recompute_raw_majorities()
        derived, _ = stats.derive_qc_cases(self.inputs, self.cases)
        row = next(r for r in derived if r["arm"] == "blind" and r["case_id"] == "SYNTHETIC-C1")
        self.assertEqual(stats.label(row, "identity", "flash_A"), "qc_excluded")
        self.assertEqual(stats.label(row, "identity", "flash_B"), "unresolvable")
        self.assertEqual(stats.label(row, "identity", "consensus"), "unresolvable")
        self.assertEqual(stats.decision(row, "identity"), "two_of_three")

    def test_multi_record_mixed_recurrence_is_retained(self):
        self.packet["records"].append({"record_id": "R003", "report_group": "earlier", "raw_reference": "Synthetic third citation."})
        derived, tables = stats.derive_qc_cases(self.inputs, self.cases)
        self.assertEqual(tables["qc_configuration_exclusions"], [])
        row = next(r for r in derived if r["arm"] == "blind" and r["case_id"] == "SYNTHETIC-C1")
        self.assertEqual(stats.label(row, "identity", "consensus"), "different_studies")
        self.assertEqual(stats.label(row, "cross_report_recurrence", "consensus"), "recurrent")

    def test_probability_denominator_and_record_discrepancies_survive_case_qc(self):
        derived, _ = stats.derive_qc_cases(self.inputs, self.cases)
        rows = stats.sample_tables(self.inputs, derived, self.records)["sample_weighted_diagnostics"]
        select = lambda outcome: next(r for r in rows if r["arm"] == "blind" and r["reviewer"] == "flash_A" and r["sample_set"] == "probability" and r["outcome"] == outcome)
        mixed, year = select("mixed_study"), select("year_discrepancy_any_record")
        self.assertEqual(mixed["population_N"], 10)
        self.assertEqual(mixed["actual_sample_n"], 2)
        self.assertEqual(mixed["weighted_unresolved_count"], 4)
        self.assertAlmostEqual(mixed["lower_share"], 0)
        self.assertAlmostEqual(mixed["upper_share"], 0.4)
        self.assertAlmostEqual(year["lower_share"], 0.4)

    def test_qc_excluded_controls_not_counted_as_model_abstentions(self):
        derived, _ = stats.derive_qc_cases(self.inputs, self.cases)
        rows = stats.control_tables(self.inputs, derived)["control_conditional_agreement"]
        row = next(r for r in rows if r["arm"] == "blind" and r["reviewer"] == "flash_A")
        self.assertEqual(row["qc_excluded_n"], 1)
        self.assertEqual(row["model_unresolvable_n"], 0)
        self.assertEqual(row["transport_available_n"], 1)
        self.assertEqual(row["available_n"], 0)
        self.assertIsNone(row["CP95_lower_given_available"])

    def test_policy_registration_is_hashed_and_immutable(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = stats.register_qc_policy(Path(temporary), 123)
            self.assertEqual(stats.read_json(path)["sampling_seed"], 123)
            self.assertEqual(stats.sha(stats.register_qc_policy(Path(temporary), 123)), stats.sha(path))
            with self.assertRaisesRegex(ValueError, "policy changed"):
                stats.register_qc_policy(Path(temporary), 999)
            self.assertIn("After partial", stats.read_json(path)["registration_stage"])


class PublicationGateTests(unittest.TestCase):
    def test_refuse_partial_before_loading_or_inference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            out = root / "results/synthetic-partial"
            out.mkdir(parents=True)
            stats.dump_json(out / "manifest.json", {"status": "partial", "all_scheduled_calls_complete": False})
            with self.assertRaisesRegex(ValueError, "incomplete"):
                stats.run_statistics(root, "synthetic-partial")
            self.assertFalse((out / "scientific_diagnostics").exists())

    def test_complete_synthetic_end_to_end_and_tamper_gate(self):
        inputs, cases, records = synthetic_fixture()
        # A blind-only full schedule is intentional; no metadata rows are invented.
        cases = [r for r in cases if r["arm"] == "blind"]
        records = [r for r in records if r["arm"] == "blind"]
        inputs.update(candidate_pairs=[], membership={}, input_paths=[])
        # Empty candidate-frame tables still get a CSV; actual input frame is not empty.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            aggregate = root / "results/synthetic-complete"
            aggregate.mkdir(parents=True)
            stats.write_csv(aggregate / "case_consensus.csv", cases)
            stats.write_csv(aggregate / "record_consensus.csv", records)
            stats.dump_json(aggregate / "summary.json", {"status": "complete", "incomplete_call_ids": []})
            manifest = {"status": "complete", "all_scheduled_calls_complete": True,
                        "input_sha256": write_synthetic_schedule(root, "synthetic-complete", inputs, ["blind"]),
                        "output_sha256": {name: stats.sha(aggregate / name) for name in ("case_consensus.csv", "record_consensus.csv", "summary.json")}}
            stats.dump_json(aggregate / "manifest.json", manifest)
            with patch.object(stats, "load_inputs", return_value=inputs):
                result = stats.run_statistics(root, "synthetic-complete")
                out = aggregate / "scientific_diagnostics"
                self.assertEqual(result["status"], "complete_model_conditioned_diagnostics")
                self.assertEqual(result["full_run_arms"], ["blind"])
                self.assertIsNone(result["metadata_pilot_run_name"])
                self.assertTrue(all(r["arm"] != "metadata" for r in stats.read_csv(out / "validation_summary.csv")))
                self.assertGreater(len(stats.read_csv(out / "validation_summary.csv")), 100)
                self.assertTrue(all(stats.sha(out / name) == digest for name, digest in result["output_sha256"].items()))
                self.assertEqual(result["no_model_calls"], True)
                self.assertEqual(result["no_credential_reads"], True)
                (aggregate / "case_consensus.csv").write_text("tampered synthetic content")
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    stats.run_statistics(root, "synthetic-complete")

    def test_logical_qc_published_before_statistics_and_quarantines_incoherence(self):
        inputs, cases, records = synthetic_fixture()
        packet = inputs["cases"]["SYNTHETIC-C1"]
        packet["records"].append({"record_id": "R002", "report_group": "later", "raw_reference": "Synthetic second citation used only for semantic-gate testing."})
        for arm in ("blind", "metadata"):
            record = dict(next(r for r in records if r["case_id"] == "SYNTHETIC-C1" and r["arm"] == arm))
            record["record_id"] = "R002"
            records.append(record)
        inputs.update(candidate_pairs=[], membership={}, input_paths=[])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            aggregate = root / "results/synthetic-incoherent"
            aggregate.mkdir(parents=True)
            stats.write_csv(aggregate / "case_consensus.csv", cases)
            stats.write_csv(aggregate / "record_consensus.csv", records)
            stats.dump_json(aggregate / "summary.json", {"status": "complete", "incomplete_call_ids": []})
            stats.dump_json(aggregate / "manifest.json", {"status": "complete", "all_scheduled_calls_complete": True,
                            "input_sha256": write_synthetic_schedule(root, "synthetic-incoherent", inputs, ["blind", "metadata"]),
                            "output_sha256": {name: stats.sha(aggregate / name) for name in ("case_consensus.csv", "record_consensus.csv", "summary.json")}})
            with patch.object(stats, "load_inputs", return_value=inputs):
                original_cases_hash = stats.sha(aggregate / "case_consensus.csv")
                original_records_hash = stats.sha(aggregate / "record_consensus.csv")
                result = stats.run_statistics(root, "synthetic-incoherent")
                self.assertEqual(result["qc_excluded_configuration_case_pairs"], 6)
                self.assertEqual(stats.sha(aggregate / "case_consensus.csv"), original_cases_hash)
                self.assertEqual(stats.sha(aggregate / "record_consensus.csv"), original_records_hash)
                output = aggregate / "scientific_diagnostics"
                self.assertTrue(output.exists())
                self.assertEqual(len(stats.read_csv(output / "qc_configuration_exclusions.csv")), 6)
                self.assertTrue((output / (stats.QC_POLICY_VERSION + ".json")).exists())
                scenarios = stats.read_csv(output / "recurrence_scenarios.csv")
                self.assertTrue(all(int(r["S_lower"]) == 0 and int(r["S_upper"]) == 2 for r in scenarios))
                qc = aggregate / "logical_semantic_qc"
                self.assertEqual(len(stats.read_csv(qc / "logical_consistency_flags.csv")), 8)
                self.assertFalse(stats.read_json(qc / "manifest.json")["substantive_inference_performed"])
                result = stats.run_statistics(root, "synthetic-incoherent", qc_only=True)
                self.assertEqual(result["status"], "raw_joint_incoherence_detected")

    def test_missing_scheduled_metadata_is_not_fabricated(self):
        inputs, cases, records = synthetic_fixture()
        inputs.update(candidate_pairs=[], membership={}, input_paths=[])
        cases = [r for r in cases if r["arm"] == "blind"]
        records = [r for r in records if r["arm"] == "blind"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            aggregate = root / "results/synthetic-missing-arm"
            aggregate.mkdir(parents=True)
            stats.write_csv(aggregate / "case_consensus.csv", cases)
            stats.write_csv(aggregate / "record_consensus.csv", records)
            stats.dump_json(aggregate / "summary.json", {"status": "complete", "incomplete_call_ids": []})
            stats.dump_json(aggregate / "manifest.json", {"status": "complete", "all_scheduled_calls_complete": True,
                            "input_sha256": write_synthetic_schedule(root, "synthetic-missing-arm", inputs, ["blind", "metadata"]),
                            "output_sha256": {name: stats.sha(aggregate / name) for name in ("case_consensus.csv", "record_consensus.csv", "summary.json")}})
            with patch.object(stats, "load_inputs", return_value=inputs):
                with self.assertRaisesRegex(ValueError, "arms differ"):
                    stats.run_statistics(root, "synthetic-missing-arm")

    def test_pilot_anchoring_remains_fixed_50_description(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_name = "synthetic-pilot-only"
            directory = root / "results" / run_name
            directory.mkdir(parents=True)
            frames = root / "work/inputs"
            frames.mkdir(parents=True)
            inputs = {"cases": {}, "key": {}}
            cases, records, shifts = [], [], []
            for i in range(50):
                cid = f"SYNTHETIC-PILOT-{i:02d}"
                inputs["cases"][cid] = {"case_id": cid, "task": "identity", "records": [
                    {"record_id": "R001", "report_group": "earlier", "raw_reference": "Invented pilot fixture citation."}]}
                inputs["key"][cid] = {"origin": "synthetic:pilot", "occurrence_ids": ["SYNTHETIC-OCC-" + cid]}
                for arm in ("blind", "metadata"):
                    case = {"arm": arm, "case_id": cid, "identity_decision": "unanimous", "cross_report_recurrence_decision": "unanimous"}
                    for reviewer in stats.REVIEWERS:
                        case["identity_" + reviewer] = "same_study"
                        case["cross_report_recurrence_" + reviewer] = "not_applicable"
                        if reviewer != "consensus":
                            case["available_" + reviewer] = 1
                    cases.append(case)
                    records.append({"arm": arm, "case_id": cid, "record_id": "R001"})
                for reviewer in stats.REVIEWERS:
                    for field in ("identity", "cross_report_recurrence", "cited_year", "outlet", "source_type", "contains_multiple_citations"):
                        changed = int(i == 0 and reviewer == "flash_A" and field == "cited_year")
                        shifts.append({"case_id": cid, "record_id": "R001" if field not in {"identity", "cross_report_recurrence"} else "",
                                       "reviewer": reviewer, "field": field, "blind": "2001" if field == "cited_year" else "synthetic_label",
                                       "metadata": "2002" if changed else ("2001" if field == "cited_year" else "synthetic_label"),
                                       "comparable": 1, "changed": changed})
            stats.dump_json(frames / "pilot_case_ids_20261021.json", list(inputs["cases"]))
            stats.write_csv(directory / "case_consensus.csv", cases)
            stats.write_csv(directory / "record_consensus.csv", records)
            stats.write_csv(directory / "metadata_arm_comparison.csv", shifts)
            stats.dump_json(directory / "summary.json", {"status": "complete"})
            names = ("case_consensus.csv", "record_consensus.csv", "metadata_arm_comparison.csv", "summary.json")
            stats.dump_json(directory / "manifest.json", {"status": "complete", "all_scheduled_calls_complete": True,
                            "input_sha256": write_synthetic_schedule(root, run_name, inputs, ["blind", "metadata"]),
                            "output_sha256": {name: stats.sha(directory / name) for name in names}})
            tables, _ = stats.pilot_anchoring_tables(root, run_name, inputs)
            rows = tables["pilot_metadata_arm_shifts"]
            self.assertEqual(len(rows), 20)  # Single-report recurrence is ineligible.
            target = next(r for r in rows if r["reviewer"] == "flash_A" and r["field"] == "cited_year")
            self.assertEqual(target["pilot_cases_N"], 50)
            self.assertEqual(target["changed_items_n"], 1)
            self.assertAlmostEqual(target["changed_given_comparable"], 1 / 50)
            self.assertFalse(any("population" in key or "weighted" in key for r in rows for key in r))
            self.assertEqual(tables["pilot_logical_consistency_flags"], [])


if __name__ == "__main__":
    unittest.main()
