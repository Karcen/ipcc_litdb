#!/usr/bin/env python3
"""Independent mathematical and synthetic audit of annotation_tools.py.

Every trial label is synthetic and exists only inside a temporary directory.
This program records software checks, not empirical human-accuracy estimates.
Frozen sources, empty templates, actual working labels and SQLite remain intact.
"""
from __future__ import annotations

import argparse
import collections
import copy
from fractions import Fraction
import importlib.util
import itertools
import json
import math
from pathlib import Path
import sys
import tempfile

sys.dont_write_bytecode = True


def load_tools(root):
    spec = importlib.util.spec_from_file_location("independently_audited_annotation_tools", root/"annotation_tools.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


def mathematical_checks(tool, nmax):
    interval_cases = coverage_cases = 0
    worst = (Fraction(1), None)
    for N in range(1, nmax+1):
        for n in range(1, N+1):
            bounds = {k:tool.finite_binary_interval(N,n,k,.05) for k in range(n+1)}
            for k,(lo,hi) in bounds.items():
                brute = [K for K in range(N+1)
                         if tool.hypergeom_tail(N,K,n,k,True)>=.025
                         and tool.hypergeom_tail(N,K,n,k,False)>=.025]
                assert brute and (lo,hi)==(min(brute),max(brute)), (N,n,k)
                interval_cases += 1
            for K in range(N+1):
                numerator = sum(math.comb(K,k)*math.comb(N-K,n-k)
                                for k in range(max(0,n-(N-K)),min(n,K)+1)
                                if bounds[k][0]<=K<=bounds[k][1])
                coverage = Fraction(numerator,math.comb(N,n))
                assert coverage>=Fraction(95,100), (N,n,K,coverage)
                coverage_cases += 1
                if coverage<worst[0]:worst=(coverage,dict(N=N,n=n,K=K))
    edges=[]
    for N,n,k,expected in [(100,1,0,(0,97)),(100,1,1,(3,100)),
                           (1000,12,0,(0,263)),(1000,12,12,(737,1000)),
                           (12,12,0,(0,0)),(12,12,12,(12,12))]:
        observed=tool.finite_binary_interval(N,n,k,.05)
        assert observed==expected
        edges.append(dict(N=N,n=n,k=k,lower=observed[0],upper=observed[1]))
    strata=[dict(stratum="domain|h1",population_ids="8",sampled_ids="1"),
            dict(stratum="domain|h2",population_ids="9",sampled_ids="3"),
            dict(stratum="other|census",population_ids="2",sampled_ids="2")]
    weighted_cases=0
    for k1,k2,k3 in itertools.product(range(2),range(4),range(3)):
        sample=[dict(stratum=s['stratum'],paper_id=s['stratum']+str(j),y=int(j<k))
                for s,k in zip(strata,[k1,k2,k3]) for j in range(int(s['sampled_ids']))]
        full,_=tool.stratified_binary(strata,sample,lambda r:r['y'],metric="synthetic_full")
        domain,details=tool.stratified_binary(strata,sample,lambda r:r['y'],metric="synthetic_domain",domain_prefix="domain|")
        assert full['estimated_positive_ids']==8*k1+3*k2+k3
        assert domain['estimated_positive_ids']==8*k1+3*k2 and domain['denominator_ids']==17
        assert full['denominator_ids']==19 and len(details)==2
        assert full['design_standard_error'] is None and domain['design_standard_error'] is None
        assert 0<=domain['lower_95']<=domain['rate']<=domain['upper_95']<=1
        weighted_cases+=1
    aggregate_worst=(Fraction(1),None)
    aggregate_cases=0
    for K1,K2 in itertools.product(range(9),range(10)):
        coverage=Fraction(0)
        for k1,k2 in itertools.product(range(2),range(4)):
            if not max(0,1-(8-K1))<=k1<=min(1,K1):continue
            if not max(0,3-(9-K2))<=k2<=min(3,K2):continue
            prob=Fraction(math.comb(K1,k1)*math.comb(8-K1,1-k1),8)*Fraction(math.comb(K2,k2)*math.comb(9-K2,3-k2),math.comb(9,3))
            sample=[dict(stratum=strata[h]['stratum'],paper_id=str(h)+str(j),y=int(j<k))
                    for h,k in enumerate([k1,k2]) for j in range(int(strata[h]['sampled_ids']))]
            rate,_=tool.stratified_binary(strata[:2],sample,lambda r:r['y'],metric="synthetic_total")
            if rate['lower_positive_ids']<=K1+K2<=rate['upper_positive_ids']:coverage+=prob
        assert coverage>=Fraction(95,100)
        aggregate_cases+=1
        if coverage<aggregate_worst[0]:aggregate_worst=(coverage,dict(K1=K1,K2=K2))
    return {'nominal_confidence_level':.95,'finite_population_N_max':nmax,
            'interval_inversion_cases':interval_cases,'finite_population_coverage_cases':coverage_cases,
            'minimum_exact_coverage_fraction':str(worst[0]),'minimum_coverage':float(worst[0]),
            'minimum_coverage_case':worst[1],'boundary_examples':edges,
            'weighted_full_and_domain_cases':weighted_cases,'aggregate_coverage_cases':aggregate_cases,
            'minimum_aggregate_coverage_fraction':str(aggregate_worst[0]),
            'minimum_aggregate_coverage':float(aggregate_worst[0]),
            'minimum_aggregate_coverage_case':aggregate_worst[1],
            'design_interpretation':'Coverage is conditional on independent SRS without replacement within fixed strata, the stated pseudo-random-hash design assumption.'}


def workflow_checks(tool,root):
    manifest,papers,occurrences,strata,summary=tool.load_bundle(root)
    source_before={k:tool.digest(root/'inputs'/v['filename']) for k,v in manifest['sources'].items()}
    templates_before={name:tool.digest(root/'templates'/name) for name in ['id_annotations.csv','occurrence_annotations.csv']}
    ids,entries=tool.canonical_templates(papers,occurrences,strata)
    actual,_,_=tool.validate(root,root/'templates/id_annotations.csv',root/'templates/occurrence_annotations.csv')
    assert not actual['errors'] and not actual['missing_files'], actual['errors'][:5]
    assert actual['status']=='pending_annotation'
    assert actual['observed']['paper_id']['unique_keys']==331
    assert actual['observed']['occurrence_id']['unique_keys']==801
    pending=tool.estimate(root,root/'templates/id_annotations.csv',root/'templates/occurrence_annotations.csv')
    assert pending['rates']==[] and pending['recurrence_stress']==[]
    for row in ids:
        row.update(annotator='SYNTHETIC SOFTWARE TEST',review_complete='yes',identity_label='correct_merge',
                   shared_link_label='same_work' if tool.is_shared(row) else 'not_shared',
                   missed_merge_search_status='complete',missed_merge_result='none_found',
                   missed_merge_search_methods='raw_reference | title | author_year',
                   missed_merge_search_notes='Synthetic test only; no human source search occurred.',
                   review_notes='Synthetic test only; no empirical judgment.')
    for row in entries:
        row.update(annotator='SYNTHETIC SOFTWARE TEST',work_groups='A',metadata_judgment='correct')
    probability={r['paper_id'] for r in papers if r['selection']==tool.PROBABILITY}
    targeted={r['paper_id'] for r in papers if r['selection']==tool.TARGETED}
    shared={r['paper_id'] for r in papers if r['selection']==tool.PROBABILITY and tool.is_shared(r)}
    checks=[]
    with tempfile.TemporaryDirectory(prefix='synthetic_independent_audit_',dir=root/'qc') as temp:
        temp=Path(temp)
        idfile,entryfile=temp/'synthetic_ids.csv',temp/'synthetic_occurrences.csv'
        def estimate(ir,er):
            tool.write_csv(idfile,ir);tool.write_csv(entryfile,er)
            result=tool.estimate(root,idfile,entryfile)
            if result['integrity']['errors']:raise AssertionError(result['integrity']['errors'][:5])
            return result
        testfile=temp/'carriage_return_round_trip.csv'
        row=[{'value':'A\rB','other':'C\nD','both':'E\r\nF'}]
        tool.write_csv(testfile,row)
        assert tool.read_csv(testfile)==row
        checks.append('Standalone CR, LF and CRLF round trip preserves field values and record count')
        result=estimate(ids,entries)
        assert result['status']=='all_planned_estimates_ready'
        rates={r['metric']:r for r in result['rates']}
        assert len(rates)==6 and rates['within_ID_identity_precision']['rate']==1
        assert rates['ID_false_merge_rate']['rate']==0 and rates['ID_false_merge_rate']['upper_95']>0
        assert rates['shared_ID_false_shared_rate']['denominator_ids']==1657
        assert rates['audited_ID_confirmed_missed_merge_rate']['denominator_ids']==46715
        checks.append('Synthetic all-zero error outcomes retain positive finite-population upper bounds')
        for missing_kind in ['probability_review','probability_search','targeted_review','targeted_search']:
            ir=copy.deepcopy(ids)
            candidates=probability if missing_kind.startswith('probability') else targeted
            victim=next(r for r in ir if r['paper_id'] in candidates)
            if missing_kind.endswith('review'):victim['review_complete']='no'
            else:victim['missed_merge_search_status']='incomplete'
            blocked=estimate(ir,entries)
            assert blocked['status']=='pending_annotation' and not blocked['rates'] and not blocked['recurrence_stress']
            dest=temp/('pending_'+missing_kind)
            tool.estimate(root,idfile,entryfile,output=dest)
            assert {p.name for p in dest.iterdir() if not p.name.startswith('._')}=={'annotation_integrity.json'}, sorted(p.name for p in dest.iterdir())
        checks.append('Incomplete probability or targeted review/search blocks every numerical estimate and emits integrity only')
        ir=copy.deepcopy(ids);er=copy.deepcopy(entries)
        for row in ir:
            if row['paper_id'] in shared:row.update(identity_label='false_merge',shared_link_label='false_shared')
        for row in er:
            if row['paper_id'] in shared:row['work_groups']='A' if row['report_id']=='AR5' else 'B'
        result=estimate(ir,er)
        rates={r['metric']:r for r in result['rates']}
        assert rates['shared_ID_false_shared_rate']['rate']==1
        for stress in result['recurrence_stress']:
            assert stress['AR5_denominator_fixed']==13503 and stress['AR6_denominator_fixed']==34869
            assert stress['retention_stress']==0 and stress['reuse_stress']==0
            corresponding=rates[stress['error_mechanism']]
            assert stress['retention_lower_95_stress']==(1657-corresponding['upper_positive_ids'])/13503
            assert stress['retention_upper_95_stress']==(1657-corresponding['lower_positive_ids'])/13503
            assert stress['reuse_lower_95_stress']==(1657-corresponding['upper_positive_ids'])/34869
            assert stress['reuse_upper_95_stress']==(1657-corresponding['lower_positive_ids'])/34869
        checks.append('Synthetic all-one false sharing propagates count bounds in reverse order at fixed denominators')
        ir=copy.deepcopy(ids);er=copy.deepcopy(entries)
        victim=next(r for r in ir if r['paper_id'] in shared)
        victim.update(identity_label='false_merge',shared_link_label='same_work')
        next(r for r in er if r['paper_id']==victim['paper_id'] and r['report_id']=='AR6')['work_groups']='A | B'
        result=estimate(ir,er)
        rates={r['metric']:r for r in result['rates']}
        assert rates['shared_ID_false_merge_rate']['rate']>0 and rates['shared_ID_false_shared_rate']['rate']==0
        checks.append('A false merger with real common work remains distinct from a false-shared identity')
        ir=copy.deepcopy(ids);er=copy.deepcopy(entries)
        victim=next(r for r in ir if r['paper_id'] in probability)
        others=[r['paper_id'] for r in ir if r['paper_id']!=victim['paper_id']][:2]
        victim.update(missed_merge_result='missed_merge_found',missed_merge_candidate_ids=' | '.join(others),
                      missed_merge_search_methods='raw_reference | title | author_year | candidate_raw_entries')
        result=estimate(ir,er)
        rate=next(r for r in result['rates'] if r['metric']=='audited_ID_confirmed_missed_merge_rate')
        assert math.isclose(rate['estimated_positive_ids'],float(victim['design_weight']),rel_tol=1e-12)
        checks.append('Several synthetic counterpart IDs count one positive audited-ID event, not duplicate pairs or linkage recall')
        ir=copy.deepcopy(ids);er=copy.deepcopy(entries)
        ir[0]['design_weight']=str(float(ir[0]['design_weight'])*2)
        tool.write_csv(idfile,ir);tool.write_csv(entryfile,er)
        bad=tool.estimate(root,idfile,entryfile)
        assert bad['status']=='invalid' and not bad['rates']
        assert any('design_weight' in e for e in bad['integrity']['errors'])
        checks.append('Altered sampling weights fail validation without changing the frozen source weights')
    assert source_before=={k:tool.digest(root/'inputs'/v['filename']) for k,v in manifest['sources'].items()}
    assert templates_before=={name:tool.digest(root/'templates'/name) for name in templates_before}
    return {'source_hashes_unchanged':True,'template_hashes_unchanged':True,
            'template_status':'pending_annotation','template_ID_count':331,'template_occurrence_count':801,
            'probability_ID_count':len(probability),'targeted_ID_count':len(targeted),
            'observed_occurrence_reports':dict(collections.Counter(r['report_id'] for r in occurrences)),
            'sampling_strata':len(strata),'sampling_frame_IDs':46715,'shared_domain_IDs':1657,
            'workflow_checks':checks,'all_trial_labels_synthetic':True,'human_accuracy_estimates_generated':False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--annotation-root',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--output',type=Path,required=True,help='New JSON audit result path')
    p.add_argument('--n-max',type=int,default=30)
    args=p.parse_args();root=args.annotation_root.expanduser().resolve();output=args.output.expanduser().resolve()
    if output.exists():raise ValueError('Choose a new audit result path')
    tool=load_tools(root)
    result={'audit':'Independent finite-population annotation estimator checks','all_checks_passed':True,
            'annotation_tools_sha256':tool.digest(root/'annotation_tools.py'),
            'audit_script_sha256':tool.digest(Path(__file__)),
            'command':'python qc/independent_estimator_audit.py --output qc/independent_estimator_audit_result.json',
            'statistical_checks':mathematical_checks(tool,args.n_max),'workflow_checks':workflow_checks(tool,root),
            'material_issue_resolutions':[
                {'issue':'LF-only minimal CSV quoting split standalone CR inside source fields',
                 'resolution':'QUOTE_ALL writer and regenerated empty templates; field round-trip and actual331/801 key integrity pass'},
                {'issue':'Within-ID accuracy wording could imply all-report correctness',
                 'resolution':'The annotation estimand concerns reviewed AR5/AR6 entries. Missed-merger search separately examines the full saved snapshot.'},
                {'issue':'Partial reviews/searches could previously produce identity estimates',
                 'resolution':'Strict bundle completion now blocks all numerical rates until probability and targeted reviews/searches are complete.'}],
            'interpretation':'Software and mathematical validation only. No actual human annotations or empirical accuracy estimates were created.'}
    output.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print(json.dumps({'all_checks_passed':True,'finite_population_coverage_cases':result['statistical_checks']['finite_population_coverage_cases'],
                      'minimum_coverage':result['statistical_checks']['minimum_coverage'],'workflow_checks':len(result['workflow_checks']['workflow_checks']),
                      'output':str(output)},indent=2))


if __name__=='__main__':main()
