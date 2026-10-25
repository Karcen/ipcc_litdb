#!/usr/bin/env python3
"""Supplement completed paired identity comparisons by frozen record count.

The all-ID comparison remains intact. IDs with one citation cannot test whether
multiple citations assigned to that ID identify the same study. This supplement
describes singleton and multi-record outputs separately without repairing or
reclassifying any response.
"""
from __future__ import annotations

import argparse
import collections
from pathlib import Path

import validation_statistics as stats
from paired_codex_comparison import LIMIT


def number(row, key):
    return int(row.get(key) or 0)


def identity_record_count_tables(packet, details, scope):
    records = {case["case_id"]: len(case["records"]) for case in packet["cases"] if case["task"] == "identity"}
    if not records or any(n < 1 for n in records.values()):
        raise ValueError("identity supplement requires nonempty identity cases")
    identity = [row for row in details if row["field"] == "identity"]
    stats.unique(identity, ["reviewer", "case_id"])
    for reviewer in stats.REVIEWERS:
        if {r["case_id"] for r in identity if r["reviewer"] == reviewer} != set(records):
            raise ValueError("identity comparison does not cover the frozen packet for every reviewer")
    annotated = [{**row, "stratum": "singleton" if records[row["case_id"]] == 1 else "multiple_records",
                  "frozen_record_count": records[row["case_id"]]} for row in identity]
    populations, summary, crosstabs = [], [], []
    for stratum in ("singleton", "multiple_records"):
        ns = [n for n in records.values() if (n == 1) == (stratum == "singleton")]
        populations.append({"arm": "blind", "reviewer": "all", "population": scope, "stratum": stratum, "field": "identity",
                            "assigned_ids_n": len(ns), "reference_records_n": sum(ns),
                            "minimum_records_per_id": min(ns) if ns else None, "maximum_records_per_id": max(ns) if ns else None,
                            "ids_with_multiple_records_n": len(ns) if stratum == "multiple_records" else 0,
                            "interpretation": "Structural input stratum: one record cannot test consistency across citations assigned to an ID. All original labels and all-ID tables are retained."})
        for reviewer in stats.REVIEWERS:
            rows = [r for r in annotated if r["reviewer"] == reviewer and r["stratum"] == stratum]
            shared = [r for r in rows if number(r, "shared_available")]
            resolved = [r for r in rows if number(r, "both_resolved")]
            exact = sum(number(r, "exact_agreement") for r in shared)
            rexact = sum(number(r, "exact_agreement") for r in resolved)
            summary.append({"arm": "blind", "reviewer": reviewer, "population": scope, "stratum": stratum, "field": "identity",
                            "scheduled_items_n": len(rows), "codex_available_n": sum(number(r, "codex_available") for r in rows),
                            "deepseek_available_n": sum(number(r, "deepseek_available") for r in rows),
                            "shared_available_n": len(shared), "not_shared_available_n": len(rows) - len(shared),
                            "codex_unresolvable_n": sum(r["codex_label"] == "unresolvable" for r in rows),
                            "deepseek_unresolvable_n": sum(r["deepseek_label"] == "unresolvable" for r in rows),
                            "codex_qc_excluded_n": sum(r["codex_label"] == "qc_excluded" for r in rows),
                            "deepseek_qc_excluded_n": sum(r["deepseek_label"] == "qc_excluded" for r in rows),
                            "deepseek_no_majority_n": sum(r["deepseek_label"] == "no_majority" for r in rows),
                            "deepseek_unavailable_n": sum(r["deepseek_label"] == "unavailable" for r in rows),
                            "both_resolved_n": len(resolved), "noncomparable_resolved_n": len(rows) - len(resolved),
                            "exact_agreement_shared_n": exact, "disagreement_shared_n": len(shared) - exact,
                            "exact_agreement_given_shared": exact / len(shared) if shared else None,
                            "exact_agreement_resolved_n": rexact, "disagreement_resolved_n": len(resolved) - rexact,
                            "exact_agreement_given_resolved": rexact / len(resolved) if resolved else None,
                            "interpretation": "Input record count defines this supplementary stratum; matching abstentions remain separate from resolved agreement. " + LIMIT})
            counts = collections.Counter((r["codex_label"], r["deepseek_label"], number(r, "both_resolved")) for r in shared)
            for (codex, deepseek, both_resolved), count in sorted(counts.items()):
                crosstabs.append({"arm": "blind", "reviewer": reviewer, "population": scope, "stratum": stratum, "field": "identity",
                                  "codex_label": codex, "deepseek_label": deepseek, "both_resolved": both_resolved,
                                  "paired_items_n": count, "interpretation": "Shared-available paired label counts in a structural input stratum; neither axis is reference truth. " + LIMIT})
    return {"identity_record_count_population": populations, "identity_record_count_agreement": summary,
            "identity_record_count_crosstabs": crosstabs, "identity_record_count_detail": annotated}


