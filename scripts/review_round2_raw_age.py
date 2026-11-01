#!/usr/bin/env python3
"""Raw-reference age, fractional working-group composition and outlet bounds.

Source unit: immutable occurrence_id; occurrence -> chapter, paper and source
joins are many-to-one. This is a fixed-snapshot descriptive cross-section by
report, with repeated assigned IDs and catalogue units explicitly retained.
Public bibliographic metadata are not participant-level research data.
All raw strings and prior archives remain immutable. No API is used.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import sqlite3
import sys

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "reproducibility"))
sys.path.insert(0, str(REPO / "revision_preparation/robustness"))
import numpy as np
from coverage_robustness import metrics, raw_year, source_year
from revision_age_networks import CUTOFFS, year
from revision_outlets import basic_title, conservative_title

VERSION = "round2-raw-age-1.0.0"
SEED = 20261011
REPORTS = ("AR5", "AR6")
GROUPS = ("WG1", "WG2", "WG3")
COHORTS = ("all_extracted", "matched_occurrences")
YEAR_FIELDS = ("raw_leading_year", "preferred_year")
ORIGINS = ("source_publication_year", "documented_cutoff_year")
WEIGHTS = ("occurrence", "equal_assigned_ID")
REASONS = ("included", "missing_or_ambiguous_origin", "missing_cited_year",
           "cited_year_after_origin", "age_over_60")
SQL = """SELECT r.occurrence_id,r.chapter_id,r.paper_id,c.report_id,w.wg_label,
 r.match_status,r.raw_reference,r.source_id AS occurrence_source_id,
 c.source_id AS chapter_source_id,s.publication_year AS source_year,
 p.year_best,p.journal_best,w.report_id AS working_group_report
 FROM references_occurrence r JOIN chapters c ON c.chapter_id=r.chapter_id
 JOIN working_groups w ON w.wg_id=c.wg_id
 JOIN papers p ON p.paper_id=r.paper_id
 LEFT JOIN sources s ON s.source_id=c.source_id
 WHERE c.report_id IN ('AR5','AR6') ORDER BY c.report_id,r.occurrence_id"""


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def csv_bytes(rows):
    if not rows:
        raise ValueError("Every output table must have explicit populated rows")
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def archive_gate(database):
    """Validate all older archived CSVs and retain their exact current hashes."""
    locations = (
        (REPO / "reproducibility/reference/manifest.json", "tables", 29),
        (REPO / "revision_preparation/robustness/output_v1/manifest.json", "registered_tables", 10),
        (REPO / "revision_preparation/annotation/ai_review_summary_v1/manifest.json", "registered_outputs", 6),
    )
    protected = {database: sha256(database)}
    counts = []
    for manifest_path, key, expected_csvs in locations:
        data = json.loads(manifest_path.read_text())
        entries = data[key]
        actual_csvs = sum(item["filename"].endswith(".csv") for item in entries)
        if actual_csvs != expected_csvs:
            raise ValueError(f"Unexpected archived table count: {manifest_path}")
        protected[manifest_path] = sha256(manifest_path)
        for item in entries:
            path = manifest_path.parent / item["filename"]
            actual = sha256(path)
            if actual != item["sha256"]:
                raise ValueError(f"Archived output digest mismatch: {path}")
            protected[path] = actual
        counts.append({"manifest":manifest_path.relative_to(REPO).as_posix(),
                       "csv_tables_checked":actual_csvs})
    reference = json.loads(locations[0][0].read_text())
    if protected[database] != reference["database_sha256"]:
        raise ValueError("Database is not the archived corrected v1.2.0 snapshot")
    if np.__version__ != reference["numpy_version"]:
        raise ValueError("Use the pinned NumPy 2.2.6 analysis environment")
    helpers = [REPO / "revision_preparation/robustness/coverage_robustness.py",
               REPO / "reproducibility/revision_age_networks.py",
               REPO / "reproducibility/revision_outlets.py"]
    protected.update({p:sha256(p) for p in helpers})
    return protected, counts


def unchanged(protected):
    for path, expected in protected.items():
        if sha256(path) != expected:
            raise ValueError(f"Protected input/archive changed: {path}")


def load_occurrences(database):
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as con:
        con.execute("PRAGMA query_only=ON")
        con.execute("PRAGMA temp_store=MEMORY")
        con.row_factory = sqlite3.Row
        for child, key, parent, parent_key in (
            ("references_occurrence","chapter_id","chapters","chapter_id"),
            ("references_occurrence","paper_id","papers","paper_id"),
            ("chapters","wg_id","working_groups","wg_id"),
        ):
            missing = con.execute(f"SELECT COUNT(*) FROM {child} a LEFT JOIN {parent} b ON a.{key}=b.{parent_key} WHERE b.{parent_key} IS NULL").fetchone()[0]
            if missing:
                raise ValueError(f"Unmatched many-to-one attachment: {child}->{parent}: {missing}")
        rows = [dict(r) for r in con.execute(SQL)]
    if len({r["occurrence_id"] for r in rows}) != len(rows):
        raise ValueError("Occurrence ID is not unique after many-to-one joins")
    if any(not r["occurrence_id"] or r["paper_id"] is None or not r["chapter_id"] for r in rows):
        raise ValueError("Missing source-unit or grouping key")
    if any(r["working_group_report"] != r["report_id"] for r in rows):
        raise ValueError("Chapter working-group report mismatch")
    with (REPO / "reproducibility/reference/coverage.csv").open(newline="") as f:
        reference = {r["report_id"]:r for r in csv.DictReader(f)}
    for report in REPORTS:
        rr = [r for r in rows if r["report_id"] == report]
        if len(rr) != int(reference[report]["reference_occurrences"]):
            raise ValueError("Joined report occurrence count differs from archived inventory")
        if len({r["paper_id"] for r in rr}) != int(reference[report]["distinct_papers"]):
            raise ValueError("Report assigned-ID count differs from archived inventory")
    for r in rows:
        r["raw_leading_year"] = raw_year(r["raw_reference"])
        r["preferred_year"] = year(r["year_best"])
        r["source_publication_year"] = source_year(r["source_year"])
        date = CUTOFFS.get(r["report_id"], {}).get(r["wg_label"])
        r["documented_cutoff_year"] = int(date[:4]) if date else None
    return rows


def age_status(row, field, origin):
    base, cited = row[origin], row[field]
    if base is None:
        return "missing_or_ambiguous_origin", None
    if cited is None:
        return "missing_cited_year", None
    if cited > base:
        return "cited_year_after_origin", None
    if base - cited > 60:
        return "age_over_60", None
    return "included", base - cited


def records_for(rows, field, origin):
    records, counts = [], Counter()
    for r in rows:
        reason, age = age_status(r, field, origin)
        counts[reason] += 1
        if reason == "included":
            records.append({"paper_id":r["paper_id"], "chapter_id":r["chapter_id"],
                            "wg_label":r["wg_label"], "age":age,
                            "occurrence_id":r["occurrence_id"]})
    if sum(counts.values()) != len(rows):
        raise ValueError("Age filter did not account for every selected occurrence")
    return records, counts


def record_weights(records, weighting):
    if weighting == "occurrence":
        return np.ones(len(records), dtype=float)
    counts = Counter(r["paper_id"] for r in records)
    return np.array([1 / counts[r["paper_id"]] for r in records])


def histogram(records, weighting):
    return np.bincount([r["age"] for r in records],
                       weights=record_weights(records, weighting), minlength=61)


def decompose(w_a, w_b, mu_a, mu_b):
    observed_a = float(np.dot(w_a, mu_a))
    observed_b = float(np.dot(w_b, mu_b))
    within = float(np.dot((w_a + w_b) / 2, mu_b - mu_a))
    composition = float(np.dot((mu_a + mu_b) / 2, w_b - w_a))
    delta = observed_b - observed_a
    residual = delta - within - composition
    if abs(residual) > 1e-10:
        raise ValueError("Kitagawa decomposition does not reconstruct the pooled change")
    return {"AR5_observed":observed_a,"AR6_observed":observed_b,"AR6_minus_AR5":delta,
            "AR6_at_AR5_composition":float(np.dot(w_a, mu_b)),
            "AR5_at_AR6_composition":float(np.dot(w_b, mu_a)),
            "within_component_symmetric":within,"composition_component_symmetric":composition,
            "reconstruction_residual":residual}


def fractional_groups(records, weighting):
    """Pooled weights are assigned before grouping, including overlapping IDs."""
    weights = record_weights(records, weighting)
    total = float(weights.sum())
    result = {}
    for group in GROUPS:
        take = np.array([r["wg_label"] == group for r in records])
        hist = np.bincount([r["age"] for r in records], weights=weights * take, minlength=61)
        if hist.sum() <= 0:
            raise ValueError("A working group has no included ages for decomposition")
        result[group] = {"fraction":float(hist.sum() / total),"hist":hist,
                         "metrics":metrics(hist)}
    if not math.isclose(sum(v["fraction"] for v in result.values()), 1, abs_tol=1e-12):
        raise ValueError("Working-group composition does not sum to one")
    pooled = metrics(histogram(records, weighting))
    for metric in ("mean_age", "recent_le5"):
        reconstructed = sum(v["fraction"] * v["metrics"][metric] for v in result.values())
        if not math.isclose(reconstructed, pooled[metric], rel_tol=1e-11, abs_tol=1e-11):
            raise ValueError("Fractional group estimates fail to reconstruct pooled metric")
    return result


def outlet_bounds(counts, unknown):
    n = sum(counts) + unknown
    if not n:
        raise ValueError("Empty fixed outlet population")
    squares = sum(k*k for k in counts)
    largest = max(counts, default=0)
    h_min = (squares + unknown) / n**2
    h_max = ((largest + unknown)**2 + squares - largest**2) / n**2
    return {"HHI_min":h_min,"HHI_max":h_max,"D2_min":1/h_max,"D2_max":1/h_min}


def self_tests():
    assert raw_year("Smith, 2012a: A title") == 2012
    assert raw_year("x"*300 + " 2012") is None
    assert source_year("2013--2014") is None
    assert source_year("2014") == 2014
    fixture = [{"paper_id":1,"age":0,"wg_label":"WG1"},
               {"paper_id":1,"age":10,"wg_label":"WG2"},
               {"paper_id":2,"age":20,"wg_label":"WG1"},
               {"paper_id":3,"age":5,"wg_label":"WG3"}]
    groups = fractional_groups(fixture, "equal_assigned_ID")
    assert math.isclose(groups["WG1"]["fraction"], .5)
    assert math.isclose(groups["WG2"]["fraction"], 1/6)
    assert math.isclose(metrics(histogram(fixture,"equal_assigned_ID"))["mean_age"],10)
    import itertools
    exhaustive = []
    for allocation in itertools.product(range(3), repeat=4):
        if sum(allocation) == 2:
            cc = [2+allocation[0],1+allocation[1],allocation[2],allocation[3]]
            exhaustive.append(sum(x*x for x in cc)/25)
    bounds = outlet_bounds([2,1],2)
    assert math.isclose(bounds["HHI_min"],min(exhaustive))
    assert math.isclose(bounds["HHI_max"],max(exhaustive))
    assert outlet_bounds([],5)["D2_min"] == 1
    assert outlet_bounds([],5)["D2_max"] == 5
    result = decompose(np.array([.4,.3,.3]),np.array([.2,.5,.3]),
                       np.array([1.,3.,4.]),np.array([2.,6.,5.]))
    assert math.isclose(result["AR5_observed"],2.5)
    assert math.isclose(result["AR6_observed"],4.9)
    return {"raw_parser_boundaries":True,"ambiguous_source_guard":True,
            "fractional_overlapping_ID_fixture":True,
            "exhaustive_small_unknown_outlet_assignments":True,
            "kitagawa_reconstruction_fixture":True}


def build(rows):
    summaries, ecdfs, paired, coverage, group_rows, decomposition = [],[],[],[],[],[]
    audit = []
    for r in rows:
        item = {k:r[k] for k in ("occurrence_id","chapter_id","paper_id","report_id",
                "wg_label","match_status","chapter_source_id","occurrence_source_id",
                "raw_leading_year","preferred_year","source_publication_year","documented_cutoff_year")}
        item["raw_reference_sha256_utf8"] = hashlib.sha256(str(r["raw_reference"] or "").encode()).hexdigest()
        for field in YEAR_FIELDS:
            for origin in ORIGINS:
                item[field + "__" + origin + "__status"] = age_status(r,field,origin)[0]
        audit.append(item)
    populations = {}
    for report in REPORTS:
        for cohort in COHORTS:
            selected = [r for r in rows if r["report_id"] == report and
                        (cohort == "all_extracted" or r["match_status"] in {"matched_doi","matched_title"})]
            for group in ("All",)+GROUPS:
                rr = [r for r in selected if group == "All" or r["wg_label"] == group]
                for field in YEAR_FIELDS:
                    for origin in ORIGINS:
                        records, exclusions = records_for(rr,field,origin)
                        for weighting in WEIGHTS:
                            key = {"report":report,"cohort":cohort,"working_group":group,
                                   "cited_year_field":field,"age_origin":origin,"weighting":weighting}
                            hist = histogram(records,weighting)
                            stats = metrics(hist)
                            if stats is None:
                                raise ValueError("Empty requested age population")
                            summaries.append(key | {"selected_occurrences":len(rr),
                                "selected_assigned_IDs":len({r["paper_id"] for r in rr}),
                                "included_occurrences":len(records),
                                "included_assigned_IDs":len({r["paper_id"] for r in records}),
                                **{reason:exclusions[reason] for reason in REASONS},**stats})
                            total = float(hist.sum())
                            cumulative = np.cumsum(hist)
                            for age in range(61):
                                ecdfs.append(key | {"age":age,"weight_at_age":float(hist[age]),
                                    "weight_denominator":total,"fraction_at_age":float(hist[age]/total),
                                    "empirical_CDF":float(cumulative[age]/total)})
                for origin in ORIGINS:
                    both = [r for r in rr if all(age_status(r,f,origin)[0] == "included" for f in YEAR_FIELDS)]
                    for weighting in WEIGHTS:
                        stats = {f:metrics(histogram(records_for(both,f,origin)[0],weighting)) for f in YEAR_FIELDS}
                        paired.append({"report":report,"cohort":cohort,"working_group":group,
                            "age_origin":origin,"weighting":weighting,"paired_occurrences":len(both),
                            "paired_assigned_IDs":len({r["paper_id"] for r in both}),
                            "raw_mean_age":stats["raw_leading_year"]["mean_age"],
                            "preferred_mean_age":stats["preferred_year"]["mean_age"],
                            "raw_minus_preferred_mean":stats["raw_leading_year"]["mean_age"]-stats["preferred_year"]["mean_age"],
                            "raw_recent_le5":stats["raw_leading_year"]["recent_le5"],
                            "preferred_recent_le5":stats["preferred_year"]["recent_le5"]})
            for field in YEAR_FIELDS:
                for origin in ORIGINS:
                    all_records,all_exclusions = records_for(selected,field,origin)
                    single_selected = [r for r in selected if r["wg_label"] in GROUPS]
                    records,exclusions = records_for(single_selected,field,origin)
                    coverage.append({"report":report,"cohort":cohort,"cited_year_field":field,
                        "age_origin":origin,"all_selected_occurrences":len(selected),
                        "single_WG_selected_occurrences":len(single_selected),
                        "other_group_selected_occurrences":len(selected)-len(single_selected),
                        "all_included_occurrences":len(all_records),"single_WG_included_occurrences":len(records),
                        "other_group_included_occurrences":len(all_records)-len(records),
                        "all_included_assigned_IDs":len({r["paper_id"] for r in all_records}),
                        "single_WG_included_assigned_IDs":len({r["paper_id"] for r in records})})
                    for weighting in WEIGHTS:
                        groups = fractional_groups(records,weighting)
                        populations[(report,cohort,field,origin,weighting)] = groups
                        weights = record_weights(records,weighting)
                        for g in GROUPS:
                            gr = [r for r in records if r["wg_label"] == g]
                            v = groups[g]
                            group_rows.append({"report":report,"cohort":cohort,"cited_year_field":field,
                                "age_origin":origin,"weighting":weighting,"working_group":g,
                                "included_occurrences":len(gr),"distinct_assigned_IDs_in_group":len({r["paper_id"] for r in gr}),
                                "fractional_pooled_weight":v["metrics"]["weight_denominator"],
                                "pooled_weight_denominator":float(weights.sum()),"group_weight_fraction":v["fraction"],
                                "within_group_mean_age":v["metrics"]["mean_age"],
                                "within_group_recent_le5":v["metrics"]["recent_le5"]})
    for cohort in COHORTS:
        for field in YEAR_FIELDS:
            for origin in ORIGINS:
                for weighting in WEIGHTS:
                    ga,gb = (populations[(r,cohort,field,origin,weighting)] for r in REPORTS)
                    wa,wb = (np.array([d[g]["fraction"] for g in GROUPS]) for d in (ga,gb))
                    for metric in ("mean_age","recent_le5"):
                        ma,mb = (np.array([d[g]["metrics"][metric] for g in GROUPS]) for d in (ga,gb))
                        decomposition.append({"cohort":cohort,"cited_year_field":field,"age_origin":origin,
                            "weighting":weighting,"metric":metric,**decompose(wa,wb,ma,mb)})
    indexed = {(r["report"],r["cohort"],r["working_group"],r["cited_year_field"],r["age_origin"],r["weighting"]):r for r in summaries}
    differences=[]
    for cohort in COHORTS:
        for group in ("All",)+GROUPS:
            for field in YEAR_FIELDS:
                for origin in ORIGINS:
                    for weighting in WEIGHTS:
                        aa,bb = (indexed[(r,cohort,group,field,origin,weighting)] for r in REPORTS)
                        differences.append({"cohort":cohort,"working_group":group,"cited_year_field":field,
                            "age_origin":origin,"weighting":weighting,
                            "AR5_mean_age":aa["mean_age"],"AR6_mean_age":bb["mean_age"],
                            "mean_age_difference":bb["mean_age"]-aa["mean_age"],
                            "AR5_recent_le5":aa["recent_le5"],"AR6_recent_le5":bb["recent_le5"],
                            "recent_le5_difference":bb["recent_le5"]-aa["recent_le5"]})
    # Paired preferred-year summaries must reproduce the older implementation.
    with (REPO / "reproducibility/reference/revision_age_summary.csv").open(newline="") as f:
        baseline = list(csv.DictReader(f))
    baseline_checks=0
    for r in baseline:
        if r["report"] not in REPORTS:
            continue
        origin = {"source_year":"source_publication_year","cutoff_year":"documented_cutoff_year"}[r["basis"]]
        cohort = "all_extracted" if r["cohort"] == "extracted" else "matched_occurrences"
        weighting = "occurrence" if r["weighting"] == "occurrence" else "equal_assigned_ID"
        observed = indexed[(r["report"],cohort,"All","preferred_year",origin,weighting)]
        for old,new in (("mean_age","mean_age"),("price_le5","recent_le5"),("weight_denominator","weight_denominator")):
            if not math.isclose(float(r[old]),observed[new],rel_tol=1e-10,abs_tol=1e-10):
                raise ValueError(f"Preferred-year comparator differs from archived summary: {r['report']} {r['basis']} {r['cohort']} {old}")
        baseline_checks += 1
    bounds_rows=[]
    for report in REPORTS:
        for group in ("All",)+GROUPS:
            rr=[r for r in rows if r["report_id"] == report and (group == "All" or r["wg_label"] == group)]
            ids={r["paper_id"] for r in rr}
            matched={r["paper_id"] for r in rr if r["match_status"] in {"matched_doi","matched_title"}}
            labels={r["paper_id"]:r["journal_best"] for r in rr}
            for normalization,normalize in (("casefold_whitespace",basic_title),("conservative_alias",conservative_title)):
                counts=Counter(normalize(labels[p]) for p in sorted(matched) if normalize(labels[p]))
                known=sum(counts.values());unknown=len(ids)-known
                bounds_rows.append({"report":report,"working_group":group,"normalization":normalization,
                    "fixed_all_extracted_assigned_IDs":len(ids),"matched_IDs":len(matched),
                    "retained_named_matched_IDs":known,"unknown_outlet_IDs":unknown,
                    "known_outlet_labels":len(counts),
                    "known_label_HHI":sum(k*k for k in counts.values())/known**2 if known else None,
                    "known_label_D2":known**2/sum(k*k for k in counts.values()) if known else None,
                    **outlet_bounds(list(counts.values()),unknown)})
    envelopes=[]
    for group in ("All",)+GROUPS:
        for normalization in ("casefold_whitespace","conservative_alias"):
            aa,bb = (next(r for r in bounds_rows if r["report"] == rep and r["working_group"] == group and r["normalization"] == normalization) for rep in REPORTS)
            lo,hi=bb["D2_min"]-aa["D2_max"],bb["D2_max"]-aa["D2_min"]
            envelopes.append({"working_group":group,"normalization":normalization,
                "known_label_D2_difference":bb["known_label_D2"]-aa["known_label_D2"],
                "D2_difference_min":lo,"D2_difference_max":hi,"D2_direction_identified":lo>0 or hi<0,
                "HHI_difference_min":bb["HHI_min"]-aa["HHI_max"],
                "HHI_difference_max":bb["HHI_max"]-aa["HHI_min"]})
    tables={"raw_age_occurrence_audit.csv":audit,"raw_age_summary.csv":summaries,
            "raw_age_ecdf.csv":ecdfs,"raw_age_differences.csv":differences,
            "raw_age_paired_year_comparison.csv":paired,"raw_age_single_WG_coverage.csv":coverage,
            "raw_age_WG_fractional_composition.csv":group_rows,"raw_age_WG_kitagawa.csv":decomposition,
            "missing_outlet_bounds.csv":bounds_rows,"missing_outlet_difference_envelopes.csv":envelopes}
    return tables,{"preferred_year_baseline_summary_rows_reproduced":baseline_checks,
        "occurrence_uniqueness":True,"all_exclusions_accounted":True,
        "fractional_compositions_sum_to_one":True,"kitagawa_reconstruction":True}


METHODS = """Source unit and scope
One immutable occurrence_id; many-to-one attachments to chapter, assigned paper
and chapter source are checked. All AR5/AR6 entries are eligible irrespective of
DOI presence. The matched comparator selects occurrences with matched_doi or
matched_title; it does not pull in unmatched occurrences merely because their
ID has another matched occurrence. No external literature population is used.

