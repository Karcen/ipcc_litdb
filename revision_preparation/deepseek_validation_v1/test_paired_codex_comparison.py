"""All fixtures and judgements are synthetic; no actual research labels here."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import paired_codex_comparison as paired
import validation_statistics as stats


def synthetic_pair():
    packet, response = {"cases": []}, {"cases": []}
    for i, groups in enumerate((("earlier", "later"), ("earlier",), ("later",))):
        cid = f"SYNTHETIC-PAIR-{i}"
        records = [{"record_id": f"R{j + 1:03d}", "report_group": group,
                    "raw_reference": f"Synthetic author, 2001: Invented unit-test citation {i}-{j}. Synthetic Journal."} for j, group in enumerate(groups)]
        packet["cases"].append({"case_id": cid, "task": "identity", "records": records, "frozen_evidence": []})
        unresolved = i == 1
        response["cases"].append({"case_id": cid, "identity": "unresolvable" if unresolved else "same_study",
                                  "cross_report_recurrence": "recurrent" if len(groups) == 2 else "not_applicable",
                                  "records": [{"record_id": r["record_id"], "cited_year": None if unresolved else 2001,
                                               "outlet": None if unresolved else "Synthetic Journal", "source_type": "unresolvable" if unresolved else "journal",
                                               "contains_multiple_citations": "unresolvable" if unresolved else "no",
                                               "evidence_quote": "" if unresolved else "Synthetic author, 2001"} for r in records], "reason": "Synthetic fixture only."})
    cases, records, outlets = [], [], {}
    for index, c in enumerate(response["cases"]):
        case = {"arm": "blind", "case_id": c["case_id"], "task": "identity", "available_configurations": 2 if index == 1 else 3}
        for config in stats.CONFIGS:
            case["available_" + config] = int(not (index == 1 and config == "flash_B"))
            case["status_" + config] = "valid" if case["available_" + config] else "unresolvable_after_two_invalid_outputs"
            case["identity_" + config] = c["identity"]
            case["cross_report_recurrence_" + config] = c["cross_report_recurrence"]
        for field in stats.CASE_FIELDS:
            case[field + "_consensus"], case[field + "_decision"] = stats._eligible_majority([case[field + "_" + config] if case["available_" + config] else None for config in stats.CONFIGS])
        cases.append(case)
        for record in c["records"]:
            row = {"arm": "blind", "case_id": c["case_id"], "record_id": record["record_id"], "available_configurations": case["available_configurations"]}
            for config in stats.CONFIGS:
                row["available_" + config] = case["available_" + config]
                for field in ("cited_year", "outlet", "source_type", "contains_multiple_citations"):
                    value = record[field]
                    if index == 0 and config == "flash_B" and field == "cited_year":
                        value = 2003
                    if index == 0 and field == "outlet":
                        value = {"flash_A": "synthetic journal", "flash_B": "Synthetic-Journal", "pro_C": None}[config]
                    if field == "outlet":
                        outlets[(config, c["case_id"], record["record_id"])] = value
                        value = stats.canon_outlet(value) or "unresolvable"
                    else:
                        value = str(value) if value is not None else "unresolvable"
                    row[field + "_" + config] = value if row["available_" + config] else None
            for field in ("cited_year", "outlet", "source_type", "contains_multiple_citations"):
                row[field + "_consensus"], row[field + "_decision"] = stats._eligible_majority([row[field + "_" + config] for config in stats.CONFIGS])
            records.append(row)
    return packet, response, cases, records, outlets


def save_reviewer_fixture(root):
    packet, response, cases, records, outlets = synthetic_pair()
    directory = root / "synthetic-reviewer"
    directory.mkdir()
    packet_path, response_path, protocol_path = directory / "packet.json", directory / "response.json", directory / "protocol.json"
    schema_path = directory / "response_schema.json"
    record_schema = {"type": "object", "properties": {"record_id": {"type": "string"},
                     "cited_year": {"type": ["integer", "null"]}, "outlet": {"type": ["string", "null"]},
                     "source_type": {"enum": ["journal", "book", "report", "conference", "other", "unresolvable"]},
                     "contains_multiple_citations": {"enum": ["yes", "no", "unresolvable"]}, "evidence_quote": {"type": "string"}},
                     "required": ["record_id", "cited_year", "outlet", "source_type", "contains_multiple_citations", "evidence_quote"], "additionalProperties": False}
    case_schema = {"type": "object", "properties": {"case_id": {"type": "string"},
                   "identity": {"enum": ["same_study", "different_studies", "unresolvable", "not_applicable"]},
                   "cross_report_recurrence": {"enum": ["recurrent", "non_recurrent", "unresolvable", "not_applicable"]},
                   "records": {"type": "array", "items": record_schema}, "reason": {"type": "string"}},
                   "required": ["case_id", "identity", "cross_report_recurrence", "records", "reason"], "additionalProperties": False}
    stats.dump_json(schema_path, {"type": "object", "properties": {"cases": {"type": "array", "items": case_schema}}, "required": ["cases"], "additionalProperties": False})
    stats.dump_json(packet_path, packet); stats.dump_json(response_path, response)
    stats.dump_json(protocol_path, {"seed": 20261011, "deepseek_outputs_visible_to_reviewer": False, "human_adjudication": False})
    provenance_path = directory / "provenance.json"
    stats.dump_json(provenance_path, {"status": "complete", "reviewer": "Synthetic Codex fixture", "human_adjudication": False,
                    "deepseek_outputs_visible_to_reviewer": False, "packet_sha256": stats.sha(packet_path), "response_sha256": stats.sha(response_path),
                    "protocol_sha256": stats.sha(protocol_path), "schema_sha256": stats.sha(schema_path)})
    return (packet_path, response_path, provenance_path, protocol_path, schema_path), (packet, response, cases, records, outlets)


def save_deepseek_fixture(root, data):
    packet, response, cases, records, outlets = data
    run_name = "synthetic-deepseek-archive"
    directory = root / "results" / run_name; directory.mkdir(parents=True)
    packet_path = root / "work/packets/synthetic/blind/batch_0001.json"; packet_path.parent.mkdir(parents=True)
    stats.dump_json(packet_path, packet)
    schedule_path = root / "work/runs" / run_name / "schedule.json"; schedule_path.parent.mkdir(parents=True)
    jobs, paths = [], [packet_path, schedule_path]
    for config in stats.CONFIGS:
        call_id = "synthetic_" + config
        jobs.append({"call_id": call_id, "arm": "blind", "configuration_id": config,
                     "packet_path": str(packet_path.relative_to(root / "work")), "packet_sha256": stats.sha(packet_path)})
        path = schedule_path.parent / "calls" / call_id / "response.json"; path.parent.mkdir(parents=True)
        raw = copy.deepcopy(response)
        for case in raw["cases"]:
            for record in case["records"]:
                record["outlet"] = outlets[(config, case["case_id"], record["record_id"])]
        stats.dump_json(path, raw); paths.append(path)
    stats.dump_json(schedule_path, {"scope": "pilot", "arms": ["blind"], "jobs": jobs})
    stats.write_csv(directory / "case_consensus.csv", cases); stats.write_csv(directory / "record_consensus.csv", records)
    stats.dump_json(directory / "summary.json", {"status": "complete", "incomplete_call_ids": []})
    stats.dump_json(directory / "manifest.json", {"status": "complete", "all_scheduled_calls_complete": True,
                    "input_sha256": {str(path.relative_to(root)): stats.sha(path) for path in paths},
                    "output_sha256": {name: stats.sha(directory / name) for name in ("case_consensus.csv", "record_consensus.csv", "summary.json")}})
    return run_name, paths


class PairedDesignTests(unittest.TestCase):
    def setUp(self):
        self.packet, self.response, self.cases, self.records, self.outlets = synthetic_pair()

    def tables(self):
        return paired.paired_tables(self.packet, self.response, self.cases, self.records, self.outlets, "synthetic_scope")

    def test_shared_and_resolved_agreement_denominators_remain_separate(self):
        row = next(r for r in self.tables()["paired_agreement"] if r["reviewer"] == "flash_A" and r["field"] == "identity")
        self.assertEqual((row["scheduled_items_n"], row["shared_available_n"], row["both_resolved_n"]), (3, 3, 2))
        self.assertEqual(row["exact_agreement_shared_n"], 3)
        self.assertEqual(row["exact_agreement_resolved_n"], 2)
        row = next(r for r in self.tables()["paired_agreement"] if r["reviewer"] == "flash_B" and r["field"] == "identity")
        self.assertEqual(row["shared_available_n"], 2)
        self.assertEqual(row["deepseek_code_abstention_n"], 1)
        self.assertEqual(row["deepseek_missing_n"], 0)

    def test_outlet_literal_and_canonical_agreement_are_distinct(self):
        rows = self.tables()["paired_agreement"]
        literal = next(r for r in rows if r["reviewer"] == "flash_A" and r["field"] == "outlet_literal")
        canonical = next(r for r in rows if r["reviewer"] == "flash_A" and r["field"] == "outlet_canonical")
        self.assertEqual(literal["disagreement_resolved_n"], 2)
        self.assertEqual(canonical["disagreement_resolved_n"], 0)
        literal_consensus = next(r for r in rows if r["reviewer"] == "consensus" and r["field"] == "outlet_literal")
        canonical_consensus = next(r for r in rows if r["reviewer"] == "consensus" and r["field"] == "outlet_canonical")
        self.assertEqual(literal_consensus["deepseek_no_majority_n"], 2)
        self.assertEqual(canonical_consensus["deepseek_no_majority_n"], 0)

    def test_year_absolute_differences_and_label_crosstabs(self):
        tables = self.tables()
        row = next(r for r in tables["paired_year_difference_summary"] if r["reviewer"] == "flash_B")
        self.assertEqual(row["both_resolved_n"], 3)
        self.assertAlmostEqual(row["mean_absolute_year_difference"], 4 / 3)
        self.assertEqual(row["maximum_absolute_year_difference"], 2)
        counts = [r for r in tables["paired_year_difference_counts"] if r["reviewer"] == "flash_B"]
        self.assertEqual({r["absolute_year_difference"]: r["paired_items_n"] for r in counts}, {0: 1, 2: 2})
        self.assertTrue(any(r["codex_label"] == "unresolvable" and r["deepseek_label"] == "unresolvable" and not r["both_resolved"] for r in tables["paired_label_crosstabs"]))

    def test_single_report_recurrence_not_counted(self):
        row = next(r for r in self.tables()["paired_agreement"] if r["reviewer"] == "flash_A" and r["field"] == "cross_report_recurrence")
        self.assertEqual(row["scheduled_items_n"], 1)

    def test_codex_joint_qc_does_not_discard_record_extractions(self):
        self.response["cases"][0]["identity"] = "different_studies"
        tables = self.tables()
        self.assertEqual(len(tables["codex_qc_exclusions"]), 1)
        identity = next(r for r in tables["paired_agreement"] if r["reviewer"] == "flash_A" and r["field"] == "identity")
        years = next(r for r in tables["paired_agreement"] if r["reviewer"] == "flash_A" and r["field"] == "cited_year")
        self.assertEqual(identity["codex_qc_excluded_n"], 1)
        self.assertEqual(years["both_resolved_n"], 3)
        self.assertEqual(self.response["cases"][0]["identity"], "different_studies")

    def test_deepseek_joint_qc_uses_two_eligible_matching_votes(self):
        row = self.cases[0]
        for config in ("flash_A", "flash_B"):
            row["identity_" + config], row["cross_report_recurrence_" + config] = "different_studies", "recurrent"
        tables = self.tables()
        consensus = next(r for r in tables["paired_comparison_detail"] if r["case_id"] == row["case_id"] and r["reviewer"] == "consensus" and r["field"] == "identity")
        self.assertEqual(consensus["deepseek_label"], "no_majority")
        self.assertEqual(consensus["shared_available"], 0)

    def test_weighted_sample_keeps_targeted_separate_and_unresolved_in_N(self):
        roles = [{"case_id": self.packet["cases"][i]["case_id"], "paper_id": str(i), "sample_stratum": stratum, "selection": selection} for i, (stratum, selection) in enumerate(
            (("shared_matched|synthetic", stats.PROBABILITY), ("AR5_only_matched|synthetic", stats.PROBABILITY), ("AR6_only_matched|synthetic", stats.TARGETED)))]
        frozen = {"sample_roles": roles, "sample_strata": [{"stratum": "shared_matched|synthetic", "population_ids": 4, "sampled_ids": 1}, {"stratum": "AR5_only_matched|synthetic", "population_ids": 6, "sampled_ids": 1}]}
        result = paired.weighted_id_tables(self.tables()["paired_comparison_detail"], self.packet, frozen)
        row = next(r for r in result["paired_id_weighted_diagnostics"] if r["reviewer"] == "flash_A" and r["sample_set"] == "probability" and r["outcome"] == "outlet_literal_disagreement_any_record")
        self.assertEqual((row["population_N"], row["actual_sample_n"]), (10, 2))
        self.assertAlmostEqual(row["lower_share"], 0.4)
        self.assertAlmostEqual(row["upper_share"], 1.0)
        targeted = next(r for r in result["paired_id_weighted_diagnostics"] if r["sample_set"] == "targeted")
        self.assertIsNone(targeted["population_N"])
        self.assertIsNone(targeted["weighted_disagreement_count"])
        truncated = copy.deepcopy(self.packet); truncated["cases"].pop()
        with self.assertRaisesRegex(ValueError, "requires all"):
            paired.weighted_id_tables(self.tables()["paired_comparison_detail"], truncated, frozen)


class PairedProvenanceTests(unittest.TestCase):
    def test_archived_packet_evidence_and_raw_literal_outlets_are_hash_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); data = synthetic_pair()
            run_name, paths = save_deepseek_fixture(root, data)
            cases, records, outlets, _ = paired.load_deepseek(root, run_name, data[0])
            self.assertEqual((len(cases), len(records), len(outlets)), (3, 4, 12))
            wrong = copy.deepcopy(data[0]); wrong["cases"][0]["records"][0]["raw_reference"] += " altered synthetic text"
            with self.assertRaisesRegex(ValueError, "strings/records/frozen evidence differ"):
                paired.load_deepseek(root, run_name, wrong)
            raw_path = next(path for path in paths if path.name == "response.json")
            raw_path.write_text('{"cases": []}')
            with self.assertRaisesRegex(ValueError, "raw DeepSeek response"):
                paired.load_deepseek(root, run_name, data[0])

    def test_saved_response_schema_and_provenance_checked_before_comparison(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, _ = save_reviewer_fixture(Path(temporary))
            paired.verify_reviewer(*paths)
            provenance = stats.read_json(paths[2]); provenance["deepseek_outputs_visible_to_reviewer"] = True
            stats.dump_json(paths[2], provenance)
            with self.assertRaisesRegex(ValueError, "blinded"):
                paired.verify_reviewer(*paths)

    def test_response_hash_and_case_id_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, _ = save_reviewer_fixture(Path(temporary))
            response = stats.read_json(paths[1]); response["cases"][0]["case_id"] = "SYNTHETIC-WRONG-ID"
            stats.dump_json(paths[1], response)
            with self.assertRaisesRegex(ValueError, "response_sha256"):
                paired.verify_reviewer(*paths)
            provenance = stats.read_json(paths[2]); provenance["response_sha256"] = stats.sha(paths[1]); stats.dump_json(paths[2], provenance)
            with self.assertRaisesRegex(ValueError, "case ID"):
                paired.verify_reviewer(*paths)

    def test_incomplete_deepseek_aggregate_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); directory = root / "results/synthetic-partial"; directory.mkdir(parents=True)
            stats.dump_json(directory / "manifest.json", {"status": "partial", "all_scheduled_calls_complete": False})
            stats.dump_json(directory / "summary.json", {"status": "partial"})
            with self.assertRaisesRegex(ValueError, "complete DeepSeek"):
                paired.load_deepseek(root, "synthetic-partial", synthetic_pair()[0])

    def test_end_to_end_registry_is_numeric_and_hashes_all_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths, (packet, response, cases, records, outlets) = save_reviewer_fixture(root)
            with patch.object(paired, "load_deepseek", return_value=(cases, records, outlets, set())):
                result = paired.run_comparison(root, "synthetic-complete", *paths, scope="synthetic_scope")
            output = root / "results/synthetic-complete/codex_paired_response"
            self.assertTrue(result["no_human_adjudication"])
            self.assertTrue(result["no_model_calls"])
            registry = stats.read_csv(output / "validation_summary.csv")
            self.assertTrue(registry)
            self.assertTrue(all(set(r) == set(stats.REGISTRY_FIELDS) for r in registry))
            self.assertTrue(any(r["population"] == "synthetic_scope" for r in registry))
            self.assertTrue(all(stats.sha(output / name) == digest for name, digest in result["output_sha256"].items()))
            self.assertTrue(any(r["unit"] == "years" for r in registry))


if __name__ == "__main__":
    unittest.main()
