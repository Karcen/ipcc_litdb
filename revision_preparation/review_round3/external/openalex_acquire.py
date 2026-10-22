#!/usr/bin/env python3
"""Archive OpenAlex bytes through the installed skill CLI transport.

The wrapper retains the skill's fetch_with_retry and polite_http transport;
it adds exact-byte capture, environment-only credentials, a cost ceiling and
the documented cursor option missing from the installed CLI argument parser.
It does not call urllib, curl, or a separate HTTP implementation.
"""
from __future__ import annotations
import argparse, csv, hashlib, importlib.util, json, logging, math, os, sys, threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

SEED=20261011
QUERIES={'broad':'"climate change" OR "global warming"','narrow':'"climate change"'}
ROOT=Path(__file__).resolve().parent
REPO=ROOT.parents[2]
DEFAULT_SKILL=Path('/Users/karcenzheng/.codex/skills/ipcc-v3-20261021/literature_search_openalex/scripts/openalex_cli.py')

def digest(b):return hashlib.sha256(b).hexdigest()
def write(p,x):
    p=Path(p);tmp=p.with_name(p.name+'.tmp-'+str(os.getpid())+'-'+str(threading.get_ident()));tmp.write_text(json.dumps(x,sort_keys=True,indent=2,allow_nan=False)+'\n');tmp.replace(p)
def rid(endpoint,params):return digest(json.dumps([endpoint,params],sort_keys=True,separators=(',',':')).encode())

class Stop(RuntimeError):pass

