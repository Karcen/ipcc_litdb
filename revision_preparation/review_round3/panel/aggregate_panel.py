#!/usr/bin/env python3
"""Aggregate actual panel responses and audit their provenance.

Agreement is not accuracy. Ledger-based rates are explicitly conditional on
provisional reference labels whose human adjudication is not archived here.
"""
from __future__ import annotations
import argparse, collections, csv, hashlib, json, math, pathlib, re
from run_panel import validate
HERE=pathlib.Path(__file__).resolve().parent
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,j):p.write_text(json.dumps(j,ensure_ascii=False,indent=2)+'\n')
def csvout(p,rows,fields=None):
    fields=fields or list(rows[0]) if rows else fields or []
    with p.open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def cp(k,n,alpha=.05):
    if not n:return None,None
    def cdf(k,n,p):return sum(math.comb(n,x)*p**x*(1-p)**(n-x) for x in range(k+1))
    def bisect(test,target):
        lo,hi=0.,1.
        for _ in range(70):
            m=(lo+hi)/2
            if test(m)>target:lo=m
            else:hi=m
        return (lo+hi)/2
    lower=0. if k==0 else bisect(lambda p:cdf(k-1,n,p),1-alpha/2)
    upper=1. if k==n else bisect(lambda p:cdf(k,n,p),alpha/2)
    return lower,upper
def kappa(votes):
    if not votes:return None
    n=len(votes[0]);N=len(votes);counts=collections.Counter(x for row in votes for x in row)
    pe=sum((v/(N*n))**2 for v in counts.values())
    pbar=sum(sum(v*(v-1) for v in collections.Counter(row).values())/(n*(n-1)) for row in votes)/N
    return (pbar-pe)/(1-pe) if pe<1 else None
def canon(v,field):
    if v is None:return 'unresolvable'
    if field=='outlet':return re.sub(r'[^\w]+','',v.casefold()) or 'unresolvable'
    return str(v)
def majority(votes):
    c=collections.Counter(votes);v,n=c.most_common(1)[0]
    return (v,'unanimous' if n==3 else 'two_of_three') if n>=2 else ('unresolvable','no_majority')
def archived_rule_year(raw):
    # Exact frozen coverage_robustness.raw_year regex; retain year-letter suffix.
    y=re.search(r'\b(1[6-9]\d{2}|20\d{2})[a-z]?\b',str(raw or '')[:300])
    return y.group(1) if y else 'unresolvable'
