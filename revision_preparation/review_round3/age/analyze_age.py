#!/usr/bin/env python3
"""Round-three raw-year age distributions and chapter-composition intervals.

No API, model classification, corrected record or human label is used.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import numpy as np
import review_round2_raw_age as prior

VERSION = "1.4.0-age-1"
SEED = 20261011
REPLICATES = 2500
COHORTS = ("all_extracted", "matched_occurrences", "unmatched_occurrences")
ORIGINS = prior.ORIGINS
WEIGHTS = prior.WEIGHTS
REPORTS = prior.REPORTS
METRICS = ("mean_age", "recent_le5", "q25_inverse_ecdf", "median_inverse_ecdf", "q75_inverse_ecdf")
MATCHED = {"matched_doi", "matched_title"}


def csv_bytes(rows):
    return prior.csv_bytes(rows)


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def selected_for(rows, report, cohort):
    return [r for r in rows if r["report_id"] == report and
            (cohort == "all_extracted" or (r["match_status"] in MATCHED) == (cohort == "matched_occurrences"))]


def pack(selected, origin, clusters):
    records = []
    exclusions = Counter()
    ci = {c:i for i,c in enumerate(clusters)}
    for r in selected:
        status, age = prior.age_status(r, "raw_leading_year", origin)
        exclusions[status] += 1
        if status == "included":
            records.append(r | {"age":age})
    papers = {p:i for i,p in enumerate(sorted({r["paper_id"] for r in records}))}
    return {"records":records, "chapter":np.array([ci[r["chapter_id"]] for r in records], dtype=np.int32),
            "paper":np.array([papers[r["paper_id"]] for r in records], dtype=np.int32),
            "age":np.array([r["age"] for r in records], dtype=np.int32),
            "matched":np.array([r["match_status"] in MATCHED for r in records], dtype=bool),
            "n_papers":len(papers), "exclusions":exclusions}


def occurrence_weights(packed, multiplicity, weighting):
    weights = np.asarray(multiplicity[packed["chapter"]], dtype=float)
    if weighting == "occurrence":
        return weights
    totals = np.bincount(packed["paper"], weights=weights, minlength=packed["n_papers"])
    return np.divide(weights, totals[packed["paper"]], out=np.zeros_like(weights), where=totals[packed["paper"]] > 0)


def histogram(packed, weights):
    hist = np.bincount(packed["age"], weights=weights, minlength=61)
    if hist.sum() <= 0:
        raise ValueError("Empty chapter draw: an explicit empty-draw protocol is required")
    return hist


def metric_vector(hist):
    values = prior.metrics(hist)
    return np.array([values[k] for k in METRICS], dtype=float)


def status_components(packed, weights):
    """Use pooled-ID weights before partitioning by status, including mixed IDs."""
    result = []
    for flag in (True, False):
        hist = np.bincount(packed["age"], weights=weights * (packed["matched"] == flag), minlength=61)
        values = prior.metrics(hist)
        if values is None:
            raise ValueError("Empty matching-status component")
        result.append([float(hist.sum()/weights.sum()), values["mean_age"], values["recent_le5"]])
    return np.array(result)


def symmetric_status_decomposition(a, b, metric_index):
    wa, wb = a[:,0], b[:,0]
    ma, mb = a[:,metric_index], b[:,metric_index]
    terms = (wa + wb)/2 * (mb - ma)
    composition = float(np.dot((ma + mb)/2, wb - wa))
    delta = float(np.dot(wb, mb) - np.dot(wa, ma))
    residual = delta - float(terms.sum()) - composition
    if abs(residual) > 1e-10:
        raise ValueError("Matching-status decomposition fails reconstruction")
    return np.array([delta, float(terms[0]), float(terms[1]), composition, residual])


def interval(values):
    lo, hi = np.quantile(values, [.025,.975], axis=0, method="linear")
    return lo, hi


def assert_close(a, b, context):
    if not math.isclose(float(a), float(b), rel_tol=1e-10, abs_tol=1e-10):
        raise ValueError(f"Frozen point estimate mismatch: {context}: {a} vs {b}")


def build(rows):
    summaries, ecdf, draw_rows, difference_rows = [],[],[],[]
    status_rows, status_decomposition, wg_rows, wg_decomposition = [],[],[],[]
    point_store, draw_store, status_store, status_draw_store = {},{},{},{}
    population_store = {}
    sequences = np.random.SeedSequence(SEED).spawn(len(REPORTS)*len(COHORTS))
    stream_index = 0
    for report in REPORTS:
        for cohort in COHORTS:
            selected = selected_for(rows, report, cohort)
            clusters = sorted({r["chapter_id"] for r in selected})
            if not clusters:
                raise ValueError("No selected chapter")
            rng = np.random.default_rng(sequences[stream_index])
            stream_index += 1
            multiplicities = rng.multinomial(len(clusters), np.full(len(clusters), 1/len(clusters)), size=REPLICATES)
            for origin in ORIGINS:
                packed = pack(selected, origin, clusters)
                if sum(packed["exclusions"].values()) != len(selected):
                    raise ValueError("Unaccounted age exclusions")
                for weighting in WEIGHTS:
                    key = (report, cohort, origin, weighting)
                    identifiers = {"report":report,"cohort":cohort,"cited_year_field":"raw_leading_year",
                                   "age_origin":origin,"weighting":weighting}
                    unit_weights = occurrence_weights(packed, np.ones(len(clusters)), weighting)
                    hist = histogram(packed, unit_weights)
                    stats = prior.metrics(hist)
                    point_store[key] = metric_vector(hist)
                    samples = np.zeros((REPLICATES,len(METRICS)))
                    status_samples = np.zeros((REPLICATES,2,3)) if cohort == "all_extracted" else None
                    for i, multiplicity in enumerate(multiplicities):
                        weights = occurrence_weights(packed, multiplicity, weighting)
                        samples[i] = metric_vector(histogram(packed, weights))
                        if status_samples is not None:
                            status_samples[i] = status_components(packed, weights)
                        draw_rows.append(identifiers | {"replicate":i, **dict(zip(METRICS,samples[i].tolist()))})
                    draw_store[key] = samples
                    lower, upper = interval(samples)
                    summary = identifiers | {"selected_occurrences":len(selected),
                        "selected_assigned_IDs":len({r["paper_id"] for r in selected}),
                        "selected_chapter_clusters":len(clusters),
                        "included_occurrences":len(packed["records"]),
                        "included_assigned_IDs":packed["n_papers"],
                        **{reason:packed["exclusions"][reason] for reason in prior.REASONS},
                        **stats}
                    for j, metric in enumerate(METRICS):
                        summary[f"{metric}_lower_95"] = float(lower[j])
                        summary[f"{metric}_upper_95"] = float(upper[j])
                    summary["replicates"] = REPLICATES
                    summary["bootstrap_stream_index"] = stream_index-1
                    summaries.append(summary)
                    cumulative = np.cumsum(hist)
                    for age in range(61):
                        ecdf.append(identifiers | {"age":age,"weight_at_age":float(hist[age]),
                            "weight_denominator":float(hist.sum()),"fraction_at_age":float(hist[age]/hist.sum()),
                            "empirical_CDF":float(cumulative[age]/hist.sum())})
                    if status_samples is not None:
                        status_store[(report,origin,weighting)] = status_components(packed, unit_weights)
                        status_draw_store[(report,origin,weighting)] = status_samples
                        for k,status in enumerate(("matched_occurrences","unmatched_occurrences")):
                            comp = status_store[(report,origin,weighting)][k]
                            status_rows.append({"report":report,"age_origin":origin,"weighting":weighting,
                                "component":status,"pooled_weight_fraction":float(comp[0]),
                                "component_mean_age_with_pooled_weights":float(comp[1]),
                                "component_recent_le5_with_pooled_weights":float(comp[2]),
                                "pooled_weight_denominator":float(unit_weights.sum()),
                                "weighting_scope":"all included report IDs before matching-status partition"})
                    single_records = [r for r in packed["records"] if r["wg_label"] in prior.GROUPS]
                    groups = prior.fractional_groups(single_records, weighting)
                    population_store[key] = groups
                    for group in prior.GROUPS:
                        v = groups[group]
                        wg_rows.append(identifiers | {"working_group":group,
                            "single_WG_included_occurrences":len(single_records),
                            "other_group_included_occurrences":len(packed["records"])-len(single_records),
                            "group_weight_fraction":v["fraction"],
                            "group_mean_age":v["metrics"]["mean_age"],
                            "group_recent_le5":v["metrics"]["recent_le5"],
                            "group_weight_denominator":v["metrics"]["weight_denominator"]})
    for cohort in COHORTS:
        for origin in ORIGINS:
            for weighting in WEIGHTS:
                ka,kb = ((report,cohort,origin,weighting) for report in REPORTS)
                delta = point_store[kb]-point_store[ka]
                samples = draw_store[kb]-draw_store[ka]
                lo,hi = interval(samples)
                row = {"cohort":cohort,"cited_year_field":"raw_leading_year","age_origin":origin,"weighting":weighting}
                for j,metric in enumerate(METRICS):
                    row.update({f"AR5_{metric}":float(point_store[ka][j]),f"AR6_{metric}":float(point_store[kb][j]),
                                f"{metric}_difference":float(delta[j]),f"{metric}_difference_lower_95":float(lo[j]),
                                f"{metric}_difference_upper_95":float(hi[j]),
                                f"{metric}_difference_interval_includes_zero":bool(lo[j] <= 0 <= hi[j])})
                row["replicates"] = REPLICATES
                difference_rows.append(row)
                ga,gb = population_store[ka],population_store[kb]
                wa,wb = (np.array([g[x]["fraction"] for x in prior.GROUPS]) for g in (ga,gb))
                for metric in ("mean_age","recent_le5"):
                    ma,mb = (np.array([g[x]["metrics"][metric] for x in prior.GROUPS]) for g in (ga,gb))
                    wg_decomposition.append({"cohort":cohort,"cited_year_field":"raw_leading_year",
                        "age_origin":origin,"weighting":weighting,"metric":metric,**prior.decompose(wa,wb,ma,mb)})
    for origin in ORIGINS:
        for weighting in WEIGHTS:
            ka,kb = ((report,origin,weighting) for report in REPORTS)
            for k,metric in ((1,"mean_age"),(2,"recent_le5")):
                values = symmetric_status_decomposition(status_store[ka],status_store[kb],k)
                samples = np.array([symmetric_status_decomposition(a,b,k)
                                    for a,b in zip(status_draw_store[ka],status_draw_store[kb])])
                low,high = interval(samples)
                names = ("all_extracted_change","matched_within_change","unmatched_within_change",
                         "matching_composition_change","reconstruction_residual")
                row = {"age_origin":origin,"weighting":weighting,"metric":metric}
                for j,name in enumerate(names):
                    row.update({name:float(values[j]),f"{name}_lower_95":float(low[j]),f"{name}_upper_95":float(high[j])})
                status_decomposition.append(row)
    baseline_rows = read_csv(REPO / "revision_preparation/review_round2/raw_age_v1/raw_age_summary.csv")
    matches = 0
    for prior_row in baseline_rows:
        if prior_row["working_group"] != "All" or prior_row["cited_year_field"] != "raw_leading_year":
            continue
        row = next(r for r in summaries if all(r[k] == prior_row[k] for k in ("report","cohort","age_origin","weighting")))
        for name in ("selected_occurrences","selected_assigned_IDs","included_occurrences","included_assigned_IDs",
                     "weight_denominator","mean_age","sd_age_population","recent_le5","recent_lt5",
                     "q25_inverse_ecdf","median_inverse_ecdf","q75_inverse_ecdf"):
            assert_close(row[name],prior_row[name],f"summary {name}")
        matches += 1
    old_wg = read_csv(REPO / "revision_preparation/review_round2/raw_age_v1/raw_age_WG_kitagawa.csv")
    wg_matches = 0
    for old in old_wg:
        if old["cited_year_field"] != "raw_leading_year":
            continue
        row = next(r for r in wg_decomposition if all(r[k] == old[k] for k in ("cohort","age_origin","weighting","metric")))
        for name in ("AR5_observed","AR6_observed","AR6_minus_AR5","AR6_at_AR5_composition","AR5_at_AR6_composition",
                     "within_component_symmetric","composition_component_symmetric"):
            assert_close(row[name],old[name],f"WG decomposition {name}")
        wg_matches += 1
    pool = read_csv(REPO / "revision_preparation/review_round2/openalex_v1/activated_attempt_03/results_v1/pool_age_comparison.csv")
    rounding = []
    chosen = [r for r in pool if r["query"] == "broad" and r["cohort"] == "all_raw_year_occurrences" and r["working_group"] == "All"]
    if len(chosen) != 2:
        raise ValueError("Unexpected pool-window comparison rows")
    difference = float(chosen[1]["IPCC_mean_annual_age"])-float(chosen[0]["IPCC_mean_annual_age"])
    for row in chosen:
        value = float(row["IPCC_mean_annual_age"])
        rounding.append({"report":row["report"],"query":"broad","cohort":row["cohort"],
                         "window_lower":row["common_publication_year_lower"],"window_upper":row["common_publication_year_upper"],
                         "included_occurrences":row["IPCC_included_occurrences"],"mean_age_full_precision":value,
                         "mean_age_three_decimals":f"{value:.3f}","mean_age_four_decimals":f"{value:.4f}",
                         "AR6_minus_AR5_unrounded":difference,"same_three_decimal_rounding_is_copy_error":False,
                         "original_table":"review_round2/openalex_v1/activated_attempt_03/results_v1/pool_age_comparison.csv"})
    tables = {"age_summary.csv":summaries,"age_changes.csv":difference_rows,"age_ecdf.csv":ecdf,
              "age_bootstrap_draws.csv.gz":draw_rows,"matching_status_composition.csv":status_rows,
              "matching_status_decomposition.csv":status_decomposition,"WG_fractional_composition.csv":wg_rows,
              "WG_kitagawa.csv":wg_decomposition,"window_mean_rounding_check.csv":rounding}
    return tables, {"frozen_round2_summary_rows_reproduced":matches,"frozen_round2_WG_rows_reproduced":wg_matches,
                    "zero_empty_bootstrap_draws":True,"all_exclusions_accounted":True,
                    "matching_status_and_WG_decompositions_reconstruct":True}


METHODS = """Round-three raw-year age analysis, package 1.4.0

