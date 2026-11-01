#!/usr/bin/env python3
"""Resumable actual full-scope calls, gated by a prior recorded cost estimate.

This script only collects responses. It never turns partial runs into estimates.
Metadata-arm anchoring is a pilot comparison by default, not a mandatory doubling
of the entire full-scope collection. All calls use the three fixed prompt variants.
"""
from __future__ import annotations
import argparse,concurrent.futures,hashlib,json,pathlib,random
from run_panel import HERE,MODEL,run,validate,dump,digest

def main():
    p=argparse.ArgumentParser();p.add_argument('--scope',choices=['shared','parser','source','sample','candidates','core'],required=True)
    p.add_argument('--cost-record',type=pathlib.Path,required=True);p.add_argument('--run-name',required=True)
    p.add_argument('--batch-size',type=int,default=50);p.add_argument('--max-workers',type=int,default=3);p.add_argument('--max-new-calls',type=int);p.add_argument('--timeout',type=int,default=1200)
    a=p.parse_args()
    stop_file=HERE/'USER_STOP.json'
    if stop_file.exists() and not json.loads(stop_file.read_text()).get('new_model_calls_allowed',False):
        p.error('Model calls were stopped by the author. Archived validators and deterministic analyses can run separately.')
    if not a.cost_record.is_file() or a.cost_record.stat().st_size==0:p.error('A nonempty prior cost record is required before full execution')
    if not 1<=a.batch_size<=100:p.error('batch size must be1–100 for context/output limits')
    if a.scope=='candidates':cases=json.loads((HERE/'candidates/blind_candidates.json').read_text())['cases']
    else:
        cases=json.loads((HERE/'frames/blind_all.json').read_text())['cases']
        origin={x['case_id']:x['origin'] for x in json.loads((HERE/'frames/case_key.json').read_text())}
        prefix={'shared':'current_shared_id:','parser':'parser:','source':'source:','sample':'sampled_id:'}.get(a.scope)
        if a.scope=='sample':
            selected={x['case_id'] for x in json.loads((HERE/'frames/sample_roles.json').read_text())};cases=[x for x in cases if x['case_id'] in selected]
        elif prefix:cases=[x for x in cases if origin[x['case_id']].startswith(prefix)]
        if a.scope=='shared':
            # Ledger/easy controls are mixed without revealing their status.
            allcases=json.loads((HERE/'frames/blind_all.json').read_text())['cases']
            ids={x['case_id'] for x in cases};cases += [x for x in allcases if origin[x['case_id']].startswith(('ledger:','raw_doi_equal:')) and x['case_id'] not in ids]
    rng=random.Random(20261011);rng.shuffle(cases)
    packets=HERE/'full_packets'/a.run_name;packets.mkdir(parents=True,exist_ok=True)
    jobs=[]
    for index,start in enumerate(range(0,len(cases),a.batch_size),1):
        path=packets/f'batch_{index:04d}.json';body={'cases':cases[start:start+a.batch_size]}
        if path.exists():
            if json.loads(path.read_text())!=body:raise SystemExit('Frozen full packet differs; use a new run name')
        else:dump(path,body)
        for variant in 'ABC':jobs.append((f'{a.run_name}/batch_{index:04d}',variant,path))
    control={'scope':a.scope,'model':MODEL,'prompt_variants':3,'model_families':1,'arm':'blind','seed':20261011,'case_packets':len(cases),'batch_size':a.batch_size,'planned_calls':len(jobs),'prior_cost_record_sha256':digest(a.cost_record),'calls':[],'complete':False,'calibration_or_estimates_computed':False}
    queued=[]
    for name,v,path in jobs:
        directory=HERE/'calls'/name/f'blind_{v}';result=directory/'result.json'
        if result.exists():
            previous=json.loads(result.read_text())
            if previous['status'] not in ('valid','unresolvable_after_two_invalid_outputs'):raise SystemExit('Previous transport failure requires explicit resolution before resuming: '+str(directory))
            request=json.loads((directory/'request.json').read_text())
            if request['packet_sha256']!=digest(path):raise SystemExit('Existing call packet SHA mismatch')
            validate(json.loads((directory/'response.json').read_text()),json.loads(path.read_text()))
            control['calls'].append({'path':str(directory.relative_to(HERE)),'status':previous['status'],'reused_archived_call':True})
        elif (directory/'request.json').exists():raise SystemExit('Incomplete call archive needs explicit inspection: '+str(directory))
        else:queued.append((name,v,path))
    if a.max_new_calls is not None:queued=queued[:a.max_new_calls]
    journal=packets/'execution.json';dump(journal,control)
    # At most three subprocesses, and no replenishment after a transport failure.
    stop=False;iterator=iter(queued);pending={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(3,a.max_workers)) as pool:
        def submit():
            try:name,v,path=next(iterator)
            except StopIteration:return False
            f=pool.submit(run,'blind',v,path,name,a.timeout);pending[f]=(name,v,path);return True
        for _ in range(min(3,a.max_workers)):submit()
        while pending:
            done,_=concurrent.futures.wait(pending,return_when=concurrent.futures.FIRST_COMPLETED)
            for f in done:
                name,v,path=pending.pop(f)
                try:r=f.result();status=r['status']
                except Exception as e:status='execution_exception';r={'error':str(e)}
                control['calls'].append({'path':f'calls/{name}/blind_{v}','status':status,'reused_archived_call':False})
                if status not in ('valid','unresolvable_after_two_invalid_outputs'):stop=True
                dump(journal,control)
                if not stop:submit()
    control['complete']=len(control['calls'])==len(jobs) and all(c['status'] in ('valid','unresolvable_after_two_invalid_outputs') for c in control['calls'])
    control['stopped_after_failure']=stop;dump(journal,control)
    print(json.dumps({'scope':a.scope,'planned_calls':len(jobs),'archived_calls':len(control['calls']),'complete':control['complete'],'failure_stop':stop}))
    if stop:raise SystemExit(2)
if __name__=='__main__':main()
