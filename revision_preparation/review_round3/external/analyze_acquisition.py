#!/usr/bin/env python3
"""Analyze complete frozen responses; never acquire data or read credentials."""
from __future__ import annotations
import argparse, csv, gzip, hashlib, io, json, math
from collections import Counter,defaultdict
from pathlib import Path
import numpy as np
import offline_external as common

SEED=20261011
ROOT=Path(__file__).resolve().parent

def table(out,name,rows):
    if name!='pool_source_counts':return common.write(out/(name+'.csv'),rows)
    rows=list(rows);stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    path=out/(name+'.csv.gz');path.write_bytes(gzip.compress(stream.getvalue().encode(),mtime=0))
    return {'filename':path.name,'rows':len(rows),'sha256':common.sha(path)}

def pool_diversity(acq):
    metadata=json.loads((acq/'source_metadata.json').read_text())['sources'];rng=np.random.default_rng(SEED);summary=[];differences=[];buckets=[]
    for query in ['broad','narrow']:
        populations={r:json.loads((acq/('pool_sources_'+query+'_'+r+'.json')).read_text()) for r in ['AR5','AR6']}
        for scope in ['all_known_sources','journal_sources']:
            for identity in ['source_id','issn_l_with_source_ID_fallback']:
                counters={};excluded={}
                for rep,p in populations.items():
                    cc=Counter();drop=Counter()
                    if sum(int(g['count']) for g in p['groups'])!=p['total']:raise ValueError('Truncated source groups')
                    for g in p['groups']:
                        sid=g['key'];n=int(g['count'])
                        if not sid or sid=='unknown':drop['missing_source']+=n;continue
                        source=metadata.get(sid)
                        if scope=='journal_sources' and not source:drop['missing_source_metadata']+=n;continue
                        if scope=='journal_sources' and source.get('type')!='journal':drop['known_nonjournal']+=n;continue
                        key=source.get('issn_l') if source and identity=='issn_l_with_source_ID_fallback' else sid
                        key=key or sid
                        cc[key]+=n
                        buckets.append({'query':query,'report':rep,'scope':scope,'identity':identity,'source_id':sid,'source_name':g['key_display_name'],
                                        'issn_l':source.get('issn_l') if source else None,'source_type':source.get('type') if source else None,'grouped_identity':key,'pool_works':n})
                    counters[rep]=cc;excluded[rep]=drop
                equal=min(sum(c.values()) for c in counters.values());draws={}
                if not equal:raise ValueError('No common sample size')
                for rep,cc in counters.items():
                    counts=np.array([cc[k] for k in sorted(cc)],dtype=np.int64);n=int(counts.sum());d2=n*n/float(np.dot(counts,counts));dd=[];d0=[]
                    for b in range(1000):
                        sampled=rng.multivariate_hypergeometric(counts,equal);dd.append(equal*equal/float(np.dot(sampled,sampled)));d0.append(int(np.count_nonzero(sampled)))
                    draws[rep]=np.array(dd)
                    p=populations[rep]
                    summary.append({'query':query,'report':rep,'scope':scope,'identity':identity,'pool_total':p['total'],'included_works':n,
                                    'missing_source_works':excluded[rep]['missing_source'],'missing_source_metadata_works':excluded[rep]['missing_source_metadata'],
                                    'known_nonjournal_works':excluded[rep]['known_nonjournal'],'D0':len(cc),'D2':d2,'equal_size':equal,'draws':1000,
                                    'equal_D0_mean':float(np.mean(d0)),'equal_D2_mean':float(np.mean(dd)),
                                    'equal_D2_q025':float(np.quantile(dd,.025)),'equal_D2_q975':float(np.quantile(dd,.975)),
                                    'interval_type':'Conditional equal-size subsampling range; not a population confidence interval.'})
                delta=draws['AR6']-draws['AR5']
                differences.append({'query':query,'scope':scope,'identity':identity,'equal_size':equal,'draws':1000,
                                    'AR6_minus_AR5_D2_mean':float(delta.mean()),'q025':float(np.quantile(delta,.025)),'q975':float(np.quantile(delta,.975)),
                                    'interval_type':'Conditional equal-size subsampling difference; not a population confidence interval.'})
    return summary,differences,buckets

def mh_ratio(cells):
    """Mantel–Haenszel risk ratio, not its more familiar odds-ratio variant."""
    n1=cells[:,0]+cells[:,1];n0=cells[:,2]+cells[:,3];n=n1+n0;valid=(n1>0)&(n0>0)
    if not np.any(valid):return None,valid
    a=cells[:,0];c=cells[:,2]
    numerator=np.sum(a[valid]*n0[valid]/n[valid]);denominator=np.sum(c[valid]*n1[valid]/n[valid])
    return (float(numerator/denominator) if denominator>0 else None),valid