Age
The raw year is the existing first-year regular expression in the first 300
characters. It is not a manually validated date. The source origin requires
exactly one four-digit year in source metadata. Cutoff origins use the existing
group-specific CUTOFFS map without a fallback for synthesis/combined units.
Missing origins, missing cited years, future years and ages above sixty are
logged by a mutually exclusive priority rule in that order; the source audit
also retains both year fields so simultaneous missingness is observable.
Only integer ages zero through sixty are included. Recent means zero through
five; strict under-five is separately exported. Mean, inverse-ECDF quartiles
and the complete age histogram/CDF are exported. Each occurrence weighs one.
Equal-assigned-ID weighting gives one total weight per included ID within each
report/cohort/group population. Standalone group summaries may share IDs.
The preferred-year comparator is the modal enriched year plus existing source
overrides. Both fields are compared on their own included sets and on a common
paired-occurrence set. Raw parsing avoids WoS selection, not extraction error.

Working-group composition
Only single-WG1/WG2/WG3 occurrences enter the decomposition. Other groups and
their included/excluded contributions are logged separately. For pooled equal
ID weighting, each ID receives total one over ALL included single-group
occurrences before grouping: an ID spanning groups contributes fractional
weight according to its included occurrences. Group weights are fractions of
that pooled denominator, and group means use the same occurrence weights.
They are not obtained by pooling independently normalized group-ID means.
For w_g and m_g, delta=sum((w5+w6)/2*(m6-m5)) +
sum((m5+m6)/2*(w6-w5)). Exact reconstruction is checked for mean age and recent
share. AR6 at AR5 composition and AR5 at AR6 composition are both exported.
These are descriptive standardizations; they do not identify topic mechanisms.

