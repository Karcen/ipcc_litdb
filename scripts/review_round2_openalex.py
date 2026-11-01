#!/usr/bin/env python3
"""Freeze and replay an OpenAlex query-defined external comparison.

No database corrections or human labels. Acquisition uses a private bearer key,
never logs credentials, obeys a local cost cap, and stops on the first HTTP/API
failure without retry. All analytical tables are a new independent registry.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import date, datetime, timezone
import csv
import gzip
import io
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import tarfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, unquote
from urllib.request import Request, urlopen

VERSION = 'review-round2-openalex-1.0.0'
SEED = 20261011
QUERY = '"climate change" OR "global warming"'
NARROW_QUERY = '"climate change"'
SELECT = 'id,doi,title,publication_date,publication_year,primary_location,referenced_works,counts_by_year,type,locations'
WINDOWS = {'AR5': (1980,2013), 'AR6': (1980,2021)}
MATCHED = {'matched_doi','matched_title'}
EXPECTED_DB = 'a887a535947ae3b9bab7656070cc038eb3a7a5161494afcb2c3df2245f5ff0ec'


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()


def jwrite(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')


def rows_write(path, rows, fields=None):
    rows=list(rows)
    if not fields and not rows:raise ValueError('Empty table requires explicit columns')
    with Path(path).open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields or list(rows[0]));w.writeheader();w.writerows(rows)


def load_key(path):
    # Do not echo parsing errors or file content: this is the sole credential read.
    for line in Path(path).expanduser().read_text(encoding='utf-8').splitlines():
        line=line.strip()
        if not line or line.startswith('#') or '=' not in line:continue
        name,value=line.removeprefix('export ').split('=',1)
        if name.strip() in ('OPENALEX_API_KEY','OPENALEX_KEY','API_KEY'):
            value=value.strip().strip('"\'')
            if value:return value
    raise ValueError('No supported nonempty API key variable in private key file')


def doi_parameters(ds):
    # Quotes escape filter syntax only; they do not repair or replace any DOI.
    values=[json.dumps(d,ensure_ascii=False) if any(c in d for c in ',:') else d for d in ds]
    return {'filter':'doi:'+'|'.join(values),'per_page':100,'select':SELECT}


def request_id(endpoint,params):
    return hashlib.sha256(json.dumps([endpoint,params],sort_keys=True,separators=(',',':')).encode()).hexdigest()


class APIStop(RuntimeError):pass


class Client:
    def __init__(self,out,keyfile=None,replay=False,budget=.60,authorized_resume=False):
        self.out=Path(out);self.raw=self.out/'raw'
        if (self.out/'bundles/index.json').exists():restore_raw(self.out)
        self.raw.mkdir(parents=True,exist_ok=True)
        self.replay=replay;self.budget=budget;self.authorized_resume=authorized_resume
        failure=self.out/'API_FAILURE_STOP.json'
        recovered=json.loads((self.out/'transport_recoveries.json').read_text()) if (self.out/'transport_recoveries.json').exists() else {'recoveries':[]}
        recovered_ids={x['failed_request_id'] for x in recovered['recoveries']}
        if failure.exists() and not replay and not authorized_resume:
            f=json.loads(failure.read_text());rid=request_id(f['endpoint'],f['parameters'])
            if rid not in recovered_ids:raise APIStop('STOP: previous API failure requires explicit authorization before acquisition')
        self.key=None if replay else load_key(keyfile)
        self.lock=threading.Lock();self.failed=threading.Event();self.last_start=0
        self.cost=sum((lambda m:m.get('budget_charge_usd',m.get('observed_cost_usd',0)) or 0)(json.loads(p.read_text())) for p in self.raw.glob('*.request.json') if not p.name.startswith('._'))
        self.requests=0
    def get(self,params,endpoint='https://api.openalex.org/works',estimated=.001):
        params={k:v for k,v in params.items() if v is not None};rid=request_id(endpoint,params)
        meta_path=self.raw/(rid+'.request.json');body_path=self.raw/(rid+'.response.json')
        if meta_path.exists():
            m=json.loads(meta_path.read_text());body=body_path.read_bytes()
            if sha(body_path)!=m['response_sha256']:raise APIStop('STOP: archived response hash differs')
            if m['http_status']!=200 or not m.get('json_success'):raise APIStop('STOP: archived API failure exists; authorization required before another acquisition')
            return json.loads(body)
        if self.replay:raise APIStop('STOP: replay response missing for '+rid)
        with self.lock:
            if self.failed.is_set():raise APIStop('STOP: previous API request failed')
            if self.cost+estimated>self.budget:raise APIStop('STOP: local API cost cap reached before request')
            wait=.12-(time.monotonic()-self.last_start)
            if wait>0:time.sleep(wait)
            self.last_start=time.monotonic();self.cost+=estimated
        url=endpoint+'?'+urlencode(params)
        if len(url.encode())>=3800:raise APIStop('STOP: encoded request exceeds 3800 bytes')
        start=datetime.now(timezone.utc).isoformat();status=None;headers={};body=b'';error_type=None
        try:
            req=Request(url,headers={'Accept':'application/json','Authorization':'Bearer '+self.key,
                                     'User-Agent':'ipcc_litdb-review-round2/1.0'})
            with urlopen(req,timeout=60) as r:
                status=r.status;headers=dict(r.headers.items());body=r.read()
        except HTTPError as e:
            status=e.code;headers=dict(e.headers.items());body=e.read();error_type='HTTPError'
        except (URLError,TimeoutError,OSError):
            error_type='network_or_timeout'
        # A response must not leak a bearer key if an upstream error echoes it.
        secret_seen=bool(self.key and self.key.encode() in body)
        if secret_seen:body=body.replace(self.key.encode(),b'[REDACTED_BEARER]')
        clean_headers={k:('[REDACTED_BEARER]' if self.key and self.key in str(v) else v) for k,v in headers.items() if k.lower() not in ('set-cookie','authorization','proxy-authorization')}
        parsed=None;json_success=False
        try:
            parsed=json.loads(body);json_success=status==200 and isinstance(parsed,dict) and ('meta' in parsed or 'id' in parsed) and 'error' not in parsed
        except (ValueError,TypeError):pass
        observed=(parsed.get('meta',{}).get('cost_usd') if isinstance(parsed,dict) else None)
        # Missing billing metadata is a local reservation, never an observed charge.
        budget_charge=estimated if observed is None else float(observed)
        observed=None if observed is None else float(observed)
        m={'endpoint':endpoint,'parameters':params,'request_url_without_credentials':url,
           'method':'GET','authentication':'private bearer header, omitted','requested_utc':start,
           'received_utc':datetime.now(timezone.utc).isoformat(),'http_status':status,
           'response_headers':clean_headers,'response_sha256':hashlib.sha256(body).hexdigest(),
           'response_bytes':len(body),'json_success':json_success,'response_secret_redacted':secret_seen,
           'retry_count':0,'error_type':error_type,'observed_cost_usd':observed,'budget_charge_usd':budget_charge,
           'cost_source':'response meta.cost_usd' if isinstance(parsed,dict) and parsed.get('meta',{}).get('cost_usd') is not None else 'unreported actual cost; conservative local budget reservation',
           'response_file':body_path.name}
        body_path.write_bytes(body);jwrite(meta_path,m)
        with self.lock:self.cost+=budget_charge-estimated;self.requests+=1
        if not json_success:
            self.failed.set();jwrite(self.out/'API_FAILURE_STOP.json',m)
            print('STOP: OpenAlex request failed; status='+str(status)+' endpoint='+endpoint+' request_id='+rid,flush=True)
            raise APIStop('No retry or dependent API request performed after failure')
        return parsed


def pool_params(query,year_end):
    return {'search.title_and_abstract':query,'filter':'publication_year:1980-'+str(year_end)+',type:article|review',
            'group_by':'publication_year','per_page':100}


def pool(client,out):
    for label,query in [('broad',QUERY),('narrow',NARROW_QUERY)]:
        for report,(low,high) in WINDOWS.items():
            p=pool_params(query,high);r=client.get(p)
            groups=r.get('group_by',[])
            if len(groups)>=100 or any(not low<=int(g['key'])<=high for g in groups):raise APIStop('Incomplete or invalid year aggregation; stop')
            if sum(int(g['count']) for g in groups)!=int(r['meta']['count']):raise APIStop('Year aggregation count differs from denominator; stop')
            jwrite(Path(out)/('pool_'+label+'_'+report+'.json'),{'parameters':p,'raw_request_id':request_id('https://api.openalex.org/works',p),'response':r})
            print(json.dumps({'phase':'pool','query':label,'report':report,'count':r['meta']['count'],'buckets':len(groups),'cumulative_budget_charge_usd':client.cost}),flush=True)


def load_inputs(repo):
    db=repo/'database/ipcc_litdb.sqlite';before=sha(db)
    if before!=EXPECTED_DB:raise ValueError('Database is not frozen v1.2.0 input')
    sys.path.insert(0,str(repo/'reproducibility'))
    import revision_identity as identity
    with sqlite3.connect(db.as_uri()+'?mode=ro&immutable=1',uri=True) as c:
        c.row_factory=sqlite3.Row
        rs=[dict(x) for x in c.execute('''SELECT r.occurrence_id,r.chapter_id,r.paper_id,r.raw_reference,r.match_status,
        c.report_id,w.wg_label,s.publication_year AS source_year,p.doi_norm AS preferred_doi,p.year_best,p.journal_best
        FROM references_occurrence r JOIN chapters c ON c.chapter_id=r.chapter_id
        JOIN working_groups w ON w.wg_id=c.wg_id LEFT JOIN sources s ON s.source_id=c.source_id
        LEFT JOIN papers p ON p.paper_id=r.paper_id WHERE c.report_id IN ('AR5','AR6') ORDER BY r.occurrence_id''')]
    if len({r['occurrence_id'] for r in rs})!=len(rs):raise ValueError('Occurrence unit uniqueness failed after many-to-one joins')
    for r in rs:
        ds=identity.punctuation_repaired_raw_dois(r['raw_reference'])
        r['raw_key']=ds[0] if len(ds)==1 else None
        d=identity.normalized_doi(r['preferred_doi'])
        r['preferred_key']=d if identity.usable_raw_doi(d) else None
    if sha(db)!=before:raise ValueError('Database changed')
    return rs,{'database_sha256_before':before,'database_sha256_after':sha(db),'database_unchanged':True,
               'unit':'One immutable occurrence_id; joins to chapter, working group, source and paper are many-to-one. Derived units are report/assigned-ID or report/raw-DOI key.',
               'raw_parser':'reproducibility/revision_identity.py:punctuation_repaired_raw_dois',
               'raw_parser_script_sha256':sha(repo/'reproducibility/revision_identity.py')}


def requested_dois(rs):
    return sorted({r['raw_key'] for r in rs if r['raw_key']}|{r['preferred_key'] for r in rs if r['match_status'] in MATCHED and r['preferred_key']})


def batches(ds):
    batch=[]
    for d in ds:
        trial=batch+[d]
        # Preserve the original batch plan; escaping never changes identity membership.
        p={'filter':'doi:'+'|'.join(trial),'per_page':100,'select':SELECT}
        if batch and (len(trial)>100 or len(('https://api.openalex.org/works?'+urlencode(p)).encode())>=3700):
            yield batch;batch=[d]
        else:batch=trial
    if batch:yield batch


def acquire_works(client,repo,out):
    rs,audit=load_inputs(repo);ds=requested_dois(rs);bb=list(batches(ds))
    plan=out/'lookup_batch_plan.json'
    planned={'requested_dois_sha256':hashlib.sha256(('\n'.join(ds)+'\n').encode()).hexdigest(),'batches':bb,'rule':'Original unquoted-value URL planning retained; existing successful requests reused exactly.'}
    if plan.exists() and json.loads(plan.read_text())!=planned:raise ValueError('Frozen lookup batch plan changed')
    if not plan.exists():jwrite(plan,planned)
    jwrite(out/'input_inventory.json',dict(audit,seed=SEED,version=VERSION,requested_doi_count=len(ds),batch_count=len(bb),
           requested_dois_sha256=hashlib.sha256(('\n'.join(ds)+'\n').encode()).hexdigest()))
    all_rows=[];completeness=[]
    for i,b in enumerate(bb,1):
        legacy={'filter':'doi:'+'|'.join(b),'per_page':100,'select':SELECT}
        legacy_file=out/'raw'/(request_id('https://api.openalex.org/works',legacy)+'.request.json')
        legacy_ok=False
        if legacy_file.exists():
            previous=json.loads(legacy_file.read_text())
            legacy_ok=previous.get('http_status')==200 and previous.get('json_success')
        if legacy_ok:
            segments=[b];params_list=[legacy]
        else:
            # A colon-bearing raw key remains one identity. A singleton request
            # prevents its literal text from being parsed as an OR of fields.
            ordinary=[d for d in b if ':' not in d];segments=[];pending=[]
            for d in ordinary:
                trial=pending+[d]
                if pending and len(('https://api.openalex.org/works?'+urlencode(doi_parameters(trial))).encode())>=3700:
                    segments.append(pending);pending=[d]
                else:pending=trial
            if pending:segments.append(pending)
            segments.extend([[d] for d in b if ':' in d])
            params_list=[doi_parameters(seg) for seg in segments]
        if sorted(d for seg in segments for d in seg)!=b:raise ValueError('Transport segments changed DOI identity membership')
        request_ids=[]
        for segment,p in zip(segments,params_list):
            r=client.get(p,estimated=.0001)
            total=int(r['meta']['count']);first_id=request_id('https://api.openalex.org/works',p)
            page_ids=[first_id];records=list(r.get('results',[]))
            if len(records)!=min(total,100) or total>10000:
                jwrite(out/'INCOMPLETE_LOOKUP_STOP.json',{'batch':i,'parameters':p,'count':total,'returned':len(records),'reason':'Unexpected count/page-cap discrepancy'})
                raise APIStop('STOP: successful API response has unexplained count discrepancy')
            if total>100:
                for page in range(2,math.ceil(total/100)+1):
                    page_params=dict(p,page=page);extra=client.get(page_params,estimated=.0001)
                    if int(extra['meta']['count'])!=total:raise APIStop('STOP: DOI result count changed between pages')
                    page_ids.append(request_id('https://api.openalex.org/works',page_params));records.extend(extra.get('results',[]))
                if len(records)!=total or len({w['id'] for w in records})!=total:
                    raise APIStop('STOP: paged DOI lookup is incomplete or has duplicated work IDs')
                completeness.append({'batch':i,'first_request_id':first_id,'reported_total':total,
                                     'first_page_records':len(r['results']),'all_page_records':len(records),
                                     'all_unique_work_ids':len({w['id'] for w in records}),'request_ids':page_ids,
                                     'DOI_filter_unchanged':True,'resolution':'All pages archived; multiple exact DOI candidates remain ambiguous'})
            request_ids.extend(page_ids)
        all_rows.append({'batch':i,'request_id':request_ids[0],'request_ids':request_ids,'requested_dois':b,'transport_segments':segments})
        if i==1 or i%25==0 or i==len(bb):
            print(json.dumps({'phase':'works','batches_done':i,'batches_total':len(bb),'cumulative_budget_charge_usd':client.cost}),flush=True)
    recoveries=[]
    for item in all_rows:
        original={'filter':'doi:'+'|'.join(item['requested_dois']),'per_page':100,'select':SELECT}
        oldid=request_id('https://api.openalex.org/works',original)
        oldfile=out/'raw'/(oldid+'.request.json')
        if oldid!=item['request_id'] and oldfile.exists():
            old=json.loads(oldfile.read_text());body=json.loads((out/'raw'/old['response_file']).read_text())
            if old['http_status']==400 and any(x in body.get('message','') for x in ('unescaped comma','OR query between filters')):
                recoveries.append({'failed_request_id':oldid,'failed_metadata_sha256':sha(oldfile),
                                   'replacement_request_id':item['request_id'],'replacement_request_ids':item['request_ids'],
                                   'replacement_transport_segments':item['transport_segments'],
                                   'replacement_metadata_hashes':{rid:sha(out/'raw'/(rid+'.request.json')) for rid in item['request_ids']},
                                   'replacement_metadata_sha256':sha(out/'raw'/(item['request_id']+'.request.json')),
                                   'DOI_identity_values_unchanged':True,
                                   'repair':'Quote comma-bearing values and isolate colon-bearing literal keys in singleton queries. Original logical batch and identities remain unchanged.',
                                   'operator_authorized_resume_flag':client.authorized_resume})
    if recoveries:
        saved=out/'transport_recoveries.json'
        if saved.exists():
            previous={r['failed_request_id']:r for r in json.loads(saved.read_text())['recoveries']}
            for r in recoveries:
                if r['failed_request_id'] in previous:
                    old=previous[r['failed_request_id']]
                    if r['replacement_request_id']!=old['replacement_request_id']:raise ValueError('Transport recovery identity changed')
                    r['operator_authorized_resume_flag']=old['operator_authorized_resume_flag']
        jwrite(saved,{'recoveries':recoveries})
    jwrite(out/'completeness_recoveries.json',{'recoveries':completeness,'original_stop_log_preserved':(out/'INCOMPLETE_LOOKUP_STOP.json').exists()})
    jwrite(out/'lookup_batches.json',{'batches':all_rows,'input':audit,'requested_dois':ds})


def normalized_doi(d):
    return re.sub(r'^https?://(?:dx\.)?doi\.org/','',str(d or '').strip().lower())


def crosswalk(out):
    inventory=json.loads((out/'lookup_batches.json').read_text());mapping=defaultdict(dict);works={}
    for batch in inventory['batches']:
        wanted=set(batch['requested_dois'])
        records=[]
        for rid in batch.get('request_ids',[batch['request_id']]):
            records.extend(json.loads((out/'raw'/(rid+'.response.json')).read_text())['results'])
        for w in records:
            wid=w['id'];works[wid]=w
            aliases={normalized_doi(w.get('doi'))}
            aliases|={normalized_doi(l.get('doi')) for l in w.get('locations',[]) if l.get('doi')}
            for d in aliases&wanted:mapping[d][wid]=w
    return inventory['requested_dois'],mapping,works


def sources_of(w):
    s=(w.get('primary_location') or {}).get('source')
    return s or {}


def source_queries(client,repo,out):
    rs,audit=load_inputs(repo);ds,mapping,works=crosswalk(out)
    ids_by_report={rep:{r['paper_id'] for r in rs if r['report_id']==rep and r['match_status'] in MATCHED and r['paper_id'] is not None} for rep in WINDOWS}
    assigned={};counters={};top={}
    for rep in WINDOWS:
        choices=defaultdict(set)
        for r in rs:
            if r['report_id']!=rep or r['match_status'] not in MATCHED:continue
            dd=mapping.get(r['preferred_key'],{})
            if len(dd)==1:
                s=sources_of(next(iter(dd.values())))
                if s.get('id'):choices[r['paper_id']].add(s['id'])
        assigned[rep]={pid:next(iter(ss)) for pid,ss in choices.items() if len(ss)==1}
        counters[rep]=Counter(assigned[rep].values());top[rep]=[s for s,n in sorted(counters[rep].items(),key=lambda x:(-x[1],x[0]))[:15]]
    union=sorted(set(top['AR5'])|set(top['AR6']))
    jwrite(out/'source_assignment.json',{'policy':'One matched assigned ID per report. Preferred DOI must resolve to exactly one OpenAlex work and one nonmissing primary source. IDs with conflicting sources are excluded, not duplicated. All matched IDs remain the overall denominator.',
           'report_matched_ids':{r:len(ids_by_report[r]) for r in WINDOWS},'mapped_source_ids':{r:len(assigned[r]) for r in WINDOWS},
           'top15_sources':top,'source_union':union,'source_counts':{r:dict(counters[r]) for r in WINDOWS},'input':audit})
    if len(union)>100:raise ValueError('Source union exceeds single page')
    for label,query in [('broad',QUERY),('narrow',NARROW_QUERY)]:
        for rep,(lo,hi) in WINDOWS.items():
            p={'search.title_and_abstract':query,'filter':'publication_year:1980-'+str(hi)+',type:article|review,primary_location.source.id:'+'|'.join(s.rsplit('/',1)[-1] for s in union),
               'group_by':'primary_location.source.id','per_page':100}
            r=client.get(p)
            if len(r.get('group_by',[]))>=100 or not {g['key'] for g in r.get('group_by',[])}<=set(union):raise APIStop('Incomplete source groups')
            if sum(int(g['count']) for g in r.get('group_by',[]))!=int(r['meta']['count']):raise APIStop('Source group counts differ from filtered total')
            jwrite(out/('source_pool_'+label+'_'+rep+'.json'),{'parameters':p,'raw_request_id':request_id('https://api.openalex.org/works',p),'response':r})
            print(json.dumps({'phase':'source_pool','query':label,'report':rep,'sources':len(r.get('group_by',[])),'cumulative_budget_charge_usd':client.cost}),flush=True)



def constants(repo):
    tree=ast.parse((repo/'reproducibility/revision_age_networks.py').read_text())
    return {node.targets[0].id:ast.literal_eval(node.value) for node in tree.body
            if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name)
            and node.targets[0].id in ('CUTOFFS','CUTOFF_SOURCES')}


def resolved_records(rs,mapping,cohort,rep,group='All'):
    grouped=defaultdict(list)
    for r in rs:
        if r['report_id']!=rep or (group!='All' and r['wg_label']!=group):continue
        if cohort=='matched_assigned':
            if r['match_status'] not in MATCHED or r['paper_id'] is None:continue
            key=str(r['paper_id']);doi=r['preferred_key']
        else:
            if not r['raw_key']:continue
            key=r['raw_key'];doi=key
        grouped[key].append((r,doi))
    out={};diagnostics=Counter()
    for key,entries in grouped.items():
        records={w['id']:w for r,d in entries for w in mapping.get(d,{}).values()}
        if not records:diagnostics['unresolved_identity']+=1;continue
        if len(records)!=1 or any(len(mapping.get(d,{}))>1 for r,d in entries):
            diagnostics['ambiguous_identity']+=1;continue
        w=next(iter(records.values()))
        out[key]={'work':w,'occurrences':[r for r,d in entries]}
    diagnostics['original_cohort_size']=len(grouped)
    diagnostics['resolved_identity']=len(out)
    return out,dict(diagnostics)


def freeze_citation_sample(repo,out):
    import numpy as np
    if np.__version__!='2.2.6':raise ValueError('Use pinned NumPy 2.2.6 for sample selection')
    rs,audit=load_inputs(repo)
    frame=sorted({r['raw_key'] for r in rs if r['report_id']=='AR5' and r['raw_key']})
    if len(frame)!=2863:raise ValueError('AR5 raw DOI frame differs from the frozen 2863-key population')
    frame_text='\n'.join(frame)+'\n';frame_hash=hashlib.sha256(frame_text.encode()).hexdigest()
    manifest=out/'citation_sample_manifest.json'
    if manifest.exists():
        old=json.loads(manifest.read_text())
        if old['frame_sha256']!=frame_hash or sha(out/'citation_sample.csv')!=old['sample_file_sha256']:
            raise ValueError('Frozen citation sample or frame changed')
        return old
    prior_counts=[p for p in (out/'raw').glob('*.request.json') if not p.name.startswith('._') and json.loads(p.read_text()).get('parameters',{}).get('filter','').startswith('cites:')]
    if prior_counts:raise ValueError('Cannot newly freeze sample after survival count requests exist')
    rng=np.random.default_rng(SEED);indices=rng.choice(len(frame),size=300,replace=False)
    sample=[frame[int(i)] for i in indices]
    assert len(set(sample))==300
    rows_write(out/'citation_sample.csv',[{'sample_index':i+1,'raw_doi':doi} for i,doi in enumerate(sample)])
    (out/'citation_frame_dois.txt').write_text(frame_text,encoding='utf-8')
    record={'frozen_utc':datetime.now(timezone.utc).isoformat(),'seed':SEED,'numpy_version':np.__version__,
            'sampling':'Simple random sample without replacement, NumPy default_rng choice from the sorted full raw DOI frame.',
            'population_raw_DOI_keys':len(frame),'sample_raw_DOI_keys':len(sample),'inclusion_probability':300/len(frame),
            'frame_sha256':frame_hash,'sample_file_sha256':sha(out/'citation_sample.csv'),
            'frozen_before_survival_query':True,'survival_count_requests_before_freeze':0,
            'selection_inputs':'Raw DOI identity strings only; no metadata or citation counts used for selection.',
            'earlier_lookup_note':'Bulk bibliographic responses may contain counts_by_year; those fields are not examined or used to select this sample.',
            'citing_population':'OpenAlex article/review works published 2014-2021, no climate text-query restriction.',
            'unit':'One raw DOI key. Aliases sharing one OpenAlex work receive the same outcome but remain distinct sampled keys.',
            'input':audit}
    jwrite(manifest,record);print(json.dumps({'phase':'sample_frozen','population':len(frame),'sample':len(sample),'frame_sha256':frame_hash}),flush=True)
    return record


def load_finite_math(repo):
    path=repo/'revision_preparation/annotation/annotation_tools.py';tree=ast.parse(path.read_text())
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('hypergeom_tail','finite_binary_interval')]
    if len(nodes)!=2:raise ValueError('Tested finite-population functions missing')
    scope={'math':math};exec(compile(ast.Module(body=nodes,type_ignores=[]),path.name,'exec'),scope)
    return scope['hypergeom_tail'],scope['finite_binary_interval']


def method_self_checks(repo,out):
    tail,interval=load_finite_math(repo);minimum=1.0;models=0
    for N in range(2,11):
        for n in range(1,N+1):
            for K in range(N+1):
                coverage=0.0
                for k in range(max(0,n-(N-K)),min(n,K)+1):
                    lo,hi=interval(N,n,k,.05)
                    if lo<=K<=hi:coverage+=math.comb(K,k)*math.comb(N-K,n-k)/math.comb(N,n)
                if coverage<.95-1e-12:raise ValueError('Finite-population interval undercoverage in toy exhaustive check')
                minimum=min(minimum,coverage);models+=1
    for N in range(2,9):
        for n in range(1,N+1):
            for k in range(n+1):
                for u in range(n-k+1):
                    lower=interval(N,n,k,.05)[0];upper=interval(N,n,k+u,.05)[1]
                    for true in range(k,k+u+1):
                        a,b=interval(N,n,true,.05)
                        if not lower<=a<=b<=upper:raise ValueError('Unknown-outcome envelope fails monotonic inclusion')
    rejected=0
    for args in [(10,0,0,.05),(10,11,2,.05),(10,4,-1,.05),(10,4,5,.05),(10,4,2,0)]:
        try:interval(*args)
        except ValueError:rejected+=1
    if rejected!=5:raise ValueError('Invalid finite-interval parameters were accepted')
    record={'status':'passed','scope':'Mathematical and serialization fixtures only; no scientific outcomes fabricated.',
            'finite_population_models_exhaustively_checked':models,'minimum_toy_coverage':minimum,
            'unknown_outcome_envelope_checked':True,'invalid_parameter_guards_passed':True,
            'comma_transport_preserves_identity':doi_parameters(['10.1002/grl,50206'])['filter']=='doi:"10.1002/grl,50206"',
            'math_source_sha256':sha(repo/'revision_preparation/annotation/annotation_tools.py'),'script_sha256':sha(Path(__file__))}
    jwrite(out/'citation_method_checks.json',record);print(json.dumps({'method_checks':'passed'}),flush=True)


def acquire_citations(client,repo,out):
    if not (out/'citation_sample_manifest.json').exists():raise ValueError('Freeze citation sample before requesting any survival counts')
    frozen=json.loads((out/'citation_sample_manifest.json').read_text())
    if sha(out/'citation_sample.csv')!=frozen['sample_file_sha256']:raise ValueError('Frozen sample hash changed')
    with (out/'citation_sample.csv').open(newline='') as f:sample=[r['raw_doi'] for r in csv.DictReader(f)]
    rs,audit=load_inputs(repo);ds,mapping,works=crosswalk(out)
    raw5={r['raw_key'] for r in rs if r['report_id']=='AR5' and r['raw_key']}
    if len(sample)!=300 or len(set(sample))!=300 or not set(sample)<=raw5:raise ValueError('Sample integrity failed')
    work_to_dois=defaultdict(list);unresolved=[]
    for doi in sample:
        records=mapping.get(doi,{})
        if len(records)!=1:unresolved.append({'raw_doi':doi,'status':'missing' if not records else 'ambiguous'});continue
        work_to_dois[next(iter(records))].append(doi)
    jwrite(out/'citation_query_plan.json',{'population':'2863 AR5 all-status repaired single raw DOI keys; 300-key frozen SRS.',
            'citing_population':'OpenAlex article/review works published 2014-2021, without a climate text filter.',
            'population_raw_keys':len(raw5),'sample_raw_keys':len(sample),'unique_resolved_sample_work_ids':len(work_to_dois),'unresolved_sample_keys':unresolved,
            'additional_documented_upper_cost_usd':len(work_to_dois)*.0001,'maximum_concurrent_requests':4,'input':audit})
    def query(wid):
        p={'filter':'cites:'+wid.rsplit('/',1)[-1]+',publication_year:2014-2021,type:article|review','per_page':1,'select':'id'}
        r=client.get(p,estimated=.0001);n=r.get('meta',{}).get('count')
        if not isinstance(n,int) or n<0:raise APIStop('STOP: citation-count response lacks valid count')
        return {'openalex_work_id':wid,'ar5_raw_dois':work_to_dois[wid],'citing_works_2014_2021':n,
                'raw_request_id':request_id('https://api.openalex.org/works',p)}
    todo=iter(sorted(work_to_dois));done=[];failures=[]
    with ThreadPoolExecutor(max_workers=4) as executor:
        pending={}
        for _ in range(4):
            wid=next(todo,None)
            if wid is not None:pending[executor.submit(query,wid)]=wid
        while pending:
            finished,_=wait(pending,return_when=FIRST_COMPLETED)
            for future in finished:
                wid=pending.pop(future)
                try:done.append(future.result())
                except Exception as error:
                    client.failed.set();failures.append(type(error).__name__)
                if not client.failed.is_set():
                    nxt=next(todo,None)
                    if nxt is not None:pending[executor.submit(query,nxt)]=nxt
            if len(done)==1 or (len(done) and len(done)%100==0):
                print(json.dumps({'phase':'citations','work_ids_done':len(done),'work_ids_total':len(work_to_dois),'cumulative_budget_charge_usd':client.cost}),flush=True)
    if failures:raise APIStop('STOP: citation API work stopped; all in-flight responses archived; no new submissions or retries')
    if len(done)!=len(work_to_dois):raise APIStop('STOP: citation acquisition incomplete')
    done.sort(key=lambda r:r['openalex_work_id'])
    jwrite(out/'citation_counts.json',{'counts':done,'unresolved_raw_keys':unresolved,'population_raw_keys':len(raw5),
                                      'sample_raw_keys':len(sample),'sample_manifest_sha256':sha(out/'citation_sample_manifest.json')})


def parsed_date(w):
    value=w.get('publication_date')
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):return None
    try:return date.fromisoformat(value)
    except ValueError:return None


def diversity(counter):
    n=sum(counter.values())
    return {'n':n,'D0':len(counter),'D2':n*n/sum(x*x for x in counter.values()) if n else None}


def analysis(repo,out,results_dir):
    """Use archived API JSON only; this function never initializes a client/key."""
    import numpy as np
    if (out/'bundles/index.json').exists():restore_raw(out)
    if np.__version__!='2.2.6':raise ValueError('Use pinned NumPy 2.2.6 for registered resampling')
    if results_dir.exists():raise ValueError('Choose fresh analytical results directory')
    for required in ['lookup_batches.json','source_assignment.json','pool_broad_AR5.json','pool_broad_AR6.json',
                     'pool_narrow_AR5.json','pool_narrow_AR6.json','source_pool_broad_AR5.json','source_pool_broad_AR6.json',
                     'source_pool_narrow_AR5.json','source_pool_narrow_AR6.json']:
        if not (out/required).exists():raise ValueError('Acquisition incomplete; missing '+required+'; no analytical tables generated')
    for p in (out/'raw').glob('*.request.json'):
        if p.name.startswith('._'):continue
        metadata=json.loads(p.read_text());body=out/'raw'/metadata['response_file']
        if metadata['http_status']!=200 or not metadata['json_success']:
            recovery_path=out/'transport_recoveries.json'
            recovered=json.loads(recovery_path.read_text())['recoveries'] if recovery_path.exists() else []
            rec=next((r for r in recovered if r['failed_request_id']==request_id(metadata['endpoint'],metadata['parameters'])),None)
            if rec is None:raise ValueError('Acquisition contains unresolved failed request; no analytical tables generated')
            replacement=out/'raw'/(rec['replacement_request_id']+'.request.json')
            rmeta=json.loads(replacement.read_text())
            if sha(replacement)!=rec['replacement_metadata_sha256'] or rmeta['http_status']!=200 or not rmeta['json_success']:
                raise ValueError('Transport recovery is not verified')
            for rid in rec.get('replacement_request_ids',[rec['replacement_request_id']]):
                path=out/'raw'/(rid+'.request.json');replacement_meta=json.loads(path.read_text())
                if sha(path)!=rec.get('replacement_metadata_hashes',{}).get(rid,rec['replacement_metadata_sha256']) or replacement_meta['http_status']!=200 or not replacement_meta['json_success']:
                    raise ValueError('A transport recovery segment is not verified')
        if sha(body)!=metadata['response_sha256']:raise ValueError('Archived raw response hash differs')
    rs,input_audit=load_inputs(repo);ds,mapping,works=crosswalk(out);config=constants(repo)
    results_dir.mkdir(parents=True);tables=[]
    def table(name,rows,fields=None):
        path=results_dir/(name+'.csv');rows=list(rows);rows_write(path,rows,fields)
        tables.append({'filename':path.name,'rows':len(rows),'sha256':sha(path)})
    # One requested DOI can have several candidate works. No title-only fallback.
    resolution=[]
    for doi in ds:
        candidates=mapping.get(doi,{})
        if len(candidates)==1:
            w=next(iter(candidates.values()));source=sources_of(w)
            resolution.append({'requested_doi':doi,'resolution':'exact_unique','candidate_count':1,'openalex_id':w['id'],
                               'canonical_doi':normalized_doi(w.get('doi')),'publication_date':w.get('publication_date'),
                               'publication_year':w.get('publication_year'),'source_id':source.get('id'),
                               'source_name':source.get('display_name'),'source_type':source.get('type'),'issn_l':source.get('issn_l')})
        else:
            resolution.append({'requested_doi':doi,'resolution':'missing' if not candidates else 'ambiguous','candidate_count':len(candidates),
                               'openalex_id':None,'canonical_doi':None,'publication_date':None,'publication_year':None,
                               'source_id':None,'source_name':None,'source_type':None,'issn_l':None})
    table('doi_resolution',resolution)
    pool_rows=[]
    for label in ('broad','narrow'):
        for rep in WINDOWS:
            frozen=json.loads((out/('pool_'+label+'_'+rep+'.json')).read_text());response=frozen['response']
            total=int(response['meta']['count'])
            for g in response['group_by']:
                pool_rows.append({'query':label,'report_window':rep,'publication_year':int(g['key']),
                                  'pool_works':int(g['count']),'pool_window_total':total,'year_share':int(g['count'])/total,
                                  'raw_request_id':frozen['raw_request_id']})
    table('pool_by_year',pool_rows)
    raw_age_dir=repo/'revision_preparation/review_round2/raw_age_v1'
    raw_age_audit=raw_age_dir/'raw_age_occurrence_audit.csv'
    if not raw_age_audit.exists():raise ValueError('Restore the registered raw-age audit archive before external-age analysis')
    raw_age_manifest=json.loads((raw_age_dir/'manifest.json').read_text())
    expected_raw_age=next(t['sha256'] for t in raw_age_manifest['output_tables'] if t['filename']==raw_age_audit.name)
    if sha(raw_age_audit)!=expected_raw_age:raise ValueError('Registered raw-age occurrence audit hash differs')
    with raw_age_audit.open(newline='',encoding='utf-8') as f:raw_age_rows=list(csv.DictReader(f))
    pool_age=[]
    for label in ('broad','narrow'):
        for rep,(low,high) in WINDOWS.items():
            counts={r['publication_year']:r['pool_works'] for r in pool_rows if r['query']==label and r['report_window']==rep}
            pool_n=sum(counts.values());pool_mean=sum((high-y)*n for y,n in counts.items())/pool_n
            pool_recent=sum(n for y,n in counts.items() if high-y<=5)/pool_n
            for cohort in ('all_raw_year_occurrences','matched_raw_year_occurrences'):
                for group in ('All','WG1','WG2','WG3'):
                    eligible=[r for r in raw_age_rows if r['report_id']==rep and (group=='All' or r['wg_label']==group)
                              and (cohort=='all_raw_year_occurrences' or r['match_status'] in MATCHED)]
                    origins={int(r['documented_cutoff_year']) for r in eligible if r['documented_cutoff_year']}
                    if origins!={high}:raise ValueError('Ambiguous annual cutoff origin for pool comparison')
                    ages=[];excluded=Counter()
                    for r in eligible:
                        if not r['documented_cutoff_year']:excluded['missing_annual_cutoff_origin']+=1;continue
                        if not r['raw_leading_year']:excluded['missing_raw_leading_year']+=1;continue
                        year=int(r['raw_leading_year'])
                        if year<low:excluded['before_common_1980_window']+=1;continue
                        if year>high:excluded['after_common_window_or_cutoff']+=1;continue
                        ages.append(high-year)
                    n=len(ages);mean=sum(ages)/n if n else None;recent=sum(a<=5 for a in ages)/n if n else None
                    pool_age.append({'query':label,'report':rep,'cohort':cohort,'working_group':group,
                        'age_origin_year':high,'common_publication_year_lower':low,'common_publication_year_upper':high,
                        'IPCC_original_occurrences':len(eligible),'IPCC_included_occurrences':n,
                        'IPCC_excluded_missing_origin':excluded['missing_annual_cutoff_origin'],
                        'IPCC_excluded_missing_raw_year':excluded['missing_raw_leading_year'],
                        'IPCC_excluded_before_1980':excluded['before_common_1980_window'],
                        'IPCC_excluded_after_window':excluded['after_common_window_or_cutoff'],
                        'IPCC_mean_annual_age':mean,'IPCC_recent_le5_fraction':recent,'pool_works':pool_n,
                        'pool_mean_annual_age':pool_mean,'pool_recent_le5_fraction':pool_recent,
                        'IPCC_minus_pool_mean_age':mean-pool_mean if mean is not None else None,
                        'IPCC_minus_pool_recent_fraction':recent-pool_recent if recent is not None else None,
                        'comparison_unit':'IPCC raw-leading-year occurrences versus query-defined unique OpenAlex works.',
                        'precision':'Annual cutoff-year proxy, common publication-year window; not fractional date or acceptance eligibility.'})
    table('pool_age_comparison',pool_age)
    cohort_records={};age_rows=[];age_ecdf=[];date_coverage=[];window_reuse=[];outlet_rows=[];outlet_draws=[];outlet_diffs=[]
    rng=np.random.default_rng(SEED)
    for cohort in ('matched_assigned','all_raw_DOI'):
        source_counters={}
        for rep in WINDOWS:
            for group in ('All','WG1','WG2','WG3'):
                rr,diag=resolved_records(rs,mapping,cohort,rep,group);cohort_records[(cohort,rep,group)]=rr
                cc=Counter(sources_of(v['work']).get('id') for v in rr.values() if sources_of(v['work']).get('id'))
                source_counters[(rep,group,'all_known_sources')]=cc
                source_counters[(rep,group,'journal_sources')]=Counter(sources_of(v['work']).get('id') for v in rr.values() if sources_of(v['work']).get('id') and sources_of(v['work']).get('type')=='journal')
                # OpenAlex day-shaped dates are proxies; Jan 1 values may conceal reduced precision.
                dates={key:parsed_date(v['work']) for key,v in rr.items()}
                date_coverage.append(dict(cohort=cohort,report=rep,working_group=group,**diag,
                    API_ISO_date_present=sum(d is not None for d in dates.values()),
                    first_day_of_month_dates=sum(d is not None and d.day==1 for d in dates.values()),
                    january_first_dates=sum(d is not None and d.month==1 and d.day==1 for d in dates.values()),
                    source_id_present=sum(sources_of(v['work']).get('id') is not None for v in rr.values())))
                if group=='All':continue # No invented global or report publication month/day origin.
                origin=date.fromisoformat(config['CUTOFFS'][rep][group]);valid=[];excluded=Counter()
                for key,v in rr.items():
                    d=dates[key]
                    if d is None:excluded['missing_invalid_date']+=1;continue
                    age=(origin-d).days/365.2425
                    if age<0:excluded['published_after_cutoff']+=1;continue
                    if age>60:excluded['age_over_60']+=1;continue
                    valid.append((key,age))
                values=sorted(a for key,a in valid)
                age_rows.append({'cohort':cohort,'report':rep,'working_group':group,'origin_basis':'documented_WG_cutoff',
                                 'origin_date':origin.isoformat(),'resolved_keys':len(rr),'included_keys':len(values),
                                 'missing_invalid_dates':excluded['missing_invalid_date'],'published_after_cutoff':excluded['published_after_cutoff'],
                                 'age_over_60':excluded['age_over_60'],'mean_age':sum(values)/len(values) if values else None,
                                 'recent_le5_fraction':sum(v<=5 for v in values)/len(values) if values else None,
                                 'median_inverse_ecdf':values[max(0,math.ceil(.5*len(values))-1)] if values else None,
                                 'date_interpretation':'API publication-date proxy, not acceptance-date eligibility; date precision unverified'})
                hist=Counter(math.floor(a) for a in values);cum=0
                for year in sorted(hist):
                    cum+=hist[year];age_ecdf.append({'cohort':cohort,'report':rep,'working_group':group,'age_year_bin':year,
                                                   'keys_in_bin':hist[year],'cumulative_fraction':cum/len(values),'included_keys':len(values)})
                if rep=='AR6':
                    earlier,_=resolved_records(rs,mapping,cohort,'AR5',group)
                    earlier_all={str(r['paper_id']) if cohort=='matched_assigned' else r['raw_key'] for r in rs
                                 if r['report_id']=='AR5' and r['wg_label']==group and
                                 (cohort!='matched_assigned' or r['match_status'] in MATCHED) and
                                 (r['paper_id'] is not None if cohort=='matched_assigned' else r['raw_key'] is not None)}
                    boundary=date.fromisoformat(config['CUTOFFS']['AR5'][group])
                    eligible={key for key,d in dates.items() if d is not None and d<=boundary}
                    shared=eligible&earlier_all
                    window_reuse.append({'cohort':cohort,'working_group':group,'earlier_publication_date_boundary':boundary.isoformat(),
                                         'later_original_keys':diag['original_cohort_size'],'later_resolved_keys':len(rr),
                                         'later_keys_with_date':sum(d is not None for d in dates.values()),
                                         'later_published_by_boundary':len(eligible),'shared_published_by_boundary':len(shared),
                                         'conditional_publication_availability_reuse':len(shared)/len(eligible) if eligible else None,
                                         'screen':'OpenAlex publication date <= earlier group cutoff; not accepted-paper eligibility'})
        for group in ('All','WG1','WG2','WG3'):
            for scope in ('all_known_sources','journal_sources'):
                size=min(sum(source_counters[(r,group,scope)].values()) for r in WINDOWS)
                if not size:continue
                sampled={}
                for rep in WINDOWS:
                    counter=source_counters[(rep,group,scope)]
                    count=np.array([counter[k] for k in sorted(counter)],dtype=np.int64);values=[]
                    for i in range(1000):
                        draw=rng.multivariate_hypergeometric(count,size)
                        d0=int(np.count_nonzero(draw));d2=size*size/float(np.dot(draw,draw));values.append((d0,d2))
                        outlet_draws.append({'cohort':cohort,'working_group':group,'source_scope':scope,'report':rep,
                                             'replicate':i+1,'equal_size':size,'D0':d0,'D2':d2})
                    d0=np.array([x[0] for x in values]);d2=np.array([x[1] for x in values]);full=diversity(counter)
                    sampled[rep]=(d0,d2)
                    outlet_rows.append({'cohort':cohort,'working_group':group,'source_scope':scope,'report':rep,
                                        'mapped_source_keys':full['n'],'source_D0_full':full['D0'],'source_D2_full':full['D2'],
                                        'equal_size':size,'draws':1000,'equal_size_D0_mean':float(d0.mean()),
                                        'equal_size_D0_q025':float(np.quantile(d0,.025)),'equal_size_D0_q975':float(np.quantile(d0,.975)),
                                        'equal_size_D2_mean':float(d2.mean()),'equal_size_D2_q025':float(np.quantile(d2,.025)),
                                        'equal_size_D2_q975':float(np.quantile(d2,.975)),
                                        'interval_type':'Conditional equal-size resampling distribution, not population CI'})
                for i,name in [(0,'D0'),(1,'D2')]:
                    delta=sampled['AR6'][i]-sampled['AR5'][i]
                    outlet_diffs.append({'cohort':cohort,'working_group':group,'source_scope':scope,'metric':name,
                                         'AR6_minus_AR5_mean':float(delta.mean()),'q025':float(np.quantile(delta,.025)),
                                         'q975':float(np.quantile(delta,.975)),'equal_size':size,'draws':1000,
                                         'interval_type':'Conditional equal-size resampling difference, not population CI'})
    table('date_coverage',date_coverage);table('date_age_summary',age_rows);table('date_age_ecdf',age_ecdf)
    table('publication_window_reuse',window_reuse);table('outlet_diversity',outlet_rows)
    table('outlet_resampling_draws',outlet_draws);table('outlet_diversity_differences',outlet_diffs)
    age_diff=[]
    for cohort in ('matched_assigned','all_raw_DOI'):
        for group in ('WG1','WG2','WG3'):
            pair={r['report']:r for r in age_rows if r['cohort']==cohort and r['working_group']==group}
            for metric in ('mean_age','recent_le5_fraction'):
                a=pair['AR5'][metric];b=pair['AR6'][metric]
                age_diff.append({'cohort':cohort,'working_group':group,'origin_basis':'documented_WG_cutoff','metric':metric,
                                 'AR5':a,'AR6':b,'AR6_minus_AR5':b-a if a is not None and b is not None else None})
    table('date_age_differences',age_diff)
    # Reconcile external work identifiers without calling the DOI mapping an audit.
    coverage_index={(r['cohort'],r['report'],r['working_group']):r for r in date_coverage}
    recurrence=[]
    for cohort in ('matched_assigned','all_raw_DOI'):
        for group in ('All','WG1','WG2','WG3'):
            a={v['work']['id'] for v in cohort_records[(cohort,'AR5',group)].values()}
            b={v['work']['id'] for v in cohort_records[(cohort,'AR6',group)].values()};shared=a&b
            ca=coverage_index[(cohort,'AR5',group)];cb=coverage_index[(cohort,'AR6',group)]
            base={'cohort':cohort,'working_group':group,'AR5_original_keys':ca['original_cohort_size'],
                  'AR6_original_keys':cb['original_cohort_size'],'AR5_uniquely_resolved_keys':ca['resolved_identity'],
                  'AR6_uniquely_resolved_keys':cb['resolved_identity'],'AR5_missing_keys':ca.get('unresolved_identity',0),
                  'AR6_missing_keys':cb.get('unresolved_identity',0),'AR5_ambiguous_keys':ca.get('ambiguous_identity',0),
                  'AR6_ambiguous_keys':cb.get('ambiguous_identity',0),'AR5_unique_OpenAlex_work_IDs':len(a),
                  'AR6_unique_OpenAlex_work_IDs':len(b),'AR5_alias_keys_collapsed':ca['resolved_identity']-len(a),
                  'AR6_alias_keys_collapsed':cb['resolved_identity']-len(b),'shared_OpenAlex_work_IDs':len(shared),
                  'retention':len(shared)/len(a) if a else None,'reuse':len(shared)/len(b) if b else None,
                  'jaccard':len(shared)/len(a|b) if a|b else None,
                  'identity_note':'Externally resolved OpenAlex work IDs; exact DOI resolution is not human-validated cited-record identity.'}
            screens=[('none',None)]+[('publication_year_through_'+str(y),y) for y in (2012,2013,2014)]
            if group!='All':screens.append(('published_by_earlier_WG_cutoff',config['CUTOFFS']['AR5'][group]))
            for screen,boundary in screens:
                eligible=set();known=set()
                if screen.startswith('publication_year'):
                    for wid in b:
                        year=works[wid].get('publication_year')
                        if isinstance(year,int):
                            known.add(wid)
                            if year<=boundary:eligible.add(wid)
                elif screen=='published_by_earlier_WG_cutoff':
                    for wid in b:
                        when=parsed_date(works[wid])
                        if when is not None:
                            known.add(wid)
                            if when<=date.fromisoformat(boundary):eligible.add(wid)
                else:eligible=set(b);known=set(b)
                recurrence.append(dict(base,publication_availability_screen=screen,boundary=boundary,
                     later_work_IDs_with_screenfield=len(known),later_work_IDs_missing_screenfield=len(b-known),
                     later_work_IDs_published_by_boundary=len(eligible),shared_work_IDs_published_by_boundary=len(shared&eligible),
                     conditional_publication_availability_reuse=len(shared&eligible)/len(eligible) if eligible else None,
                     date_note='OpenAlex publication-date/year proxy, not adjudicated acceptance eligibility.'))
    table('openalex_work_recurrence',recurrence)
    # Compare source shares only for a common article/review and year-defined population.
    assignment=json.loads((out/'source_assignment.json').read_text());union=assignment['source_union'];source_meta={}
    for w in works.values():
        s=sources_of(w)
        if s.get('id'):source_meta[s['id']]=s
    share_rows=[];window_source_counts={};window_source_denominators={}
    for label in ('broad','narrow'):
        for rep,(lo,hi) in WINDOWS.items():
            rr=cohort_records[('matched_assigned',rep,'All')]
            within={key:v for key,v in rr.items() if v['work'].get('type') in ('article','review') and
                    isinstance(v['work'].get('publication_year'),int) and lo<=v['work']['publication_year']<=hi}
            eligible_counts=Counter(sources_of(v['work']).get('id') for v in within.values() if sources_of(v['work']).get('id'))
            known=sum(eligible_counts.values());all_eligible=len(within)
            window_source_counts[rep]=eligible_counts;window_source_denominators[rep]=all_eligible
            pool_all=json.loads((out/('pool_'+label+'_'+rep+'.json')).read_text())['response']['meta']['count']
            frozen=json.loads((out/('source_pool_'+label+'_'+rep+'.json')).read_text())
            pc={g['key']:int(g['count']) for g in frozen['response']['group_by']}
            for sid in union:
                ipcc=eligible_counts[sid];pool_count=pc.get(sid,0);pool_share=pool_count/pool_all if pool_all else None
                share=ipcc/known if known else None;meta=source_meta.get(sid,{})
                share_rows.append({'query':label,'report':rep,'source_id':sid,'source_name':meta.get('display_name'),
                                   'source_type':meta.get('type'),'issn_l':meta.get('issn_l'),
                                   'is_report_top15':sid in assignment['top15_sources'][rep],
                                   'all_matched_original_IDs':assignment['report_matched_ids'][rep],
                                   'all_matched_mapped_source_IDs':assignment['mapped_source_ids'][rep],
                                   'IPCC_article_review_keys_in_year_window':all_eligible,'IPCC_source_known_keys_in_window':known,
                                   'IPCC_source_count_in_window':ipcc,'IPCC_source_share_known':share,
                                   'IPCC_source_share_all_resolved_in_window':ipcc/all_eligible if all_eligible else None,
                                   'pool_window_works':pool_all,'pool_source_works':pool_count,'pool_source_share':pool_share,
                                   'IPCC_all_resolved_source_share_divided_by_pool_share':(ipcc/all_eligible)/pool_share if pool_share and all_eligible else None,
                                   'pool_zero_status':'HTTP200_zero_bucket_count' if not pool_count else 'positive',
                                   'raw_source_request_id':frozen['raw_request_id'],
                                   'population_note':'Composition comparison; IPCC references meet type/year filters but are not verified members of the climate text query. Not a selection probability or a WoS supply reconstruction.'})
    table('source_shares',share_rows)
    top15={rep:[sid for sid,n in sorted(window_source_counts[rep].items(),key=lambda item:(-item[1],item[0]))[:15]] for rep in WINDOWS}
    top_union=set(top15['AR5'])|set(top15['AR6']);share_lookup={(r['report'],r['source_id']):r for r in share_rows if r['query']=='broad'}
    top_rows=[]
    order=sorted(top_union,key=lambda sid:(-max(window_source_counts[rep][sid]/window_source_denominators[rep] for rep in WINDOWS),sid))
    for sid in order:
        meta=source_meta.get(sid,{})
        rec={'source_id':sid,'source_name':meta.get('display_name'),'source_type':meta.get('type'),'issn_l':meta.get('issn_l')}
        shares={}
        for rep in WINDOWS:
            n=window_source_counts[rep][sid];den=window_source_denominators[rep];shares[rep]=n/den
            existing=share_lookup.get((rep,sid))
            rec.update({rep+'_count':n,rep+'_all_resolved_type_year_denominator':den,rep+'_share':shares[rep],
                rep+'_is_top15':sid in top15[rep],rep+'_pool_count':existing['pool_source_works'] if existing else None,
                rep+'_pool_window_denominator':existing['pool_window_works'] if existing else None,
                rep+'_pool_share':existing['pool_source_share'] if existing else None,
                rep+'_IPCC_share_divided_by_pool_share':existing['IPCC_all_resolved_source_share_divided_by_pool_share'] if existing else None,
                rep+'_pool_count_status':existing['pool_zero_status'] if existing else 'not_in_frozen_source_query_unknown'})
        rec['AR6_minus_AR5_share']=shares['AR6']-shares['AR5']
        rec['population_note']='Broad-query composition comparison, not selection probability; top15 by type/year-restricted IPCC share.'
        top_rows.append(rec)
    table('outlet_top15_comparison',top_rows)
    indirect=[]
    targets={v['work']['id'] for v in cohort_records[('all_raw_DOI','AR5','All')].values()}
    for cohort in ('matched_assigned','all_raw_DOI'):
        later=cohort_records[(cohort,'AR6','All')];hasref=0;positive=0;unique_later={}
        for v in later.values():
            w=v['work'];refs=set(w.get('referenced_works') or []);hasref+=bool(refs);positive+=bool(refs&targets);unique_later[w['id']]=refs
        indirect.append({'cohort':cohort,'AR5_target_resolved_raw_DOI_work_IDs':len(targets),'AR6_resolved_keys':len(later),
                         'AR6_keys_with_recorded_references':hasref,'AR6_keys_citing_AR5_target':positive,
                         'fraction_among_all_resolved_keys':positive/len(later) if later else None,
                         'fraction_among_keys_with_recorded_references':positive/hasref if hasref else None,
                         'AR6_unique_openalex_work_IDs':len(unique_later),'AR6_unique_work_IDs_citing_target':sum(bool(refs&targets) for refs in unique_later.values()),
                         'AR6_unique_work_IDs_with_recorded_references':sum(bool(refs) for refs in unique_later.values()),
                         'fraction_among_unique_resolved_work_IDs':sum(bool(refs&targets) for refs in unique_later.values())/len(unique_later) if unique_later else None,
                         'fraction_among_unique_work_IDs_with_recorded_references':sum(bool(refs&targets) for refs in unique_later.values())/sum(bool(refs) for refs in unique_later.values()) if any(unique_later.values()) else None,
                         'missing_reference_interpretation':'Empty outgoing lists are unobserved citation coverage, not verified absence of a real-world citation.'})
    table('indirect_continuity',indirect)
    if (out/'citation_counts.json').exists():
        data=json.loads((out/'citation_counts.json').read_text());citation_rows=[]
        later_raw={r['raw_key'] for r in rs if r['report_id']=='AR6' and r['raw_key']}
        for rec in data['counts']:
            for doi in rec['ar5_raw_dois']:
                citation_rows.append({'AR5_raw_doi':doi,'openalex_work_id':rec['openalex_work_id'],
                                      'citing_article_review_works_2014_2021':rec['citing_works_2014_2021'],
                                      'cited_at_least_once_2014_2021':rec['citing_works_2014_2021']>0,
                                      'direct_raw_DOI_reused_in_AR6':doi in later_raw,'resolution':'exact_unique','raw_request_id':rec['raw_request_id']})
        for rec in data['unresolved_raw_keys']:
            citation_rows.append({'AR5_raw_doi':rec['raw_doi'],'openalex_work_id':None,'citing_article_review_works_2014_2021':None,
                                  'cited_at_least_once_2014_2021':None,'direct_raw_DOI_reused_in_AR6':rec['raw_doi'] in later_raw,
                                  'resolution':rec['status'],'raw_request_id':None})
        assert len(citation_rows)==data['sample_raw_keys']==300
        table('AR5_raw_DOI_citation_survival',citation_rows)
        known=[r for r in citation_rows if r['resolution']=='exact_unique']
        k=sum(r['cited_at_least_once_2014_2021'] for r in known);z=len(known)-k;u=len(citation_rows)-len(known)
        tail,interval=load_finite_math(repo);N=data['population_raw_keys'];n=data['sample_raw_keys']
        low=interval(N,n,k,.05)[0];high=interval(N,n,k+u,.05)[1]
        table('citation_survival_summary',[{'population_raw_DOI_keys':N,'sample_raw_DOI_keys':n,'positive_keys':k,'zero_keys':z,'unresolved_keys':u,
             'conditional_resolved_sample_fraction':k/(k+z) if k+z else None,'sample_outcome_lower':k/n,'sample_outcome_upper':(k+u)/n,
             'finite_population_total_lower95':low,'finite_population_total_upper95':high,
             'finite_population_fraction_lower95':low/N,'finite_population_fraction_upper95':high/N,
             'interval_method':'Exact finite hypergeometric lower endpoint at k and upper at k+u, N=2863, n=300, alpha=.05.',
             'interpretation':'Recorded citation survival in the raw DOI frame; not human linkage accuracy or all matched IPCC literature.',
             'math_source_sha256':sha(repo/'revision_preparation/annotation/annotation_tools.py')}])
    baseline=json.loads((repo/'reproducibility/reference/manifest.json').read_text())
    for t in baseline['tables']:
        if sha(repo/'reproducibility/reference'/t['filename'])!=t['sha256']:raise ValueError('Original registered table changed')
    if sha(repo/'database/ipcc_litdb.sqlite')!=input_audit['database_sha256_before']:raise ValueError('Database changed')
    jwrite(results_dir/'manifest.json',{'version':VERSION,'seed':SEED,'numpy_version':np.__version__,
         'script_sha256':sha(Path(__file__)),'source_snapshot':input_audit,'baseline_registry_sha256':sha(repo/'reproducibility/reference/manifest.json'),
         'original_29_table_hashes_unchanged':True,'cutoff_dates':config['CUTOFFS'],'cutoff_sources':config['CUTOFF_SOURCES'],
         'publication_origin_analysis':'Not generated: exact report release-date provenance was not supplied. Annual source years are not imputed to month/day.',
         'dates':'OpenAlex date proxies. ISO day format does not establish deposited day/month precision. January 1 proxies are counted separately.',
         'matching':'Exact requested DOI against work DOI or a location DOI; zero/multiple candidate works remain unresolved. No title-based fallback.',
         'pool':'Search-defined OpenAlex article/review populations in stated year windows, not WoS Topic replication or all climate science.',
         'source_ratio':'Composition comparison only. IPCC references are not verified climate-query members. Both ratio denominators retain missing source assignments; known-source shares are separate diagnostics.',
         'external_age_raw_audit':{'path':str(raw_age_audit.relative_to(repo)),'sha256':expected_raw_age,
                                   'registry_sha256':sha(raw_age_dir/'manifest.json')},
         'tables':tables,'raw_request_registry':[{'file':str(p.relative_to(out)),'sha256':sha(p)} for p in sorted((out/'raw').glob('*.request.json')) if not p.name.startswith('._')]})
    print(json.dumps({'phase':'analysis','registered_tables':len(tables),'results':str(results_dir),'old_tables_unchanged':True}),flush=True)

def verify_archives(out):
    base=out/'bundles';index=json.loads((base/'index.json').read_text());seen=set()
    for archive in index['archives']:
        path=base/archive['filename']
        if sha(path)!=archive['sha256']:raise ValueError('Compressed archive hash differs')
        expected={r['path']:r for r in archive['members']}
        with tarfile.open(path,'r:gz') as tar:
            for member in tar:
                if not member.isfile() or member.name not in expected:raise ValueError('Unexpected archive member')
                if member.name in seen:raise ValueError('Duplicate archive member')
                seen.add(member.name);payload=tar.extractfile(member).read();r=expected[member.name]
                if len(payload)!=r['bytes'] or hashlib.sha256(payload).hexdigest()!=r['sha256']:
                    raise ValueError('Recovered archive member bytes/hash differ')
        if not set(expected)<=seen:raise ValueError('Archive member missing')
    if len(seen)!=index.get('total_file_count',index['raw_file_count']):raise ValueError('Archive file count differs')
    return {'all_member_hashes_passed':True,'files_verified':len(seen),
            'raw_files_verified':sum(p.startswith('raw/') for p in seen),'uncompressed_bytes':index['uncompressed_bytes'],
            'compressed_bytes':sum(a['bytes'] for a in index['archives']),'index_sha256':sha(base/'index.json')}


def restore_raw(out,raw_target=None):
    """Recover raw and oversized derived bytes before replay or registry checks."""
    base=out/'bundles';index=json.loads((base/'index.json').read_text());target=Path(raw_target) if raw_target else out/'raw'
    target.mkdir(parents=True,exist_ok=True)
    for archive in index['archives']:
        path=base/archive['filename']
        if sha(path)!=archive['sha256']:raise ValueError('Archive hash differs before restore')
        expected={r['path']:r for r in archive['members']}
        with tarfile.open(path,'r:gz') as tar:
            for member in tar:
                safe_raw=bool(re.fullmatch(r'raw/[a-f0-9]{64}\.(request|response)\.json',member.name))
                safe_derived=(member.name=='lookup_batches.json' or
                              bool(re.fullmatch(r'results_v[0-9]+/[a-zA-Z0-9_]+\.csv',member.name)))
                if not member.isfile() or member.name not in expected or not (safe_raw or safe_derived):
                    raise ValueError('Unsafe or unexpected archived path')
                r=expected[member.name];dest=target/Path(member.name).name if safe_raw else out/member.name
                if dest.exists():
                    if sha(dest)!=r['sha256']:raise ValueError('Existing raw cache differs; no overwrite')
                    continue
                payload=tar.extractfile(member).read()
                if len(payload)!=r['bytes'] or hashlib.sha256(payload).hexdigest()!=r['sha256']:raise ValueError('Restored payload differs')
                dest.parent.mkdir(parents=True,exist_ok=True)
                with dest.open('xb') as f:f.write(payload)
    return target


def archive_raw(out):
    raw=out/'raw';base=out/'bundles'
    if (base/'index.json').exists():
        report=verify_archives(out);print(json.dumps(report),flush=True);return report
    if base.exists():raise ValueError('Incomplete bundle directory already exists; inspect it before rebuilding')
    raw_files=sorted(p for p in raw.iterdir() if p.is_file() and not p.name.startswith('._'))
    if not raw_files:raise ValueError('No raw API files to archive')
    if any(not re.fullmatch(r'[a-f0-9]{64}\.(request|response)\.json',p.name) for p in raw_files):raise ValueError('Unexpected raw cache filename')
    derived=[p for p in out.glob('results_v*/*.csv') if p.stat().st_size>2_000_000]
    if (out/'lookup_batches.json').exists() and (out/'lookup_batches.json').stat().st_size>2_000_000:
        derived.append(out/'lookup_batches.json')
    files=sorted(raw_files+derived,key=lambda p:p.relative_to(out).as_posix())
    chunks=[];chunk=[];total=0
    for p in files:
        if chunk and total+p.stat().st_size>8*1024*1024:chunks.append(chunk);chunk=[];total=0
        chunk.append(p);total+=p.stat().st_size
    if chunk:chunks.append(chunk)
    base.mkdir();archives=[]
    def pack(chunk):
        dest=base/('raw-part-'+str(len(archives)+1).zfill(3)+'.tar.gz');members=[]
        with dest.open('xb') as stream:
            with gzip.GzipFile(filename='',mode='wb',fileobj=stream,compresslevel=9,mtime=0) as compressed:
                with tarfile.open(fileobj=compressed,mode='w|') as tar:
                    for p in chunk:
                        data=p.read_bytes();name=p.relative_to(out).as_posix();info=tarfile.TarInfo(name)
                        info.size=len(data);info.mtime=0;info.uid=info.gid=0;info.uname=info.gname='';info.mode=0o644
                        tar.addfile(info,io.BytesIO(data));members.append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
        if dest.stat().st_size>2_000_000:
            dest.unlink()
            if len(chunk)<2:raise ValueError('A single compressed member exceeds 2 MB; explicit chunked-file format required')
            middle=len(chunk)//2;pack(chunk[:middle]);pack(chunk[middle:]);return
        archives.append({'filename':dest.name,'bytes':dest.stat().st_size,'sha256':sha(dest),'members':members})
    for chunk in chunks:pack(chunk)
    index={'format':'deterministic tar.gz, sorted paths, gzip mtime=0, fixed tar ownership/mode/time',
           'version':VERSION,'raw_file_count':len(raw_files),'total_file_count':len(files),
           'oversized_derived_files':[p.relative_to(out).as_posix() for p in derived],
           'maximum_compressed_part_bytes':2_000_000,'uncompressed_bytes':sum(p.stat().st_size for p in files),
           'raw_cache_retained':True,'archives':archives,'contains_credentials':False,
           'raw_fields':'Selected OpenAlex bibliographic/source/date/reference fields and sanitized HTTP provenance; no abstracts or authors requested.'}
    jwrite(base/'index.json',index);report=verify_archives(out)
    jwrite(out/'archive_replay_verification.json',report)
    print(json.dumps({'phase':'archive','raw_files':len(raw_files),'total_files':len(files),'uncompressed_bytes':index['uncompressed_bytes'],
                      'compressed_bytes':report['compressed_bytes'],'all_member_hashes_passed':True}),flush=True)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase',choices=['pool','works','sources','freeze-citation-sample','check-methods','citations','analyse','archive','restore-raw'])
    p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--api-key-file',type=Path,default=Path.home()/'.config/ipcc_litdb/openalex.env')
    p.add_argument('--budget-usd',type=float,default=.60)
    p.add_argument('--replay',action='store_true')
    p.add_argument('--authorized-resume-after-failure',action='store_true',help='Use only after explicit authorization; preserve prior error archives')
    p.add_argument('--results-dir',type=Path,help='Fresh independent registry output for offline analysis')
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    if a.phase=='archive':archive_raw(a.output);return 0
    if a.phase=='restore-raw':restore_raw(a.output);return 0
    if a.phase=='check-methods':method_self_checks(a.repo.resolve(),a.output);return 0
    if a.phase=='freeze-citation-sample':freeze_citation_sample(a.repo.resolve(),a.output);return 0
    if a.phase=='analyse':
        if not a.results_dir:p.error('--results-dir is required for offline analysis')
        analysis(a.repo.resolve(),a.output,a.results_dir.resolve());return 0
    try:c=Client(a.output,a.api_key_file,a.replay,a.budget_usd,a.authorized_resume_after_failure)
    except APIStop as e:print(str(e),flush=True);return 1
    try:
        if a.phase=='pool':pool(c,a.output)
        elif a.phase=='works':acquire_works(c,a.repo.resolve(),a.output)
        elif a.phase=='sources':source_queries(c,a.repo.resolve(),a.output)
        elif a.phase=='citations':acquire_citations(c,a.repo.resolve(),a.output)
    except (APIStop,ValueError) as e:
        print(str(e),flush=True);return 1
    print(json.dumps({'phase_complete':a.phase,'new_requests':c.requests,'cumulative_budget_charge_usd':c.cost}),flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
