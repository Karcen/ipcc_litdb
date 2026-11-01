#!/usr/bin/env python3
"""Check initial AI suggestions without creating human labels or accuracy rates."""
from __future__ import annotations
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def csvrows(path):
    with path.open(encoding='utf-8',newline='') as f:return list(csv.DictReader(f))


def run(repo):
    base=repo/'revision_preparation/annotation'; bundle=base/'ai_review'
    frozen=json.loads((base/'input_manifest.json').read_text())
    manifest=json.loads((bundle/'manifest.json').read_text())
    proposal=json.loads((bundle/'proposals.json').read_text())
    papers=csvrows(base/'inputs/revision_identity_audit_sample_papers.csv')
    raw=csvrows(base/'inputs/revision_identity_audit_sample_entries.csv')
    index=csvrows(bundle/'source_index.csv')
    errors=[]
    def need(condition,message):
        if not condition:errors.append(message)
    for item in frozen['sources'].values():
        need(sha(base/'inputs'/item['filename'])==item['sha256'],'Frozen input changed: '+item['filename'])
    need(manifest['source_manifest_sha256']==sha(base/'input_manifest.json'),'Frozen annotation manifest changed')
    need(manifest['human_validated'] is False and proposal['human_validated'] is False,'AI suggestions must not claim human validation')
    need(manifest['weighted_estimates_generated'] is False,'AI suggestions must not contain accuracy estimates')
    need(proposal['review_type']=='ai_assisted_initial_review','Wrong review provenance')
    need(proposal['sampling_seed_unchanged']==frozen['sampling_seed']==20261011,'Sampling seed changed')
    for name,item in manifest['files'].items():
        need(sha(bundle/name)==item['sha256'],'Proposal artifact hash changed: '+name)
        need((bundle/name).stat().st_size==item['bytes'],'Proposal artifact size changed: '+name)
    for name,item in manifest.get('reproduction_helpers',{}).items():
        need(sha(repo/name)==item['sha256'],'Replay helper hash changed: '+name)
        need((repo/name).stat().st_size==item['bytes'],'Replay helper size changed: '+name)
    cases=proposal['cases']; pmap={p['paper_id']:p for p in papers}; omap={o['occurrence_id']:o for o in raw}
    need(len(cases)==len(pmap)==331,'AI suggestion ID count differs from frozen sample')
    need(len({c['paper_id']for c in cases})==331,'Duplicate suggestion IDs')
    need({c['paper_id']for c in cases}==set(pmap),'Suggestion ID set differs from frozen sample')
    seen=[]; positive=[]
    valid_identity={'correct_merge','false_merge','unresolved'}
    valid_shared={'same_work','false_shared','not_shared','unresolved'}
    valid_metadata={'correct','metadata_error','unresolved'}
    valid_fields={'doi','title','year','journal','authors','other'}
    for c in cases:
        pid=c['paper_id']; need(c['review_complete']=='no'and c['human_confirmed']is False,'Human completion claimed for ID '+pid)
        need(c['annotator'].startswith('Codex'),'AI reviewer not identified for ID '+pid)
        need(c['identity_label']in valid_identity,'Invalid identity label '+pid)
        need(c['shared_link_label']in valid_shared,'Invalid shared label '+pid)
        need(c['missed_merge_search_status']=='incomplete','Exhaustive duplicate-search completion claimed '+pid)
        need(bool(c['review_notes'].strip()),'Missing per-ID reasoning '+pid)
        rr=[o for o in raw if o['paper_id']==pid]; expected={o['occurrence_id']for o in rr}; oo=c['occurrences']
        need(len(oo)==len(expected) and {o['occurrence_id']for o in oo}==expected,'Occurrence ownership/count mismatch '+pid)
        groups=set(); matched_groups={'AR5':set(),'AR6':set()}
        for o in oo:
            oid=o['occurrence_id']; seen.append(oid)
            if oid not in omap:continue
            r=omap[oid];gg={x.strip()for x in o['work_groups'].split('|')if x.strip()};groups|=gg
            # An initial proposal can explicitly defer a version grouping.
            # The human-label estimator retains its stricter completeness rule.
            need(bool(gg) or (c['identity_label']=='unresolved' and
                 o['metadata_judgment']=='unresolved' and
                 bool(c['unresolved_author_questions'])),
                 'Unexplained missing work group '+oid)
            need(o['metadata_judgment']in valid_metadata,'Invalid metadata judgment '+oid)
            ff={x.strip()for x in o['metadata_error_fields'].split('|')if x.strip()}
            need(ff<=valid_fields,'Invalid error field '+oid)
            need(bool(ff)if o['metadata_judgment']=='metadata_error'else not ff,'Error fields/judgment conflict '+oid)
            need(o['human_confirmed']is False,'Occurrence human-confirmed flag '+oid)
            need(o['source_page_zero_based']==int(r['source_page'])and o['viewer_page_one_based']==int(r['source_page'])+1,'Source page conversion mismatch '+oid)
            need(o['raw_reference_sha256_utf8']==hashlib.sha256(r['raw_reference'].encode()).hexdigest(),'Original reference changed '+oid)
            if r['match_status']in {'matched_doi','matched_title'}:matched_groups[r['report_id']]|=gg
        if c['identity_label']=='correct_merge':need(len(groups)==1,'Correct-merge proposal contains multiple document groups '+pid)
        if c['identity_label']=='false_merge':need(len(groups)>1,'False-merge proposal lacks distinct document groups '+pid)
        is_shared=all(matched_groups.values()); common=matched_groups['AR5']&matched_groups['AR6']
        if c['shared_link_label']=='not_shared':need(not is_shared,'Not-shared proposal is in shared matched set '+pid)
        if c['shared_link_label']=='same_work':need(is_shared and bool(common),'Same-work proposal lacks common matched document '+pid)
        if c['shared_link_label']=='false_shared':need(is_shared and not common,'False-shared proposal has common matched document '+pid)
        for q in c['missed_merge_candidate_ids']:
            need(q.isdecimal()and int(q)>0 and q!=pid,'Invalid counterpart ID '+pid+': '+q);positive.append(q)
            need(any(str(x['candidate_paper_id'])==q for x in c['candidate_judgments']),'Counterpart lacks explicit comparison '+pid+': '+q)
    need(len(seen)==len(set(seen))==801 and set(seen)==set(omap),'Frozen 801 occurrence set not covered exactly once')
    imap={r['occurrence_id']:r for r in index};need(len(index)==len(imap)==801 and set(imap)==set(omap),'Source index is incomplete')
    for oid,r in imap.items():
        if oid not in omap:continue
        e=omap[oid];need(r['raw_reference_sha256_utf8']==hashlib.sha256(e['raw_reference'].encode()).hexdigest(),'Source-index raw hash changed '+oid)
        need(int(r['viewer_page_one_based'])==int(e['source_page'])+1,'Source-index page mismatch '+oid)
    schema=json.loads((base/'annotation_schema.json').read_text())
    cmap={c['paper_id']:c for c in cases}
    cop={o['occurrence_id']:o for c in cases for o in c['occurrences']}
    for name,template,key,fields in [
        ('id_proposals.csv','id_annotations.csv','paper_id',schema['id_fields']),
        ('occurrence_proposals.csv','occurrence_annotations.csv','occurrence_id',schema['occurrence_fields']),
    ]:
        exported=csvrows(bundle/name);original=csvrows(base/'templates'/template)
        old={r[key]:r for r in original};new={r[key]:r for r in exported}
        need(len(exported)==len(new)==len(old)and set(new)==set(old),'AI CSV key/count mismatch '+name)
        for identifier,row in new.items():
            if identifier not in old:continue
            need(set(row)==set(old[identifier]),'AI CSV columns changed '+name)
            for field,value in old[identifier].items():
                if field not in fields:need(row.get(field)==value,'AI CSV original field changed '+identifier+': '+field)
            need(row['annotator'].startswith('Codex'),'AI CSV reviewer missing '+identifier)
            expected=cmap[identifier]if key=='paper_id'else cop[identifier]
            checks=['identity_label','shared_link_label','review_complete']if key=='paper_id'else ['work_groups','metadata_judgment','metadata_error_fields']
            for field in checks:need(row[field]==expected[field],'AI CSV/JSON decision conflict '+identifier+': '+field)
    reference=repo/'reproducibility/reference';baseline=json.loads((reference/'manifest.json').read_text())
    need(sha(reference/'manifest.json')==manifest['original_analysis_manifest_sha256'],'Original analysis manifest changed')
    need(len(baseline['tables'])==29 and baseline['seed']==20261011,'Original registered analysis inventory changed')
    for item in baseline['tables']:need(sha(reference/item['filename'])==item['sha256'],'Original registered table changed: '+item['filename'])
    db=repo/'database/ipcc_litdb.sqlite';need(sha(db)==frozen['database_sha256']==proposal['database_sha256']==manifest['database_sha256'],'Read-only snapshot hash changed')
    with sqlite3.connect(db.as_uri()+'?mode=ro&immutable=1',uri=True)as conn:
        known={str(row[0])for row in conn.execute('SELECT paper_id FROM papers')}
    need(set(positive)<=known,'Proposed counterpart ID is absent from snapshot')
    observed={'sampled_ids':len(cases),'occurrences':len(seen),'identity_proposals':dict(Counter(c['identity_label']for c in cases)),'metadata_proposals':dict(Counter(o['metadata_judgment']for c in cases for o in c['occurrences'])),'explicitly_unresolved_work_groups':sum(not o['work_groups'].strip()for c in cases for o in c['occurrences']),'original_registered_tables_unchanged':len(baseline['tables'])}
    need(observed['identity_proposals']==manifest['counts']['ai_identity_proposals'],'Identity proposal counts differ from manifest')
    need(observed['metadata_proposals']==manifest['counts']['ai_occurrence_metadata_proposals'],'Metadata proposal counts differ from manifest')
    if 'version_decisions.json' in manifest['files']:
        ledger=json.loads((bundle/'version_decisions.json').read_text())
        need(ledger['human_case_confirmation'] is False,'Version ledger claims human case confirmation')
        need(ledger['policy']==manifest['version_policy']==proposal['version_policy'],'Version policies differ')
        need(ledger['updated_proposals_sha256']==sha(bundle/'proposals.json'),'Version ledger proposal hash differs')
        for update in ledger['changes']:
            case=cmap[update['paper_id']]
            digest=hashlib.sha256((json.dumps(case,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()).hexdigest()
            need(digest==update['updated_case_sha256'],'Version ledger case hash differs: '+case['paper_id'])
    return {'schema_version':1,'checked_utc':datetime.now(timezone.utc).isoformat(),'status':'passed'if not errors else 'failed','scope':'Structural and provenance checks on AI suggestions, not validation of bibliographic judgments.','proposal_sha256':sha(bundle/'proposals.json'),'human_validated':False,'weighted_estimates_generated':False,'observed':observed,'errors':errors}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--report',type=Path);a=p.parse_args()
    try:r=run(a.repo.resolve())
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error)as e:r={'status':'failed','errors':[str(e)],'human_validated':False,'weighted_estimates_generated':False}
    if a.report:a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(r,ensure_ascii=False,indent=2));return 0 if r['status']=='passed'else 2


if __name__=='__main__':sys.exit(main())