class Client:
    def __init__(self,out,skill,cap=.50,replay=False,authorized_resume=False):
        self.out=Path(out);self.raw=self.out/'raw';self.raw.mkdir(parents=True,exist_ok=True)
        self.replay=replay;self.cap=cap;self.failed=False;self.retry_limit=2 if authorized_resume else 0
        self.cost_lock=threading.Lock();self.request_locks={}
        self.cost=sum(json.loads(p.read_text()).get('budget_charge_usd',0) for p in self.raw.glob('*.request.json') if not p.name.startswith('._'))
        self.key=None if replay else os.environ.get('OPENALEX_API_KEY')
        if not replay and not self.key:raise Stop('OPENALEX_API_KEY environment variable is absent')
        if (self.out/'API_FAILURE_STOP.json').exists() and not replay and not authorized_resume:raise Stop('Existing API failure: explicit authorization is required before any further acquisition')
        if replay:self.cli=None;return
        spec=importlib.util.spec_from_file_location('skill_openalex_cli',skill);self.cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.cli)
        # Disable the CLI's home .env reader: this caller only receives child env.
        self.cli.dotenv.load_dotenv=lambda *args,**kwargs:False
        self.transport=self.cli._API_CLIENT
        self.cli._API_CLIENT=self
        self.skill_sha=digest(Path(skill).read_bytes())
        # No library log may include an authenticated URL in an exception.
        logging.disable(logging.CRITICAL)
    def fetch(self,url,max_retries=0):
        """Skill transport hook. Called only by CLI fetch_with_retry."""
        if self.failed:raise Stop('Earlier API request failed')
        if self.cost+.001>self.cap:raise Stop('Conservative cost reservation reaches local ceiling')
        parsed=urlsplit(url);params=dict(parse_qsl(parsed.query,keep_blank_values=True));params.pop('api_key',None)
        endpoint=parsed.scheme+'://'+parsed.netloc+parsed.path;key=rid(endpoint,params)
        response=None
        prior_attempts=max([int(json.loads(p.read_text()).get('attempt_number',0)) for p in self.raw.glob(key+'.attempt-*.request.json')]+[0])
        remaining=min(getattr(self,'retry_limit',0)+1,3-prior_attempts)
        if remaining<=0:self.failed=True;raise Stop('Three total attempts already consumed for unchanged request')
        for attempt in range(remaining):
            ordinal=prior_attempts+attempt+1
            with self.cost_lock:
                if self.failed:raise Stop('Earlier API request failed')
                if self.cost+.001>self.cap:raise Stop('Conservative cost reservation reaches local ceiling')
                self.cost+=.001
            started=datetime.now(timezone.utc).isoformat()
            try:
                response=self.transport.fetch(url,max_retries=0,timeout=120)
                raw=self.response_bytes(response)
                break
            except Exception as exc:
                error_body=getattr(exc,'body',None)
                archived_error=isinstance(error_body,bytes) and self.key.encode() not in error_body
                prefix=key+'.attempt-'+str(ordinal).zfill(2)
                if archived_error:(self.raw/(prefix+'.response.json')).write_bytes(error_body)
                status=getattr(exc,'status_code',None)
                transient=status in (None,429,500,502,503,504)
                failure={'requested_utc':started,'received_utc':datetime.now(timezone.utc).isoformat(),'endpoint':endpoint,'parameters':params,'request_id':key,
                         'error_class':type(exc).__name__,'http_status':status,'retry_count':ordinal-1,'attempt_number':ordinal,'response_bytes_archived':archived_error,
                         'response_sha256':digest(error_body) if archived_error else None,'response_bytes':len(error_body) if archived_error else 0,
                         'json_success':False,'budget_charge_usd':.001,'observed_cost_usd':None,
                         'retry_authorization':'User authorized at most two additional attempts per unchanged request' if getattr(self,'retry_limit',0) else 'No retry authorized'}
                write(self.raw/(prefix+'.request.json'),failure)
                if transient and attempt+1<remaining:
                    print(json.dumps({'phase':'authorized_retry','request_id':key,'failed_attempt':ordinal,'next_attempt':ordinal+1}),flush=True)
                    import time;time.sleep(5)
                    continue
                self.failed=True;write(self.out/'API_FAILURE_STOP.json',failure)
                raise Stop('OpenAlex transport failure; allowed attempts exhausted or non-transient status; no dependent request performed') from None
        # Exact content is archived before JSON parsing. A credential-bearing
        # body is quarantined rather than rewritten and mislabelled raw.
        if self.key.encode() in raw:
            self.failed=True;write(self.out/'API_FAILURE_STOP.json',{'error':'credential present in response; raw body not published','request_id':key})
            raise Stop('Credential-bearing API response quarantined')
        (self.raw/(key+'.response.json')).write_bytes(raw)
        metadata={'requested_utc':started,'received_utc':datetime.now(timezone.utc).isoformat(),'endpoint':endpoint,'parameters':params,
                  'response_bytes':len(raw),'response_sha256':digest(raw),'retry_count':ordinal-1,'attempt_number':ordinal,'request_id':key,'skill_cli_sha256':self.skill_sha,
                  'http_status':getattr(response,'status_code',None),
                  'authentication':'OPENALEX_API_KEY inherited environment; key omitted from provenance','raw_capture':'transport response content before CLI JSON parsing'}
        try:body=json.loads(raw)
        except (ValueError,TypeError):body=None
        success=isinstance(body,dict) and ('meta' in body or 'id' in body) and 'error' not in body
        observed=body.get('meta',{}).get('cost_usd') if isinstance(body,dict) else None
        charge=float(observed) if observed is not None else .001
        metadata.update(json_success=success,observed_cost_usd=observed,budget_charge_usd=charge)
        write(self.raw/(key+'.request.json'),metadata)
        with self.cost_lock:self.cost+=charge-.001
        if not success:
            self.failed=True;write(self.out/'API_FAILURE_STOP.json',metadata);raise Stop('OpenAlex response invalid; no further request performed')
        return response
    @staticmethod
    def response_bytes(response):
        for attribute in ['content','body','data','raw_content']:
            value=getattr(response,attribute,None)
            if isinstance(value,bytes):return value
        raise Stop('Installed skill transport exposes no exact byte buffer')
    def get(self,params,entity='works'):
        params={k:str(v) for k,v in params.items() if v is not None};endpoint='https://api.openalex.org/'+entity;key=rid(endpoint,params)
        with self.cost_lock:lock=self.request_locks.setdefault(key,threading.Lock())
        with lock:
            meta=self.raw/(key+'.request.json');body=self.raw/(key+'.response.json')
            if meta.exists():
                m=json.loads(meta.read_text());b=body.read_bytes()
                if not m.get('json_success') or digest(b)!=m['response_sha256']:raise Stop('Archived response incomplete or corrupted')
                return json.loads(b),key
            if self.replay:raise Stop('Missing response in replay: '+key)
            if self.failed:raise Stop('Earlier API request failed')
            result=self.cli.fetch_with_retry(endpoint,params,api_key=self.key,max_retries=0,exit_on_error=False)
            if result is None:raise Stop('CLI returned no response')
            return result,key