def main():
    a=argparse.ArgumentParser();a.add_argument('--run-name',required=True);args=a.parse_args()
    root=HERE/'calls'/args.run_name;out=HERE/'results'/args.run_name;out.mkdir(parents=True,exist_ok=True)
    armdata={};usages=[];inputpaths=[]
    for arm in ('blind','metadata'):
        packet=json.loads((HERE/f'pilot/{arm}_50.json').read_text());armdata[arm]={}
        for variant in 'ABC':
            d=root/f'{arm}_{variant}';result=json.loads((d/'result.json').read_text());req=json.loads((d/'request.json').read_text())
            if result['status'] not in ('valid','unresolvable_after_two_invalid_outputs'):raise SystemExit(f'Cannot aggregate incomplete/invalid call {arm} {variant}: {result["status"]}')
            p=d/'response.json';j=json.loads(p.read_text());validate(j,packet)
            if req['packet_sha256']!=sha(HERE/f'pilot/{arm}_50.json'):raise SystemExit('packet changed after call')
            armdata[arm][variant]={c['case_id']:c for c in j['cases']};inputpaths += [d/'request.json',d/'result.json',d/'prompt.txt',p]
            for attempt in result['attempts']:
                for u in attempt['usage']:
                    usages.append({'arm':arm,'variant':variant,'input_tokens':u['input_tokens'],'cached_input_tokens':u.get('cached_input_tokens',0),'output_tokens':u['output_tokens'],'reasoning_output_tokens':u.get('reasoning_output_tokens',0),'elapsed_seconds':result['elapsed_seconds']})
    packet=json.loads((HERE/'pilot/blind_50.json').read_text());cases={c['case_id']:c for c in packet['cases']}
    controls={c['case_id']:c for c in json.loads((HERE/'frames/control_key.json').read_text())}
    design=json.loads((HERE/'frames/sampling_design.json').read_text());parserroles={m['case_id']:m for m in json.loads((HERE/'frames/case_key.json').read_text()) if m['origin'].startswith('parser:')}
    case_rows=[];record_rows=[];agreement_rows=[];control_rows=[];parser=[];effects=[]
    consensus={}
    for arm,data in armdata.items():
        consensus[arm]={}
        for cid,c in cases.items():
            votes=[data[v][cid]['identity'] for v in 'ABC'];label,decision=majority(votes)
            rvotes=[data[v][cid]['cross_report_recurrence'] for v in 'ABC'];rlab,rdec=majority(rvotes)
            case_rows.append({'arm':arm,'case_id':cid,'task':c['task'],'A':votes[0],'B':votes[1],'C':votes[2],'consensus_identity':label,'decision':decision,'recurrence_A':rvotes[0],'recurrence_B':rvotes[1],'recurrence_C':rvotes[2],'consensus_recurrence':rlab,'recurrence_decision':rdec})
            consensus[arm][cid]={'identity':label,'cross_report_recurrence':rlab,'records':{}}
            for rr in c['records']:
                rid=rr['record_id'];vs={v:next(r for r in data[v][cid]['records'] if r['record_id']==rid) for v in 'ABC'}
                row={'arm':arm,'case_id':cid,'record_id':rid}
                for field in ('cited_year','outlet','source_type','contains_multiple_citations'):
                    values=[canon(vs[v][field],field) for v in 'ABC'];value,decision=majority(values)
                    row.update({field+'_'+v:values[i] for i,v in enumerate('ABC')});row[field+'_consensus']=value;row[field+'_decision']=decision
                record_rows.append(row);consensus[arm][cid]['records'][rid]=row
                if arm=='blind' and cid in parserroles:
                    raw=rr['raw_reference'];rule=archived_rule_year(raw)
                    panel=row['cited_year_consensus'];stratum=parserroles[cid]['origin'].split(':',2)[1]
                    parser.append({'case_id':cid,'stratum':stratum,'rule_year':rule,'panel_year':panel,'panel_resolved':int(panel!='unresolvable'),'disagreement':int(panel!='unresolvable' and rule!=panel),'interpretation':'parser-panel disagreement, not a human-validated error'})
        for field in ('identity','cross_report_recurrence','cited_year','outlet','source_type','contains_multiple_citations'):
            if field in ('identity','cross_report_recurrence'):votes=[[data[v][cid][field] for v in 'ABC'] for cid,c in cases.items() if c['task']=='identity']
            else:votes=[[r[field+'_'+v] for v in 'ABC'] for r in record_rows if r['arm']==arm]
            agreement_rows.append({'arm':arm,'field':field,'items':len(votes),'unanimous_items':sum(len(set(v))==1 for v in votes),'majority_items':sum(collections.Counter(v).most_common(1)[0][1]>=2 for v in votes),'fleiss_kappa':kappa(votes),'interpretation':'same-model prompt agreement; not accuracy or independent-model reliability'})
        for group in ('ledger','raw_doi_equal'):
            selected=[(cid,x) for cid,x in controls.items() if cid in cases and x['reference_provenance'].startswith(group+':')]
            for reviewer in (*'ABC','majority'):
                for reference in ('same_study','different_studies'):
                    ss=[(cid,x) for cid,x in selected if x['reference_label']==reference]
                    if not ss:continue
                    labels=[consensus[arm][cid]['identity'] if reviewer=='majority' else data[reviewer][cid]['identity'] for cid,_ in ss]
                    k=sum(x==reference for x in labels);lo,hi=cp(k,len(ss))
                    control_rows.append({'arm':arm,'control_set':group,'reviewer':reviewer,'target_field':'all_focal_citations_one_study','reference_label':reference,'matching_reference_labels':k,'denominator':len(ss),'agreement':k/len(ss),'cp95_lower':lo,'cp95_upper':hi,'unresolvable':labels.count('unresolvable'),'human_reference_labels_archived':False,'interpretation':'agreement conditional on provisional ledger identity labels; NOT recurrence specificity' if group=='ledger' else 'agreement with raw-DOI-equality heuristic; not sensitivity'})
    for cid,c in cases.items():
        effects.append({'case_id':cid,'field':'identity','blind':consensus['blind'][cid]['identity'],'metadata':consensus['metadata'][cid]['identity'],'changed':int(consensus['blind'][cid]['identity']!=consensus['metadata'][cid]['identity'])})
        effects.append({'case_id':cid,'field':'cross_report_recurrence','blind':consensus['blind'][cid]['cross_report_recurrence'],'metadata':consensus['metadata'][cid]['cross_report_recurrence'],'changed':int(consensus['blind'][cid]['cross_report_recurrence']!=consensus['metadata'][cid]['cross_report_recurrence'])})
        for rr in c['records']:
            rid=rr['record_id']
            for field in ('cited_year','outlet','source_type','contains_multiple_citations'):
                key=field+'_consensus';x=consensus['blind'][cid]['records'][rid][key];y=consensus['metadata'][cid]['records'][rid][key]
                effects.append({'case_id':cid+':'+rid,'field':field,'blind':x,'metadata':y,'changed':int(x!=y)})
    csvout(out/'case_consensus.csv',case_rows);csvout(out/'record_consensus.csv',record_rows)
    csvout(out/'agreement.csv',agreement_rows);csvout(out/'control_reference_agreement.csv',control_rows)
    csvout(out/'parser_disagreements.csv',parser);csvout(out/'metadata_arm_comparison.csv',effects);csvout(out/'call_usage.csv',usages)
    summary_rows=[{'quantity':'pilot_distinct_cases','numerator':len(cases),'denominator':'','value':len(cases),'interpretation':'pilot only'},
                  {'quantity':'pilot_record_count','numerator':sum(len(c['records']) for c in cases.values()),'denominator':'','value':sum(len(c['records']) for c in cases.values()),'interpretation':'raw records per arm'},
                  {'quantity':'model_families','numerator':1,'denominator':'','value':1,'interpretation':'single requested model, three prompts'},
                  {'quantity':'prompt_variants','numerator':3,'denominator':'','value':3,'interpretation':'not independent models'},
                  {'quantity':'actual_completed_calls','numerator':6,'denominator':'','value':6,'interpretation':'two arms by three prompts'}]
    for field in ('identity','cross_report_recurrence','cited_year','outlet','source_type','contains_multiple_citations'):
        r=[x for x in effects if x['field']==field];n=sum(x['changed'] for x in r)
        summary_rows.append({'quantity':'metadata_arm_changes_'+field,'numerator':n,'denominator':len(r),'value':n/len(r),'interpretation':'within-case metadata-arm association, not randomized independent-model causal effect'})
    for stratum in sorted({r['stratum'] for r in parser}):
        r=[x for x in parser if x['stratum']==stratum];n=sum(x['disagreement'] for x in r);den=sum(x['panel_resolved'] for x in r)
        summary_rows.append({'quantity':'parser_panel_disagreement_'+stratum,'numerator':n,'denominator':den,'value':n/den if den else None,'interpretation':'pilot five per stratum; not full-sample or human-validated error'})
    csvout(out/'pilot_summary.csv',summary_rows)
    total=sum(u['input_tokens']+u['output_tokens'] for u in usages);full=len(json.loads((HERE/'frames/blind_all.json').read_text())['cases']);projected=math.ceil(full/50)*6
    cost={'pilot_distinct_cases':50,'pilot_model_calls':6,'prompt_variants':3,'model_families':1,'requested_model':'gpt-6.1-sol','actual_reported_tokens':{'input':sum(u['input_tokens'] for u in usages),'cached_input':sum(u['cached_input_tokens'] for u in usages),'output':sum(u['output_tokens'] for u in usages),'total':total},'largest_elapsed_call_seconds':max(u['elapsed_seconds'] for u in usages),'canonical_frame_cases_before_candidate_extensions':full,'full_calls_at_50_cases_per_call':projected,'linear_pilot_token_extrapolation_including_context_overhead':total*projected/6,'money_cost':None,'money_reason':'Existing ChatGPT subscription authentication; no per-token price or incremental monetary charge exposed by the CLI. Usage limits still apply. Token extrapolation is a planning approximation, not a bill.','full_execution_started':False,'temperature':None,'generation_seed':None,'sampling_seed':20261011,'temperature_and_generation_seed_exposed':False}
    dump(out/'cost_estimate.json',cost)
    candidate_summary=HERE/'candidates/summary.json'
    candidates=json.loads(candidate_summary.read_text())['candidate_cross_id_pairs'] if candidate_summary.exists() else 0
    plan=[]
    for scope,n,arms in [('core_blind_only',full,1),('core_both_arms',full,2),('core_plus_missing_link_candidates_blind_only',full+candidates,1),('core_plus_candidates_both_arms',full+candidates,2)]:
        calls=math.ceil(n/50)*3*arms
        plan.append({'scope':scope,'case_packets':n,'prompt_variants':3,'arms':arms,'planned_calls_at_50_cases':calls,'linear_token_extrapolation':total*calls/6,'wall_clock_hours_at_observed_slowest_call_and_three_workers':max(u['elapsed_seconds'] for u in usages)*math.ceil(calls/3)/3600,'monetary_cost':'unavailable','status':'planning approximation; not executed'})
    csvout(out/'full_run_planning.csv',plan)
    outputs=[p for p in out.iterdir() if p.is_file() and p.name!='manifest.json']
    dump(out/'manifest.json',{'analysis_package':'1.4.0','data_seed':20261011,'input_sha256':{str(p.relative_to(HERE)):sha(p) for p in inputpaths+[pathlib.Path(__file__),HERE/'preparation_manifest.json']},'output_sha256':{p.name:sha(p) for p in outputs},'controls_have_unconfirmed_human_reference_provenance':True,'no_human_adjudication':True,'model_family_count':1,'model_string':'gpt-6.1-sol','pilot_only':True})
    print(json.dumps({'pilot_cases':50,'actual_calls':6,'output_tables':9,'actual_tokens':total,'projected_full_calls':projected,'full_execution_started':False}))
if __name__=='__main__':main()