def citation_bin(n):return '0' if n==0 else '1-9' if n<10 else '10-99' if n<100 else '100+'

def rr_tables(acq):
    raw=json.loads((acq/'case_cohort_observations.json').read_text());obs=raw['observations'];protocol=raw['protocol'];rng=np.random.default_rng(SEED);summary=[];stratum_rows=[]
    for adjustment in ['publication_year_source_type','publication_year_source_type_citation_bin']:
        keys=[]
        for row in obs:
            key=(row['publication_year'],row['source_type'])
            if adjustment.endswith('citation_bin'):key+=(citation_bin(row['citations_2014_2021']),)
            keys.append(key)
        levels=sorted(set(keys));index={k:i for i,k in enumerate(levels)};sample_counts=np.zeros((len(levels),4),dtype=np.int64)
        for row,key in zip(obs,keys):
            col=(0 if row['AR5_cited'] else 2)+(0 if row['AR6_cited'] else 1);sample_counts[index[key],col]+=1
        weights=[];fpc=[]
        for exposed in [True,False]:
            rr=[r for r in obs if r['AR5_cited']==exposed];pop=rr[0]['sampling_population'];n=len(rr)
            weights.append(pop/n);fpc.append(math.sqrt(max(0.,1-n/pop)))
        cells=sample_counts.astype(float);cells[:,:2]*=weights[0];cells[:,2:]*=weights[1]
        estimate,overlap=mh_ratio(cells);draws=[];undefined=0
        for b in range(2500):
            boot=np.zeros_like(cells)
            for arm,cols in enumerate([slice(0,2),slice(2,4)]):
                counts=sample_counts[:,cols];n=int(counts.sum());draw=rng.multinomial(n,counts.ravel()/n).reshape(counts.shape)
                boot[:,cols]=(counts+fpc[arm]*(draw-counts))*weights[arm]
            result,_=mh_ratio(boot)
            if result is None or not math.isfinite(result):undefined+=1
            else:draws.append(result)
        for key,j in index.items():
            row={'adjustment':adjustment,'publication_year':key[0],'source_type':key[1],'citation_bin':key[2] if len(key)>2 else 'not_conditioned',
                 'exposed_AR6_yes_sample':int(sample_counts[j,0]),'exposed_AR6_no_sample':int(sample_counts[j,1]),
                 'unexposed_AR6_yes_sample':int(sample_counts[j,2]),'unexposed_AR6_no_sample':int(sample_counts[j,3]),
                 'exposed_AR6_yes_weighted':float(cells[j,0]),'exposed_AR6_no_weighted':float(cells[j,1]),
                 'unexposed_AR6_yes_weighted':float(cells[j,2]),'unexposed_AR6_no_weighted':float(cells[j,3]),'has_both_exposure_groups':bool(overlap[j])}
            stratum_rows.append(row)
        summary.append({'adjustment':adjustment,'AR5_cited_population':protocol['population_AR5_cited'],'AR5_not_cited_population':protocol['population_AR5_not_cited'],
                        'exposed_sample':sum(r['AR5_cited'] for r in obs),'unexposed_sample':sum(not r['AR5_cited'] for r in obs),
                        'exposed_AR6_positive_sample':sum(r['AR5_cited'] and r['AR6_cited'] for r in obs),
                        'unexposed_AR6_positive_sample':sum(not r['AR5_cited'] and r['AR6_cited'] for r in obs),
                        'strata':len(levels),'overlap_strata':int(overlap.sum()),'RR_Mantel_Haenszel':estimate,
                        'bootstrap_draws':2500,'undefined_bootstrap_draws':undefined,
                        'CI025':float(np.quantile(draws,.025)) if len(draws)>=2375 else None,'CI975':float(np.quantile(draws,.975)) if len(draws)>=2375 else None,
                        'interval_method':'Exposure-stratified design bootstrap with finite-population scaling and fixed inverse inclusion probabilities.',
                        'interpretation':'Association of resolved AR5 citation with resolved AR6 citation within the query-defined journal article/review population; conditioning on later citations is not a causal adjustment.'})
    return summary,stratum_rows

