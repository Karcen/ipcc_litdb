#!/usr/bin/env python3
"""Refresh derived AI-review files after an explicit, sourced proposal update.

This exports existing judgments. It makes no identity decisions or estimates.
Run check_ai_annotation_proposals.py after writing the derived files.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path


def read_csv(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def text_value(value):
    if isinstance(value, list):
        return " | ".join(str(item) for item in value)
    return str(value) if value is not None else ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--write", action="store_true", help="Write derived exports and manifest hashes")
    args = parser.parse_args()
    if not args.write:
        parser.error("Pass --write to refresh derived AI exports; human labels are never edited")
    repo = args.repo.resolve()
    base = repo / "revision_preparation/annotation"
    bundle = base / "ai_review"
    proposal = json.loads((bundle / "proposals.json").read_text())
    manifest = json.loads((bundle / "manifest.json").read_text())
    schema = json.loads((base / "annotation_schema.json").read_text())
    papers = read_csv(base / "inputs/revision_identity_audit_sample_papers.csv")
    originals = read_csv(base / "inputs/revision_identity_audit_sample_entries.csv")
    cases = proposal["cases"]
    cmap = {case["paper_id"]: case for case in cases}
    occurrences = {row["occurrence_id"]: row for case in cases for row in case["occurrences"]}
    if (proposal["human_validated"] is not False
            or len(cases) != len(cmap) or set(cmap) != {row["paper_id"] for row in papers}
            or set(occurrences) != {row["occurrence_id"] for row in originals}
            or any(case["review_complete"] != "no" or case["human_confirmed"] is not False
                   or case["missed_merge_search_status"] != "incomplete" for case in cases)):
        raise SystemExit("AI provenance or frozen keys differ; no exports written")
    prefix = "Initial review by Codex; author confirmation pending. "
    for name, template, key, fields, mapping in [
        ("id_proposals.csv", "id_annotations.csv", "paper_id", schema["id_fields"], cmap),
        ("occurrence_proposals.csv", "occurrence_annotations.csv", "occurrence_id",
         schema["occurrence_fields"], occurrences),
    ]:
        rows = read_csv(base / "templates" / template)
        for row in rows:
            source = mapping[row[key]]
            for field in fields:
                row[field] = text_value(source.get(field, ""))
            row["annotator"] = "Codex (AI-assisted initial review)"
            row["review_notes"] = prefix + source["review_notes"]
        write_csv(bundle / name, rows)
    titles = {row["paper_id"]: row["title_best"] for row in papers}
    queue = [{
        "paper_id": case["paper_id"], "title": titles[case["paper_id"]],
        "issue_types": " | ".join(case["issue_types"]) or "initially_consistent",
        "identity_proposal": case["identity_label"],
        "metadata_error_occurrences": sum(row["metadata_judgment"] == "metadata_error" for row in case["occurrences"]),
        "metadata_unresolved_occurrences": sum(row["metadata_judgment"] == "unresolved" for row in case["occurrences"]),
        "counterpart_proposals": " | ".join(case["missed_merge_candidate_ids"]),
        "unresolved_counterpart_ids": " | ".join(case["unresolved_candidate_ids"]),
        "review_notes": case["review_notes"], "human_confirmed": "no",
    } for case in cases]
    queue.sort(key=lambda row: (
        0 if row["identity_proposal"] == "false_merge" else
        1 if row["identity_proposal"] == "unresolved" else
        2 if row["counterpart_proposals"] else
        3 if row["metadata_error_occurrences"] else
        4 if row["metadata_unresolved_occurrences"] else 5, int(row["paper_id"])))
    write_csv(bundle / "priority_queue.csv", queue)
    manifest["counts"].update({
        "ai_identity_proposals": dict(Counter(case["identity_label"] for case in cases)),
        "ai_occurrence_metadata_proposals": dict(Counter(row["metadata_judgment"] for row in occurrences.values())),
        "ids_with_counterpart_proposals": sum(bool(case["missed_merge_candidate_ids"]) for case in cases),
        "ids_with_identity_or_metadata_uncertainty": sum(
            case["identity_label"] == "unresolved" or any(row["metadata_judgment"] == "unresolved" for row in case["occurrences"])
            for case in cases),
    })
    for paths, root in [(manifest["files"], bundle), (manifest["reproduction_helpers"], repo)]:
        for name in paths:
            data = (root / name).read_bytes()
            paths[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    (bundle / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print("Refreshed AI exports and hashes; no human annotations or analysis tables edited.")


if __name__ == "__main__":
    main()