def write_supplement(root, comparison_directory, packet_path, output=None):
    root, directory, packet_path = Path(root).resolve(), Path(comparison_directory).resolve(), Path(packet_path).resolve()
    source_manifest_path = directory / "manifest.json"
    source_manifest = stats.read_json(source_manifest_path)
    if source_manifest.get("status") != "complete_paired_model_comparison":
        raise ValueError("record-count supplement requires a completed paired comparison")
    expected = source_manifest["input_sha256"].get(str(packet_path.relative_to(root)) if packet_path.is_relative_to(root) else str(packet_path))
    if expected != stats.sha(packet_path):
        raise ValueError("supplement packet differs from the paired frozen input")
    original_hashes = {directory / name: digest for name, digest in source_manifest["output_sha256"].items()}
    original_hashes[source_manifest_path] = stats.sha(source_manifest_path)
    if any(stats.sha(path) != digest for path, digest in original_hashes.items()):
        raise ValueError("original paired result hash differs")
    packet = stats.read_json(packet_path)
    stats.unique(packet["cases"], ["case_id"])
    details = stats.read_csv(directory / "paired_comparison_detail.csv")
    tables = identity_record_count_tables(packet, details, source_manifest["scope"])
    stats.COUNT_FIELDS |= {key for rows in tables.values() for row in rows for key in row if key.endswith("_n")}
    stats.COUNT_FIELDS |= {"minimum_records_per_id", "maximum_records_per_id", "both_resolved"}
    tables["validation_summary"] = stats.numeric_registry(tables)
    output = Path(output).resolve() if output else directory / "identity_record_count_supplement"
    if output in (root, directory):
        raise ValueError("identity supplement requires a separate output directory")
    output.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        stats.write_csv(output / (name + ".csv"), rows, stats.REGISTRY_FIELDS if name == "validation_summary" else None)
    policy = {"selection": "Frozen identity packets classified structurally into exactly one record versus at least two records.",
              "registration_timing": "Supplement requested after inspecting the completed all-ID paired comparison; structural criterion uses only input record counts.",
              "purpose": "Multi-record IDs can test consistency across citations assigned to an ID; singleton IDs cannot.",
              "labels_changed": False, "original_tables_changed": False,
              "population_projection": False, "interpretation": LIMIT}
    stats.dump_json(output / "supplement_policy.json", policy)
    if any(stats.sha(path) != digest for path, digest in original_hashes.items()):
        raise ValueError("original paired artifacts changed during supplementary derivation")
    result = {"status": "complete_structural_identity_supplement", "scope": source_manifest["scope"],
              "input_sha256": {str(path.relative_to(root)) if path.is_relative_to(root) else str(path): stats.sha(path)
                               for path in (packet_path, source_manifest_path, directory / "paired_comparison_detail.csv")},
              "code_sha256": {path.name: stats.sha(path) for path in (Path(__file__).resolve(), Path(stats.__file__).resolve(), root / "test_paired_identity_record_counts.py") if path.exists()},
              "output_sha256": {**{name + ".csv": stats.sha(output / (name + ".csv")) for name in tables}, "supplement_policy.json": stats.sha(output / "supplement_policy.json")},
              "row_counts": {name: len(rows) for name, rows in tables.items()}, "original_results_unchanged": True,
              "no_model_calls": True, "no_credential_reads": True, "labels_changed": False, "interpretation": LIMIT}
    stats.dump_json(output / "manifest.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--comparison-directory", type=Path, required=True)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(write_supplement(args.root, args.comparison_directory, args.packet, args.output)["status"])


if __name__ == "__main__":
    main()
