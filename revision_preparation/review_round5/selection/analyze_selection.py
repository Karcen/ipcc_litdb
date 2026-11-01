#!/usr/bin/env python3
"""Add source-ID stratification to the saved descriptive selection comparison."""
from pathlib import Path
import argparse,csv,hashlib,json,math,tarfile
import numpy as np

SEED=20261011
REPS=2500

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()

def mh(c):
    n1=c[:,:2].sum(1);n0=c[:,2:].sum(1);n=n1+n0;valid=(n1>0)&(n0>0)
    a=np.sum(c[valid,0]*n0[valid]/n[valid]);b=np.sum(c[valid,2]*n1[valid]/n[valid])
    return (float(a/b) if b>0 else None),valid

def main():
    p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[3]);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    repo=a.repo.resolve();base=repo/'revision_preparation/review_round3';mp=base/'frozen_inputs/manifest.json';m=json.loads(mp.read_text())
    member=next(r for r in m['members'] if r['path']=='external/acquisition_v1/case_cohort_observations.json')
    archive=base/member['archive'];expected=next(r for r in m['archives'] if r['path']==member['archive'])
    assert sha(archive)==expected['sha256']
    with tarfile.open(archive) as t: raw=t.extractfile(member['path']).read()
    assert hashlib.sha256(raw).hexdigest()==member['sha256']
    observations=json.loads(raw)['observations'];assert len(observations)==1500
    summaries=[];strata=[];rng=np.random.default_rng(SEED)
    for specification in ['publication_year_source_ID','publication_year_source_ID_later_citation_bin']:
        keys=[]
        for r in observations:
            k=(r['publication_year'],r['source_id'] or 'unknown_source')
            if specification.endswith('citation_bin'):
                n=r['citations_2014_2021'];k+=('0' if n==0 else '1-9' if n<10 else '10-99' if n<100 else '100+',)
            keys.append(k)
        levels=sorted(set(keys));index={k:i for i,k in enumerate(levels)};counts=np.zeros((len(levels),4),dtype=np.int64)
        for r,k in zip(observations,keys): counts[index[k],(0 if r['AR5_cited'] else 2)+(0 if r['AR6_cited'] else 1)]+=1
        weights=[];fpcs=[]
        for exposed in [True,False]:
            arm=[r for r in observations if r['AR5_cited']==exposed];population=arm[0]['sampling_population'];n=len(arm)
            assert all(r['sampling_population']==population for r in arm)
            weights.append(population/n);fpcs.append(math.sqrt(1-n/population))
        weighted=counts.astype(float);weighted[:,:2]*=weights[0];weighted[:,2:]*=weights[1]
        estimate,valid=mh(weighted);draws=[];undefined=0
        for _ in range(REPS):
            boot=np.zeros_like(weighted)
            for arm,sl in enumerate([slice(0,2),slice(2,4)]):
                x=counts[:,sl];n=int(x.sum());b=rng.multinomial(n,x.ravel()/n).reshape(x.shape)
                boot[:,sl]=(x+fpcs[arm]*(b-x))*weights[arm]
            value,_=mh(boot)
            if value is None or not math.isfinite(value): undefined+=1
            else: draws.append(value)
        summaries.append(dict(specification=specification,RR=estimate,lower_95=float(np.quantile(draws,.025)) if len(draws)>=.95*REPS else '',upper_95=float(np.quantile(draws,.975)) if len(draws)>=.95*REPS else '',strata=len(levels),common_support_strata=int(valid.sum()),AR5_cited_sample_in_common_support=int(counts[valid,:2].sum()),AR5_not_cited_sample_in_common_support=int(counts[valid,2:].sum()),AR5_cited_positive_in_support=int(counts[valid,0].sum()),AR5_not_cited_positive_in_support=int(counts[valid,2].sum()),bootstrap_draws=REPS,undefined_draws=undefined,interpretation='Descriptive common-support association; source-ID and later citation strata do not identify a causal effect or repair asymmetric DOI observation'))
        for key,i in index.items(): strata.append(dict(specification=specification,publication_year=key[0],source_ID=key[1],later_citation_bin=key[2] if len(key)>2 else 'not_conditioned',AR5_cited_AR6_yes=int(counts[i,0]),AR5_cited_AR6_no=int(counts[i,1]),other_AR6_yes=int(counts[i,2]),other_AR6_no=int(counts[i,3]),common_support=bool(valid[i])))
    a.output.mkdir(parents=True,exist_ok=False)
    for name,rows in [('source_stratified_RR.csv',summaries),('source_stratified_support.csv',strata)]:
        with (a.output/name).open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]),lineterminator='\n');w.writeheader();w.writerows(rows)
    manifest=dict(analysis_version='1.6.0',seed=SEED,numpy_version=np.__version__,producer_sha256=sha(Path(__file__)),inputs={str(mp.relative_to(repo)):sha(mp),str(archive.relative_to(repo)):sha(archive),member['path']:member['sha256']},outputs={p.name:sha(p) for p in sorted(a.output.glob('*.csv'))},limitations=['Later citations are post-exposure variables.','Topic, author overlap and symmetric DOI observation are not supplied.','Stratification changes the observed common-support population.'])
    (a.output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(summaries,indent=2))

if __name__=='__main__': main()