def source_groups(client,out):
    summary=[]
    for label,query in QUERIES.items():
        for rep,hi in [('AR5',2013),('AR6',2021)]:
            path=out/('pool_sources_'+label+'_'+rep+'.json')
            if path.exists():summary.append(json.loads(path.read_text()));continue
            cursor='*';groups={};requests=[];expected=None
            while cursor is not None:
                params={'search.title_and_abstract':query,'filter':'publication_year:1980-'+str(hi)+',type:article|review',
                        'group_by':'primary_location.source.id:include_unknown','per_page':100,'cursor':cursor}
                # Resume every archived 100-group prefix exactly, then use
                # the officially supported 200 groups per cursor page.
                legacy=rid('https://api.openalex.org/works',{k:str(v) for k,v in params.items()})
                previous=client.raw/(legacy+'.request.json')
                if not previous.exists() or not json.loads(previous.read_text()).get('json_success'):params['per_page']=200
                response,r=client.get(params);requests.append(r)
                n=int(response['meta']['count'])
                if expected is None:expected=n
                if n!=expected:raise Stop('Pool denominator changed between group pages')
                for g in response.get('group_by',[]):
                    if g['key'] in groups:raise Stop('Duplicate source bucket in cursor pages')
                    groups[g['key']]=g
                nxt=response.get('meta',{}).get('next_cursor')
                if nxt==cursor:raise Stop('Group cursor did not advance')
                cursor=nxt
            total=sum(int(g['count']) for g in groups.values())
            if total!=expected:raise Stop('Source groups are truncated: bucket counts do not equal pool count')
            item={'query':label,'report':rep,'window':[1980,hi],'total':expected,'groups':[groups[k] for k in sorted(groups)],'request_ids':requests,
                  'group_count_sum_verified':True,'official_paging_reference':'https://help.openalex.org/api/grouping/'}
            write(path,item);summary.append(item)
            print(json.dumps({'phase':'pool_sources','query':label,'report':rep,'works':expected,'groups':len(groups),'requests':len(requests),'budget_charge_usd':client.cost}),flush=True)
    ids=sorted({g['key'] for item in summary for g in item['groups'] if str(g['key']).startswith('https://openalex.org/S')})
    sources={};requests=[]
    batches=[ids[i:i+80] for i in range(0,len(ids),80)]
    def lookup(batch):
        p={'filter':'ids.openalex:'+'|'.join(x.rsplit('/',1)[-1] for x in batch),'per_page':100,'select':'id,display_name,type,issn_l,issn'}
        response,r=client.get(p,entity='sources')
        if len(response.get('results',[]))!=int(response['meta']['count']) or int(response['meta']['count'])>len(batch):raise Stop('Source lookup pagination or duplicate mismatch')
        return response,r
    # Bounded scheduling stops new work at the first failure. Inflight
    # responses still finish through Client and are frozen for later replay.
    from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
    indexed={};next_index=0;failure=None
    with ThreadPoolExecutor(max_workers=4) as executor:
        active={}
        while next_index<len(batches) and len(active)<4:
            active[executor.submit(lookup,batches[next_index])]=next_index;next_index+=1
        while active:
            done,_=wait(active,return_when=FIRST_COMPLETED)
            for future in done:
                index=active.pop(future)
                try:indexed[index]=future.result()
                except Exception as exc:client.failed=True;failure=exc
            if failure is None:
                while next_index<len(batches) and len(active)<4:
                    active[executor.submit(lookup,batches[next_index])]=next_index;next_index+=1
            if len(indexed)%50==0:print(json.dumps({'phase':'source_metadata','complete':len(indexed),'total':len(batches),'cost':client.cost}),flush=True)
    if failure is not None:raise Stop('Source metadata acquisition stopped after a failed request; inflight responses retained') from None
    for index in sorted(indexed):
        response,r=indexed[index];requests.append(r)
        for source in response.get('results',[]):sources[source['id']]=source
    write(out/'source_metadata.json',{'sources':sources,'requested_source_ids':ids,'unresolved_source_ids':sorted(set(ids)-set(sources)),'request_ids':requests})