The immutable occurrence is the source unit. Matching status belongs to each
occurrence; an ID with another matched occurrence does not pull an unmatched
occurrence into the matched cohort. The unmatched cohort is the complement of
the two accepted matching statuses. Existing raw-year parsing and mutually
exclusive zero-through-sixty inclusion/exclusion rules are imported unchanged.
Age origins are unambiguous report-source publication years and the documented
working-group cutoff YEAR. Full dates and monthly precision are not used here.
Recent-reference fractions include ages zero through five. Quantiles use the
left inverse empirical CDF, matching the frozen raw-age point summaries.

For each report and cohort, all chapters with selected occurrences, including
chapters with no included ages, enter 2,500 multinomial draws of the observed
number of chapters. Chapters are exchangeable composition clusters within a
report/cohort, without working-group stratification, as in the preferred-year
baseline. Reports have independent draws. SeedSequence(20261011) spawns six
streams in report order AR5,AR6, each with cohort order all,matched,unmatched.
The same chapter multiplicities serve both age origins and both weightings
within a report/cohort. A missing origin does not remove a chapter from draws.
Occurrence weights are chapter multiplicities. Equal-ID weights are recomputed
in every draw: an occurrence receives its multiplicity divided by the sum of
multiplicities over all included occurrences of that ID in the report/cohort.
An ID present in the draw therefore has total weight one; an absent ID has zero.
This recomputation preserves cross-chapter identity dependence. No replicate
is discarded or redrawn. Empty draws cause failure. Intervals are 2.5th and
97.5th percentiles (linear interpolation); change intervals use paired replicate
indices of independently sampled reports. They quantify chapter-composition
sensitivity conditional on the frozen corpus and assigned identities. They
exclude record-linkage, extraction, parser and population-selection errors.

