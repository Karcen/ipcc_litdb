"""Offline synthetic-archive tests. No live provider or credentials required."""
import copy
import csv
from decimal import Decimal
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest

SOURCE = pathlib.Path(os.environ.get("IPCC_VALIDATION_SOURCE_DIR", str(pathlib.Path(__file__).parent / "source_panel")))

SPEC = importlib.util.spec_from_file_location("aggregate", pathlib.Path(__file__).with_name("aggregate.py"))
aggregate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(aggregate)


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def response(case, identity="same_study", year=2001):
    return {"cases": [{"case_id": case["case_id"], "identity": identity,
                        "cross_report_recurrence": "recurrent", "reason": "Supported by supplied text.",
                        "records": [{"record_id": record["record_id"], "cited_year": year,
                                     "outlet": "Nature", "source_type": "journal",
                                     "contains_multiple_citations": "no", "evidence_quote": "Nature."}
                                    for record in case["records"]]}]}


def synthetic_validator(result, packet):
    if {c["case_id"] for c in result["cases"]} != {c["case_id"] for c in packet["cases"]}:
        raise ValueError("case IDs differ")


class Archive(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.run = self.root / "work" / "runs" / "synthetic"
        self.case = {"case_id": "C1", "task": "identity", "frozen_evidence": [],
                     "records": [{"record_id": "R001", "report_group": "earlier", "raw_reference": "Smith 2001. Nature."},
                                 {"record_id": "R002", "report_group": "later", "raw_reference": "Smith 2001a. Nature."}]}
        self.jobs = []
        for arm in ("blind", "metadata"):
            case = copy.deepcopy(self.case)
            if arm == "metadata":
                case["pipeline_metadata"] = [{"record_id": "R001", "pipeline_year": 2001}]
            packet = {"cases": [case]}
            packet_path = self.root / "work" / "packets" / (arm + ".json")
            dump(packet_path, packet)
            for config in aggregate.CONFIGURATIONS:
                call_id = arm + "_" + config
                job = {"call_id": call_id, "arm": arm, "configuration_id": config,
                       "model_id": aggregate.MODEL_IDS[config], "prompt_variant": config[-1],
                       "batch_id": "b01", "packet_path": "packets/" + arm + ".json"}
                self.jobs.append(job)
                directory = self.run / "calls" / call_id
                directory.mkdir(parents=True)
                (directory / "prompt.txt").write_text("synthetic prompt")
                dump(directory / "request.json", {**job, "packet_sha256": aggregate.sha256(packet_path),
                                                  "prompt_sha256": aggregate.sha256(directory / "prompt.txt")})
                dump(directory / "response.json", response(case))
                dump(directory / "attempt_1.response.json", {"choices": [{"message": {"content": json.dumps(response(case))}}], "usage": {"prompt_tokens": 10, "completion_tokens": 20}})
                dump(directory / "result.json", {"status": "valid", "model_decision_available": True,
                                                 "response_sha256": aggregate.sha256(directory / "response.json"),
                                                 "attempts": [{"attempt": 1, "status": "valid", "raw_response_sha256": aggregate.sha256(directory / "attempt_1.response.json"), "usage": {"prompt_tokens": 10, "completion_tokens": 20,
                                                                                            "prompt_cache_hit_tokens": 2, "prompt_cache_miss_tokens": 8},
                                                               "cost_upper_cny": .01}]})
        dump(self.run / "schedule.json", {"stage": "pilot", "jobs": self.jobs})

    def tearDown(self):
        self.temp.cleanup()

    def mutate_response(self, call_id, transform):
        directory = self.run / "calls" / call_id
        result = json.loads((directory / "response.json").read_text())
        transform(result)
        dump(directory / "response.json", result)
        raw_path = directory / "attempt_1.response.json"
        raw = json.loads(raw_path.read_text())
        raw["choices"][0]["message"]["content"] = json.dumps(result)
        dump(raw_path, raw)
        status = json.loads((directory / "result.json").read_text())
        status["response_sha256"] = aggregate.sha256(directory / "response.json")
        status["attempts"][0]["raw_response_sha256"] = aggregate.sha256(raw_path)
        dump(directory / "result.json", status)

    def run_summary(self, **kwargs):
        return aggregate.aggregate_run(self.root, "synthetic", validator=synthetic_validator, **kwargs)

    def test_complete_summary_manifest_and_usage(self):
        summary = self.run_summary()
        self.assertEqual(summary["status"], "complete")
        self.assertEqual(summary["scheduled_calls"], 6)
        self.assertEqual(summary["completed_calls"], 6)
        self.assertEqual(summary["record_count_by_arm"], {"blind": 2, "metadata": 2})
        self.assertEqual(summary["tokens"]["prompt_tokens"], 60)
        self.assertEqual(summary["tokens"]["completion_tokens"], 120)
        self.assertEqual(Decimal(summary["cost_upper_cny_recorded"]), Decimal("0.06"))
        self.assertEqual(summary["configured_model_count"], 2)
        self.assertEqual(summary["configuration_count"], 3)
        out = self.root / "results" / "synthetic"
        manifest = json.loads((out / "manifest.json").read_text())
        self.assertTrue(manifest["all_scheduled_calls_complete"])
        self.assertEqual(len([p for p in manifest["input_sha256"] if p.endswith("attempt_1.response.json")]), 6)
        for name, digest in manifest["output_sha256"].items():
            self.assertEqual(digest, aggregate.sha256(out / name))
        self.assertFalse(list(out.glob("*.md")))

    def test_real_decimal_string_cost_contract(self):
        for job in self.jobs:
            path = self.run / "calls" / job["call_id"] / "result.json"
            result = json.loads(path.read_text())
            result["attempts"][0]["cost_upper_cny"] = "0.12345678"
            dump(path, result)
        summary = self.run_summary()
        self.assertEqual(summary["cost_upper_cny_recorded"], "0.74074068")

    def test_missing_calls_block_normal_summary(self):
        (self.run / "calls" / "blind_flash_A" / "result.json").unlink()
        with self.assertRaisesRegex(ValueError, "refuse complete summary"):
            self.run_summary()
        self.assertFalse((self.root / "results" / "synthetic").exists())
        summary = self.run_summary(allow_partial=True)
        self.assertEqual(summary["completed_calls"], 5)
        self.assertEqual(summary["status"], "partial")
        out = self.root / "results" / "synthetic"
        self.assertTrue((out / "partial_summary.json").exists())
        self.assertFalse((out / "summary.json").exists())
        with (out / "partial_agreement.csv").open() as f:
            rows = list(csv.DictReader(f))
        item = next(row for row in rows if row["arm"] == "blind" and row["field"] == "identity")
        self.assertEqual(item["complete_configuration_items"], "0")
        self.assertEqual(item["fleiss_kappa"], "")

    def test_all_calls_absent_are_never_reported_complete(self):
        for job in self.jobs:
            (self.run / "calls" / job["call_id"] / "result.json").unlink()
        summary = self.run_summary(allow_partial=True)
        self.assertEqual(summary["completed_calls"], 0)
        self.assertEqual(summary["model_decision_calls"], 0)
        self.assertEqual(summary["tokens"]["prompt_tokens"], 0)
        self.assertTrue(all(value["comparable"] == 0 for value in summary["metadata_arm_shifts"].values()))

    @unittest.skipUnless((SOURCE / "response_schema.json").exists(), "Frozen response schema unavailable")
    def test_real_validator_integration(self):
        import sys
        sys.path.insert(0, str(pathlib.Path(__file__).parent))
        from validate import validate_response
        schema = json.loads((SOURCE / "response_schema.json").read_text())
        summary = aggregate.aggregate_run(self.root, "synthetic", validator=lambda result, packet: validate_response(result, packet, schema))
        self.assertEqual(summary["completed_calls"], 6)

    def test_code_abstention_is_not_a_model_vote(self):
        directory = self.run / "calls" / "blind_pro_C"
        result = json.loads((directory / "result.json").read_text())
        result.update(status="unresolvable_after_two_invalid_outputs", code_generated_abstention=True, model_decision_available=False)
        def abstain(response):
            for case in response["cases"]:
                case.update(identity="unresolvable", cross_report_recurrence="unresolvable")
                for record in case["records"]:
                    record.update(cited_year=None, outlet=None, source_type="unresolvable", contains_multiple_citations="unresolvable", evidence_quote="")
        self.mutate_response("blind_pro_C", abstain)
        result["response_sha256"] = aggregate.sha256(directory / "response.json")
        result["attempts"][0]["raw_response_sha256"] = aggregate.sha256(directory / "attempt_1.response.json")
        dump(directory / "attempt_2.response.json", {"choices": [{"message": {"content": "invalid"}}]})
        result["attempts"].append({"attempt": 2, "status": "schema_error", "usage": {}, "cost_upper_cny": 0,
                                   "raw_response_sha256": aggregate.sha256(directory / "attempt_2.response.json")})
        dump(directory / "result.json", result)
        data = aggregate.load_run(self.root, "synthetic", synthetic_validator)
        case = next(r for r in aggregate.summarise_cases(data) if r["arm"] == "blind")
        self.assertIsNone(case["identity_pro_C"])
        self.assertEqual(case["available_configurations"], 2)
        self.assertEqual(case["identity_decision"], "two_of_three")
        summary = self.run_summary()
        self.assertEqual(summary["code_abstention_calls"], 1)
        self.assertEqual(summary["model_decision_calls"], 5)
        rows = aggregate.agreement_table(data, aggregate.summarise_cases(data), aggregate.summarise_records(data))
        self.assertEqual(next(r for r in rows if r["arm"] == "blind" and r["field"] == "identity")["complete_configuration_items"], 0)

    def test_contradictory_abstention_flags_rejected(self):
        directory = self.run / "calls" / "blind_flash_A"
        result = json.loads((directory / "result.json").read_text())
        result["model_decision_available"] = False
        dump(directory / "result.json", result)
        with self.assertRaisesRegex(ValueError, "contradictory abstention flags"):
            self.run_summary()

    def test_partial_cannot_coexist_with_stale_complete_manifest(self):
        self.run_summary()
        (self.run / "calls" / "blind_flash_A" / "result.json").unlink()
        with self.assertRaisesRegex(ValueError, "existing complete manifest"):
            self.run_summary(allow_partial=True)

    def test_raw_response_archive_required(self):
        (self.run / "calls" / "blind_flash_A" / "attempt_1.response.json").unlink()
        with self.assertRaisesRegex(ValueError, "raw attempt archive missing"):
            self.run_summary()

    def test_parsed_content_must_equal_raw_api_content(self):
        directory = self.run / "calls" / "blind_flash_A"
        raw_path = directory / "attempt_1.response.json"
        raw = json.loads(raw_path.read_text())
        raw["choices"][0]["message"]["content"] = json.dumps({"cases": []})
        dump(raw_path, raw)
        result = json.loads((directory / "result.json").read_text())
        result["attempts"][0]["raw_response_sha256"] = aggregate.sha256(raw_path)
        dump(directory / "result.json", result)
        with self.assertRaisesRegex(ValueError, "differs from archived raw"):
            self.run_summary()

    def test_hash_mismatches_fail_closed(self):
        (self.run / "calls" / "blind_flash_A" / "response.json").write_text('{"cases": []}')
        with self.assertRaisesRegex(ValueError, "response hash mismatch"):
            self.run_summary()

    def test_changed_packet_fails_closed(self):
        packet_path = self.root / "work" / "packets" / "blind.json"
        packet = json.loads(packet_path.read_text())
        packet["cases"][0]["records"][0]["raw_reference"] = "Changed"
        dump(packet_path, packet)
        with self.assertRaisesRegex(ValueError, "packet hash mismatch"):
            self.run_summary()

    def test_duplicate_schedule_coverage_rejected(self):
        self.jobs.append({**self.jobs[0], "call_id": "duplicate"})
        dump(self.run / "schedule.json", {"jobs": self.jobs})
        with self.assertRaisesRegex(ValueError, "scheduled twice"):
            self.run_summary()

    def test_omitted_configuration_rejected(self):
        dump(self.run / "schedule.json", {"jobs": self.jobs[1:]})
        with self.assertRaisesRegex(ValueError, "omits a configuration"):
            self.run_summary()

    def test_within_item_metadata_shift(self):
        for config in aggregate.CONFIGURATIONS:
            self.mutate_response("metadata_" + config, lambda result: result["cases"][0].update(identity="different_studies"))
        summary = self.run_summary()
        self.assertEqual(summary["metadata_arm_shifts"]["identity"], {"comparable": 1, "changed": 1})
        self.assertEqual(summary["metadata_arm_shifts"]["cited_year"], {"comparable": 2, "changed": 0})

    def test_provisional_controls_and_parser_diagnostics(self):
        data = aggregate.load_run(self.root, "synthetic", synthetic_validator)
        cases, records = aggregate.summarise_cases(data), aggregate.summarise_records(data)
        tables = aggregate.diagnostic_tables(data, cases, records,
                    case_key=[{"case_id": "C1", "origin": "parser:AR5|matched:occ1"}],
                    control_key=[{"case_id": "C1", "reference_label": "same_study", "reference_provenance": "ledger:a:1"}])
        self.assertEqual(len(tables["parser_disagreements"]), 4)
        self.assertTrue(all(r["disagreement"] == 0 for r in tables["parser_disagreements"]))
        self.assertTrue(all(not r["human_reference_labels_archived"] for r in tables["control_reference_agreement"]))
        self.assertTrue(all("conditional on provisional" in r["interpretation"] for r in tables["control_reference_agreement"]))

    def test_declared_scope_counts_verified(self):
        dump(self.run / "schedule.json", {"jobs": self.jobs, "case_count": 50})
        with self.assertRaisesRegex(ValueError, "distinct case count differs"):
            self.run_summary()
        dump(self.run / "schedule.json", {"jobs": self.jobs, "initial_call_count": 36})
        with self.assertRaisesRegex(ValueError, "initial call count differs"):
            self.run_summary()

    def test_directory_traversal_rejected(self):
        self.jobs[0]["packet_path"] = "../../outside.json"
        dump(self.run / "schedule.json", {"jobs": self.jobs})
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.run_summary()


class PureFunctions(unittest.TestCase):
    def test_majority_and_availability(self):
        self.assertEqual(aggregate.majority(["yes"] * 3), ("yes", "unanimous"))
        self.assertEqual(aggregate.majority(["yes", "yes", "no"]), ("yes", "two_of_three"))
        self.assertEqual(aggregate.majority(["a", "b", "c"]), ("unresolvable", "no_majority"))
        self.assertEqual(aggregate.majority(["yes", "yes", None]), ("yes", "two_of_three"))
        self.assertEqual(aggregate.majority([None, None, "yes"]), ("unresolvable", "no_majority"))
        self.assertEqual(aggregate.majority(["unresolvable"] * 3), ("unresolvable", "unanimous"))
        with self.assertRaises(ValueError):
            aggregate.majority(["yes", "yes"])

    def test_fleiss_kappa(self):
        self.assertEqual(aggregate.fleiss_kappa([["a"] * 3, ["b"] * 3]), 1)
        self.assertAlmostEqual(aggregate.fleiss_kappa([["a", "a", "b"], ["a", "b", "b"]]), -1 / 3)
        self.assertIsNone(aggregate.fleiss_kappa([["a"] * 3]))
        self.assertIsNone(aggregate.fleiss_kappa([]))
        with self.assertRaises(ValueError):
            aggregate.fleiss_kappa([["a", None, "b"]])

    def test_regex_matches_frozen_behavior(self):
        self.assertEqual(aggregate.archived_rule_year("Smith (2001a). Later 2020"), "2001")
        self.assertEqual(aggregate.archived_rule_year("x" * 301 + " 2001"), "unresolvable")
        self.assertEqual(aggregate.archived_rule_year("1501 1600"), "1600")
        self.assertEqual(aggregate.canon("Nature – Climate Change", "outlet"), "natureclimatechange")

    def test_weighted_projection_requires_complete_probability_sample(self):
        design = {"source_extraction_strata": {"a": {"population_occurrences": 100, "sampled_occurrences": 2},
                                                 "b": {"population_occurrences": 40, "sampled_occurrences": 1}}}
        rows = [{"arm": "blind", "stratum": s, "source_type": label, "decision": "unanimous", "available_configurations": 3}
                for s, label in (("a", "journal"), ("a", "report"), ("b", "journal"))]
        result = aggregate.weighted_ai_label_projections(rows, design)
        self.assertEqual({r["source_type"]: r["projected_occurrences"] for r in result}, {"journal": 90, "report": 50})
        self.assertEqual(aggregate.weighted_ai_label_projections(rows[:1], design)[0]["status"], "not_estimated_incomplete_probability_sample")
        rows[0]["available_configurations"] = 2
        self.assertIsNone(aggregate.weighted_ai_label_projections(rows, design)[0]["projected_occurrences"])


if __name__ == "__main__":
    unittest.main()