def bounded_results(client,items,operation,phase):
    """At most four active requests; never enqueue work after a failure."""
    from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
    indexed={};next_index=0;failure=None;last_report=0
    with ThreadPoolExecutor(max_workers=4) as executor:
        active={}
        while next_index<len(items) and len(active)<4:
            active[executor.submit(operation,items[next_index])]=next_index;next_index+=1
        while active:
            done,_=wait(active,return_when=FIRST_COMPLETED)
            for future in done:
                index=active.pop(future)
                try:indexed[index]=future.result()
                except Exception as exc:client.failed=True;failure=exc
            if failure is None:
                while next_index<len(items) and len(active)<4:
                    active[executor.submit(operation,items[next_index])]=next_index;next_index+=1
            if len(indexed)//100>last_report:
                last_report=len(indexed)//100;print(json.dumps({'phase':phase,'complete':len(indexed),'total':len(items),'cost':client.cost}),flush=True)
    if failure is not None:raise Stop(phase+' stopped; successful inflight responses retained and no later requests enqueued') from None
    return [indexed[i] for i in sorted(indexed)]

def sample_works(client,filters,query,size,fields):
    """Seeded OpenAlex sample with guarded complete sample pagination."""
    items={};requests=[]
    for page in range(1,math.ceil(size/100)+1):
        params={'search.title_and_abstract':query,'filter':filters,'sample':size,'seed':SEED,'per_page':100,'page':page,'select':fields}
        response,r=client.get(params);requests.append(r)
        for w in response.get('results',[]):
            if w['id'] in items:raise Stop('Duplicate ID in seeded sample pages')
            items[w['id']]=w
    if len(items)!=size:raise Stop('Seeded sample completeness check failed')
    return list(items.values()),requests

def reference_status(work):
    if 'referenced_works' not in work or work['referenced_works'] is None:return 'missing'
    if not isinstance(work['referenced_works'],list):raise Stop('Unexpected referenced_works type')
    return 'nonempty_array' if work['referenced_works'] else 'empty_array'

