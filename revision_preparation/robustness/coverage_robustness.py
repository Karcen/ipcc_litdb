#!/usr/bin/env python3
"""Reproduce raw-reference DOI coverage and age sensitivities.

One source observation is immutable occurrence_id. It joins chapters and sources
many-to-one. One identity is a DOI token freshly extracted from raw_reference;
the identity key never uses paper_id, enriched DOI or a WoS matching result.
Report/working-group DOI sets are derived views, not changes to the source unit.
Published bibliographic strings contain no participant-level research data.

The analysis is a descriptive census conditional on usable printed DOI tokens.
Chapter draws describe composition in this selected snapshot, not population
linkage accuracy or coverage of the wider climate-literature pool. Every filter
is exported. Input SQLite, registered baseline tables and public code are read
without alteration. Outputs require a fresh directory.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

import numpy as np

VERSION = "coverage-robustness-1.0.0"
SEED = 20261011
GROUPS = ("All", "WG1", "WG2", "WG3")
VARIANTS = ("literal", "punctuation_repaired")
BASES = ("source_publication_year", "documented_cutoff_year")
WEIGHTINGS = ("occurrence", "equal_raw_DOI")
METRICS = ("mean_age", "median_inverse_ecdf", "q25_inverse_ecdf",
           "q75_inverse_ecdf", "recent_le5", "recent_lt5")
SQL = """SELECT r.occurrence_id, r.chapter_id, c.report_id, w.wg_label,
 r.raw_reference, r.match_status, s.publication_year AS source_year,
 c.source_id AS chapter_source_id, r.source_id AS occurrence_source_id,
 r.source_page
 FROM references_occurrence r
 JOIN chapters c ON c.chapter_id=r.chapter_id
 JOIN working_groups w ON w.wg_id=c.wg_id
 LEFT JOIN sources s ON s.source_id=c.source_id
 WHERE c.report_id IN ('AR4','AR5','AR6')
 ORDER BY c.report_id,r.occurrence_id"""


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, obj):
    path.write_text(json.dumps(obj, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path, rows):
    rows = list(rows)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else [])
        w.writeheader()
        w.writerows({k: v.replace("\x00", "\\u0000") if isinstance(v, str) else v
                     for k, v in row.items()} for row in rows)


def csv_compare(actual, reference):
    with Path(actual).open(newline="", encoding="utf-8") as f:
        a = list(csv.reader(f))
    with Path(reference).open(newline="", encoding="utf-8") as f:
        b = list(csv.reader(f))
    if len(a) != len(b):
        raise ValueError(f"Baseline row count differs: {Path(actual).name}")
    for n, (aa, bb) in enumerate(zip(a, b), 1):
        if len(aa) != len(bb):
            raise ValueError(f"Baseline column count differs: {Path(actual).name}")
        for x, y in zip(aa, bb):
            if x == y:
                continue
            try:
                same = math.isclose(float(x), float(y), rel_tol=1e-10, abs_tol=1e-10)
            except ValueError:
                same = False
            if n == 1 or not same:
                raise ValueError(f"Baseline cell differs: {Path(actual).name}, row {n}")


def baseline_gate(repo, db, reproduction):
    reference = repo / "reproducibility/reference/manifest.json"
    reg = json.loads(reference.read_text())
    record_path = reproduction / "reproduction.json"
    record = json.loads(record_path.read_text())
    if reg.get("analysis_version") != "v1.2.1" or len(reg["tables"]) != 29:
        raise ValueError("The registered v1.2.1 baseline of 29 tables is required")
    if np.__version__ != reg["numpy_version"]:
        raise ValueError("Use the pinned analysis environment with NumPy 2.2.6")
    digest = sha256(db)
    if digest != reg["database_sha256"]:
        raise ValueError("Input differs from the corrected v1.2.0 snapshot")
    if not all(record.get(k) for k in ("database_unchanged", "checked_reference", "all_requested_checks_passed")):
        raise ValueError("Complete reference-checked baseline reproduction is required")
    if record["database_sha256_before"] != digest or record["database_sha256_after"] != digest:
        raise ValueError("Baseline reproduction input differs")
    checked = {x["filename"] for x in record["table_comparisons"] if x.get("passed")}
    required = {x["filename"] for x in reg["tables"]}
    if checked != required:
        raise ValueError("All 29 registered baseline tables must have passed")
    for name, expected in record["code_sha256"].items():
        if sha256(repo / "reproducibility" / name) != expected:
            raise ValueError(f"Baseline code changed: {name}")
    results = []
    for item in reg["tables"]:
        ref = reference.parent / item["filename"]
        generated = reproduction / "results" / item["filename"]
        if sha256(ref) != item["sha256"]:
            raise ValueError(f"Registered baseline table changed: {ref.name}")
        csv_compare(generated, ref)
        results.append({"filename": ref.name, "registered_sha256": item["sha256"],
                        "baseline_generated_sha256": sha256(generated), "passed": True})
    return {"database_sha256": digest, "baseline_analysis_version": "v1.2.1",
            "baseline_manifest_sha256": sha256(reference),
            "baseline_reproduction_sha256": sha256(record_path),
            "baseline_code_sha256": record["code_sha256"],
            "baseline_tables_checked": results, "all_29_passed": True}


def source_year(value):
    ys = re.findall(r"\b([12][0-9]{3})\b", str(value or ""))
    return int(ys[0]) if len(ys) == 1 else None


def raw_year(value):
    found = re.search(r"\b(1[6-9]\d{2}|20\d{2})[a-z]?\b", str(value or "")[:300])
    return int(found.group(1)) if found else None


def comparison(a, b):
    shared = a & b
    return {"earlier_raw_DOI_keys": len(a), "later_raw_DOI_keys": len(b),
            "shared_raw_DOI_keys": len(shared), "earlier_only_raw_DOI_keys": len(a-b),
            "later_only_raw_DOI_keys": len(b-a), "retention": len(shared)/len(a) if a else None,
            "reuse": len(shared)/len(b) if b else None,
            "jaccard": len(shared)/len(a|b) if a|b else None}


def accepted_keys(rows, variant):
    return {r[variant+"_key"] for r in rows if r[variant+"_key"] is not None}


def year_key_set(rows, variant, lower, upper, policy):
    grouped = defaultdict(list)
    for r in rows:
        if r[variant+"_key"] is not None:
            grouped[r[variant+"_key"]].append(r["raw_leading_year"])
    result = set()
    for key, yy in grouped.items():
        observed = {y for y in yy if y is not None}
        if not observed:
            continue
        if policy == "unambiguous_complete" and (len(observed) != 1 or None in yy):
            continue
        if min(observed) >= lower and max(observed) <= upper:
            result.add(key)
    return result


def metrics(hist):
    total = float(hist.sum())
    if total <= 0:
        return None
    cdf = np.cumsum(hist)
    quantile = [int(np.searchsorted(cdf, p*total, side="left")) for p in (.25, .5, .75)]
    ages = np.arange(61)
    mean = float(np.dot(hist, ages)/total)
    return {"weight_denominator": total, "mean_age": mean,
            "sd_age_population": math.sqrt(float(np.dot(hist, (ages-mean)**2)/total)),
            "q25_inverse_ecdf": quantile[0], "median_inverse_ecdf": quantile[1],
            "q75_inverse_ecdf": quantile[2],
            "recent_le5": float(hist[:6].sum()/total), "recent_lt5": float(hist[:5].sum()/total)}


def histogram(records, multiplicities=None):
    if not records:
        return {w: np.zeros(61) for w in WEIGHTINGS}
    key_index = {key:i for i,key in enumerate(sorted({r["key"] for r in records}))}
    kk = np.array([key_index[r["key"]] for r in records], dtype=np.int32)
    ages = np.array([r["age"] for r in records], dtype=np.int32)
    weights = np.ones(len(records)) if multiplicities is None else np.array([multiplicities[r["chapter_id"]] for r in records], dtype=float)
    totals = np.bincount(kk, weights=weights, minlength=len(key_index))
    equal = np.divide(weights, totals[kk], out=np.zeros_like(weights), where=totals[kk]>0)
    return {"occurrence": np.bincount(ages, weights=weights, minlength=61),
            "equal_raw_DOI": np.bincount(ages, weights=equal, minlength=61)}


def standard_histogram(group_hist, weight):
    if any(group_hist[g][weight].sum() <= 0 for g in GROUPS[1:]):
        return None
    return sum(group_hist[g][weight]/group_hist[g][weight].sum() for g in GROUPS[1:])/3


def age_records(selected, basis, cutoffs):
    accepted = []
    exclusions = Counter()
    for r in selected:
        base = r["source_year_integer"]
        if basis == "documented_cutoff_year":
            date = cutoffs.get(r["report_id"], {}).get(r["wg_label"])
            base = int(date[:4]) if date else None
        yr = r["raw_leading_year"]
        reason = ("missing_or_ambiguous_origin_year" if base is None else
                  "missing_raw_year" if yr is None else "raw_year_after_origin" if yr > base else
                  "raw_age_over_60" if base-yr > 60 else "included")
        exclusions[reason] += 1
        if reason == "included":
            accepted.append({"chapter_id":r["chapter_id"], "key":r["key"],
                             "wg_label":r["wg_label"], "age":base-yr})
    return accepted, exclusions


def bootstrap(selected, records, replicates, rng):
    # Stratify catalogue-unit draws by observed working group. All units with
    # selected DOI occurrences enter, including units with no included ages.
    clusters = defaultdict(set)
    for r in selected:
        clusters[r["wg_label"]].add(r["chapter_id"])
    units_all = sorted({unit for units in clusters.values() for unit in units})
    unit_index = {unit:i for i,unit in enumerate(units_all)}
    draws = {}
    for group, units in sorted(clusters.items()):
        units = sorted(units)
        draws[group] = (np.array([unit_index[u] for u in units],dtype=np.int32),
                        rng.multinomial(len(units), np.full(len(units),1/len(units)), size=replicates))
    def pack(rr):
        key_index = {key:i for i,key in enumerate(sorted({r['key'] for r in rr}))}
        return (np.array([unit_index[r['chapter_id']] for r in rr],dtype=np.int32),
                np.array([key_index[r['key']] for r in rr],dtype=np.int32),
                np.array([r['age'] for r in rr],dtype=np.int32),len(key_index))
    packed = {'All':pack(records)} | {g:pack([r for r in records if r['wg_label']==g]) for g in GROUPS[1:]}
    def packed_histogram(p, chapter_weights):
        chapter, key, age, key_count = p
        weights = chapter_weights[chapter]
        totals = np.bincount(key,weights=weights,minlength=key_count)
        equal = np.divide(weights,totals[key],out=np.zeros_like(weights),where=totals[key]>0)
        return {'occurrence':np.bincount(age,weights=weights,minlength=61),
                'equal_raw_DOI':np.bincount(age,weights=equal,minlength=61)}
    result = defaultdict(list)
    for n in range(replicates):
        multiplicities = np.zeros(len(units_all),dtype=float)
        for indices,matrix in draws.values():multiplicities[indices]=matrix[n]
        hh = packed_histogram(packed['All'],multiplicities)
        gh = {g:packed_histogram(packed[g],multiplicities) for g in GROUPS[1:]}
        for w in WEIGHTINGS:
            result[("All",w)].append(metrics(hh[w]))
            std = standard_histogram(gh,w)
            result[("equal_working_group",w)].append(metrics(std) if std is not None else None)
    return result


def run(repo, db, output, reproduction, replicates):
    if output.exists():
        raise ValueError("Choose a fresh output directory; existing outputs are preserved")
    gate = baseline_gate(repo,db,reproduction)
    sys.path.insert(0,str(repo/"reproducibility"))
    import revision_identity as identity
    from revision_age_networks import CUTOFFS, CUTOFF_SOURCES
    before = sha256(db)
    with sqlite3.connect(db.as_uri()+"?mode=ro&immutable=1",uri=True) as c:
        c.row_factory=sqlite3.Row
        rows=[dict(r) for r in c.execute(SQL)]
        orphan_chapters=c.execute("select count(*) from references_occurrence r left join chapters c on c.chapter_id=r.chapter_id where c.chapter_id is null").fetchone()[0]
    if len({r['occurrence_id'] for r in rows})!=len(rows) or len(rows)!=107071 or orphan_chapters:
        raise ValueError("Source unit or occurrence-to-chapter cardinality failed")
    audit=[]
    for r in rows:
        literal=sorted({d for value in identity.DOI_RE.findall(str(r['raw_reference'] or ''))
                        if identity.usable_raw_doi(d:=identity.normalized_doi(value))})
        repaired=identity.punctuation_repaired_raw_dois(r['raw_reference'])
        r.update({'literal_tokens':literal,'punctuation_repaired_tokens':repaired,
                  'literal_key':literal[0] if len(literal)==1 else None,
                  'punctuation_repaired_key':repaired[0] if len(repaired)==1 else None,
                  'raw_leading_year':raw_year(r['raw_reference']),
                  'source_year_integer':source_year(r['source_year'])})
        audit.append({k:r[k] for k in ['occurrence_id','chapter_id','report_id','wg_label',
                      'chapter_source_id','occurrence_source_id','source_page','match_status',
                      'source_year','source_year_integer','raw_leading_year']} |
                     {v+'_tokens':' | '.join(r[v+'_tokens']) for v in VARIANTS} |
                     {v+'_token_count':len(r[v+'_tokens']) for v in VARIANTS} |
                     {v+'_single_key':r[v+'_key'] for v in VARIANTS})
    output.mkdir(parents=True)
    (output/'results').mkdir();(output/'qc').mkdir()
    coverage=[];transitions=[];eligibility=[];year_diagnostics=[]
    subsets={(rep,g):[r for r in rows if r['report_id']==rep and (g=='All' or r['wg_label']==g)]
             for rep in ['AR4','AR5','AR6'] for g in GROUPS}
    for v in VARIANTS:
        for (rep,g),rr in subsets.items():
            selected=[r for r in rr if r[v+'_key'] is not None]
            keys=accepted_keys(rr,v)
            statuses=Counter(r['match_status'] for r in selected)
            coverage.append({'report':rep,'working_group':g,'DOI_parser':v,
                'extracted_occurrences':len(rr),'single_DOI_occurrences':len(selected),
                'single_DOI_occurrence_fraction':len(selected)/len(rr) if rr else None,
                'no_usable_DOI_occurrences':sum(len(r[v+'_tokens'])==0 for r in rr),
                'multiple_usable_DOI_occurrences':sum(len(r[v+'_tokens'])>1 for r in rr),
                'distinct_raw_DOI_keys':len(keys),'selected_matched_DOI_occurrences':statuses['matched_doi'],
                'selected_matched_title_occurrences':statuses['matched_title'],'selected_unmatched_occurrences':statuses['unmatched'],
                'selected_missing_raw_year_occurrences':sum(r['raw_leading_year'] is None for r in selected),
                'selected_missing_origin_occurrences':sum(r['source_year_integer'] is None for r in selected)})
            grouped=defaultdict(list)
            for r in selected:grouped[r[v+'_key']].append(r['raw_leading_year'])
            year_diagnostics.append({'report':rep,'working_group':g,'DOI_parser':v,
                'raw_DOI_keys':len(grouped),'keys_without_any_raw_year':sum(all(y is None for y in yy) for yy in grouped.values()),
                'keys_with_some_missing_raw_year':sum(None in yy for yy in grouped.values()),
                'keys_with_multiple_observed_raw_years':sum(len({y for y in yy if y is not None})>1 for yy in grouped.values())})
        for earlier,later,cutoffs in [('AR4','AR5',[2006,2007,2008]),('AR5','AR6',[2012,2013,2014])]:
            for g in GROUPS:
                aa,bb=subsets[(earlier,g)],subsets[(later,g)]
                a,b=accepted_keys(aa,v),accepted_keys(bb,v)
                transitions.append({'earlier':earlier,'later':later,'working_group':g,'DOI_parser':v,
                    'vintage_rule':'all_usable_single_DOI_entries','lower_raw_year':None,'upper_raw_year':None,
                    'year_policy':'no_year_filter',**comparison(a,b)})
                for policy in ['maximum_observed_known','unambiguous_complete']:
                    upper=2007 if earlier=='AR4' else 2013
                    va=year_key_set(aa,v,2000,upper,policy);vb=year_key_set(bb,v,2000,upper,policy)
                    transitions.append({'earlier':earlier,'later':later,'working_group':g,'DOI_parser':v,
                        'vintage_rule':'common_raw_publication_window','lower_raw_year':2000,'upper_raw_year':upper,
                        'year_policy':policy,**comparison(va,vb)})
                    for cutoff in cutoffs:
                        eligible=year_key_set(bb,v,1600,cutoff,policy)
                        shared=eligible&a
                        eligibility.append({'earlier':earlier,'later':later,'working_group':g,'DOI_parser':v,
                            'raw_year_cutoff':cutoff,'year_policy':policy,'later_raw_DOI_keys':len(b),
                            'eligible_later_raw_DOI_keys':len(eligible),'shared_eligible_raw_DOI_keys':len(shared),
                            'conditional_reuse':len(shared)/len(eligible) if eligible else None})
    summary=[];ecdf=[];draw_rows=[];intervals=[];differences=[];draw_results={}
    rng=np.random.default_rng(SEED)
    for v in VARIANTS:
        for basis in BASES:
            for rep in ['AR5','AR6']:
                selected=[dict(r,key=r[v+'_key']) for r in subsets[(rep,'All')] if r[v+'_key'] is not None]
                records,exclusions=age_records(selected,basis,CUTOFFS)
                group_hists={g:histogram([r for r in records if r['wg_label']==g]) for g in GROUPS[1:]}
                for g in GROUPS:
                    rr=selected if g=='All' else [r for r in selected if r['wg_label']==g]
                    rec,ex=age_records(rr,basis,CUTOFFS)
                    h=histogram(rec)
                    for w in WEIGHTINGS:
                        met=metrics(h[w])
                        if met is None:continue
                        line={'report':rep,'working_group':g,'DOI_parser':v,'age_origin':basis,'weighting':w,
                              'selected_DOI_occurrences':len(rr),'included_age_occurrences':len(rec),
                              'included_raw_DOI_keys':len({r['key'] for r in rec}),
                              'missing_or_ambiguous_origin_year':ex['missing_or_ambiguous_origin_year'],
                              'missing_raw_year':ex['missing_raw_year'],'raw_year_after_origin':ex['raw_year_after_origin'],
                              'raw_age_over_60':ex['raw_age_over_60'],**met}
                        summary.append(line)
                        for age,p in enumerate(np.cumsum(h[w])/h[w].sum()):
                            ecdf.append({k:line[k] for k in ['report','working_group','DOI_parser','age_origin','weighting']} |
                                {'age':age,'weight_at_age':float(h[w][age]),'cdf':float(p),'weight_denominator':met['weight_denominator']})
                for w in WEIGHTINGS:
                    hist=standard_histogram(group_hists,w)
                    if hist is None:continue
                    rec=[r for r in records if r['wg_label'] in GROUPS[1:]]
                    summary.append({'report':rep,'working_group':'equal_working_group','DOI_parser':v,
                        'age_origin':basis,'weighting':w,'selected_DOI_occurrences':sum(r['wg_label'] in GROUPS[1:] for r in selected),
                        'included_age_occurrences':len(rec),'included_raw_DOI_keys':len({r['key'] for r in rec}),
                        'missing_or_ambiguous_origin_year':None,'missing_raw_year':None,'raw_year_after_origin':None,
                        'raw_age_over_60':None,**metrics(hist)})
                    for age,p in enumerate(np.cumsum(hist)/hist.sum()):
                        ecdf.append({'report':rep,'working_group':'equal_working_group','DOI_parser':v,
                            'age_origin':basis,'weighting':w,'age':age,'weight_at_age':float(hist[age]),
                            'cdf':float(p),'weight_denominator':float(hist.sum())})
                draw_results[(v,basis,rep)]=bootstrap(selected,records,replicates,rng)
                for (g,w),draws in draw_results[(v,basis,rep)].items():
                    for n,d in enumerate(draws):
                        if d is not None:draw_rows.append({'DOI_parser':v,'age_origin':basis,'report':rep,
                            'working_group':g,'weighting':w,'draw':n+1,**d})
            index={(r['report'],r['working_group'],r['weighting']):r for r in summary if r['DOI_parser']==v and r['age_origin']==basis}
            for g in [*GROUPS,'equal_working_group']:
                for w in WEIGHTINGS:
                    left=index.get(('AR5',g,w));right=index.get(('AR6',g,w))
                    if not left or not right:continue
                    differences.append({'earlier':'AR5','later':'AR6','working_group':g,'DOI_parser':v,
                        'age_origin':basis,'weighting':w,**{m+'_difference':right[m]-left[m] for m in METRICS}})
            for g in ['All','equal_working_group']:
                for w in WEIGHTINGS:
                    left=draw_results[(v,basis,'AR5')][(g,w)];right=draw_results[(v,basis,'AR6')][(g,w)]
                    pairs=[(a,b) for a,b in zip(left,right) if a is not None and b is not None]
                    for m in METRICS:
                        delta=np.array([b[m]-a[m] for a,b in pairs])
                        valid=len(delta)>=math.ceil(.95*replicates)
                        lo,hi=np.quantile(delta,[.025,.975]) if valid else (None,None)
                        intervals.append({'earlier':'AR5','later':'AR6','working_group':g,'DOI_parser':v,
                            'age_origin':basis,'weighting':w,'metric':m,'replicates_requested':replicates,
                            'paired_nonempty_draws':len(delta),'interval_available':valid,
                            'bootstrap_difference_mean':float(delta.mean()) if len(delta) else None,
                            'composition_p025':float(lo) if lo is not None else None,
                            'composition_p975':float(hi) if hi is not None else None,
                            'seed':SEED})
    tables={'raw_doi_coverage':coverage,'raw_doi_year_diagnostics':year_diagnostics,
            'raw_doi_recurrence':transitions,'raw_doi_eligible_reuse':eligibility,
            'raw_doi_age_summary':summary,'raw_doi_age_ecdf':ecdf,'raw_doi_age_differences':differences,
            'raw_doi_age_bootstrap_intervals':intervals,'raw_doi_age_bootstrap_draws':draw_rows,
            'raw_doi_occurrence_audit':audit}
    for name,contents in tables.items():write_csv(output/'results'/(name+'.csv'),contents)
    # Pooled all-status DOI cohorts and raw-year eligibility must reproduce the
    # existing independent raw baseline before interpreting its extensions.
    legacy=list(csv.DictReader((reproduction/'results/revision_identity_independent_raw_baselines.csv').open()))
    checks=[]
    for r in transitions:
        if r['working_group']!='All' or r['year_policy']!='no_year_filter':continue
        identity_label=('literal_single_raw_DOI_all_statuses' if r['DOI_parser']=='literal' else 'punctuation_repaired_single_raw_DOI_all_statuses')
        old=next(x for x in legacy if x['earlier']==r['earlier'] and x['later']==r['later'] and x['identity_unit']==identity_label)
        ok=(r['earlier_raw_DOI_keys'],r['later_raw_DOI_keys'],r['shared_raw_DOI_keys'])==tuple(int(old[k]) for k in ['papers_a','papers_b','shared'])
        if not ok:raise ValueError('Independent raw DOI baseline bridge differs')
        checks.append({'check':identity_label+' '+r['earlier']+'-'+r['later'],'passed':ok})
    after=sha256(db)
    if after!=before:raise ValueError('Input database changed')
    manifests=[]
    for name, contents in tables.items():
        path=output/'results'/(name+'.csv')
        manifests.append({'filename':path.relative_to(output).as_posix(),'rows':len(contents),
                          'bytes':path.stat().st_size,'sha256':sha256(path)})
    manifest={'analysis_version':VERSION,'baseline_gate':gate,'database_sha256_before':before,
        'database_sha256_after':after,'database_unchanged':True,'script_sha256':sha256(Path(__file__)),
        'python_version':sys.version.split()[0],'numpy_version':np.__version__,'seed':SEED,
        'age_bootstrap_replicates':replicates,'query':SQL,'source_occurrences':len(rows),
        'occurrence_uniqueness_passed':True,'orphan_chapter_rows':orphan_chapters,
        'baseline_bridge_checks':checks,'registered_tables':manifests,
        'units':{'input':'occurrence_id','identity':'one usable DOI extracted from preserved raw_reference',
                 'recurrence':'distinct DOI key per report or report/working group',
                 'age_occurrence':'one included printed-DOI reference occurrence',
                 'age_equal_raw_DOI':'total weight one per included DOI key within report/group',
                 'age_equal_working_group':'mean of the three separately normalized WG age distributions'},
        'raw_DOI_eligibility_policies':{
            'maximum_observed_known':'At least one raw leading year; maximum observed known year at/below cutoff; common windows also require minimum at/above lower boundary. Missing-year occurrences do not change known bounds.',
            'unambiguous_complete':'Every selected occurrence has a raw year and all years agree; that year must fall within the boundary.'},
        'age_inclusion':'Unambiguous source year or documented WG cutoff year, raw leading year present, age 0-60. No fallback origin is used.',
        'resampling':'Catalogue units with selected DOI occurrences are resampled with replacement within each report/WG. Report draws are independent. Equal-DOI weights are recalculated in each draw. Empty draws are logged; intervals require at least 95% paired nonempty draws.',
        'interpretation':'Descriptive recurrence and age in the raw-DOI-bearing portion of the saved references. Selection is independent of WoS match status, but raw DOI availability and extraction remain unequal across reports/groups. Composition intervals condition on these extracted entries and raw DOI keys.',
        'accuracy':'No manual adjudication labels were supplied. No population correspondence accuracy is estimated.',
        'external_literature_pool':'No external pool is constructed; all inputs come from cited references in this snapshot.',
        'outlet_analysis':'No new outlet result is estimated because stored outlet metadata are enriched rather than independently parsed or validated from raw citations.',
        'official_cutoff_sources':CUTOFF_SOURCES}
    write_json(output/'manifest.json',manifest)
    write_json(output/'qc/checks.json',{'all_passed':True,'database_unchanged':True,'baseline_bridge_checks':checks})
    print(json.dumps({'output':str(output),'database_unchanged':True,'registered_tables':len(tables),
                      'baseline_tables_passed':29,'source_occurrences':len(rows)},indent=2))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,required=True,help='Checkout with registered v1.2.1 analysis code and reference tables')
    p.add_argument('--db',type=Path,help='Corrected SQLite; defaults to REPO/database/ipcc_litdb.sqlite')
    p.add_argument('--baseline-reproduction',type=Path,required=True,help='Fresh completed --check-reference reproduction output')
    p.add_argument('--output-dir',type=Path,required=True,help='New directory for additional results and provenance')
    p.add_argument('--replicates',type=int,default=2500)
    a=p.parse_args()
    if a.replicates<1:p.error('--replicates must be positive')
    repo=a.repo.expanduser().resolve()
    run(repo,(a.db or repo/'database/ipcc_litdb.sqlite').expanduser().resolve(),
        a.output_dir.expanduser().resolve(),a.baseline_reproduction.expanduser().resolve(),a.replicates)


if __name__=='__main__':
    main()
