#!/usr/bin/env python3
"""Transparent raw-text blocking for possible cross-ID missing links.

Candidate generation is not adjudication and makes no claim of exhaustive
recall. Different DOI strings do not themselves establish different studies.
"""
from __future__ import annotations
import csv,difflib,hashlib,itertools,json,pathlib,re,sqlite3,unicodedata
from collections import defaultdict
from prepare_panel import normalized_dois
HERE=pathlib.Path(__file__).resolve().parent;REPO=HERE.parents[2]
def norm(s):return ' '.join(re.findall(r'\w+',unicodedata.normalize('NFKC',s).casefold()))
def parsed(raw):
    y=re.search(r'\b(1[5-9]\d{2}|20[0-2]\d)([a-z])?\b',raw[:300])
    if not y:return None
    # Literal text parsing, deliberately separate from the preferred title.
    suffix=raw[y.end():].lstrip(' :.,;)')
    title=suffix.split('. ')[0].split(' doi:')[0].split(' DOI:')[0]
    tt=norm(title).split();author=norm(raw[:y.start()].split(',')[0])
    if len(tt)<4:return None
    return {'year':int(y.group(1)),'author':author,'title':' '.join(tt),'tokens':tt}
def main():
    con=sqlite3.connect(f'file:{REPO}/database/ipcc_litdb.sqlite?mode=ro&immutable=1',uri=True);con.row_factory=sqlite3.Row
    rows=[dict(x) for x in con.execute("SELECT occurrence_id,paper_id,raw_reference,report_id FROM references_occurrence JOIN chapters USING(chapter_id) WHERE report_id IN ('AR5','AR6') ORDER BY occurrence_id")]
    blocks=defaultdict(lambda:defaultdict(list));byocc={};extracted={}
    for r in rows:
        r['raw_dois']=normalized_dois(r['raw_reference'] or '');p=parsed(r['raw_reference'] or '');byocc[r['occurrence_id']]=r;extracted[r['occurrence_id']]=p
        keys=[]
        for d in r['raw_dois']:keys.append(('raw_doi',d))
        if p:
            keys.extend([('title_prefix',' '.join(p['tokens'][:6])),('title_suffix',' '.join(p['tokens'][-6:]))])
            # Two adjacent bins guarantee adjacent years share at least one bin.
            keys.extend(('first_author_year',p['author']+'|'+str((p['year']+i)//2)) for i in (0,1))
        for key in keys:blocks[key][r['report_id']].append(r['occurrence_id'])
    candidates={};skipped=[];hard_pairs={}
    for (method,key),reports in sorted(blocks.items()):
        left=reports['AR5'];right=reports['AR6']
        if not left or not right:continue
        if len(left)*len(right)>10000:
            skipped.append({'method':method,'key':key,'AR5_occurrences':len(left),'AR6_occurrences':len(right),'potential_pairs':len(left)*len(right),'reason':'block product exceeds10000; not enumerated'})
            continue
        for aid,bid in itertools.product(left,right):
            a,b=byocc[aid],byocc[bid]
            pa,pb=extracted[aid],extracted[bid]
            if pa and pb and a['raw_dois'] and b['raw_dois'] and not set(a['raw_dois'])&set(b['raw_dois']):
                sim=difflib.SequenceMatcher(None,pa['title'],pb['title']).ratio()
                if sim>=.90:
                    dkey=(tuple(a['raw_dois']),tuple(b['raw_dois']))
                    value={'AR5_paper_id':a['paper_id'],'AR6_paper_id':b['paper_id'],'AR5_occurrence_id':aid,'AR6_occurrence_id':bid,'AR5_raw_doi':'|'.join(a['raw_dois']),'AR6_raw_doi':'|'.join(b['raw_dois']),'title_similarity':sim,'same_assigned_id':int(a['paper_id']==b['paper_id']),'reference_label':'unresolvable','reason':'distinct DOI and similar raw title are not proof of distinct studies'}
                    if dkey not in hard_pairs or sim>hard_pairs[dkey]['title_similarity']:hard_pairs[dkey]=value
            if a['paper_id']==b['paper_id']:continue
            if method=='first_author_year':
                if not pa or not pb or abs(pa['year']-pb['year'])>1:continue
                aa,bb=set(pa['tokens']),set(pb['tokens'])
                if len(aa&bb)/len(aa|bb)<.5:continue
            pair=(a['paper_id'],b['paper_id'])
            if pair not in candidates:candidates[pair]={'AR5_paper_id':pair[0],'AR6_paper_id':pair[1],'AR5_occurrence_id':aid,'AR6_occurrence_id':bid,'blocking_methods':set(),'title_similarity':None,'raw_doi_relation':'unknown'}
            c=candidates[pair];c['blocking_methods'].add(method)
            sim=difflib.SequenceMatcher(None,pa['title'],pb['title']).ratio() if pa and pb else None
            relation='equal' if set(a['raw_dois'])&set(b['raw_dois']) else 'distinct' if a['raw_dois'] and b['raw_dois'] else 'unknown'
            if sim is not None and (c['title_similarity'] is None or sim>c['title_similarity']):c.update(AR5_occurrence_id=aid,AR6_occurrence_id=bid,title_similarity=sim,raw_doi_relation=relation)
    out=HERE/'candidates';out.mkdir(exist_ok=True)
    cc=[];packets=[]
    for pair,c in sorted(candidates.items()):
        c['blocking_methods']='|'.join(sorted(c['blocking_methods']));cid='M'+hashlib.sha256((str(20261011)+'|'+str(pair)).encode()).hexdigest()[:12].upper();c['case_id']=cid
        aa,bb=(byocc[c[f'{r}_occurrence_id']] for r in ('AR5','AR6'))
        packets.append({'case_id':cid,'task':'identity','records':[{'record_id':f'R{i+1:03d}','report_group':'earlier' if i==0 else 'later','raw_reference':r['raw_reference']} for i,r in enumerate((aa,bb))],'frozen_evidence':[]})
        cc.append(c)
    hard=sorted(hard_pairs.values(),key=lambda x:(-x['title_similarity'],x['AR5_occurrence_id'],x['AR6_occurrence_id']))[:60]
    for name,data in [('candidate_pairs.csv',cc),('hard_distinct_doi_candidates.csv',hard),('skipped_large_blocks.csv',skipped)]:
        with (out/name).open('w',newline='') as f:
            fields=list(data[0]) if data else (['method','key','AR5_occurrences','AR6_occurrences','potential_pairs','reason'] if name.startswith('skipped') else ['case_id','AR5_paper_id','AR6_paper_id'])
            w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(data)
    (out/'blind_candidates.json').write_text(json.dumps({'cases':packets},ensure_ascii=False,indent=2)+'\n')
    summary={'seed':20261011,'raw_occurrences':len(rows),'parsed_raw_titles':sum(x is not None for x in extracted.values()),'candidate_cross_id_pairs':len(cc),'high_title_similarity_distinct_doi_candidates':len(hard),'available_distinct_raw_doi_pairs_at_title_similarity_090':len(hard_pairs),'requested_hard_reference_controls':60,'established_hard_reference_controls':0,'reason':'High title similarity with distinct DOIs is an adversarial candidate criterion, not a verified distinct-study reference label. Versions may share a study. Hard-candidate search includes same-assigned-ID cases as well as cross-ID cases.','skipped_blocks':len(skipped),'recall_scope':'Only literal raw DOI equality, raw title prefix/suffix and author with adjacent-year bins and Jaccard>=0.5; blocks over10000 products excluded. Does not cover every missing link.','llm_adjudication_completed':False}
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    paths=[p for p in out.iterdir() if p.is_file() and p.name!='manifest.json' and not p.name.startswith('._')]
    (out/'manifest.json').write_text(json.dumps({'analysis_package':'1.4.0','seed':20261011,'input_sha256':{'database/ipcc_litdb.sqlite':hashlib.sha256((REPO/'database/ipcc_litdb.sqlite').read_bytes()).hexdigest(),'code':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()},'output_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}},indent=2)+'\n')
    print(json.dumps(summary))
if __name__=='__main__':main()
