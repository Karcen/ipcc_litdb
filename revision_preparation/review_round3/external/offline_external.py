#!/usr/bin/env python3
"""Replay date, source and equal-window comparisons from frozen Round 2 bytes.

The Round 2 directories, database and baseline tables are read-only inputs.
No network or credentials are used by this script.
"""
from __future__ import annotations
import argparse, ast, csv, gzip, hashlib, io, json, math, sqlite3, sys, tarfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

VERSION='1.4.0'
SEED=20261011
MATCHED={'matched_doi','matched_title'}

def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()

def js(p,x):
    Path(p).write_text(json.dumps(x,indent=2,sort_keys=True,allow_nan=False)+'\n')

def csvread(p):
    with Path(p).open(newline='') as f:return list(csv.DictReader(f))

def write(p,rs):
    rs=list(rs)
    if not rs:raise ValueError('An empty table must not be silently published')
    with Path(p).open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rs[0]));w.writeheader();w.writerows(rs)
    return dict(filename=Path(p).name,rows=len(rs),sha256=sha(p))

class FrozenBundles:
    def __init__(self,p):
        self.p=p;self.index=json.loads((p/'index.json').read_text());self.locations={};self.inputs=[]
        for a in self.index['archives']:
            q=p/a['filename']
            if sha(q)!=a['sha256']:raise ValueError('Frozen bundle hash mismatch')
            self.inputs.append(dict(path=q.as_posix(),sha256=a['sha256']))
            for m in a['members']:self.locations[m['path']]=(q,m)
    def read(self,name):
        p,m=self.locations[name]
        with tarfile.open(p,'r:gz') as t:b=t.extractfile(name).read()
        if len(b)!=m['bytes'] or hashlib.sha256(b).hexdigest()!=m['sha256']:raise ValueError('Frozen member hash mismatch')
        return b
    def iter_members(self,names):
        names=set(names)
        for a in self.index['archives']:
            selected={m['path']:m for m in a['members'] if m['path'] in names}
            if not selected:continue
            with tarfile.open(self.p/a['filename'],'r:gz') as t:
                for member in t:
                    if member.name not in selected:continue
                    b=t.extractfile(member).read();m=selected[member.name]
                    if len(b)!=m['bytes'] or hashlib.sha256(b).hexdigest()!=m['sha256']:raise ValueError('Frozen member hash mismatch')
                    yield member.name,b

