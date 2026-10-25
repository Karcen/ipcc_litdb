"""Offline tests for evidence isolation, frozen copies, and record accounting."""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from prepare import SOURCE, build_batches, encoded, prepare_inputs, prepare_stage, verify_original_outputs, verify_prepared_inputs, write_frozen
from validate import ResponseValidationError, make_abstention, make_abstention_result, validate_response
SOURCE = Path(os.environ.get("IPCC_VALIDATION_SOURCE_DIR", str(SOURCE)))


def packet(counts=(2,), extraction=False):
    return {"cases": [{"case_id": "C" + str(i), "task": "extract_fields" if extraction else "identity",
                       "records": [{"record_id": "R" + str(j), "report_group": "earlier" if j == 0 else "later",
                                    "raw_reference": f"Author{j}, 2001: Supplied title {i} {j}. Journal."}
                                   for j in range(count)], "frozen_evidence": []} for i, count in enumerate(counts)]}


def response_for(body):
    result = make_abstention(body)
    for supplied, case in zip(body["cases"], result["cases"]):
        if supplied["task"] == "identity":
            case["identity"] = "same_study"
            if len({r["report_group"] for r in supplied["records"]}) == 2:
                case["cross_report_recurrence"] = "recurrent"
        for record, original in zip(case["records"], supplied["records"]):
            record.update(cited_year=2001, outlet="Journal", source_type="journal", contains_multiple_citations="no", evidence_quote=original["raw_reference"])
    return result


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.packet = packet()
        self.response = response_for(self.packet)
        self.schema = json.loads((SOURCE / "response_schema.json").read_text())

    def invalid(self, result=None, body=None):
        with self.assertRaises(ResponseValidationError):
            validate_response(self.response if result is None else result, self.packet if body is None else body, self.schema)

    def test_valid_and_mixed_recurrence(self):
        validate_response(self.response, self.packet, self.schema)
        self.response["cases"][0]["identity"] = "different_studies"
        validate_response(self.response, self.packet, self.schema)

    def test_case_accounting(self):
        self.invalid({"cases": []})
        self.invalid({"cases": self.response["cases"] * 2})
        self.response["cases"][0]["case_id"] = "UNKNOWN"
        self.invalid()

    def test_record_accounting(self):
        case = self.response["cases"][0]
        saved = copy.deepcopy(case["records"])
        for records in (saved[:1], [saved[0], saved[0]]):
            case["records"] = records
            self.invalid()
        case["records"] = saved
        case["records"][0]["record_id"] = "UNKNOWN"
        self.invalid()

    def test_quote_record_and_metadata_isolation(self):
        self.response["cases"][0]["records"][0]["evidence_quote"] = self.packet["cases"][0]["records"][1]["raw_reference"]
        self.invalid()
        self.packet["cases"][0]["pipeline_metadata"] = [{"pipeline_title": "Metadata only title"}]
        self.response["cases"][0]["records"][0]["evidence_quote"] = "Metadata only title"
        self.invalid()

    def test_frozen_evidence_allowed(self):
        self.packet["cases"][0]["frozen_evidence"] = [{"text": "A supplied source transcription."}]
        self.response["cases"][0]["records"][0]["evidence_quote"] = "supplied source transcription"
        validate_response(self.response, self.packet, self.schema)

    def test_resolved_fields_require_nonempty_evidence_quote(self):
        self.response["cases"][0]["records"][0]["evidence_quote"] = ""
        self.invalid()

    def test_word_limits(self):
        text = " ".join(["evidence"] * 26)
        self.packet["cases"][0]["frozen_evidence"] = [{"text": text}]
        self.response["cases"][0]["records"][0]["evidence_quote"] = text
        self.invalid()
        self.response["cases"][0]["records"][0]["evidence_quote"] = ""
        self.response["cases"][0]["reason"] = " ".join(["reason"] * 31)
        self.invalid()

    def test_types_and_extra_properties(self):
        for field, value in (("outlet", 1), ("cited_year", True), ("cited_year", 2001.0), ("evidence_quote", [])):
            result = copy.deepcopy(self.response)
            result["cases"][0]["records"][0][field] = value
            self.invalid(result)
        result = copy.deepcopy(self.response)
        result["cases"][0]["unexpected"] = "value"
        self.invalid(result)
        self.invalid({"cases": {}})

    def test_task_and_group_semantics(self):
        self.response["cases"][0]["identity"] = "not_applicable"
        self.invalid()
        for recurrence in ("not_applicable", "non_recurrent"):
            result = response_for(self.packet)
            result["cases"][0]["cross_report_recurrence"] = recurrence
            self.invalid(result)
        one = packet((1,))
        result = response_for(one)
        result["cases"][0]["cross_report_recurrence"] = "recurrent"
        self.invalid(result, one)
        extraction = packet((1,), extraction=True)
        result = response_for(extraction)
        result["cases"][0]["identity"] = "same_study"
        self.invalid(result, extraction)

    def test_abstention_unavailable(self):
        result = make_abstention_result(self.packet)
        self.assertFalse(result["valid_model_decision_available"])
        self.assertTrue(result["code_generated_abstention"])
        validate_response(result["response"], self.packet, self.schema)
        for attempts in (0, 1, 3, True):
            with self.assertRaises(ValueError):
                make_abstention(self.packet, failed_schema_attempts=attempts)


