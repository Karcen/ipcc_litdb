#!/usr/bin/env python3
"""Run actual ephemeral Codex calls; never manufacture missing responses.

No external API credentials are read. Authentication is handled by the installed
Codex CLI and the user's existing ChatGPT subscription session.
"""
from __future__ import annotations
import argparse, concurrent.futures, datetime, hashlib, json, os, pathlib, shutil, subprocess, time

HERE=pathlib.Path(__file__).resolve().parent
MODEL='gpt-6.1-sol'
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,j):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(j,ensure_ascii=False,indent=2)+'\n')
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def validate(response,packet):
    if not isinstance(response,dict) or set(response)!={'cases'}:raise ValueError('root schema')
    expected={c['case_id']:c for c in packet['cases']};got={}
    for c in response['cases']:
        if set(c)!={'case_id','identity','cross_report_recurrence','records','reason'} or c['identity'] not in ('same_study','different_studies','unresolvable','not_applicable'):raise ValueError('case schema')
        if c['cross_report_recurrence'] not in ('recurrent','non_recurrent','unresolvable','not_applicable'):raise ValueError('recurrence schema')
        cid=c['case_id']
        if cid in got or cid not in expected:raise ValueError('case IDs differ')
        got[cid]=c
        ids=[r['record_id'] for r in c['records']]
        if sorted(ids)!=sorted(r['record_id'] for r in expected[cid]['records']):raise ValueError('record IDs differ')
        for r in c['records']:
            if set(r)!={'record_id','cited_year','outlet','source_type','contains_multiple_citations','evidence_quote'}:raise ValueError('record schema')
            if r['contains_multiple_citations'] not in ('yes','no','unresolvable'):raise ValueError('compound-entry flag')
            if r['cited_year'] is not None and (type(r['cited_year']) is not int or not 1500<=r['cited_year']<=2026):raise ValueError('year value')
            if r['source_type'] not in ('journal','book','report','conference','other','unresolvable'):raise ValueError('source type')
            quote=r['evidence_quote']
            if len(quote.split())>25:raise ValueError('quote exceeds 25 words')
            corpus='\n'.join(x['raw_reference'] for x in expected[cid]['records'])+'\n'+'\n'.join(x.get('text','') for x in expected[cid].get('frozen_evidence',[]))
            if quote and quote not in corpus:raise ValueError('quote is not verbatim')
    if set(got)!=set(expected):raise ValueError('missing cases')