def import_r2(repo):
    import importlib.util
    p=repo/'scripts/review_round2_openalex.py'
    spec=importlib.util.spec_from_file_location('r2_external',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m

def load_works(repo):
    old=repo/'revision_preparation/review_round2/openalex_v1/activated_attempt_03'
    bundles=FrozenBundles(old/'bundles')
    batches=json.loads(bundles.read('lookup_batches.json'))
    names={'raw/'+rid+'.response.json' for b in batches['batches'] for rid in b.get('request_ids',[b['request_id']])}
    works={}
    for name,b in bundles.iter_members(names):
        for w in json.loads(b)['results']:works[w['id']]=w
    resolution=csvread(old/'results_v1/doi_resolution.csv')
    mapping={r['requested_doi']:works[r['openalex_id']] for r in resolution if r['resolution']=='exact_unique'}
    if any(r['openalex_id'] not in works for r in resolution if r['resolution']=='exact_unique'):raise ValueError('Resolved work missing from frozen bytes')
    return works,mapping,bundles

def read_occurrence_audit(repo):
    p=repo/'revision_preparation/review_round2/raw_age_v1/raw_age_occurrence_audit.csv.gz'
    with gzip.open(p,'rt',newline='') as f:return list(csv.DictReader(f))

def cutoff_reuse(rs,mapping,r2):
    cfg=r2.constants(REPO);out=[]
    for cohort in ['matched_assigned','doi_matched_assigned','doi_matched_common_sources','all_raw_DOI']:
        for group in ['WG1','WG2','WG3']:
            grouped={rep:defaultdict(list) for rep in ['AR5','AR6']}
            for r in rs:
                if r['wg_label']!=group:continue
                if cohort=='all_raw_DOI':key=r['raw_key'];d=r['raw_key']
                else:
                    if r['match_status'] not in (MATCHED if cohort=='matched_assigned' else {'matched_doi'}):continue
                    key=str(r['paper_id']) if r['paper_id'] is not None else None;d=r['preferred_key']
                if key:grouped[r['report_id']][key].append(d)
            records={}
            for rep in grouped:
                valid={}
                for key,ds in grouped[rep].items():
                    ws={mapping[d]['id']:mapping[d] for d in ds if d in mapping}
                    if len(ws)==1:valid[key]=next(iter(ws.values()))
                records[rep]=valid
            common_sources={r2.sources_of(w).get('id') for w in records['AR5'].values()}&{r2.sources_of(w).get('id') for w in records['AR6'].values()}
            common_sources.discard(None)
            if cohort=='doi_matched_common_sources':
                records={rep:{k:w for k,w in rr.items() if r2.sources_of(w).get('id') in common_sources} for rep,rr in records.items()}
            boundary=date.fromisoformat(cfg['CUTOFFS']['AR5'][group])
            for exclusion in ['none','exclude_january_first','exclude_every_month_first']:
                valid={};removed=Counter()
                for key,w in records['AR6'].items():
                    d=r2.parsed_date(w)
                    if d is None:removed['missing_date']+=1;continue
                    if exclusion=='exclude_january_first' and d.month==1 and d.day==1:removed['date_precision_exclusion']+=1;continue
                    if exclusion=='exclude_every_month_first' and d.day==1:removed['date_precision_exclusion']+=1;continue
                    valid[key]=(d,w)
                eligible={k for k,(d,w) in valid.items() if d<=boundary}
                earlier_keys=set(records['AR5']) if cohort=='doi_matched_common_sources' else set(grouped['AR5'])
                shared=eligible&earlier_keys
                out.append(dict(cohort=cohort,working_group=group,date_exclusion=exclusion,earlier_cutoff_date=boundary.isoformat(),
                   AR5_original_keys=len(grouped['AR5']),AR6_original_keys=len(grouped['AR6']),
                   AR5_uniquely_resolved_keys=len(records['AR5']),AR6_uniquely_resolved_keys=len(records['AR6']),
                   common_source_count=len(common_sources),AR6_missing_date=removed['missing_date'],
                   AR6_date_precision_excluded=removed['date_precision_exclusion'],AR6_eligible_keys=len(eligible),shared_eligible_keys=len(shared),
                   conditional_reuse=len(shared)/len(eligible) if eligible else None,
                   interpretation='OpenAlex publication availability, not acceptance eligibility; assigned IDs except raw DOI cohort.',
                   common_coverage_definition='DOI-matched occurrence cohort in each report, restricted to sources observed in both reports; not an intersection of document IDs.' if cohort=='doi_matched_common_sources' else 'Cohort defined separately in each report; the intersection of document IDs is not used as a denominator.'))
    return out

def equal_window_ages(repo):
    old=repo/'revision_preparation/review_round2/openalex_v1/activated_attempt_03/results_v1'
    pools=csvread(old/'pool_by_year.csv');raw=read_occurrence_audit(repo);rows=[]
    for query in ['broad','narrow']:
        for rep,hi in [('AR5',2013),('AR6',2021)]:
            for length in [30,0]:
                lo=hi-29 if length else 1980
                counts={int(r['publication_year']):int(r['pool_works']) for r in pools if r['query']==query and r['report_window']==rep and lo<=int(r['publication_year'])<=hi}
                n=sum(counts.values());mean=sum((hi-y)*v for y,v in counts.items())/n;recent=sum(v for y,v in counts.items() if hi-y<=5)/n
                for cohort in ['all','matched','unmatched']:
                    for group in ['All','WG1','WG2','WG3']:
                        rr=[r for r in raw if r['report_id']==rep and (group=='All' or r['wg_label']==group) and (cohort=='all' or (r['match_status'] in MATCHED)==(cohort=='matched'))]
                        selected=[hi-int(r['raw_leading_year']) for r in rr if r['documented_cutoff_year'] and r['raw_leading_year'] and lo<=int(r['raw_leading_year'])<=hi]
                        m=sum(selected)/len(selected) if selected else None;f=sum(x<=5 for x in selected)/len(selected) if selected else None
                        rows.append(dict(query=query,report=rep,window='equal_30_annual_bins' if length else 'fixed_1980_origin',publication_year_lower=lo,publication_year_upper=hi,
                           cohort=cohort,working_group=group,pool_works=n,pool_mean_age=mean,pool_recent_fraction=recent,IPCC_occurrences=len(selected),
                           IPCC_mean_age=m,IPCC_recent_fraction=f,IPCC_minus_pool_mean_age=m-mean if m is not None else None,
                           IPCC_minus_pool_recent_fraction=f-recent if f is not None else None,
                           interpretation='Annual cutoff-year origin; 30 inclusive annual bins (ages 0 through 29); IPCC occurrence-weighted, pool work-weighted.'))
    return rows

def enhanced_sources(rs,mapping,r2):
    out=[];entry=[]
    for rep in ['AR5','AR6']:
        for status in ['matched','unmatched']:
            grouped=defaultdict(list)
            for r in rs:
                if r['report_id']!=rep or (r['match_status'] in MATCHED)!=(status=='matched') or r['paper_id'] is None:continue
                grouped[r['paper_id']].append(r)
            cc=Counter();resolved=Counter()
            for key,rr in grouped.items():
                ds={r['preferred_key'] if status=='matched' else r['raw_key'] for r in rr};ds.discard(None)
                ws={mapping[d]['id']:mapping[d] for d in ds if d in mapping}
                if len(ws)!=1:
                    resolved['ambiguous' if ws else 'unresolved']+=1;continue
                w=next(iter(ws.values()));source=r2.sources_of(w)
                is_journal=(w.get('type') in ['article','review'] and source.get('type')=='journal' and source.get('id'))
                resolved['resolved']+=1;resolved['journal_article_review' if is_journal else 'other_type_or_missing_source']+=1
                if is_journal:cc[source['id']]+=1
                entry.append(dict(report=rep,occurrence_status_group=status,paper_id=key,openalex_id=w['id'],work_type=w.get('type'),source_id=source.get('id'),
                           source_name=source.get('display_name'),source_type=source.get('type'),issn_l=source.get('issn_l'),journal_article_review=bool(is_journal),
                           identity_input='preferred DOI for matched occurrences; unique raw DOI for unmatched occurrences; conflicting work resolutions excluded'))
            n=sum(cc.values())
            out.append(dict(report=rep,occurrence_status_group=status,assigned_IDs_in_group=len(grouped),resolved_IDs=resolved['resolved'],
                       unresolved_IDs=resolved['unresolved'],ambiguous_IDs=resolved['ambiguous'],journal_article_review_IDs=n,
                       nonjournal_or_missing_source_IDs=resolved['other_type_or_missing_source'],journal_D0=len(cc),journal_D2=n*n/sum(x*x for x in cc.values()) if n else None,
                       interpretation='Source recovery from frozen DOI metadata; database untouched; a shared assigned ID can occur in both status groups.'))
    return out,entry

def missing_source_curve(rs,mapping,r2):
    """Deterministic fractional-allocation scenarios, not imputed observations.

    Unknown type is deliberately retained in the envelope. A second scope
    fixes known OpenAlex article/review IDs with journal sources only.
    """
    bins=defaultdict(Counter);unknown=Counter();status=Counter()
    grouped=defaultdict(list)
    for r in rs:
        if r['paper_id'] is not None:grouped[(r['report_id'],r['paper_id'])].append(r)
    for (rep,pid),rr in grouped.items():
        all_dois={r['preferred_key'] if r['match_status'] in MATCHED else r['raw_key'] for r in rr};all_dois.discard(None)
        ws={mapping[d]['id']:mapping[d] for d in all_dois if d in mapping}
        w=next(iter(ws.values())) if len(ws)==1 else None;s=r2.sources_of(w) if w else {}
        rawyears=[]
        for r in rr:
            # Same frozen leading-year parser used in Round 2, called directly.
            import re
            years=re.findall(r'\b(1[6-9]\d{2}|20\d{2})[a-z]?\b',r['raw_reference'][:300])
            if years:rawyears.append(int(years[0]))
        year=min(rawyears) if rawyears else None
        yearbin=str((year//5)*5) if year else 'unknown'
        if w and w.get('type') in ['article','review'] and s.get('type')=='journal' and s.get('id'):
            bins[(rep,yearbin)][s['id']]+=1;status[(rep,'known_journal')]+=1
        elif w and (w.get('type') not in ['article','review'] or s.get('type') not in ['journal',None]):
            status[(rep,'known_other')]+=1
        else:unknown[(rep,yearbin)]+=1;status[(rep,'unknown_type_or_source')]+=1
    rows=[]
    for t in range(21):
        alpha=t/20
        for direction in ['toward_largest_source','toward_distinct_sources']:
            vals={}
            for rep in ['AR5','AR6']:
                counts=Counter();unique_square=0.;n=0
                yearbins=sorted({y for r,y in bins if r==rep}|{y for r,y in unknown if r==rep})
                global_known=Counter()
                for (r,y),cc in bins.items():
                    if r==rep:global_known.update(cc)
                if not global_known:raise ValueError('No known journal source support')
                largest=sorted(global_known,key=lambda k:(-global_known[k],k))[0]
                empty_years=0
                for y in yearbins:
                    cc=bins[(rep,y)];u=unknown[(rep,y)];n+=sum(cc.values())+u;counts.update(cc)
                    support=cc or global_known
                    if u and not cc:empty_years+=1
                    total=sum(support.values())
                    for k,v in support.items():counts[k]+=(1-alpha)*u*v/total
                    if direction=='toward_largest_source':counts[largest]+=alpha*u
                    else:unique_square+=u*alpha*alpha
                d2=n*n/(sum(v*v for v in counts.values())+unique_square)
                vals[rep]=d2
                rows.append(dict(alpha=alpha,direction=direction,report=rep,target='known journal article/review IDs plus unresolved type/source IDs; known other types excluded',
                       known_journal_IDs=status[(rep,'known_journal')],excluded_known_other_IDs=status[(rep,'known_other')],unknown_type_or_source_IDs=status[(rep,'unknown_type_or_source')],
                       denominator=n,empty_year_bins_using_global_support=empty_years,D2=d2,AR6_minus_AR5_D2=None,
                       interpretation='Fractional allocation scenario, not a population estimate. At alpha zero unknowns follow within-five-year known-source frequencies; unsupported bins use report-wide frequencies.'))
            for row in rows[-2:]:row['AR6_minus_AR5_D2']=vals['AR6']-vals['AR5']
    return rows

def main():
    global REPO
    p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[3]);p.add_argument('--output',type=Path);a=p.parse_args();REPO=a.repo.resolve()
    out=a.output or Path(__file__).parent/'offline_v1';out.mkdir(parents=True,exist_ok=True)
    r2=import_r2(REPO);rs,input_audit=r2.load_inputs(REPO);works,mapping,bundles=load_works(REPO)
    tables=[]
    tables.append(write(out/'cutoff_reuse_sensitivity.csv',cutoff_reuse(rs,mapping,r2)))
    tables.append(write(out/'equal_window_age_comparison.csv',equal_window_ages(REPO)))
    tables.append(write(out/'top18_pool_shares.csv',csvread(REPO/'revision_preparation/review_round2/openalex_v1/activated_attempt_03/results_v1/outlet_top15_comparison.csv')))
    tables.append(write(out/'top18_pool_shares_broad_narrow.csv',csvread(REPO/'revision_preparation/review_round2/openalex_v1/activated_attempt_03/results_v1/source_shares.csv')))
    summary,entries=enhanced_sources(rs,mapping,r2)
    tables.append(write(out/'unmatched_DOI_source_recovery.csv',summary));tables.append(write(out/'resolved_journal_source_assignments.csv',entries))
    tables.append(write(out/'missing_source_allocation_curve.csv',missing_source_curve(rs,mapping,r2)))
    old=REPO/'revision_preparation/review_round2/openalex_v1/activated_attempt_03'
    inputs=[dict(path=str(p.relative_to(REPO)),sha256=sha(p)) for p in [REPO/'database/ipcc_litdb.sqlite',REPO/'scripts/review_round2_openalex.py',old/'results_v1/doi_resolution.csv',old/'results_v1/pool_by_year.csv',old/'results_v1/outlet_top15_comparison.csv',old/'results_v1/source_shares.csv',REPO/'revision_preparation/review_round2/raw_age_v1/raw_age_occurrence_audit.csv.gz']]
    inputs.extend(dict(path=str(Path(x['path']).relative_to(REPO)),sha256=x['sha256']) for x in bundles.inputs)
    manifest=dict(analysis_package=VERSION,seed=SEED,network_used=False,credentials_read=False,snapshot=input_audit,script_sha256=sha(__file__),inputs=inputs,output_tables=tables,
        methods={'equal_window':'Thirty inclusive annual publication bins: 1984–2013 and 1992–2021. Ages 0–29 relative to annual cutoff. Original 1980 comparison retained.',
        'date_precision':'Missing dates and date-precision exclusions reported separately; January-first and every-month-first exclusions alter observed cohort composition.',
        'common_DOI_coverage':'DOI-matched occurrences in each report; the common-source variant restricts each report to source IDs present in both, rather than selecting shared studies.',
        'source_target':'Journal article/review records defined by OpenAlex work type article/review and source type journal. Unknown types remain unknown.',
        'allocation_curve':'Conditional fractional allocation; unknown type/source treated as potentially journal. Earliest parsed cited-version year within each assigned ID determines its five-year bin; unsupported bins use report-wide support. It does not classify unknown types or produce an identified journal-only estimate.'})
    js(out/'manifest.json',manifest)
    print(json.dumps(dict(output=str(out),tables=len(tables),works=len(works),resolved_DOIs=len(mapping))))

if __name__=='__main__':main()