class BatchTests(unittest.TestCase):
    def test_record_cap_and_order(self):
        body = packet((9, 12, 2, 17))
        batches = build_batches(body)
        self.assertEqual([(b["case_count"], b["record_count"]) for b in batches], [(1, 9), (2, 14), (1, 17)])
        self.assertEqual([c for b in batches for c in b["packet"]["cases"]], body["cases"])

    def test_case_cap_and_oversize(self):
        self.assertEqual([b["case_count"] for b in build_batches(packet((1,) * 11))], [10, 1])
        batches = build_batches(packet((21, 1)))
        self.assertEqual([b["record_count"] for b in batches], [21, 1])
        self.assertTrue(batches[0]["oversized_single_case"])

    def test_paired_utf8_byte_limit(self):
        blind = packet((1, 1))
        metadata = copy.deepcopy(blind)
        for case in metadata["cases"]:
            case["pipeline_metadata"] = [{"pipeline_title": "\u00e9" * 300}]
        cap = max(len(encoded({"cases": [case]})) for case in metadata["cases"])
        batches = build_batches(blind, paired_packet=metadata, max_bytes=cap)
        self.assertEqual([b["case_count"] for b in batches], [1, 1])
        self.assertTrue(all(b["paired_packet_bytes"] <= cap for b in batches))

    def test_blinding_changes_and_duplicate_ids(self):
        blind = packet((1, 1))
        metadata = copy.deepcopy(blind)
        metadata["cases"][0]["records"][0]["raw_reference"] = "Altered"
        with self.assertRaises(ValueError):
            build_batches(blind, paired_packet=metadata)
        metadata = copy.deepcopy(blind)
        blind["cases"][0]["paper_id"] = metadata["cases"][0]["paper_id"] = 42
        with self.assertRaises(ValueError):
            build_batches(blind, paired_packet=metadata)
        body = packet((1, 1))
        body["cases"][1]["case_id"] = body["cases"][0]["case_id"]
        with self.assertRaises(ValueError):
            build_batches(body)

    def test_frozen_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "packet.json"
            write_frozen(path, b"first")
            write_frozen(path, b"first")
            with self.assertRaises(ValueError):
                write_frozen(path, b"second")


@unittest.skipUnless((SOURCE / "frames/blind_all.json").is_file(), "Frozen source frames are not available")
class FrozenInputTests(unittest.TestCase):
    def test_frozen_pilot_full_and_candidates(self):
        verification = verify_original_outputs(source_dir=SOURCE)
        self.assertFalse(verification["packet_regeneration_from_current_code_claimed"])
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            manifest = prepare_inputs(source_dir=SOURCE, work_dir=work)
            self.assertEqual((manifest["canonical_case_count"], manifest["canonical_record_count"]), (5956, 9701))
            self.assertEqual(manifest["pilot_records_per_arm"], 100)
            pilot = prepare_stage(work_dir=work)
            self.assertEqual(pilot["initial_call_count"], 36)
            self.assertEqual([(b["case_count"], b["record_count"]) for b in pilot["batch_summaries"]], [(10, 17), (7, 20), (10, 14), (10, 19), (7, 12), (6, 18)])
            self.assertEqual(prepare_stage(work_dir=work), pilot)
            full = prepare_stage("full", work_dir=work)
            self.assertEqual((full["case_count"], full["record_count"]), (5956, 9701))
            self.assertTrue(full["full_execution_requires_completed_pilot"])
            self.assertFalse(full["automatic_execution_enabled"])
            self.assertEqual(prepare_stage("candidates", work_dir=work)["case_count"], 3944)
            (work / "inputs/prompt_A.txt").write_text("Changed")
            with self.assertRaises(ValueError):
                verify_prepared_inputs(work)


if __name__ == "__main__":
    unittest.main()