def case_cohort(client,out):
    """Design-based exposure-stratified comparison of observable resolved IPCC recurrence."""
    import numpy as np
    import offline_external as frozen
    r2=frozen.import_r2(REPO);rs,_=r2.load_inputs(REPO);works,mapping,bundles=frozen.load_works(REPO)
    exposure=set();later=set()
    for rep,store in [('AR5',exposure),('AR6',later)]:
        for r in rs:
            if r['report_id']==rep and r['match_status'] in frozen.MATCHED and r['preferred_key'] in mapping:
                w=mapping[r['preferred_key']]
                if 1980<=int(w.get('publication_year') or 0)<=2013 and w.get('type') in ['article','review'] and ((w.get('primary_location') or {}).get('source') or {}).get('type')=='journal':store.add(w['id'])
    base='publication_year:1980-2013,type:article|review,primary_location.source.type:journal'
    query=QUERIES['broad'];members={};requests=[]
    ids=sorted(exposure)
    batches=[ids[i:i+100] for i in range(0,len(ids),100)]
    def member_lookup(batch):
        return client.get({'search.title_and_abstract':query,'filter':base+',ids.openalex:'+'|'.join(x.rsplit('/',1)[-1] for x in batch),
                           'per_page':100,'select':'id,publication_year,primary_location,type'})
    for response,r in bounded_results(client,batches,member_lookup,'exposure_membership'):
        requests.append(r)
        if len(response.get('results',[]))!=int(response['meta']['count']):raise Stop('Exposure membership query incomplete')
        for work in response['results']:members[work['id']]=work
    count_response,count_id=client.get({'search.title_and_abstract':query,'filter':base,'per_page':1,'select':'id'})
    population_n=int(count_response['meta']['count']);requests.append(count_id)
    fields='id,publication_year,primary_location,type,referenced_works'
    sample,ss=sample_works(client,base,query,1100,fields);requests.extend(ss)
    unexposed=[w for w in sample if w['id'] not in members]
    # Conditional on its attained size, the non-exposed portion of an SRS is
    # itself an SRS from the non-exposed population. Use at most 1000 in a
    # second, frozen RNG draw made before any citation-count outcomes.
    rng=np.random.default_rng(SEED)
    exposed_ids=sorted(members)
    selected_exposed=sorted(rng.choice(exposed_ids,size=min(500,len(exposed_ids)),replace=False).tolist())
    unexposed.sort(key=lambda w:w['id'])
    if len(unexposed)>1000:
        indexes=sorted(rng.choice(len(unexposed),size=1000,replace=False).tolist());unexposed=[unexposed[i] for i in indexes]
    sample_rows=[]
    for exposed,pop,selected in [(True,len(members),[members[i] for i in selected_exposed]),(False,population_n-len(members),unexposed)]:
        for w in selected:
            s=(w.get('primary_location') or {}).get('source') or {}
            sample_rows.append({'work_id':w['id'],'AR5_cited':exposed,'AR6_cited':w['id'] in later,'publication_year':w['publication_year'],
                                'source_type':s.get('type') or 'unknown','source_id':s.get('id'),'sampling_population':pop,'sample_size':len(selected),
                                'inclusion_probability':len(selected)/pop,'sampling_weight':pop/len(selected)})
    protocol={'version':'1.4.0','seed':SEED,'numpy_version':np.__version__,'population_total':population_n,'population_AR5_cited':len(members),
              'population_AR5_not_cited':population_n-len(members),'exposed_sample':len(selected_exposed),'unexposed_sample':len(unexposed),
              'selection':'One seeded OpenAlex pool sample of 1100; remove AR5-cited frame; if more than 1000 remain, seeded SRS 1000. Independent seeded SRS of up to 500 from complete AR5-cited query members.',
              'query':query,'filter':base,'membership_request_ids':requests,'sample':sample_rows,
              'outcome_scope':'Membership in frozen DOI-resolved, matched-assigned AR6 works; not complete extraction or adjudicated record identity.',
              'citation_covariate':'Number of OpenAlex article/review citing works published 2014–2021; post-exposure conditioning, not a causal adjustment.',
              'planned_strata':['publication_year × source_type','publication_year × source_type × citation_count_bin (0,1–9,10–99,100+)']}
    path=out/'case_cohort_protocol.json'
    if path.exists() and json.loads(path.read_text())!=protocol:raise Stop('Frozen case-cohort protocol differs')
    if not path.exists():write(path,protocol)
    # A separately sampled later pool has the same date/type/source filters
    # as its IPCC DOI-resolved counterpart, and a frozen AR5 raw-DOI target.
    targets={mapping[r['raw_key']]['id'] for r in rs if r['report_id']=='AR5' and r['raw_key'] in mapping}
    old_indirect=frozen.csvread(REPO/'revision_preparation/review_round2/openalex_v1/activated_attempt_03/results_v1/indirect_continuity.csv')
    old_target=next(int(r['AR5_target_resolved_raw_DOI_work_IDs']) for r in old_indirect if r['cohort']=='all_raw_DOI')
    if len(targets)!=old_target:raise Stop('AR5 raw-DOI target set differs from frozen Round2 scope')
    target_sha=digest(('\n'.join(sorted(targets))+'\n').encode())
    write(out/'indirect_AR5_target_set.json',{'scope':'Frozen Round2 all_raw_DOI AR5 uniquely resolved OpenAlex works','work_IDs':sorted(targets),'count':len(targets),'sorted_IDs_sha256':target_sha})
    same_scope={mapping[r['raw_key']]['id']:mapping[r['raw_key']] for r in rs if r['report_id']=='AR6' and r['raw_key'] in mapping
                and 1980<=int(mapping[r['raw_key']].get('publication_year') or 0)<=2021
                and mapping[r['raw_key']].get('type') in ['article','review']
                and ((mapping[r['raw_key']].get('primary_location') or {}).get('source') or {}).get('type')=='journal'}
    same_scope_positive=sum(bool(set(w.get('referenced_works') or [])&targets) for w in same_scope.values())
    write(out/'indirect_IPCC_common_scope.json',{'scope':'AR6 raw-DOI resolved unique works; article/review in journal sources; publication years 1980–2021',
          'AR5_target_work_IDs':len(targets),'AR5_target_sorted_IDs_sha256':target_sha,'AR6_work_IDs':len(same_scope),'AR6_at_least_one_AR5_target':same_scope_positive,
          'fraction':same_scope_positive/len(same_scope),'AR6_empty_references':sum(not w.get('referenced_works') for w in same_scope.values()),
          'AR6_missing_reference_fields':sum(reference_status(w)=='missing' for w in same_scope.values()),'AR6_empty_reference_arrays':sum(reference_status(w)=='empty_array' for w in same_scope.values()),'AR6_nonempty_reference_arrays':sum(reference_status(w)=='nonempty_array' for w in same_scope.values()),
          'metadata_acquisition':'Frozen Round 2 responses; pool sample fetched separately in Round 3.'})
    later_sample,requests=sample_works(client,'publication_year:1980-2021,type:article|review,primary_location.source.type:journal',query,1000,fields)
    indirect=[]
    for w in later_sample:
        refs=w.get('referenced_works') or [];indirect.append({'work_id':w['id'],'publication_year':w['publication_year'],'stored_references':len(refs),
            'AR5_targets_cited':len(set(refs)&targets),'has_AR5_target':bool(set(refs)&targets),'reference_status':reference_status(w)})
    write(out/'indirect_pool_sample.json',{'seed':SEED,'sample_size':len(later_sample),'AR5_target_work_IDs':len(targets),'AR5_target_sorted_IDs_sha256':target_sha,'request_ids':requests,'observations':indirect,
          'empty_reference_note':'Empty referenced_works may represent missing coverage; an empty array is not adjudicated absence of citations.'})
    # Independent citation-count requests run in four workers. The skill
    # transport supplies a cross-process 10-QPS limiter; the wrapper reserves
    # the full per-request budget under a lock before issuing each call.
    from concurrent.futures import ThreadPoolExecutor
    def observe(row):
        response,r=client.get({'filter':'cites:'+row['work_id'].rsplit('/',1)[-1]+',publication_year:2014-2021,type:article|review','per_page':1,'select':'id'})
        return dict(row,citations_2014_2021=int(response['meta']['count']),citation_count_request_id=r)
    observed=bounded_results(client,sample_rows,observe,'case_cohort_citations')
    write(out/'case_cohort_observations.json',{'protocol':protocol,'observations':observed})

def cli():
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['sources','case-cohort']);p.add_argument('--output',type=Path,default=ROOT/'acquisition_v1');p.add_argument('--skill-cli',type=Path,default=DEFAULT_SKILL);p.add_argument('--budget',type=float,default=.50);p.add_argument('--replay',action='store_true');p.add_argument('--authorized-resume',action='store_true');a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True)
    try:
        c=Client(a.output,a.skill_cli,a.budget,a.replay,a.authorized_resume)
        if a.phase=='sources':source_groups(c,a.output)
        elif a.phase=='case-cohort':case_cohort(c,a.output)
    except Stop as e:
        print('STOP: '+str(e));return 2
    return 0

if __name__=='__main__':sys.exit(cli())