Status decomposition: weights are first assigned to ALL included occurrences
of the pooled report. For equal-ID weighting an ID spanning matching statuses
contributes fractional weight to each status. Status-specific means using these
pooled weights can consequently differ from the independently normalized
standalone matched/unmatched equal-ID means. The decomposition is exact:
delta = mean(p5,p6)*delta(matched_mean) +
        mean(1-p5,1-p6)*delta(unmatched_mean) +
        delta(p)*(mean(matched_means)-mean(unmatched_means)).
Component intervals reuse the all-extracted chapter draws and recompute pooled
ID weights before partitioning. This preserves exact reconstruction in every
replicate. Fractions refer to included weight, not original matching coverage.

WG decomposition uses only single-WG1/WG2/WG3 catalogue units, exactly as in the
round-two fractional-weight standardization. Other catalogue units are counted
explicitly. Pooled equal-ID weights are applied before working-group partition.
The symmetric two-term Kitagawa identity partitions within-group and group-
composition changes; each report at the other's composition is also exported.
Standalone report/cohort confidence intervals include other catalogue units.

The previous external-pool window check is read only: 4.452604299740847 and
4.453424461119343 legitimately round to the same three-decimal value. Their
unrounded difference is separately exported. This is not a copy error.

This script performs no model or human validation and no API acquisition.
Old 45 baseline/DOI/AI tables and the database are checked before and after;
round-two raw-age outputs and the external age table are also hash protected.
--check replays all calculations and compares bytes in this independent archive.
"""


def run(database, output, check=False):
    protected, older_archives = prior.archive_gate(database)
    additional = [REPO / "scripts/review_round2_raw_age.py",
                  REPO / "revision_preparation/review_round2/raw_age_v1/manifest.json",
                  REPO / "revision_preparation/review_round2/raw_age_v1/raw_age_summary.csv",
                  REPO / "revision_preparation/review_round2/raw_age_v1/raw_age_WG_kitagawa.csv",
                  REPO / "revision_preparation/review_round2/openalex_v1/activated_attempt_03/results_v1/manifest.json",
                  REPO / "revision_preparation/review_round2/openalex_v1/activated_attempt_03/results_v1/pool_age_comparison.csv"]
    protected.update({p:prior.sha256(p) for p in additional})
    tables, checks = build(prior.load_occurrences(database))
    checks.update({"older_archives":older_archives,"seed":SEED,"replicates":REPLICATES})
    files, output_entries = {},[]
    for name, rows in sorted(tables.items()):
        raw = csv_bytes(rows)
        content = gzip.compress(raw, compresslevel=9, mtime=0) if name.endswith(".gz") else raw
        files[name] = content
        output_entries.append({"filename":name,"rows":len(rows),"bytes":len(content),
            "sha256":hashlib.sha256(content).hexdigest(),"csv_content_sha256":hashlib.sha256(raw).hexdigest()})
    files["checks.json"] = prior.json_bytes(checks)
    files["methods.txt"] = METHODS.encode()
    for name in ("checks.json","methods.txt"):
        output_entries.append({"filename":name,"bytes":len(files[name]),"sha256":hashlib.sha256(files[name]).hexdigest()})
    manifest = {"analysis_package":"1.4.0","analysis_version":VERSION,"seed":SEED,"replicates":REPLICATES,
        "python_version":sys.version.split()[0],"numpy_version":np.__version__,
        "script_sha256":prior.sha256(Path(__file__)),"test_script_sha256":prior.sha256(HERE/"test_age.py"),
        "database_sha256":prior.sha256(database),"inputs":{str(p.relative_to(REPO)):h for p,h in sorted(protected.items(),key=lambda x:str(x[0]))},
        "outputs":output_entries,"cutoff_map":prior.CUTOFFS,"old_tables_and_database_unchanged":True,
        "LLM_validation_performed":False,"human_validation_performed":False,
        "interval_type":"95% percentile chapter-composition sensitivity interval, not linkage/parser/population error"}
    prior.unchanged(protected)
    if check:
        if json.loads((output/"manifest.json").read_text()) != manifest:
            raise ValueError("Recomputed age manifest differs")
        for name,data in files.items():
            if (output/name).read_bytes() != data:
                raise ValueError(f"Recomputed age output differs: {name}")
    else:
        if output.exists():
            raise ValueError("Existing age output archive cannot be overwritten")
        output.mkdir(parents=True)
        for name,data in files.items():
            (output/name).write_bytes(data)
        (output/"manifest.json").write_bytes(prior.json_bytes(manifest))
    print(json.dumps({"check":check,"output":str(output),"tables":len(tables),"checks":checks,
        "all_extracted_mean_changes":[{k:v for k,v in row.items() if k in ("cohort","age_origin","weighting") or k.startswith("mean_age_difference")}
            for row in tables["age_changes.csv"] if row["cohort"] == "all_extracted"]},indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=REPO/"database/ipcc_litdb.sqlite")
    parser.add_argument("--output-dir", type=Path, default=HERE/"output_v1")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    run(args.database.resolve(), args.output_dir.resolve(), args.check)


if __name__ == "__main__":
    main()
