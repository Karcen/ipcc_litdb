#!/usr/bin/env python3
"""Roll completed candidate classifications across consensus-strength rows.

The source tables and case labels remain immutable. Explicit zero cells cover
all eligible labels, including availability/QC states, by geometry and overall.
No pair-level hypothetical count changes are added together.
"""
from __future__ import annotations

import argparse
import collections
from pathlib import Path

import validation_statistics as stats


BASES = {"archived_raw": "archived_raw", "postclassification_qc": "qc_derived"}
LABELS = {"identity": ("same_study", "different_studies", "unresolvable", "unavailable", "no_majority", "qc_excluded"),
          "cross_report_recurrence": ("recurrent", "non_recurrent", "unresolvable", "unavailable", "no_majority", "qc_excluded")}
LIMIT = "Counts describe candidate-pair model classifications only; overlapping endpoints prevent additive shared-count corrections. No population false-split or human-accuracy estimate."


def raw_label(row, field, reviewer):
    if reviewer == "consensus" and row[field + "_decision"] == "no_majority":
        return "no_majority"
    if reviewer != "consensus" and int(row["available_" + reviewer]) != 1:
        return "unavailable"
    return row[field + "_" + reviewer] or "unresolvable"


def roll_up(source, details, coverage, expected_total):
    stats.unique(source, ["arm", "reviewer", "geometry", "field", "classification_basis", "model_label", "consensus_status"])
    stats.unique(details, ["arm", "case_id"])
    stats.unique(coverage, ["arm", "geometry"])
    arms = sorted({r["arm"] for r in coverage})
    geometries = sorted({r["geometry"] for r in coverage})
    if "all" in geometries:
        raise ValueError("source geometry cannot use reserved overall label all")
    expected = {(r["arm"], r["geometry"]): int(r["scheduled_candidate_rows_n"]) for r in coverage}
    for arm in arms:
        if sum(expected[(arm, g)] for g in geometries) != expected_total:
            raise ValueError("candidate geometry coverage does not partition the expected total")
        actual = collections.Counter(r["geometry"] for r in details if r["arm"] == arm)
        if any(actual[g] != expected[(arm, g)] for g in geometries) or set(actual) - set(geometries):
            raise ValueError("candidate detail/geometry coverage differs")
    counts, from_detail = collections.Counter(), collections.Counter()
    for row in source:
        basis, field, value = row["classification_basis"], row["field"], row["model_label"]
        if basis not in BASES or field not in LABELS or value not in LABELS[field] or row["reviewer"] not in stats.REVIEWERS:
            raise ValueError("unexpected candidate basis, field, label, or reviewer")
        if (row["arm"], row["geometry"]) not in expected or int(row["scheduled_geometry_N"]) != expected[(row["arm"], row["geometry"])]:
            raise ValueError("source candidate denominator differs from coverage")
        n = int(row["candidate_pair_rows"])
        if n < 0:
            raise ValueError("negative candidate classification count")
        counts[(row["arm"], row["geometry"], row["reviewer"], field, basis, value)] += n
    for row in details:
        for reviewer in stats.REVIEWERS:
            for field in LABELS:
                for basis in BASES:
                    value = raw_label(row, field, reviewer) if basis == "archived_raw" else stats.label(row, field, reviewer)
                    from_detail[(row["arm"], row["geometry"], reviewer, field, basis, value)] += 1
    if +counts != +from_detail:
        raise ValueError("rolled-up source counts disagree with preserved raw/QC detail labels")
    labels, resolution = [], []
    for arm in arms:
        for geometry in ["all", *geometries]:
            selected = geometries if geometry == "all" else [geometry]
            N = sum(expected[(arm, g)] for g in selected)
            for reviewer in stats.REVIEWERS:
                for field in LABELS:
                    for basis, alias in BASES.items():
                        values = {value: sum(counts[(arm, g, reviewer, field, basis, value)] for g in selected) for value in LABELS[field]}
                        if sum(values.values()) != N:
                            raise ValueError("candidate label partition does not equal its fixed denominator")
                        for value, count in values.items():
                            labels.append({"arm": arm, "reviewer": reviewer, "geometry": geometry, "field": field,
                                           "classification_basis": alias, "source_classification_basis": basis,
                                           "model_label": value, "candidate_pair_rows": count, "candidate_N": N,
                                           "candidate_share": count / N if N else None, "interpretation": LIMIT})
                        resolved = sum(values[value] for value in LABELS[field][:2])
                        for value, count in (("resolved", resolved), ("unresolved", N - resolved)):
                            resolution.append({"arm": arm, "reviewer": reviewer, "geometry": geometry, "field": field,
                                               "classification_basis": alias, "model_label": value,
                                               "candidate_pair_rows": count, "candidate_N": N,
                                               "candidate_share": count / N if N else None,
                                               "interpretation": "Unresolved combines model unresolvable, no-majority, unavailable and QC-excluded states; disaggregated labels remain in candidate_label_totals. " + LIMIT})
    return {"candidate_label_totals": labels, "candidate_resolution_totals": resolution}


