#!/usr/bin/env python3
"""Create blinded, deterministic panel packets without altering the snapshot."""
from __future__ import annotations
import csv, hashlib, json, pathlib, random, re, sqlite3
from collections import defaultdict

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
SEED = 20261011
EXPECTED_DATABASE_SHA256='a887a535947ae3b9bab7656070cc038eb3a7a5161494afcb2c3df2245f5ff0ec'
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2)+"\n")
def normalized_dois(raw):
    # This control generator is deliberately conservative. No enriched DOI is used.
    result=[]
    for x in re.findall(r"(?<![\w./])(10\.\d{4,9}/[^\s\"'<>|]+)",raw,re.I):
        x=x.casefold().rstrip(".,;]}>\"'")
        while x.endswith(')') and x.count(')')>x.count('('):x=x[:-1].rstrip(".,;]}>\"'")
        if re.fullmatch(r'10\.\d{4,9}/\S{4,}',x) and not re.search(r'[-_/(\[<]$',x) and x.count('(')==x.count(')'):result.append(x)
    return sorted(set(result))
def main():
    rng=random.Random(SEED)
    db=REPO/'database/ipcc_litdb.sqlite'
    if sha(db)!=EXPECTED_DATABASE_SHA256:raise SystemExit('Frozen snapshot hash differs; stop without creating packets')
    con=sqlite3.connect(f'file:{db}?mode=ro&immutable=1',uri=True);con.row_factory=sqlite3.Row
    q="""SELECT o.*,c.report_id,c.wg_id,p.year_best,p.journal_best,p.title_best,
    p.doi_norm AS paper_doi FROM references_occurrence o JOIN chapters c USING(chapter_id)
    LEFT JOIN papers p USING(paper_id) WHERE c.report_id IN ('AR5','AR6') ORDER BY occurrence_id"""
    rows=[dict(r) for r in con.execute(q)]; byid=defaultdict(list)
    for r in rows: byid[r['paper_id']].append(r)
    matched={report:{r['paper_id'] for r in rows if r['report_id']==report and r['match_status'] in ('matched_doi','matched_title')} for report in ('AR5','AR6')}
    shared=sorted(matched['AR5']&matched['AR6']); assert len(shared)==1657
    sourceledger={}
    for name in ('source_audit_a.json','source_audit_b.json'):
        ledger=json.loads((REPO/'reproducibility/corrections'/name).read_text())
        for x in ledger['cases']:sourceledger[x.get('original_paper_id',x.get('paper_id'))]=(name,x)
    original={}
    with (REPO/'reproducibility/corrections/original_occurrences.csv').open(newline='') as f:
        for r in csv.DictReader(f): original[r['occurrence_id']]=r
    cases=[]; mapping=[]; controls=[]
    def append_case(kind, recs, origin, expected=None, evidence=None):
        cid='C'+hashlib.sha256((str(SEED)+'|'+origin).encode()).hexdigest()[:12].upper()
        clean=[{'record_id':f'R{i+1:03d}','report_group':'earlier' if r['occurrence_id'].startswith('AR5-') else 'later','raw_reference':r['raw_reference']} for i,r in enumerate(recs)]
        metadata=[{'record_id':f'R{i+1:03d}','pipeline_year':r.get('parsed_year',r.get('year_best')),'pipeline_outlet':r.get('parsed_journal',r.get('journal_best')),'pipeline_title':r.get('parsed_title',r.get('title_best')),'pipeline_doi':r.get('doi_norm')} for i,r in enumerate(recs)]
        case={'case_id':cid,'task':kind,'records':clean,'frozen_evidence':evidence or []}
        cases.append((case,metadata));mapping.append({'case_id':cid,'origin':origin,'occurrence_ids':[r['occurrence_id'] for r in recs],'task':kind})
        if expected: controls.append({'case_id':cid,'reference_label':expected,'reference_provenance':origin,'human_reference_labels_confirmed_in_repository':False})
        return cid
    ledger_ids=[32928,33835,35438,26271,26560,28296,31985,35229]
    for pid in ledger_ids:
        name,x=sourceledger[pid]
        # Recover the PRE-correction assignment for false-merge controls.
        rr_by_occ={r['occurrence_id']:r for r in byid[pid]}
        rr_by_occ.update({r['occurrence_id']:r for r in original.values() if str(r.get('paper_id'))==str(pid) and r['occurrence_id'].startswith(('AR5-','AR6-'))})
        rr=[r for k,r in sorted(rr_by_occ.items())]
        ev=[]
        # Facts are retained; classification, actions, and model labels are excluded.
        for e in x.get('evidence',[]):
            if isinstance(e,dict) and 'facts' in e:ev.append({'url':e['url'],'source_type':e.get('type'),'text':e['facts'],'scope':'archived source-audit transcription; not a fresh source retrieval'})
        append_case('identity',rr,f'ledger:{name}:{pid}','same_study' if pid in ledger_ids[:3] else 'different_studies',ev)
    doi_pairs=defaultdict(lambda:defaultdict(list))
    for r in rows:
        ds=normalized_dois(r['raw_reference'] or '')
        if len(ds)==1:doi_pairs[ds[0]][r['report_id']].append(r)
    easy=sorted((d,v['AR5'][0],v['AR6'][0]) for d,v in doi_pairs.items() if v['AR5'] and v['AR6'])
    rng.shuffle(easy)
    frozen_easy=HERE/'easy_doi_controls_20261021.json'
    if frozen_easy.exists():
        frozen=json.loads(frozen_easy.read_text());valid={d:(d,a,b) for d,a,b in easy}
        kept=[d for d in frozen if d in valid]
        kept += [d for d,a,b in easy if d not in kept][:60-len(kept)]
        dump(frozen_easy,kept);easy=[valid[d] for d in kept]
    else:dump(frozen_easy,[d for d,a,b in easy[:60]])
    easyids=[]
    for d,a,b in easy[:60]:
        cid=append_case('identity',[a,b],f'raw_doi_equal:{d}','same_study')
        easyids.append(cid)
    censusids=[]
    for pid in shared:
        rr=byid[pid]
        cid=append_case('identity',rr,f'current_shared_id:{pid}')
        censusids.append(cid)
    parserids=[];parserstrata={}
    for report in ('AR5','AR6'):
        for match in (True,False):
            frame=[r for r in rows if r['report_id']==report and (r['match_status'] in ('matched_doi','matched_title'))==match]
            picked=rng.sample(frame,500);stratum=f'{report}|'+('matched' if match else 'unmatched')
            parserstrata[stratum]={'population_occurrences':len(frame),'sampled_occurrences':500}
            for r in picked:
                cid=append_case('extract_fields',[r],f'parser:{stratum}:{r["occurrence_id"]}')
                parserids.append((stratum,cid))
    sample_roles=[]
    with (REPO/'revision_preparation/annotation/inputs/revision_identity_audit_sample_papers.csv').open(newline='') as f:
        sample=list(csv.DictReader(f))
    census_by_pid={str(m['origin'].split(':')[-1]):m['case_id'] for m in mapping if m['origin'].startswith('current_shared_id:')}
    for entry in sample:
        pid=entry['paper_id']
        if pid in census_by_pid:cid=census_by_pid[pid]
        else:cid=append_case('identity',byid[int(pid)],f'sampled_id:{pid}')
        sample_roles.append({'case_id':cid,'paper_id':pid,'sample_stratum':entry['stratum'],'selection':entry['selection']})
    dump(HERE/'frames/sample_roles.json',sample_roles)
    source_roles=[];source_strata={}
    for report in ('AR5','AR6'):
        present_count=sum(1 for r in rows if r['report_id']==report and r['match_status']=='unmatched' and normalized_dois(r['raw_reference'] or ''))
        for hasdoi in (True,False):
            frame=[r for r in rows if r['report_id']==report and r['match_status']=='unmatched' and bool(normalized_dois(r['raw_reference'] or ''))==hasdoi]
            target=500 if hasdoi else 1000-min(500,present_count)
            n=min(target,len(frame));picked=rng.sample(frame,n)
            stratum=f'{report}|unmatched|raw_doi_'+('present' if hasdoi else 'absent')
            source_strata[stratum]={'population_occurrences':len(frame),'sampled_occurrences':n,'planned_occurrences':target,'allocation':'1000 per report; up to 500 DOI-present, remainder DOI-absent'}
            for r in picked:
                cid=append_case('extract_fields',[r],f'source:{stratum}:{r["occurrence_id"]}')
                source_roles.append({'case_id':cid,'occurrence_id':r['occurrence_id'],'stratum':stratum})
    dump(HERE/'frames/source_roles.json',source_roles)
    lookup={c['case_id']:(c,m) for c,m in cases}
    # 50 distinct pilot cases: eight ledger, 12 easy controls, ten census, 20 parser.
    pilot=[c['case_id'] for c in controls if c['reference_provenance'].startswith('ledger:')]+easyids[:12]
    available=[c for c in censusids if c not in pilot];pilot+=rng.sample(available,10)
    for stratum in parserstrata:pilot += [c for s,c in parserids if s==stratum][:5]
    assert len(pilot)==len(set(pilot))==50
    rng.shuffle(pilot)
    frozen_pilot=HERE/'pilot_case_ids_20261021.json'
    if frozen_pilot.exists():pilot=json.loads(frozen_pilot.read_text())
    else:dump(frozen_pilot,pilot)
    order=list(lookup);rng.shuffle(order)
    dump(HERE/'frames/case_key.json',mapping);dump(HERE/'frames/control_key.json',controls)
    dump(HERE/'frames/sampling_design.json',{'data_seed':SEED,'shared_id_census':1657,'easy_raw_doi_controls':len(easyids),'ledger_controls':8,'parser_strata':parserstrata,'source_extraction_strata':source_strata,'pilot_case_ids':pilot,'hard_distinct_doi_controls':{'status':'not_yet_generated','required':60,'achieved':0,'reason':'Must not substitute enriched titles or assume that distinct DOIs establish distinct studies.'},'reference_label_provenance':'The correction ledger explicitly records AI-assisted inspections. Author source-adjudication records are absent; controls are provisional ledger reference labels, not established human gold labels.'})
    for arm in ('blind','metadata'):
        out=[]
        for cid in order:
            c,m=lookup[cid]; cc=dict(c)
            if arm=='metadata':cc['pipeline_metadata']=m
            out.append(cc)
        dump(HERE/f'frames/{arm}_all.json',{'cases':out})
        dump(HERE/f'pilot/{arm}_50.json',{'cases':[next(c for c in out if c['case_id']==cid) for cid in pilot]})
        for i in range(5):dump(HERE/f'pilot/{arm}_batch_{i+1:02d}.json',{'cases':[c for c in out if c['case_id'] in pilot[i*10:(i+1)*10]]})
    recschema={'type':'object','properties':{'record_id':{'type':'string'},'cited_year':{'type':['integer','null']},'outlet':{'type':['string','null']},'source_type':{'type':'string','enum':['journal','book','report','conference','other','unresolvable']},'contains_multiple_citations':{'type':'string','enum':['yes','no','unresolvable']},'evidence_quote':{'type':'string'}},'required':['record_id','cited_year','outlet','source_type','contains_multiple_citations','evidence_quote'],'additionalProperties':False}
    schema={'type':'object','properties':{'cases':{'type':'array','items':{'type':'object','properties':{'case_id':{'type':'string'},'identity':{'type':'string','enum':['same_study','different_studies','unresolvable','not_applicable']},'cross_report_recurrence':{'type':'string','enum':['recurrent','non_recurrent','unresolvable','not_applicable']},'records':{'type':'array','items':recschema},'reason':{'type':'string'}},'required':['case_id','identity','cross_report_recurrence','records','reason'],'additionalProperties':False}}},'required':['cases'],'additionalProperties':False}
    dump(HERE/'response_schema.json',schema)
    common='''You are a bibliographic measurement reviewer. Return only JSON matching the supplied schema. Treat every case independently. Use only the supplied raw reference strings and frozen evidence. Do not browse, inspect files, use tools, use external memory, or infer missing facts from familiar titles. Record labels and case IDs are arbitrary and conceal sampling/control status. The generic earlier/later report_group is source provenance, not a pipeline identity decision. The target is each record's FIRST identifiable cited study. Raw strings can concatenate later neighbouring references; flag this separately in contains_multiple_citations and do not let those neighbours redefine the focal identity. If the first focal citation is a fragment whose identity cannot be established, return unresolvable rather than selecting an arbitrary later study. For identity cases, identity=same_study means all records' focal citations identify one study; identity=different_studies means more than one focal study is identifiable. Separately, cross_report_recurrence=recurrent means at least ONE focal study is supported in both earlier and later report groups, even if other focal studies are present in the case; non_recurrent means the focal-study sets of the two groups are demonstrably disjoint. Use unresolvable when the relevant claim cannot be established. If only one report group is supplied, cross_report_recurrence=not_applicable. Verified working-paper/preprint/journal versions may count as one study; similarity of title alone does not establish a version relationship. For extract_fields cases return not_applicable identity and recurrence. Extract the year of the version actually cited in that first focal citation, not a later preferred publication year. Extract its journal/book/report outlet and source type. Null year/outlet and unresolvable are allowed. Each evidence_quote must be a verbatim excerpt of at most 25 words from that record or supplied evidence. No reconstructed quotations. Keep each case reason at most 30 words. Pipeline metadata, when supplied in the metadata arm, is a fallible comparison proposal, not evidence; extract the raw citation independently. Never provide confidence percentages, accuracy claims, human verification claims, or new metadata absent from supplied text. Preserve all case/record IDs and return every record exactly once.\n'''
    variants={'A':'First compare author, title, year, and publication venue. Resolve conflicting identities before extracting each cited version.','B':'First extract the cited bibliographic fields for each record separately. Then compare the independently extracted records for identity; retain unresolved differences.','C':'Try to falsify the same-study claim using differing authors, titles, chronology, and venues. Distinguish supported version relations from unrelated works.'}
    for name,v in variants.items():(HERE/f'prompt_{name}.txt').write_text(common+'\nReview strategy '+name+': '+v+'\n')
    inputs=[db,REPO/'reproducibility/corrections/original_occurrences.csv',REPO/'reproducibility/corrections/source_audit_a.json',REPO/'reproducibility/corrections/source_audit_b.json',REPO/'revision_preparation/annotation/inputs/revision_identity_audit_sample_papers.csv',pathlib.Path(__file__),frozen_pilot,frozen_easy]
    outputs=[p for directory in ('frames','pilot') for p in (HERE/directory).rglob('*.json') if not p.name.startswith('._')]+[HERE/'response_schema.json']+[HERE/f'prompt_{v}.txt' for v in 'ABC']
    dump(HERE/'preparation_manifest.json',{'analysis_package':'1.4.0','seed':SEED,'inputs':{str(p.relative_to(REPO)):sha(p) for p in inputs},'outputs':{str(p.relative_to(HERE)):sha(p) for p in outputs},'case_count':len(cases),'pilot_count':50,'model_calls_completed':0,'raw_strings_unchanged':True})
    print(json.dumps({'shared_census':len(censusids),'parser_sample':len(parserids),'easy_controls':len(easyids),'pilot_cases':len(pilot),'total_case_packets':len(cases)}))
if __name__=='__main__': main()