Missing-outlet envelopes
The fixed population is every extracted assigned ID per report/group. Only
matched IDs with a nonempty preferred source label are retained as known;
other IDs, including unmatched named IDs, are treated as unknown. Basic and
conservative alias normalization reuse the archived public helper. Conditional
on retained labels/IDs being correct and an unrestricted set of possible
source categories, HHI_min=(sum(k_j^2)+U)/N^2; HHI_max=
((max(k_j)+U)^2+sum(other k_j^2))/N^2. Unknown IDs each occupying a distinct
new source attain the minimum; all joining the largest source attain the
maximum. D2 endpoints invert HHI endpoints. Difference envelopes combine the
reports' extrema independently. These are missing-label assignment bounds,
not confidence intervals, a coverage correction or an external supply baseline.

Reproduction
No API, human labels or database correction is used. No random draws or new
uncertainty intervals are generated; seed 20261011 is retained as protocol
metadata. Prior 29+10+6 CSV tables, their manifests and associated JSON outputs,
helper code and the immutable database are hash-checked before and after.
--check rebuilds the new tables in memory and compares their hashes with this
independent output archive. No older table is written or overwritten.
"""


def run(database, output, check=False):
    tests = self_tests()
    protected, archive_counts = archive_gate(database)
    rows = load_occurrences(database)
    tables, checks = build(rows)
    checks.update({"self_tests":tests,"older_archive_counts":archive_counts,
                   "source_occurrences":len(rows),"no_random_draws":True})
    files = {name:csv_bytes(data) for name,data in tables.items()}
    files["checks.json"] = json_bytes(checks)
    files["methods.txt"] = METHODS.encode()
    unchanged(protected)
    outputs = [{"filename":name,"bytes":len(content),"sha256":hashlib.sha256(content).hexdigest(),
                **({"rows":len(tables[name])} if name in tables else {})} for name,content in sorted(files.items())]
    manifest={"analysis_version":VERSION,"seed":SEED,"random_draws_used":False,
        "python_version":sys.version.split()[0],"numpy_version":np.__version__,
        "script_sha256":sha256(Path(__file__)),"database_sha256":sha256(database),
        "protected_inputs":{str(p.relative_to(REPO)):h for p,h in sorted(protected.items(),key=lambda x:str(x[0]))},
        "output_tables":outputs,"cutoff_map":CUTOFFS,"old_tables_unchanged":True,
        "database_modified":False,"accuracy_or_human_validation_estimated":False,
        "intervals_generated":False,"units":["occurrence_id","assigned_ID_per_report","fractional_single_WG_weights"],
        "scope":"All extracted raw-leading-year ages; assigned-ID weighted compositions; conditional unknown-outlet-label bounds"}
    if check:
        archived=json.loads((output/"manifest.json").read_text())
        if archived != manifest:
            raise ValueError("Recomputed manifest differs from the independent archive")
        for name,content in files.items():
            if (output/name).read_bytes() != content:
                raise ValueError(f"Recomputed output differs: {name}")
    else:
        if output.exists():
            raise ValueError("Choose a fresh output directory; existing archives are preserved")
        output.mkdir(parents=True)
        for name,content in files.items():
            (output/name).write_bytes(content)
        (output/"manifest.json").write_bytes(json_bytes(manifest))
    raw_all=[r for r in tables["raw_age_differences.csv"] if r["cited_year_field"] == "raw_leading_year" and r["working_group"] == "All"]
    print(json.dumps({"checked":check,"output_tables":len(tables),"raw_age_All_differences":raw_all,
        "pooled_outlet_envelopes":[r for r in tables["missing_outlet_difference_envelopes.csv"] if r["working_group"] == "All"],
        "database_and_older_archives_unchanged":True},indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database",type=Path,default=REPO/"database/ipcc_litdb.sqlite")
    parser.add_argument("--output-dir",type=Path,default=REPO/"revision_preparation/review_round2/raw_age_v1")
    parser.add_argument("--check",action="store_true")
    parser.add_argument("--self-test",action="store_true")
    args=parser.parse_args()
    if args.self_test:
        print(json.dumps(self_tests(),indent=2));return 0
    try:
        run(args.database.resolve(),args.output_dir.resolve(),args.check)
    except (OSError,ValueError,AssertionError) as error:
        print(f"Raw-age analysis failed: {error}",file=sys.stderr);return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
