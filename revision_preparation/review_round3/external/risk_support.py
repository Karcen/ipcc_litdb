#!/usr/bin/env python3
"""Validate exact-period citation counts and disclose MH common support."""
from __future__ import annotations
import argparse,csv,json
from pathlib import Path
import offline_external as common

ROOT=Path(__file__).resolve().parent

def main():
    p=argparse.ArgumentParser();p.add_argument('--acquisition',type=Path,default=ROOT/'acquisition_v1');p.add_argument('--results',type=Path,default=ROOT/'new_api_results_v2');p.add_argument('--output',type=Path,default=ROOT/'risk_support_v1');a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    case=a.acquisition/'case_cohort_observations.json';data=json.loads(case.read_text());proto=data['protocol'];obs=data['observations'];expected={r['work_id']:r for r in proto['sample']}
    if len(expected)!=len(proto['sample']) or len(obs)!=len(expected) or len({r['work_id'] for r in obs})!=len(obs) or set(expected)!={r['work_id'] for r in obs}:raise ValueError('Case-cohort observations incomplete or duplicate')
    inputs=[case,a.results/'relative_risk_strata.csv',a.results/'conditional_relative_risk.csv'];count_files=[]
    for row in obs:
        if any(row.get(k)!=v for k,v in expected[row['work_id']].items()):raise ValueError('Sample identity/outcome changed after protocol freeze')
        count=row['citations_2014_2021']
        if type(count) is not int or count<0:raise ValueError('Missing or invalid citation count')
        rid=row['citation_count_request_id'];req=a.acquisition/'raw'/(rid+'.request.json');res=a.acquisition/'raw'/(rid+'.response.json');q=json.loads(req.read_text());raw=res.read_bytes();d=json.loads(raw)
        wanted={'filter':'cites:'+row['work_id'].rsplit('/',1)[-1]+',publication_year:2014-2021,type:article|review','per_page':'1','select':'id'}
        if q['parameters']!=wanted or q.get('response_sha256')!=__import__('hashlib').sha256(raw).hexdigest() or not q.get('json_success') or d['meta']['count']!=count:raise ValueError('Count does not match exact-period frozen API response')
        count_files.extend([req,res])
    strata=list(csv.DictReader((a.results/'relative_risk_strata.csv').open()));out=[]
    for adjustment in sorted({r['adjustment'] for r in strata}):
        rr=[r for r in strata if r['adjustment']==adjustment];both=[r for r in rr if r['has_both_exposure_groups']=='True']
        for arm in ['exposed','unexposed']:
            sample=sum(int(r[arm+'_AR6_yes_sample'])+int(r[arm+'_AR6_no_sample']) for r in rr);inside=sum(int(r[arm+'_AR6_yes_sample'])+int(r[arm+'_AR6_no_sample']) for r in both)
            mass=sum(float(r[arm+'_AR6_yes_weighted'])+float(r[arm+'_AR6_no_weighted']) for r in rr);mass_in=sum(float(r[arm+'_AR6_yes_weighted'])+float(r[arm+'_AR6_no_weighted']) for r in both)
            pop=proto['population_AR5_cited'] if arm=='exposed' else proto['population_AR5_not_cited']
            target_sample=proto['exposed_sample'] if arm=='exposed' else proto['unexposed_sample']
            if sample!=target_sample or abs(mass-pop)>1e-6:raise ValueError('Risk-stratum sample mass does not match probability design')
            out.append({'adjustment':adjustment,'arm':arm,'sampling_population':pop,'sample_n':sample,'inverse_inclusion_weight':pop/sample,'total_strata':len(rr),'strata_with_both_arms':len(both),'strata_without_both_arms':len(rr)-len(both),'sample_in_supported_strata':inside,'sample_excluded_without_both_arms':sample-inside,'sample_support_fraction':inside/sample,'expanded_sample_mass_in_supported_strata':mass_in,'expanded_sample_mass_excluded':mass-mass_in,'interpretation':'Common support is observed in the probability sample; expanded sample mass is not an exact census of population support. RR pertains to the supported journal-pool comparison.'})
    tables=[common.write(a.output/'relative_risk_support.csv',out)]
    inputs+=count_files
    common.js(a.output/'manifest.json',{'analysis_package':'1.4.0','seed':20261011,'script_sha256':common.sha(__file__),'inputs':[{'path':str(p.relative_to(ROOT)),'sha256':common.sha(p),'bytes':p.stat().st_size} for p in inputs],'output_tables':tables,'network_used':False,'credentials_read':False})
    report={'passed':True,'probability_sample_observations':len(obs),'exact_2014_2021_count_responses_verified':len(obs),'observed_zero_counts':sum(r['citations_2014_2021']==0 for r in obs),'observed_positive_counts':sum(r['citations_2014_2021']>0 for r in obs),'missing_counts':0,'zero_imputation_used':False,'sample_IDs_unique_and_protocol_unchanged':True,'source_types':sorted({r['source_type'] for r in obs})}
    common.js(a.output/'validation_report.json',report);print(json.dumps({'support':out,'validation':report},indent=2))

if __name__=='__main__':main()
