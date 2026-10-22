#!/usr/bin/env python3
"""All-known-source diversity from complete archived broad-query cursor responses.

No network or credential access. This is not a journal-only or ISSN-L analysis.
"""
from __future__ import annotations
import argparse, csv, json
from collections import Counter
from pathlib import Path
import numpy as np
import offline_external as common

ROOT=Path(__file__).resolve().parent
SEED=20261011
DRAWS=1000

def replay(acquisition, name):
    p=acquisition/name; bundle=json.loads(p.read_text()); counts=Counter(); names={}; inputs=[p]; cursor='*'
    request_ids=bundle['request_ids']
    if len(set(request_ids))!=len(request_ids): raise ValueError('Duplicate request IDs')
    for rid in request_ids:
        req=acquisition/'raw'/(rid+'.request.json'); res=acquisition/'raw'/(rid+'.response.json')
        if not req.exists() or not res.exists(): raise ValueError('Missing archived response or parameters')
        q=json.loads(req.read_text()); d=json.loads(res.read_text()); inputs.extend([req,res])
        if q['parameters'].get('cursor')!=cursor: raise ValueError('Broken cursor chain')
        if int(d['meta']['count'])!=int(bundle['total']): raise ValueError('Changing population total during paging')
        for g in d['group_by']:
            k=g['key']; n=g['count']
            if k in counts: raise ValueError('Repeated source bucket across pages')
            if not isinstance(n,int) or n<1: raise ValueError('Invalid source count')
            counts[k]=n; names[k]=g['key_display_name']
        cursor=d['meta'].get('next_cursor')
    if cursor is not None: raise ValueError('Incomplete cursor sequence')
    if sum(counts.values())!=int(bundle['total']): raise ValueError('Source totals do not cover population')
    if counts!=Counter({g['key']:g['count'] for g in bundle['groups']}): raise ValueError('Aggregated source data differ from raw responses')
    return bundle,counts,names,inputs

def known_counts(counts):
    return {k:n for k,n in counts.items() if k and k!='unknown'}

def diversity(counts):
    x=np.array(list(counts.values()),dtype=np.int64); n=int(x.sum())
    if not n: raise ValueError('Empty known-source population')
    return len(x),n*n/sum(int(k)*int(k) for k in x)

def analyze(acquisition, draws=DRAWS):
    populations={}; inputs=[]
    for rep in ['AR5','AR6']:
        bundle,counts,names,pp=replay(acquisition,'pool_sources_broad_'+rep+'.json')
        populations[rep]=(bundle,counts,names); inputs.extend(pp)
    equal=min(sum(known_counts(v[1]).values()) for v in populations.values()); rng=np.random.default_rng(SEED)
    summary=[]; sample_values={}; buckets=[]
    for rep in ['AR5','AR6']:
        bundle,cc,names=populations[rep]; known=known_counts(cc); d0,d2=diversity(known); n=sum(known.values()); x=np.array([known[k] for k in sorted(known)],dtype=np.int64)
        d0draw=[]; d2draw=[]
        for _ in range(draws):
            z=rng.multivariate_hypergeometric(x,equal); d0draw.append(int(np.count_nonzero(z))); d2draw.append(equal*equal/sum(int(k)*int(k) for k in z))
        sample_values[rep]={'D0':np.array(d0draw),'D2':np.array(d2draw)}
        summary.append({'query':'broad','report':rep,'window':str(bundle['window'][0])+'-'+str(bundle['window'][1]),'source_scope':'all_known_sources','identity':'source_id','pool_total':bundle['total'],'known_source_works':n,'missing_source_works':bundle['total']-n,'missing_source_fraction':1-n/bundle['total'],'D0_full':d0,'D2_full':d2,'equal_size':equal,'draws':draws,'equal_D0_mean':float(np.mean(d0draw)),'equal_D0_q025':float(np.quantile(d0draw,.025)),'equal_D0_q975':float(np.quantile(d0draw,.975)),'equal_D2_mean':float(np.mean(d2draw)),'equal_D2_q025':float(np.quantile(d2draw,.025)),'equal_D2_q975':float(np.quantile(d2draw,.975)),'interpretation':'Broad title/abstract climate query; article/review works in all known primary sources, including nonjournals; unknown-source works excluded. Subsampling range is not a population confidence interval.'})
        for k,n in sorted(cc.items(),key=lambda z:str(z[0])):
            buckets.append({'query':'broad','report':rep,'source_id':k,'source_name':names[k],'pool_works':n,'known_source':bool(k and k!='unknown')})
    differences=[]
    for metric in ['D0','D2']:
        delta=sample_values['AR6'][metric]-sample_values['AR5'][metric]
        differences.append({'query':'broad','source_scope':'all_known_sources','identity':'source_id','metric':metric,'equal_size':equal,'draws':draws,'AR6_minus_AR5_mean':float(np.mean(delta)),'q025':float(np.quantile(delta,.025)),'q975':float(np.quantile(delta,.975)),'interpretation':'Equal-size independent subsampling from complete archived known-source buckets; broader-source sensitivity, not a journal-only result.'})
    return summary,differences,buckets,inputs

def main():
    p=argparse.ArgumentParser();p.add_argument('--acquisition',type=Path,default=ROOT/'acquisition_v1');p.add_argument('--output',type=Path,default=ROOT/'completed_pool_v1');args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    summary,differences,buckets,inputs=analyze(args.acquisition)
    tables=[common.write(args.output/(name+'.csv'),rows) for name,rows in [('pool_outlet_diversity',summary),('pool_outlet_diversity_differences',differences),('pool_source_counts',buckets)]]
    common.js(args.output/'manifest.json',{'analysis_package':'1.4.0','seed':SEED,'numpy_version':np.__version__,'script_sha256':common.sha(__file__),'helper_script_sha256':common.sha(ROOT/'offline_external.py'),'inputs':[{'path':str(p.relative_to(ROOT)),'sha256':common.sha(p),'bytes':p.stat().st_size} for p in inputs], 'output_tables':tables,'network_used':False,'credentials_read':False,'scope':'Complete broad-query populations only. Unknown sources excluded; source type and ISSN-L enrichment were not acquired. Narrow AR6, adjusted relative risk and indirect random-pool comparator unavailable.'})
    print(json.dumps({'summary':summary,'differences':differences},indent=2))

if __name__=='__main__':main()
