#!/usr/bin/env python3
"""Register unweighted descriptions of AI proposals, never linkage accuracy.

Units: one frozen paper_id, or one frozen occurrence_id. The paper join is
1:1; occurrence-to-paper is many:1. Every frozen row must match exactly once.
The two sampling selections remain separate. No rows, labels, raw strings,
design weights, database fields or prior registered results are changed.

Uses only the Python standard library and local files. The inherited seed is
20261011; these deterministic counts use no random draws. Bibliographic names
are public citation metadata. No human reviewer identities are collected.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import io
import itertools
import json
from pathlib import Path
import subprocess
import sys


VERSION = "ai-review-descriptive-1.0.0"
SEED = 20261011
OUTPUT = "revision_preparation/annotation/ai_review_summary_v1"
SELECTIONS = {
    "stratified_hash_sample": "probability_sample",
    "targeted_supplement_not_probability_sample": "targeted_supplement",
}
IDENTITY = ("correct_merge", "false_merge", "unresolved")
SHARED = ("same_work", "false_shared", "not_shared", "unresolved")
METADATA = ("correct", "metadata_error", "unresolved")
CAUTIONS = [
    "These are unweighted descriptions of AI-coded proposals, not human-validated labels or population accuracy estimates.",
    "The probability sample has unequal inclusion probabilities; no sampling weights or population extrapolation are used here.",
    "Targeted supplements were selected for anomalies and are never pooled with the probability sample in a reported count.",
    "Every duplicate search remains incomplete. Counterpart proposals do not estimate missed-merge rates, recall or their uncertainty.",
    "A compound extracted entry can contain several works while preserving a real work shared by AR5 and AR6. Identity and shared-link labels answer different questions.",
    "Metadata errors, metadata uncertainty, false merges and counterpart proposals can occur in the same ID. Their counts must not be added as disjoint errors.",
    "An unresolved metadata field is not coded as a confirmed metadata error. Missing enriched fields alone do not establish an error.",
    "No confidence intervals, weighted error estimates, corrected headline estimates or new bibliographic API responses are produced.",
]
DEFINITIONS = {
    "identity_label": "AI proposal about whether the original entries assigned to one sampled ID represent one study under the recorded version policy.",
    "shared_link_label": "AI proposal about the AR5/AR6 matched occurrences already assigned to the sampled ID; this does not adjudicate every counterpart or unsampled ID.",
    "any_metadata_error": "At least one occurrence has metadata_judgment=metadata_error; different erroneous fields can coexist in one occurrence.",
    "any_metadata_unresolved": "At least one occurrence has metadata_judgment=unresolved. This flag does not count every open question or an explicitly uncertain field attached to an otherwise definite error.",
    "counterpart_proposed": "At least one candidate ID is listed in missed_merge_candidate_ids; this is a positive AI proposal within an incomplete duplicate search.",
    "flagged": "Union of non-correct identity proposals, false/unresolved shared links, metadata_error or unresolved occurrence judgments, and positive or explicitly unresolved counterpart IDs.",
    "explicitly_unresolved_occurrences": "Occurrence has the named metadata_unresolved_fields value, regardless of its overall metadata judgment. Unresolved judgments without typed fields appear as unspecified. Field rows overlap.",
    "denominator_ids": "Number of frozen sampled IDs in the specified sampling selection, with no design weights.",
    "denominator_occurrences": "All frozen original entries attached to those sampled IDs; repeated appearances remain separate occurrences.",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def digest_file(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_csv(path):
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        require(all(None not in row for row in rows), "Malformed CSV: " + str(path.name))
        return rows


def unique_map(rows, key):
    result = {row[key]: row for row in rows}
    require(len(result) == len(rows) and all(result), "Missing/duplicate " + key)
    return result


def field_set(value):
    if value is None:
        return set()
    if isinstance(value, list):
        return {str(v).strip() for v in value if str(v).strip()}
    return {v.strip() for v in str(value).split("|") if v.strip()}


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def csv_bytes(rows, fields):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def run(repo, output, check_only):
    base = repo / "revision_preparation/annotation"
    ai = base / "ai_review"
    input_manifest = load_json(base / "input_manifest.json")
    manifest = load_json(ai / "manifest.json")
    proposals = load_json(ai / "proposals.json")
    ledger = load_json(ai / "version_decisions.json")
    schema = load_json(base / "annotation_schema.json")
    fields = tuple(schema["metadata_fields"])
    papers = load_csv(base / "inputs/revision_identity_audit_sample_papers.csv")
    entries = load_csv(base / "inputs/revision_identity_audit_sample_entries.csv")
    pmap = unique_map(papers, "paper_id")
    emap = unique_map(entries, "occurrence_id")
    cases = proposals["cases"]
    cmap = unique_map(cases, "paper_id")
    require(set(cmap) == set(pmap), "AI/frozen paper IDs differ")
    require(set(SELECTIONS) == {p["selection"] for p in papers}, "Unexpected sample selections")
    require(input_manifest["sampling_seed"] == manifest["sampling_seed_unchanged"] == SEED,
            "Frozen seed changed")
    require(ledger["human_case_confirmation"] is False and ledger["analysis_recomputed"] is False,
            "Version ledger must remain an AI-only proposal ledger")

    # Fail before writing anything if the existing package validator rejects it.
    checked = subprocess.run(
        [sys.executable, str(repo / "scripts/check_ai_annotation_proposals.py"), "--repo", str(repo)],
        capture_output=True, text=True, check=False,
    )
    require(checked.returncode == 0, "AI package check failed: " + checked.stdout + checked.stderr)
    package_check = json.loads(checked.stdout)
    package_check.pop("checked_utc", None)  # Replays remain byte-for-byte deterministic.

    protected = {}
    private_protected = {}

    def protect(path, expected=None):
        relative = path.relative_to(repo).as_posix()
        actual = digest_file(path)
        require(expected is None or actual == expected, "Protected input hash differs: " + relative)
        protected[relative] = {"sha256": actual, "bytes": path.stat().st_size}

    for name in ("input_manifest.json", "annotation_schema.json"):
        protect(base / name)
    for record in input_manifest["sources"].values():
        protect(base / "inputs" / record["filename"], record["sha256"])
    for name in ("manifest.json", *manifest["files"]):
        protect(ai / name)
    for record in manifest.get("reproduction_helpers", {}):
        protect(repo / record)
    protect(repo / "scripts/check_ai_annotation_proposals.py")
    protect(repo / "database/ipcc_litdb.sqlite", input_manifest["database_sha256"])

    previous = []
    for relative, table_key, expected_count in (
        ("reproducibility/reference/manifest.json", "tables", 29),
        ("revision_preparation/robustness/output_v1/manifest.json", "registered_tables", 10),
    ):
        path = repo / relative
        prior = load_json(path)
        require(prior["seed"] == SEED and len(prior[table_key]) == expected_count,
                "Prior registered inventory or seed changed: " + relative)
        protect(path)
        for item in prior[table_key]:
            table = path.parent / item["filename"]
            protect(table, item["sha256"])
            rows = load_csv(table)
            if "rows" in item:
                require(len(rows) == item["rows"], "Prior registered row count changed")
            previous.append({"path": table.relative_to(repo).as_posix(), "rows": len(rows),
                             "sha256": item["sha256"]})

    # Human-label work files are optional and private. If present, they must
    # still be blank; no AI proposal is silently imported into those files.
    human_state = []
    for filename, key, editable in (
        ("id_annotations.csv", "paper_id", schema["id_fields"]),
        ("occurrence_annotations.csv", "occurrence_id", schema["occurrence_fields"]),
    ):
        template = base / "templates" / filename
        protect(template)
        for path in (template, base / "work" / filename):
            if not path.exists():
                continue
            rows = load_csv(path)
            unique_map(rows, key)
            nonblank = sum(any(row.get(field, "").strip() for field in editable) for row in rows)
            require(nonblank == 0, "Human annotation fields are no longer blank: " + filename)
            require(digest_file(path) == digest_file(template), "Human work/template bytes differ")
            if path == template:
                human_state.append({"path": path.relative_to(repo).as_posix(), "rows": len(rows),
                                    "nonblank_annotation_rows": nonblank})
            else:
                # These optional, ignored files do not become registered inputs.
                # A clean checkout must replay the same public summary bytes.
                private_protected[path] = digest_file(path)

    diagnostics = []
    seen_occurrences = set()
    for c in cases:
        p = pmap[c["paper_id"]]
        group = SELECTIONS[p["selection"]]
        oo = c["occurrences"]
        require(len(oo) == int(p["occurrences_reviewed"]), "Frozen occurrence count differs")
        for o in oo:
            oid = o["occurrence_id"]
            require(oid not in seen_occurrences and emap[oid]["paper_id"] == c["paper_id"],
                    "Occurrence join is not many:1 with unique occurrence IDs")
            require(emap[oid]["selection"] == p["selection"], "Occurrence selection differs")
            seen_occurrences.add(oid)
            require(field_set(o.get("metadata_unresolved_fields")) <= set(fields),
                    "Invalid unresolved metadata field")
        mc = Counter(o["metadata_judgment"] for o in oo)
        row = {
            "sample_group": group, "paper_id": c["paper_id"], "stratum": p["stratum"],
            "selection": p["selection"], "n_occurrences": len(oo),
            "identity_label": c["identity_label"], "shared_link_label": c["shared_link_label"],
            "metadata_correct_occurrences": mc["correct"],
            "metadata_error_occurrences": mc["metadata_error"],
            "metadata_unresolved_occurrences": mc["unresolved"],
            "any_metadata_error": "yes" if mc["metadata_error"] else "no",
            "any_metadata_unresolved": "yes" if mc["unresolved"] else "no",
            "counterpart_proposed": "yes" if c["missed_merge_candidate_ids"] else "no",
            "counterpart_ids": "|".join(c["missed_merge_candidate_ids"]),
            "unresolved_candidate_ids": "|".join(c.get("unresolved_candidate_ids", [])),
            "duplicate_search_status": c["missed_merge_search_status"],
            "review_complete": c["review_complete"], "human_confirmed": "false",
        }
        for field in fields:
            row["error_" + field + "_occurrences"] = sum(
                o["metadata_judgment"] == "metadata_error"
                and field in field_set(o["metadata_error_fields"]) for o in oo
            )
        row["flagged"] = "yes" if (
            c["identity_label"] != "correct_merge"
            or c["shared_link_label"] in {"false_shared", "unresolved"}
            or mc["metadata_error"] or mc["unresolved"] or c["missed_merge_candidate_ids"]
            or c.get("unresolved_candidate_ids")
        ) else "no"
        diagnostics.append(row)
    require(seen_occurrences == set(emap), "Frozen entries are not covered exactly once")
    diagnostics.sort(key=lambda row: (row["sample_group"], int(row["paper_id"])))

    category_rows, occurrence_rows, cross_rows, metadata_rows = [], [], [], []
    summaries = {}
    cross_fields = ("identity_label", "shared_link_label", "any_metadata_error",
                    "any_metadata_unresolved", "counterpart_proposed")
    for group in SELECTIONS.values():
        dd = [d for d in diagnostics if d["sample_group"] == group]
        cc = [c for c in cases if SELECTIONS[pmap[c["paper_id"]]["selection"]] == group]
        oo = [o for c in cc for o in c["occurrences"]]
        nids, nocc = len(dd), len(oo)
        summaries[group] = {"sampled_ids": nids, "occurrences": nocc}
        categories = (("identity_label", IDENTITY), ("shared_link_label", SHARED),
                      ("any_metadata_error", ("no", "yes")),
                      ("any_metadata_unresolved", ("no", "yes")),
                      ("counterpart_proposed", ("no", "yes")), ("flagged", ("no", "yes")))
        for family, values in categories:
            counts = Counter(d[family] for d in dd)
            summaries[group][family] = {value: counts[value] for value in values}
            require(sum(counts.values()) == nids, "ID category denominator mismatch")
            for value in values:
                category_rows.append({"sample_group": group, "category_family": family,
                                      "category": value, "n_ids": counts[value],
                                      "denominator_ids": nids})
        metadata_counts = Counter(o["metadata_judgment"] for o in oo)
        summaries[group]["occurrence_metadata"] = {k: metadata_counts[k] for k in METADATA}
        for judgment in METADATA:
            occurrence_rows.append({"sample_group": group, "metadata_judgment": judgment,
                                    "n_occurrences": metadata_counts[judgment],
                                    "denominator_occurrences": nocc})
        cross = Counter(tuple(d[field] for field in cross_fields) for d in dd)
        require(sum(cross.values()) == nids, "Cross-tab denominator mismatch")
        for combination in itertools.product(IDENTITY, SHARED, ("no", "yes"),
                                             ("no", "yes"), ("no", "yes")):
            cross_rows.append(dict(sample_group=group, **dict(zip(cross_fields, combination)),
                                   n_ids=cross[combination], denominator_ids=nids))
        # Metadata fields overlap, so each row keeps its own explicit denominator.
        for field in (*fields, "unspecified"):
            error_occ, error_ids, unresolved_occ, unresolved_ids = 0, set(), 0, set()
            for c in cc:
                for o in c["occurrences"]:
                    ef = field_set(o["metadata_error_fields"])
                    uf = field_set(o.get("metadata_unresolved_fields"))
                    if o["metadata_judgment"] == "metadata_error" and field in ef:
                        error_occ += 1
                        error_ids.add(c["paper_id"])
                    # Explicit unresolved fields can coexist with a definite error.
                    if field in uf or (field == "unspecified" and not uf
                                         and o["metadata_judgment"] == "unresolved"):
                        unresolved_occ += 1
                        unresolved_ids.add(c["paper_id"])
            metadata_rows.append({"sample_group": group, "metadata_field": field,
                                  "error_occurrences": error_occ, "ids_with_error": len(error_ids),
                                  "explicitly_unresolved_occurrences": unresolved_occ,
                                  "ids_with_explicit_uncertainty": len(unresolved_ids),
                                  "denominator_occurrences": nocc, "denominator_ids": nids})

    require(summaries["probability_sample"]["sampled_ids"] == 319
            and summaries["targeted_supplement"]["sampled_ids"] == 12,
            "Frozen probability/targeted partition changed")
    summary = {
        "analysis_version": VERSION, "seed": SEED, "random_draws_used": False,
        "review_origin": "AI-assisted initial textual/source review by Codex and delegated Codex agents",
        "human_validated": False, "population_accuracy_estimated": False,
        "weighted_estimates_generated": False, "confidence_intervals_generated": False,
        "headline_estimates_corrected": False, "completed_human_reviews": 0,
        "completed_exhaustive_duplicate_searches": 0,
        "source_artifact_version": manifest["artifact_version"],
        "groups": summaries, "definitions": DEFINITIONS, "interpretation": CAUTIONS,
    }
    validation = {
        "status": "passed", "package_check": package_check,
        "joins": {"paper_to_proposal": "1:1, exact 331-ID match",
                  "occurrence_to_paper": "many:1, all 801 unique entries retained"},
        "prior_registered_tables": previous, "human_label_state": human_state,
        "optional_local_work_policy": "If present, require unchanged blank templates; these private files are not summary inputs.",
        "dropped_ids": 0, "dropped_occurrences": 0,
        "raw_strings_modified": False, "human_labels_modified": False,
    }
    files = {
        "ai_review_id_category_counts.csv": csv_bytes(category_rows, tuple(category_rows[0])),
        "ai_review_occurrence_category_counts.csv": csv_bytes(occurrence_rows, tuple(occurrence_rows[0])),
        "ai_review_id_cross_tabs.csv": csv_bytes(cross_rows, tuple(cross_rows[0])),
        "ai_review_id_diagnostics.csv": csv_bytes(diagnostics, tuple(diagnostics[0])),
        "ai_review_flagged_ids.csv": csv_bytes([d for d in diagnostics if d["flagged"] == "yes"],
                                               tuple(diagnostics[0])),
        "ai_review_metadata_field_counts.csv": csv_bytes(metadata_rows, tuple(metadata_rows[0])),
        "summary.json": json_bytes(summary), "validation_report.json": json_bytes(validation),
    }
    registered = []
    for name, data in files.items():
        record = {"filename": name, "sha256": digest_bytes(data), "bytes": len(data)}
        if name.endswith(".csv"):
            record["rows"] = len(list(csv.DictReader(io.StringIO(data.decode("utf-8")))))
        registered.append(record)
    script = Path(__file__).resolve()
    registry = {
        "schema_version": 1, "analysis_version": VERSION, "seed": SEED,
        "random_draws_used": False, "units": ["paper_id", "occurrence_id"],
        "source_artifact_version": manifest["artifact_version"],
        "source_updated_utc": manifest["updated_utc"],
        "database_sha256": input_manifest["database_sha256"],
        "script": {"path": script.relative_to(repo).as_posix(), "sha256": digest_file(script)},
        "inputs_and_protected_files": protected, "registered_outputs": registered,
        "human_validated": False, "weighted_estimates_generated": False,
        "confidence_intervals_generated": False, "headline_estimates_corrected": False,
        "database_modified": False, "prior_registered_tables_modified": False,
        "definitions": DEFINITIONS, "interpretation": CAUTIONS,
        "reproduce": "python3 scripts/summarize_ai_annotation_proposals.py --check",
    }
    files["manifest.json"] = json_bytes(registry)
    # Recheck inputs immediately before the only write operation.
    for name, record in protected.items():
        require(digest_file(repo / name) == record["sha256"], "Input changed during run: " + name)
    for path, expected in private_protected.items():
        require(digest_file(path) == expected, "Private human-label work changed during run")
    protected_paths = {(repo / name).resolve() for name in protected}
    require(all((output / name).resolve() not in protected_paths for name in files),
            "Output would overwrite a protected input")
    if check_only:
        for name, data in files.items():
            require((output / name).read_bytes() == data, "Registered output differs: " + name)
    else:
        if output.exists() and any(output.iterdir()):
            old = load_json(output / "manifest.json")
            require(old.get("analysis_version") == VERSION, "Output directory belongs to another analysis")
        output.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (output / name).write_bytes(data)
    print(json.dumps({"status": "passed", "check_only": check_only,
                      "analysis_version": VERSION, "groups": summaries,
                      "prior_tables_verified": len(previous), "registered_files": len(files),
                      "human_validated": False, "weighted_estimates_generated": False}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path,
                        help="Separate output directory; defaults to the dedicated registry under the repo")
    parser.add_argument("--check", action="store_true", help="Replay and compare without writing")
    args = parser.parse_args()
    repo = args.repo.resolve()
    try:
        run(repo, (args.output or repo / OUTPUT).resolve(), args.check)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": str(error),
                          "human_validated": False, "weighted_estimates_generated": False}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
