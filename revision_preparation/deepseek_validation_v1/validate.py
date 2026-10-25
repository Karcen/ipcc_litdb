"""Strict offline response checks; unavailable decisions remain explicit."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator

HERE = Path(__file__).resolve().parent
DEFAULT_SCHEMA = HERE / "work" / "inputs" / "response_schema.json"
ARCHIVED_SCHEMA = HERE / "source_panel" / "response_schema.json"


class ResponseValidationError(ValueError):
    """An invalid output cannot supply a model decision."""


def _schema(schema):
    if isinstance(schema, dict):
        return schema
    path = Path(schema) if schema is not None else DEFAULT_SCHEMA
    if schema is None and not path.is_file():
        path = ARCHIVED_SCHEMA
    return json.loads(path.read_text(encoding="utf-8"))


def _packet_cases(packet):
    if not isinstance(packet, dict) or not isinstance(packet.get("cases"), list):
        raise ResponseValidationError("Packet must contain a cases array")
    cases = {}
    for case in packet["cases"]:
        if not isinstance(case, dict) or not isinstance(case.get("case_id"), str):
            raise ResponseValidationError("Packet case ID must be a string")
        cid = case["case_id"]
        if cid in cases:
            raise ResponseValidationError("Duplicate packet case ID: " + cid)
        if case.get("task") not in ("identity", "extract_fields"):
            raise ResponseValidationError("Unsupported packet task: " + cid)
        records = case.get("records")
        if not isinstance(records, list) or not records:
            raise ResponseValidationError("Packet case must contain records: " + cid)
        ids = []
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("record_id"), str):
                raise ResponseValidationError("Packet record ID must be a string")
            if record.get("report_group") not in ("earlier", "later"):
                raise ResponseValidationError("Unknown packet report group: " + cid)
            if not isinstance(record.get("raw_reference"), str):
                raise ResponseValidationError("Packet raw reference must be a string")
            ids.append(record["record_id"])
        if len(ids) != len(set(ids)):
            raise ResponseValidationError("Duplicate packet record IDs: " + cid)
        evidence = case.get("frozen_evidence", [])
        if not isinstance(evidence, list) or any(not isinstance(item, dict) or not isinstance(item.get("text", ""), str) for item in evidence):
            raise ResponseValidationError("Invalid frozen evidence: " + cid)
        cases[cid] = case
    return cases


def validate_response(response: Any, packet: dict, schema=None) -> None:
    expected = _packet_cases(packet)
    specification = _schema(schema)
    Draft202012Validator.check_schema(specification)
    errors = sorted(Draft202012Validator(specification).iter_errors(response), key=lambda e: str(list(e.path)))
    if errors:
        error = errors[0]
        raise ResponseValidationError("JSON Schema at " + ("/".join(map(str, error.path)) or "root") + ": " + error.message)
    seen = set()
    for case in response["cases"]:
        cid = case["case_id"]
        if cid not in expected or cid in seen:
            raise ResponseValidationError("Unknown or duplicate output case ID: " + cid)
        seen.add(cid)
        original = expected[cid]
        records = {record["record_id"]: record for record in original["records"]}
        ids = [record["record_id"] for record in case["records"]]
        if len(ids) != len(set(ids)) or set(ids) != set(records):
            raise ResponseValidationError("Record IDs differ: " + cid)
        groups = {record["report_group"] for record in original["records"]}
        if original["task"] == "extract_fields":
            if case["identity"] != "not_applicable" or case["cross_report_recurrence"] != "not_applicable":
                raise ResponseValidationError("Extraction task requires not_applicable case labels: " + cid)
        else:
            if case["identity"] == "not_applicable":
                raise ResponseValidationError("Identity task requires an identity decision: " + cid)
            recurrence = case["cross_report_recurrence"]
            if (len(groups) < 2) != (recurrence == "not_applicable"):
                raise ResponseValidationError("Recurrence applicability differs from report groups: " + cid)
            if len(groups) == 2 and case["identity"] == "same_study" and recurrence != "recurrent":
                raise ResponseValidationError("Same focal study in both reports requires recurrence: " + cid)
        if len(case["reason"].split()) > 30:
            raise ResponseValidationError("Reason exceeds 30 words: " + cid)
        evidence = [item.get("text", "") for item in original.get("frozen_evidence", [])]
        for record in case["records"]:
            year = record["cited_year"]
            if year is not None and (type(year) is not int or not 1500 <= year <= 2026):
                raise ResponseValidationError("Cited year must be an integer from 1500 through 2026 or null")
            quote = record["evidence_quote"]
            if len(quote.split()) > 25:
                raise ResponseValidationError("Evidence quote exceeds 25 words: " + cid)
            if not quote and (year is not None or record["outlet"] is not None or record["source_type"] != "unresolvable"):
                raise ResponseValidationError("Resolved fields require a nonempty evidence quote: " + cid)
            sources = [records[record["record_id"]]["raw_reference"], *evidence]
            if quote and not any(quote in source for source in sources):
                raise ResponseValidationError("Quote is not verbatim from its record or frozen evidence: " + cid)
    if seen != set(expected):
        raise ResponseValidationError("Missing output cases")


validate = validate_response


def make_abstention(packet, *, failed_schema_attempts=2):
    """Return accounting abstentions; archive availability metadata beside them."""
    if type(failed_schema_attempts) is not int or failed_schema_attempts != 2:
        raise ValueError("Code-generated abstention requires exactly two failed schema attempts")
    cases = []
    for case in _packet_cases(packet).values():
        extraction = case["task"] == "extract_fields"
        groups = {record["report_group"] for record in case["records"]}
        cases.append({"case_id": case["case_id"], "identity": "not_applicable" if extraction else "unresolvable",
                      "cross_report_recurrence": "not_applicable" if extraction or len(groups) < 2 else "unresolvable",
                      "records": [{"record_id": record["record_id"], "cited_year": None, "outlet": None,
                                   "source_type": "unresolvable", "contains_multiple_citations": "unresolvable", "evidence_quote": ""}
                                  for record in case["records"]],
                      "reason": "Two schema failures; no valid model decision is available."})
    return {"cases": cases}


def availability_metadata():
    return {"status": "unavailable_after_two_schema_failures", "valid_model_decision_available": False,
            "code_generated_abstention": True, "failed_schema_attempts": 2}


def make_abstention_result(packet, *, failed_schema_attempts=2):
    return {**availability_metadata(), "response": make_abstention(packet, failed_schema_attempts=failed_schema_attempts)}