def run(arm,variant,packet_path,run_name,timeout):
    directory=HERE/'calls'/run_name/f'{arm}_{variant}'
    if (directory/'request.json').exists():raise RuntimeError(f'Refuse to overwrite call archive {directory}')
    directory.mkdir(parents=True,exist_ok=True)
    packet=json.loads(packet_path.read_text())
    prompt=(HERE/f'prompt_{variant}.txt').read_text()+'\nOUTPUT JSON SCHEMA:\n'+(HERE/'response_schema.json').read_text()+'\nCASE PACKET:\n'+packet_path.read_text()
    (directory/'prompt.txt').write_text(prompt)
    command=[shutil.which('codex') or 'codex','exec','--model',MODEL,'--ephemeral','--ignore-user-config','--skip-git-repo-check','--sandbox','read-only','--json','--output-schema',str(HERE/'response_schema.json'),'-o',str(directory/'response.json'),'-C',str(HERE/'empty_workspace'),'-']
    request={'requested_model':MODEL,'provider':'OpenAI','model_family_count':1,'prompt_variant':variant,'arm':arm,'utc_started':now(),'prompt_sha256':digest(directory/'prompt.txt'),'packet_sha256':digest(packet_path),'packet_path':str(packet_path.relative_to(HERE)),'case_count':len(packet['cases']),'temperature':None,'temperature_interface_exposed':False,'generation_seed':None,'generation_seed_interface_exposed':False,'data_sampling_seed':20261011,'context':'fresh ephemeral CLI session; empty working directory; user config ignored','read_only':True,'schema_sha256':digest(HERE/'response_schema.json'),'call_interface':'local_codex_cli_existing_chatgpt_auth','command_flags':command[1:],'no_tools_requested':True}
    # Avoid publishing machine-specific absolute paths from the argv.
    request.pop('command_flags')
    dump(directory/'request.json',request)
    started=time.monotonic();attempts=[]
    for attempt in (1,2):
        ep=directory/f'events_attempt_{attempt}.jsonl';sp=directory/f'stderr_attempt_{attempt}.txt'
        with ep.open('w') as events,sp.open('w') as errors:
            try:
                result=subprocess.run(command,input=prompt,text=True,stdout=events,stderr=errors,timeout=timeout,check=False)
                code=result.returncode;expired=False
            except subprocess.TimeoutExpired:code=None;expired=True
        usage=[];tool_calls=[];event_errors=[]
        for line in ep.read_text().splitlines():
            try:e=json.loads(line)
            except json.JSONDecodeError:continue
            if e.get('type')=='turn.completed':usage.append(e.get('usage'))
            item=e.get('item',{})
            if item.get('type') not in (None,'agent_message','reasoning','error'):tool_calls.append(item.get('type'))
            if e.get('type')=='error':event_errors.append(e.get('message'))
        item={'attempt':attempt,'exit_code':code,'timeout':expired,'usage':usage,'tool_calls':tool_calls,'event_errors':event_errors,'events_sha256':digest(ep),'stderr_sha256':digest(sp)}
        if code!=0 or expired:
            item['status']='transport_or_execution_failure';attempts.append(item);break
        try:
            response=json.loads((directory/'response.json').read_text());validate(response,packet)
            if tool_calls:raise ValueError('Unexpected tool use invalidates blind protocol')
            item['status']='valid';attempts.append(item);break
        except (ValueError,KeyError,FileNotFoundError) as e:
            item['status']='parse_or_schema_failure';item['failure']=str(e);attempts.append(item)
            if (directory/'response.json').exists():(directory/f'invalid_response_attempt_{attempt}.json').write_bytes((directory/'response.json').read_bytes());(directory/'response.json').unlink()
    status=attempts[-1]['status']
    if status=='parse_or_schema_failure' and len(attempts)==2:
        fallback=[]
        for c in packet['cases']:
            groups={r['report_group'] for r in c['records']}
            fallback.append({'case_id':c['case_id'],'identity':'not_applicable' if c['task']=='extract_fields' else 'unresolvable','cross_report_recurrence':'not_applicable' if c['task']=='extract_fields' or len(groups)<2 else 'unresolvable','records':[{'record_id':r['record_id'],'cited_year':None,'outlet':None,'source_type':'unresolvable','contains_multiple_citations':'unresolvable','evidence_quote':''} for r in c['records']],'reason':'Two machine parsing or schema failures; no model decision available.'})
        dump(directory/'response.json',{'cases':fallback});status='unresolvable_after_two_invalid_outputs'
    summary={'utc_finished':now(),'elapsed_seconds':time.monotonic()-started,'attempts':attempts,'status':status,'response_sha256':digest(directory/'response.json') if (directory/'response.json').exists() else None,'fallback_abstention_generated_by_code':status=='unresolvable_after_two_invalid_outputs'}
    dump(directory/'result.json',summary)
    print(json.dumps({'call':directory.name,'status':summary['status'],'seconds':round(summary['elapsed_seconds'],1)}),flush=True)
    return summary
def main():
    p=argparse.ArgumentParser();p.add_argument('--run-name',required=True);p.add_argument('--pilot',action='store_true');p.add_argument('--full',action='store_true');p.add_argument('--full-cost-record',type=pathlib.Path);p.add_argument('--max-workers',type=int,default=3);p.add_argument('--timeout',type=int,default=900)
    a=p.parse_args()
    stop_file=HERE/'USER_STOP.json'
    if stop_file.exists() and not json.loads(stop_file.read_text()).get('new_model_calls_allowed',False):
        p.error('Model calls were stopped by the author. Archived validators and deterministic analyses can run separately.')
    if a.full:
        if not a.full_cost_record or not a.full_cost_record.is_file():p.error('full execution requires the prior recorded cost estimate')
        p.error('full calls require batching by canonical case IDs; use archived pilots to choose context-safe batch sizes first')
    if not a.pilot:p.error('select --pilot')
    calls=[(arm,v,HERE/f'pilot/{arm}_50.json',a.run_name,a.timeout) for arm in ('blind','metadata') for v in 'ABC']
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(a.max_workers,3)) as pool:result=list(pool.map(lambda x:run(*x),calls))
    dump(HERE/'calls'/a.run_name/'run_summary.json',{'requested_model':MODEL,'case_count':50,'arms':2,'prompt_variants':3,'results':result})
if __name__=='__main__':main()
