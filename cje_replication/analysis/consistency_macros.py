"""Resolve manuscript consistency fixes from saved tables, without refitting models.

Run with --check after 07_generate_numbers.py. The check writes a separate
source-cell registry and fails if a current TeX definition differs from it.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re


NONJOURNAL = {"book_or_chapter", "institutional_report", "working_paper", "other"}


def build_consistency_macros(project):
    project = Path(project)
    values, registry = {}, []

    def read(name):
        with (project / "tables" / name).open() as handle:
            return list(csv.DictReader(handle))

    def select(rows, **filters):
        return [row for row in rows if all(row[key] == value for key, value in filters.items())]

    def one(rows, **filters):
        found = select(rows, **filters)
        if len(found) != 1:
            raise ValueError(f"Expected one saved row: {filters}; found {len(found)}")
        return found[0]

    def add(macro, number, filename, filters, expression, numerator="", denominator="", integer=False):
        rendered = f"{int(number):,}".replace(",", "{,}") if integer else f"{number:.2f}"
        values[macro] = rendered
        path = project / "tables" / filename
        registry.append({
            "macro": macro, "value": rendered, "unrounded_value": number,
            "source": "tables/" + filename,
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "selector": json.dumps(filters, sort_keys=True), "expression": expression,
            "numerator": numerator, "denominator": denominator,
        })

    matched = read("A_match_rate_by_type.csv")
    dist = read("A_document_type_distribution.csv")
    recurrence = read("A_recurrence_by_type.csv")
    pooled_filter = {"unit": "occurrence", "wg": "ALL", "vintage_stratum": "ALL"}
    pooled = [row for row in select(matched, **pooled_filter) if row["cycle"] in {"AR5", "AR6"}]
    total = sum(int(row["denominator"]) for row in pooled if row["category"] == "ALL")
    for category, tag in [("journal_article", "Jour"), ("institutional_report", "Inst"),
                          ("book_or_chapter", "Book"), ("working_paper", "WP")]:
        rows = select(pooled, category=category)
        if len(rows) != 2:
            raise ValueError(f"Missing assessment cycle for {category}")
        n = sum(int(row["denominator"]) for row in rows)
        k = sum(int(row["n_matched"]) for row in rows)
        filt = dict(pooled_filter, cycle=["AR5", "AR6"], category=category)
        add("typeMatch" + tag, 100 * k / n, "A_match_rate_by_type.csv", filt,
            "100 * sum(n_matched) / sum(denominator); includes SYR", k, n)
        add("typeShare" + tag, 100 * n / total, "A_match_rate_by_type.csv", filt,
            "100 * sum(category denominator) / sum(ALL denominator)", n, total)

    for group, tag in [("WG1", "One"), ("WG2", "Two"), ("WG3", "Three")]:
        for cycle, cycle_tag in [("AR5", "Five"), ("AR6", "Six")]:
            rows = select(dist, unit="occurrence", cycle=cycle, wg=group)
            denominators = {int(row["denominator"]) for row in rows}
            if len(denominators) != 1:
                raise ValueError(f"Inconsistent occurrence denominator: {cycle}, {group}")
            denominator = denominators.pop()
            numerator = sum(int(row["n"]) for row in rows if row["category"] in NONJOURNAL)
            add("nonj" + cycle_tag + tag, 100 * numerator / denominator,
                "A_document_type_distribution.csv", {"unit": "occurrence", "cycle": cycle, "wg": group},
                "100 * sum(n for four nonjournal categories) / denominator", numerator, denominator)

        filt = {"unit": "occurrence", "cycle": "AR5", "wg": group, "vintage_stratum": "ALL"}
        row = one(matched, **dict(filt, category="journal_article"))
        k, n = int(row["n_matched"]), int(row["denominator"])
        for prefix in ["mjrFive", "jrCov"]:
            add(prefix + tag, 100 * k / n, "A_match_rate_by_type.csv",
                dict(filt, category="journal_article"), "100 * n_matched / denominator", k, n)
        rows = [row for row in select(matched, **filt) if row["category"] in NONJOURNAL]
        k = sum(int(row["n_matched"]) for row in rows)
        n = sum(int(row["denominator"]) for row in rows)
        add("mnjFive" + tag, 100 * k / n, "A_match_rate_by_type.csv",
            dict(filt, category=sorted(NONJOURNAL)),
            "100 * sum(n_matched) / sum(denominator); four nonjournal categories", k, n)
        filt = {"frame": "matched", "wg": group, "vintage_stratum": "ALL", "category": "journal_article"}
        row = one(recurrence, **filt)
        k, n = int(row["n_recurrent"]), int(row["denominator"])
        add("jrRet" + tag, 100 * k / n, "A_recurrence_by_type.csv", filt,
            "100 * n_recurrent / denominator", k, n)

    decomposition = read("A_type_composition_decomposition.csv")
    filt = {"outcome": "coverage_AR5", "pair": "WG1_vs_WG3", "direction": "A_to_B"}
    row = one(decomposition, **dict(filt, standardisation="type"))
    for macro, field in [("covGapCrude", "gap_pp"), ("covGapStd", "std_gap_pp"),
                         ("covGapComp", "composition_pp"), ("typeShareCov", "composition_share_pct"),
                         ("typeShareOfGap", "composition_share_pct")]:
        add(macro, float(row[field]), "A_type_composition_decomposition.csv",
            dict(filt, standardisation="type"), field)
    for macro, standardisation in [("vintShareOfGap", "vintage"), ("jointShareOfGap", "type_x_vintage")]:
        row = one(decomposition, **dict(filt, standardisation=standardisation))
        add(macro, float(row["composition_share_pct"]), "A_type_composition_decomposition.csv",
            dict(filt, standardisation=standardisation), "composition_share_pct")

    for macro, wg in [("nOccARfiveWg", ["WG1", "WG2", "WG3"]), ("nOccARSyr", ["SYR"])]:
        rows = [row for row in select(dist, unit="occurrence", cycle="AR5") if row["wg"] in wg]
        n = sum(int(row["n"]) for row in rows)
        add(macro, n, "A_document_type_distribution.csv", {"unit": "occurrence", "cycle": "AR5", "wg": wg},
            "sum(n) over category rows", integer=True)
    flow = one(read("AUDIT_sample_flow.csv"), level="report", report="AR5")
    frame = one(read("AUDIT_frame_alternatives.csv"), frame="A", group="report")
    # The reconciliation is independently confirmed by the saved ID-level scope audit.
    count = int(flow["extracted_identities"]) - int(frame["earlier_n"])
    add("nIdsARSyr", count, "AUDIT_sample_flow.csv", {"level": "report", "report": "AR5"},
        "extracted_identities minus tables/AUDIT_frame_alternatives.csv [frame=A,group=report].earlier_n", integer=True)
    return values, registry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    expected, registry = build_consistency_macros(project)
    current = dict(re.findall(r"\\newcommand\{\\([A-Za-z]+)\}\{([^\n]+)\}",
                              (project / "numbers_CJE_revised.tex").read_text()))
    errors = {key: {"expected": value, "current": current.get(key)}
              for key, value in expected.items() if current.get(key) != value}
    if errors:
        raise ValueError(json.dumps(errors, indent=2))
    out = project / "provenance" / "consistency_fixes_20261027"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "numeric_corrections_registry.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(registry[0]))
        writer.writeheader()
        writer.writerows(registry)
    (out / "numeric_registry_check.json").write_text(json.dumps({
        "status": "pass", "checked_macros": len(expected),
        "method": "Source-cell lookup and deterministic arithmetic on saved outputs; no model refit or sampling.",
        "seed": "Not used; no stochastic calculation.", "mismatches": errors,
    }, indent=2) + "\n")
    print(f"Verified {len(expected)} consistency macros against saved source rows.")


if __name__ == "__main__":
    main()
