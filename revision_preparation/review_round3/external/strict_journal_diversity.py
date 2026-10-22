#!/usr/bin/env python3
"""Source-ID diversity in resolved journal article/review assigned-ID cohorts.

Offline only. A repeated assigned ID is counted once per report, even when it has
both matched and unmatched occurrences. Conflicting resolved mappings stop.
"""
from __future__ import annotations
import argparse,csv,json
from collections import Counter,defaultdict
from pathlib import Path
import numpy as np
import offline_external as common

ROOT=Path(__file__).resolve().parent
SEED=20261011
DRAWS=2000

def identities(rows,rep,cohort):
    selected=[r for r in rows if r['report']==rep and (cohort=='matched_plus_DOI_recovered_unmatched' or r['occurrence_status_group']=='matched')]
    grouped=defaultdict(list)
    for r in selected:
        eligible=r['work_type'] in {'article','review'} and r['source_type']=='journal' and bool(r['source_id'])
        if (r['journal_article_review']=='True')!=eligible: raise ValueError('Eligibility flag conflicts with work/source fields')
        grouped[r['paper_id']].append(r)
    mapping={};dup=0
    for pid,rr in grouped.items():
        keys={(r['openalex_id'],r['source_id'],r['work_type'],r['source_type']) for r in rr}
        if len(keys)!=1:raise ValueError('Conflicting resolved mapping for '+rep+' assigned ID '+pid)
        dup+=len(rr)-1
        if rr[0]['journal_article_review']=='True':mapping[pid]=rr[0]['source_id']
    return mapping,{'selected_rows':len(selected),'unique_resolved_IDs':len(grouped),'duplicate_rows_collapsed':dup,'conflicting_IDs':0,'journal_article_review_IDs':len(mapping),'known_other_type_or_missing_source_IDs':len(grouped)-len(mapping)}

def d2(counts):
    n=sum(counts);return n*n/sum(int(x)*int(x) for x in counts)

def analyze(rows,draws=DRAWS):
    rng=np.random.default_rng(SEED);summary=[];differences=[];audit=[];buckets=[]
    for cohort in ['matched','matched_plus_DOI_recovered_unmatched']:
        sources={};populations={}
        for rep in ['AR5','AR6']:
            mapping,aa=identities(rows,rep,cohort);sources[rep]=Counter(mapping.values());populations[rep]=len(mapping)
            audit.append({'cohort':cohort,'report':rep,**aa})
        equal=min(populations.values()); samples={}
        if equal<1:raise ValueError('No journal article/review assigned IDs')
        for rep in ['AR5','AR6']:
            cc=sources[rep];x=np.array([cc[k] for k in sorted(cc)],dtype=np.int64);dd0=[];dd2=[]
            for b in range(draws):
                z=rng.multivariate_hypergeometric(x,equal);dd0.append(int(np.count_nonzero(z)));dd2.append(d2(z))
            samples[rep]={'D0':np.array(dd0),'D2':np.array(dd2)}
            summary.append({'cohort':cohort,'report':rep,'source_identity':'source_id','work_scope':'article or review','source_scope':'journal','unit':'one assigned ID per report','assigned_IDs':populations[rep],'D0_full':len(cc),'D2_full':d2(x),'equal_size':equal,'draws':draws,'equal_D0_mean':float(np.mean(dd0)),'equal_D0_q025':float(np.quantile(dd0,.025)),'equal_D0_q975':float(np.quantile(dd0,.975)),'equal_D2_mean':float(np.mean(dd2)),'equal_D2_q025':float(np.quantile(dd2,.025)),'equal_D2_q975':float(np.quantile(dd2,.975)),'interpretation':'Resolved DOI metadata only; equal-size subsampling range, not a population confidence interval or linkage-accuracy interval.'})
            buckets.extend({'cohort':cohort,'report':rep,'source_id':sid,'assigned_IDs':n} for sid,n in sorted(cc.items()))
        for metric in ['D0','D2']:
            delta=samples['AR6'][metric]-samples['AR5'][metric]
            differences.append({'cohort':cohort,'source_identity':'source_id','work_scope':'article or review','source_scope':'journal','metric':metric,'equal_size':equal,'draws':draws,'AR6_minus_AR5_mean':float(np.mean(delta)),'q025':float(np.quantile(delta,.025)),'q975':float(np.quantile(delta,.975)),'interpretation':'Independent equal-size subsampling; conditionally resolved journal article/review cohort, not full extracted bibliography.'})
    return summary,differences,audit,buckets

def main():
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,default=ROOT/'offline_v1/resolved_journal_source_assignments.csv');p.add_argument('--output',type=Path,default=ROOT/'journal_strict_v1');a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    with a.input.open(newline='') as f:rows=list(csv.DictReader(f))
    summary,differences,audit,buckets=analyze(rows)
    tables=[common.write(a.output/(name+'.csv'),rr) for name,rr in [('outlet_diversity',summary),('outlet_diversity_differences',differences),('identity_mapping_audit',audit),('source_counts',buckets)]]
    common.js(a.output/'manifest.json',{'analysis_package':'1.4.0','seed':SEED,'draws':DRAWS,'numpy_version':np.__version__,'script_sha256':common.sha(__file__),'helper_script_sha256':common.sha(ROOT/'offline_external.py'),'inputs':[{'path':'offline_v1/resolved_journal_source_assignments.csv','sha256':common.sha(a.input),'bytes':a.input.stat().st_size}],'output_tables':tables,'network_used':False,'credentials_read':False,'scope':'OpenAlex resolved assigned IDs; work.type in article/review AND primary source.type journal; source-ID identity. Matched+recovered cohort deduplicates same assigned ID across occurrence statuses. Any conflicting work/source mapping stops.'})
    print(json.dumps({'summary':summary,'differences':differences,'audit':audit},indent=2))

if __name__=='__main__':main()
