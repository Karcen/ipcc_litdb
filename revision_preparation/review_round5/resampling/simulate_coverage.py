#!/usr/bin/env python3
"""Coverage in an explicit empirical chapter model, not a real-world error model."""
from pathlib import Path
from collections import defaultdict
import argparse,csv,gzip,hashlib,json,math
import numpy as np

SEED=20261011
OUTER=250
INNER=1000

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()

def boot(totals,strata,scheme,rng):
    multiplicities=np.zeros((INNER,len(totals)),dtype=np.int64)
    blocks=strata if scheme=='WG_stratified' else [list(range(len(totals)))]
    for ix in blocks:
        n=len(ix);multiplicities[:,ix]=rng.multinomial(n,np.full(n,1/n),size=INNER)
    sums=np.einsum('bc,ct->bt',multiplicities,totals,optimize=False)
    if np.any(sums[:,0]<=0): raise ValueError('Empty bootstrap draw; not replaced')
    return sums[:,1]/sums[:,0]

def main():
    p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[3]);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    repo=a.repo.resolve();source=repo/'revision_preparation/review_round2/raw_age_v1/raw_age_occurrence_audit.csv.gz'
    with gzip.open(source,'rt') as f: rows=list(csv.DictReader(f))
    rng=np.random.default_rng(SEED);outputs=[];trials=[]
    for cohort in ['all_extracted','matched_occurrences']:
      for origin in ['source_publication_year','documented_cutoff_year']:
        frames={};true={}
        for report in ['AR5','AR6']:
            rr=[r for r in rows if r['report_id']==report and (cohort=='all_extracted' or r['match_status'] in {'matched_doi','matched_title'})]
            chapters=sorted({r['chapter_id'] for r in rr});index={c:i for i,c in enumerate(chapters)}
            totals=np.zeros((len(chapters),2),dtype=np.int64);groups=defaultdict(set)
            for r in rr:
                groups[r['wg_label']].add(index[r['chapter_id']])
                if not r['raw_leading_year'] or not r[origin]: continue
                age=int(r[origin])-int(r['raw_leading_year'])
                if 0<=age<=60: totals[index[r['chapter_id']]]+=[1,age]
            strata=[sorted(ix) for _,ix in sorted(groups.items())]
            assert sorted(i for ix in strata for i in ix)==list(range(len(chapters)))
            frames[report]=(totals,strata);true[report]=totals[:,1].sum()/totals[:,0].sum()
        truth=float(true['AR6']-true['AR5']);covered={s:[] for s in ['unstratified','WG_stratified']};widths={s:[] for s in covered}
        for trial in range(OUTER):
            synthetic={}
            for report,(totals,strata) in frames.items():
                t=totals.copy()
                for ix in strata: t[ix]=totals[rng.choice(ix,size=len(ix),replace=True)]
                synthetic[report]=(t,strata)
            for scheme in covered:
                draws={r:boot(t,ix,scheme,rng) for r,(t,ix) in synthetic.items()}
                lo,hi=np.quantile(draws['AR6']-draws['AR5'],[.025,.975],method='linear')
                hit=bool(lo<=truth<=hi);covered[scheme].append(hit);widths[scheme].append(float(hi-lo))
                trials.append(dict(cohort=cohort,age_origin=origin,scheme=scheme,trial=trial,known_model_parameter=truth,lower=float(lo),upper=float(hi),covered=hit))
        for scheme,hits in covered.items():
            estimate=sum(hits)/OUTER;z=1.959963984540054;den=1+z*z/OUTER;center=(estimate+z*z/(2*OUTER))/den;half=z*math.sqrt(estimate*(1-estimate)/OUTER+z*z/(4*OUTER**2))/den
            outputs.append(dict(cohort=cohort,age_origin=origin,scheme=scheme,known_model_mean_difference=truth,covered_trials=sum(hits),outer_trials=OUTER,inner_bootstrap_draws=INNER,estimated_coverage=estimate,MC_standard_error=math.sqrt(estimate*(1-estimate)/OUTER),MC_Wilson_lower_95=center-half,MC_Wilson_upper_95=center+half,mean_interval_width=float(np.mean(widths[scheme])),data_generator='Independent report draws from empirical chapter distributions, stratified by observed WG label with fixed chapter counts',scope='Coverage only under this empirical cluster model; excludes extraction, linkage, parser and index errors'))
        print(cohort,origin,'complete',flush=True)
    a.output.mkdir(parents=True,exist_ok=False)
    for name,data in [('empirical_model_coverage.csv',outputs),('simulation_trials.csv',trials)]:
        with (a.output/name).open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(data[0]),lineterminator='\n');w.writeheader();w.writerows(data)
    (a.output/'manifest.json').write_text(json.dumps(dict(analysis_version='1.6.0',seed=SEED,outer_trials=OUTER,inner_bootstrap_draws=INNER,numpy_version=np.__version__,producer_sha256=sha(Path(__file__)),source_path=str(source.relative_to(repo)),source_sha256=sha(source),outputs={p.name:sha(p) for p in sorted(a.output.glob('*.csv'))},interpretation='A conditional coverage experiment under an explicit empirical chapter model, not calibrated uncertainty for the original collection.'),indent=2)+'\n')

if __name__=='__main__': main()