def write_totals(root, source_directory, output=None):
    root, directory = Path(root).resolve(), Path(source_directory).resolve()
    manifest_path = directory / "manifest.json"
    source_manifest = stats.read_json(manifest_path)
    if source_manifest.get("status") != "complete_conditional_candidate_classifications":
        raise ValueError("candidate totals require completed candidate classifications")
    original_hashes = {directory / name: digest for name, digest in source_manifest["output_sha256"].items()}
    original_hashes[manifest_path] = stats.sha(manifest_path)
    if any(stats.sha(path) != digest for path, digest in original_hashes.items()):
        raise ValueError("original candidate output hash differs")
    names = ("candidate_geometry_classifications.csv", "candidate_classification_detail.csv", "candidate_review_coverage.csv")
    source, details, coverage = [stats.read_csv(directory / name) for name in names]
    expected = int(source_manifest["scheduled_candidate_cases"])
    tables = roll_up(source, details, coverage, expected)
    stats.COUNT_FIELDS.add("candidate_N")
    tables["validation_summary"] = stats.numeric_registry(tables)
    output = Path(output).resolve() if output else directory / "candidate_totals"
    if output in (root, directory):
        raise ValueError("candidate totals require a separate output directory")
    output.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        stats.write_csv(output / (name + ".csv"), rows, stats.REGISTRY_FIELDS if name == "validation_summary" else None)
    policy = {"derivation": "Sum label counts over all consensus_status rows; preserve reviewer, field and original labels.",
              "basis_aliases": BASES, "zero_cells": "Explicit for every eligible label in each geometry and geometry=all.",
              "overall_partition_N": expected, "full_candidate_frame_N": source_manifest["full_candidate_frame_cases"],
              "detail_crosscheck": "Every raw/QC label count independently reconstructed from preserved per-case detail.",
              "labels_changed": False, "original_outputs_changed": False, "pair_deltas_summed": False, "interpretation": LIMIT}
    stats.dump_json(output / "derivation_policy.json", policy)
    if any(stats.sha(path) != digest for path, digest in original_hashes.items()):
        raise ValueError("original candidate artifacts changed during totals derivation")
    inputs = [manifest_path] + [directory / name for name in names]
    result = {"status": "complete_candidate_label_totals", "run_name": source_manifest["run_name"],
              "scheduled_candidate_cases": expected, "full_candidate_frame_cases": source_manifest["full_candidate_frame_cases"],
              "input_sha256": {str(path.relative_to(root)) if path.is_relative_to(root) else str(path): stats.sha(path) for path in inputs},
              "code_sha256": {path.name: stats.sha(path) for path in (Path(__file__).resolve(), Path(stats.__file__).resolve(), root / "test_candidate_totals.py") if path.exists()},
              "output_sha256": {**{name + ".csv": stats.sha(output / (name + ".csv")) for name in tables}, "derivation_policy.json": stats.sha(output / "derivation_policy.json")},
              "row_counts": {name: len(rows) for name, rows in tables.items()}, "original_outputs_unchanged": True,
              "no_model_calls": True, "no_credential_reads": True, "no_summed_pair_deltas": True, "interpretation": LIMIT}
    stats.dump_json(output / "manifest.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--source-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(write_totals(args.root, args.source_directory, args.output)["status"])


if __name__ == "__main__":
    main()