def indirect_table(acq):
    sample=json.loads((acq/'indirect_pool_sample.json').read_text());obs=sample['observations'];n=len(obs);k=sum(r['has_AR5_target'] for r in obs);missing=sum(r['stored_references']==0 for r in obs)
    # Wilson descriptive sample interval; not a coverage-error interval.
    z=1.959963984540054;p=k/n;d=1+z*z/n;center=(p+z*z/(2*n))/d;half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    ipcc=json.loads((acq/'indirect_IPCC_common_scope.json').read_text())
    if ipcc['AR5_target_work_IDs']!=sample['AR5_target_work_IDs']:raise ValueError('Indirect comparator uses a different target set')
    return [{'query':'broad','window':'1980-2021','scope':'article/review in journal sources','sample_size':n,'AR5_target_work_IDs':sample['AR5_target_work_IDs'],
             'at_least_one_AR5_target':k,'fraction':p,'wilson025':center-half,'wilson975':center+half,'empty_reference_lists':missing,
             'empty_reference_unknown_upper_fraction':min(1,(k+missing)/n),
             'IPCC_common_scope_AR6_works':ipcc['AR6_work_IDs'],'IPCC_common_scope_at_least_one_AR5_target':ipcc['AR6_at_least_one_AR5_target'],
             'IPCC_common_scope_fraction':ipcc['fraction'],'IPCC_common_scope_empty_references':ipcc['AR6_empty_references'],
             'IPCC_minus_pool_fraction':ipcc['fraction']-p,
             'interpretation':'Reference-list coverage is incomplete; stored-zero is not confirmed absence. Comparator uses the same raw-DOI AR5 target works, but a journal/type/year restriction.'}]

def main():
    p=argparse.ArgumentParser();p.add_argument('--acquisition',type=Path,default=ROOT/'acquisition_v1');p.add_argument('--output',type=Path,default=ROOT/'new_api_results_v1');p.add_argument('--sources-only',action='store_true');a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    tables=[];unavailable=[]
    if all((a.acquisition/('pool_sources_'+q+'_'+r+'.json')).exists() for q in ['broad','narrow'] for r in ['AR5','AR6']) and (a.acquisition/'source_metadata.json').exists():
        summary,diffs,buckets=pool_diversity(a.acquisition);tables.extend([table(a.output,'pool_outlet_diversity',summary),table(a.output,'pool_outlet_diversity_differences',diffs),table(a.output,'pool_source_counts',buckets)])
    else:unavailable.append('The complete four query/report source populations and source metadata are absent: the journal-only and ISSN-L full-pool comparisons are not estimated. A complete broad-query all-known-source-ID subset is reported separately in completed_pool_v1 when present.')
    if not a.sources_only and (a.acquisition/'case_cohort_observations.json').exists():
        summary,strata=rr_tables(a.acquisition);tables.extend([table(a.output,'conditional_relative_risk',summary),table(a.output,'relative_risk_strata',strata)])
    else:unavailable.append('Complete case-cohort observations, 2014–2021 citation counts or exposure membership queries are absent: relative risk is not estimated.')
    if not a.sources_only and (a.acquisition/'indirect_pool_sample.json').exists():tables.append(table(a.output,'indirect_pool_baseline',indirect_table(a.acquisition)))
    else:unavailable.append('Seeded later-pool reference-list sample is absent: no random-pool indirect-link proportion is reported.')
    raw=[]
    if a.sources_only:
        paths=[a.acquisition/('pool_sources_'+q+'_'+r+'.json') for q in ['broad','narrow'] for r in ['AR5','AR6']]+[a.acquisition/'source_metadata.json']
        request_ids={rid for p in paths for rid in json.loads(p.read_text())['request_ids']}
        paths += [a.acquisition/'raw'/(rid+suffix) for rid in sorted(request_ids) for suffix in ['.request.json','.response.json']]
    else:
        paths=[path for path in sorted((a.acquisition/'raw').glob('*')) if path.is_file() and not path.name.startswith('._') and '.tmp-' not in path.name]
        paths += [path for path in sorted(a.acquisition.glob('*.json')) if not path.name.startswith('._')]
    for path in paths:
        raw.append({'path':str(path.relative_to(ROOT)),'sha256':common.sha(path),'bytes':path.stat().st_size})
    for path in [ROOT/'openalex_acquire.py',ROOT/'offline_external.py']:
        raw.append({'path':path.name,'sha256':common.sha(path),'bytes':path.stat().st_size})
    manifest={'analysis_package':'1.4.0','seed':SEED,'numpy_version':np.__version__,'script_sha256':common.sha(__file__),'inputs':raw,'output_tables':tables,'unavailable':unavailable,
              'network_used':False,'credentials_read':False,'sources_only':a.sources_only,'sampling_note':'Intervals describe the specified sampling/resampling design, not metadata-linkage accuracy or complete bibliographic coverage.'}
    subset=ROOT/'completed_pool_v1/manifest.json'
    if subset.exists():manifest['completed_subset_registry']={'path':'completed_pool_v1/manifest.json','sha256':common.sha(subset),'scope':'Complete broad-query all-known-source-ID populations only.'}
    common.js(a.output/'manifest.json',manifest);print(json.dumps({'tables':len(tables),'unavailable':unavailable}))

if __name__=='__main__':main()
