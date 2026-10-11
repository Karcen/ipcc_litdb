"""Reproduce record-identity and temporal-cohort checks.

Units: immutable reference occurrences (occurrence_id), distinct paper-ID sets
within report/status groups, and a separate paper-level stratified review frame.
Occurrence-to-paper/source joins are many-to-one; missing paper IDs are logged.
This is a descriptive census of the saved snapshot. Automated consistency flags
do not estimate independently human-validated error rates.
Raw bibliography strings and authors are published bibliographic information.
All outputs are new revision_identity* files; existing results are not changed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import statistics
import unicodedata

from analyse_corpus import compare, digest, journal_key, write_csv
from deepen_analysis import age_record, age_summary, journal_summary, year

ROOT = Path(__file__).resolve().parent
EXPECTED_DATABASE_SHA256 = "faa7fc711ae4607acfa9833739ec8f202f8aefd6d929b474a12fceb589c5b85d"
MATCHED = {"matched_doi", "matched_title"}
WGS = ("WG1", "WG2", "WG3")
DOI_RE = re.compile(r"(?<![\w./])(10\.\d{4,9}/[^\s\"'<>|]+)", re.I)
SQL = """
SELECT r.*, c.report_id, w.wg_label, c.chapter_label, c.chapter_title,
       s.publication_year AS source_year, s.file_url AS source_file_url,
       p.doi_norm AS paper_doi, p.title_best, p.year_best, p.journal_best,
       p.identity_method, p.metadata_source, p.verification_status,
       p.first_author_surname AS preferred_first_author, p.authors_best
FROM references_occurrence r
JOIN chapters c ON c.chapter_id=r.chapter_id
LEFT JOIN working_groups w ON w.wg_id=c.wg_id
LEFT JOIN sources s ON s.source_id=c.source_id
LEFT JOIN papers p ON p.paper_id=r.paper_id
WHERE c.report_id IN ('AR1','AR2','AR3','AR4','AR5','AR6')
ORDER BY c.report_id,r.occurrence_id
"""


def json_write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def normalized_doi(value):
    text = str(value or "").strip().casefold()
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/|^doi:\s*", "", text)
    text=text.rstrip(".,;]}>\"'")
    while text.endswith(")") and text.count(")")>text.count("("):
        text=text[:-1].rstrip(".,;]}>\"'")
    return text


def text_key(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text.casefold()).split())


def usable_raw_doi(value):
    return bool(re.fullmatch(r"10\.\d{4,9}/\S{4,}",value) and not re.search(r"[-_/(\[<]$",value)
                and value.count("(")==value.count(")"))


def punctuation_repaired_raw_dois(raw):
    # Rejoin spaces adjacent to DOI punctuation, never enrich from metadata.
    # Case-sensitive lower-case/digit lookahead preserves terminal '. Author'.
    fixed=str(raw or "")
    for dash in ["\u2010","\u2011","\u2012","\u2013","\u2212"]:fixed=fixed.replace(dash,"-")
    fixed=re.sub(r"\s+([/.-])",r"\1",fixed)
    fixed=re.sub(r"([/.-])\s+(?=[a-z0-9])",r"\1",fixed)
    return sorted({d for v in DOI_RE.findall(fixed) if usable_raw_doi(d:=normalized_doi(v))})


def raw_checks(row):
    raw = str(row["raw_reference"] or "")
    detected = sorted({normalized_doi(m) for m in DOI_RE.findall(raw)})
    dois = [d for d in detected if usable_raw_doi(d)]
    repaired=punctuation_repaired_raw_dois(raw)
    lead = re.search(r"\b(1[6-9]\d{2}|20\d{2})[a-z]?\b", raw[:300])
    lead_year = int(lead.group(1)) if lead else None
    preferred_year = year(row["year_best"])
    paper_doi = normalized_doi(row["paper_doi"])
    occurrence_doi = normalized_doi(row["doi_norm"])
    source = age_record(row)
    author_match=re.match(r"\s*([A-Za-z\u00c0-\u00ff'’-]+)",raw)
    raw_author=text_key(author_match.group(1)) if author_match else ""
    preferred_author=text_key(row["preferred_first_author"])
    title_tokens=set(text_key(row["title_best"]).split())
    raw_tokens=set(text_key(raw).split())
    return {
        "raw_dois": " | ".join(dois), "raw_doi_count": len(dois),
        "raw_doi_fragments_rejected":len(detected)-len(dois),
        "punctuation_repaired_raw_dois":" | ".join(repaired),"punctuation_repaired_raw_doi_count":len(repaired),
        "single_raw_doi_equals_paper_doi": bool(paper_doi and len(dois) == 1 and dois[0] == paper_doi),
        "paper_doi_present_in_raw": bool(paper_doi and paper_doi in dois),
        "raw_doi_disagrees_with_paper": bool(paper_doi and dois and paper_doi not in dois),
        "occurrence_doi_equals_paper_doi": bool(paper_doi and occurrence_doi == paper_doi),
        "raw_leading_year": lead_year,
        "raw_leading_year_gap": preferred_year - lead_year if lead_year and preferred_year else None,
        "raw_year_gap_over_one": bool(lead_year and preferred_year and abs(preferred_year-lead_year)>1),
        "raw_leading_author":raw_author,"preferred_first_author_normalized":preferred_author,
        "raw_first_author_disagrees":bool(raw_author and preferred_author and raw_author!=preferred_author),
        "preferred_title_token_recall_in_raw":len(title_tokens&raw_tokens)/len(title_tokens) if title_tokens else None,
        "preferred_year_after_source": source["inclusion"] == "future_publication_year",
        "raw_length": len(raw), "raw_entry_over_900_characters": len(raw)>900,
    }


def status_category(statuses):
    d, t = "matched_doi" in statuses, "matched_title" in statuses
    return "doi_and_title" if d and t else "doi_only" if d else "title_only" if t else "unmatched_only"


def year_summary(ids, papers):
    values = [year(papers[i]["year_best"]) for i in sorted(ids)]
    known = [v for v in values if v is not None]
    q = statistics.quantiles(known, n=4, method="exclusive") if len(known)>=4 else [None]*3
    return {"distinct_papers":len(ids), "known_year_papers":len(known),
            "missing_year_papers":len(ids)-len(known),
            "median_publication_year":statistics.median(known) if known else None,
            "publication_year_p25":q[0], "publication_year_p75":q[2]}


def run(database, output=ROOT, pipeline_root=None, seed=20261011, per_stratum=12):
    output = Path(output).expanduser().resolve()
    result_dir, qc_dir = output/"results", output/"qc"
    result_dir.mkdir(parents=True, exist_ok=True); qc_dir.mkdir(parents=True, exist_ok=True)
    database = Path(database).expanduser().resolve()
    start_hash = digest(database)
    if start_hash != EXPECTED_DATABASE_SHA256:
        raise ValueError("This revision and its targeted inspection notes require the documented 6 October 2026 snapshot")
    c = sqlite3.connect(database.as_uri()+"?mode=ro&immutable=1", uri=True)
    c.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in c.execute(SQL)]
        build_info = dict(c.execute("SELECT key,value FROM build_info"))
        wos = {r["record_id"]:dict(r) for r in c.execute("SELECT * FROM wos_records")}
        retrieval = dict(c.execute("SELECT MIN(retrieved_date) AS first,MAX(retrieved_date) AS last FROM sources").fetchone())
    finally:
        c.close()
    checks=[]
    def check(name, observed, expected):
        ok = observed == expected
        checks.append({"check":name,"observed":observed,"expected":expected,"passed":ok})
        if not ok:raise ValueError(f"{name}: {observed} != {expected}")
    check("unique occurrence ID",len({r["occurrence_id"] for r in rows}),len(rows))
    check("selected occurrence count",len(rows),125164)
    all_ids, matched, doi_ids, title_ids, anchored, doi_workflow = (defaultdict(set) for _ in range(6))
    by_report, by_pid, by_report_pid = (defaultdict(list) for _ in range(3))
    group_matched=defaultdict(set); status_by_report_pid=defaultdict(set); papers={}
    source_years=defaultdict(set); source_count=Counter(); audit_by_occ={}
    literal_doi_all=defaultdict(set); literal_doi_matched=defaultdict(set)
    repaired_doi_all=defaultdict(set);repaired_doi_matched=defaultdict(set);repaired_doi_years=defaultdict(set)
    literal_doi_years=defaultdict(set); literal_doi_ids=defaultdict(set)
    literal_text_all=defaultdict(set); literal_text_matched=defaultdict(set)
    raw_year_age_rows=[]
    drops=Counter()
    for row in rows:
        report,pid=row["report_id"],row["paper_id"]
        by_report[report].append(row)
        source_count[(report,row["wg_label"],row["source_year"])]+=1
        sy=age_record(row)["base_year"]
        source_years[(report,row["wg_label"])].add(sy)
        if pid is None:
            drops[report]+=1;continue
        meta={k:row[k] for k in ("year_best","journal_best","title_best","paper_doi","identity_method")}
        if pid in papers and papers[pid]!=meta:raise ValueError("Nonunique paper metadata")
        papers[pid]=meta;all_ids[report].add(pid)
        by_pid[pid].append(row);by_report_pid[(report,pid)].append(row)
        status_by_report_pid[(report,pid)].add(row["match_status"])
        audit=raw_checks(row);audit_by_occ[row["occurrence_id"]]=audit
        if row["verification_status"]=="doi_verified":doi_workflow[report].add(pid)
        if audit["raw_doi_count"]==1:
            d=audit["raw_dois"]
            if re.fullmatch(r"10\.\d{4,9}/\S{4,}",d) and d.count("(")==d.count(")"):
                literal_doi_all[report].add(d);literal_doi_ids[(report,d)].add(pid)
                if audit["raw_leading_year"]:literal_doi_years[(report,d)].add(audit["raw_leading_year"])
                if row["match_status"] in MATCHED:literal_doi_matched[report].add(d)
        if audit["punctuation_repaired_raw_doi_count"]==1:
            d=audit["punctuation_repaired_raw_dois"];repaired_doi_all[report].add(d)
            if audit["raw_leading_year"]:repaired_doi_years[(report,d)].add(audit["raw_leading_year"])
            if row["match_status"] in MATCHED:repaired_doi_matched[report].add(d)
        raw_key=text_key(row["raw_reference"])
        if len(raw_key)>=100 and audit["raw_leading_year"] is not None:
            literal_text_all[report].add(raw_key)
            if row["match_status"] in MATCHED:literal_text_matched[report].add(raw_key)
        if row["match_status"] in MATCHED:
            matched[report].add(pid);group_matched[(report,row["wg_label"])].add(pid)
            if audit["single_raw_doi_equals_paper_doi"]:anchored[report].add(pid)
            raw_age=sy-audit["raw_leading_year"] if audit["raw_leading_year"] else None
            raw_year_age_rows.append({"report":report,"working_group":row["wg_label"],"paper_id":pid,
                "occurrence_id":row["occurrence_id"],"raw_leading_year":audit["raw_leading_year"],
                "preferred_year":row["year_best"],"source_year":sy,"raw_year_age":raw_age,
                "raw_age_inclusion":"missing_raw_year" if raw_age is None else "future_raw_year" if raw_age<0 else "over_60" if raw_age>60 else "included"})
        if row["match_status"]=="matched_doi":doi_ids[report].add(pid)
        if row["match_status"]=="matched_title":title_ids[report].add(pid)
    check("selected distinct paper IDs",len(papers),93430)
    check("AR5 matched IDs",len(matched["AR5"]),13510)
    check("AR6 matched IDs",len(matched["AR6"]),34873)
    check("primary shared IDs",len(matched["AR5"]&matched["AR6"]),1664)
    coverage=[];consistency=[]
    for report in sorted(all_ids):
        for wg in ["All",*WGS]:
            selected=by_report[report] if wg=="All" else [r for r in by_report[report] if r["wg_label"]==wg]
            for status in ["All","matched_doi","matched_title","unmatched"]:
                rr=selected if status=="All" else [r for r in selected if r["match_status"]==status]
                ids={r["paper_id"] for r in rr if r["paper_id"] is not None}
                conf=[float(r["match_confidence"]) for r in rr if str(r["match_confidence"] or "").strip()]
                coverage.append({"report":report,"working_group":wg,"status":status,"occurrences":len(rr),
                    "distinct_ids":len(ids),"confidence_min":min(conf) if conf else None,
                    "confidence_max":max(conf) if conf else None,
                    "embedded_doi_occurrences":sum(r["doi_source"]=="embedded" for r in rr),
                    "crossref_doi_occurrences":sum(r["doi_source"]=="crossref" for r in rr),
                    "openalex_doi_occurrences":sum(r["doi_source"]=="openalex" for r in rr)})
            for status in ["matched_doi","matched_title","unmatched"]:
                rr=[r for r in selected if r["match_status"]==status and r["paper_id"] is not None]
                aa=[audit_by_occ[r["occurrence_id"]] for r in rr]
                consistency.append({"report":report,"working_group":wg,"status":status,"occurrences":len(rr),
                    **{k:sum(bool(a[k]) for a in aa) for k in ["single_raw_doi_equals_paper_doi","raw_doi_disagrees_with_paper",
                     "raw_year_gap_over_one","preferred_year_after_source","raw_entry_over_900_characters","raw_first_author_disagrees"]},
                    "raw_doi_available":sum(a["raw_doi_count"]>0 for a in aa),
                    "raw_leading_year_available":sum(a["raw_leading_year"] is not None for a in aa)})
    transitions=[]
    definitions=[("matched",matched), ("DOI_status",doi_ids),("title_status_any",title_ids),
                 ("title_status_only",{r:title_ids[r]-doi_ids[r] for r in all_ids}),
                 ("raw_single_DOI_anchored_matched",anchored),("paper_DOI_verified_workflow_label",doi_workflow),("all_extracted",all_ids)]
    for a,b in [("AR4","AR5"),("AR5","AR6")]:
        for label,sets in definitions:transitions.append({"earlier":a,"later":b,"definition":label,**compare(sets[a],sets[b])})
        transitions.append({"earlier":a,"later":b,"definition":"later_matched_vs_any_earlier_extracted",
                            **compare(all_ids[a],matched[b])})
    raw_baselines=[];raw_eligibility=[];raw_doi_crosswalk=[];raw_age_summaries=[]
    matched_raw_years=defaultdict(set)
    for r in raw_year_age_rows:
        if r["raw_leading_year"] is not None:matched_raw_years[(r["report"],r["paper_id"])].add(r["raw_leading_year"])
    raw_year_eligibility=[]
    for earlier,later,cutoffs in [("AR4","AR5",[2006,2007,2008]),("AR5","AR6",[2012,2013,2014])]:
        for label,sets in [("literal_single_raw_DOI_all_statuses",literal_doi_all),
                           ("literal_single_raw_DOI_matched_occurrences",literal_doi_matched),
                           ("punctuation_repaired_single_raw_DOI_all_statuses",repaired_doi_all),
                           ("punctuation_repaired_single_raw_DOI_matched_occurrences",repaired_doi_matched),
                           ("exact_normalized_raw_reference_all_statuses",literal_text_all),
                           ("exact_normalized_raw_reference_matched_occurrences",literal_text_matched)]:
            raw_baselines.append({"earlier":earlier,"later":later,"identity_unit":label,**compare(sets[earlier],sets[later])})
        for cutoff in cutoffs:
            for label,sets,years in [("literal_single_raw_DOI_all_statuses",literal_doi_all,literal_doi_years),
                               ("literal_single_raw_DOI_matched_occurrences",literal_doi_matched,literal_doi_years),
                               ("punctuation_repaired_single_raw_DOI_all_statuses",repaired_doi_all,repaired_doi_years),
                               ("punctuation_repaired_single_raw_DOI_matched_occurrences",repaired_doi_matched,repaired_doi_years)]:
                eligible={d for d in sets[later] if years[(later,d)] and max(years[(later,d)])<=cutoff}
                shared=eligible&sets[earlier]
                raw_eligibility.append({"earlier":earlier,"later":later,"identity_unit":label,"cutoff_year":cutoff,
                    "later_raw_DOIs":len(sets[later]),"eligible_later_raw_DOIs":len(eligible),"shared_eligible_raw_DOIs":len(shared),
                    "conditional_reuse":len(shared)/len(eligible) if eligible else None,
                    "eligibility_year":"Maximum observed raw leading year in later-report entries for each DOI"})
    for (report,d),ids in sorted(literal_doi_ids.items()):
        raw_doi_crosswalk.append({"report":report,"raw_doi":d,"paper_ids":" | ".join(map(str,sorted(ids))),
            "distinct_assigned_paper_ids":len(ids),"observed_raw_leading_years":" | ".join(map(str,sorted(literal_doi_years[(report,d)])))})
    for report in ["AR4","AR5","AR6"]:
        for wg in ["All",*WGS]:
            rr=[r for r in raw_year_age_rows if r["report"]==report and (wg=="All" or r["working_group"]==wg)]
            values=[r["raw_year_age"] for r in rr if r["raw_age_inclusion"]=="included"]
            q=statistics.quantiles(values,n=4,method="exclusive") if len(values)>=4 else [None]*3
            raw_age_summaries.append({"report":report,"working_group":wg,"matched_occurrences":len(rr),"included_raw_year_ages":len(values),
                "missing_raw_year":sum(r["raw_age_inclusion"]=="missing_raw_year" for r in rr),
                "future_raw_year":sum(r["raw_age_inclusion"]=="future_raw_year" for r in rr),
                "raw_age_over_60":sum(r["raw_age_inclusion"]=="over_60" for r in rr),
                "median_raw_year_age":statistics.median(values) if values else None,"p25":q[0],"p75":q[2]})
    for earlier,later,cutoffs in [("AR4","AR5",[2006,2007,2008]),("AR5","AR6",[2012,2013,2014])]:
        for cutoff in cutoffs:
            eligible={i for i in matched[later] if matched_raw_years[(later,i)] and max(matched_raw_years[(later,i)])<=cutoff}
            for label,prior in [("earlier_matched",matched[earlier]),("any_earlier_extracted",all_ids[earlier])]:
                n=len(eligible&prior)
                raw_year_eligibility.append({"earlier":earlier,"later":later,"cutoff_year":cutoff,
                    "identity_unit":"recorded_paper_ID","earlier_membership":label,"eligible_later_matched_IDs":len(eligible),
                    "shared_eligible_IDs":n,"conditional_reuse":n/len(eligible) if eligible else None,
                    "later_IDs_with_conflicting_raw_leading_years":sum(len(matched_raw_years[(later,i)])>1 for i in matched[later]),
                    "eligibility_year":"Maximum fresh raw leading year in later matched occurrences for each recorded ID; identity remains database-assigned"})
    categories=[]
    for a,b in [("AR4","AR5"),("AR5","AR6")]:
        counts=Counter((status_category(status_by_report_pid[(a,i)]),status_category(status_by_report_pid[(b,i)]))
                       for i in sorted(all_ids[a]&all_ids[b]))
        for (sa,sb),n in sorted(counts.items()):categories.append({"earlier":a,"later":b,"earlier_status_set":sa,
                                                                "later_status_set":sb,"shared_ids":n})
    a,b=matched["AR5"],matched["AR6"]
    reused,new=a&b,b-a;present=b&all_ids["AR5"];absent=b-all_ids["AR5"]
    switches=b&(all_ids["AR5"]-a)
    old_incoming={i for i in new if year(papers[i]["year_best"]) is not None and year(papers[i]["year_best"])<=2013}
    check("incoming IDs through 2013",len(old_incoming),4160)
    check("primary later partition",len(reused)+len(new),len(b))
    check("symmetric later partition",len(present)+len(absent),len(b))
    check("status-switch partition",len(switches),len(present)-len(reused))
    old_partition=[]
    for i in sorted(old_incoming):
        old_partition.append({"paper_id":i,"year_best":papers[i]["year_best"],"title_best":papers[i]["title_best"],
            "AR5_extracted":i in all_ids["AR5"],"AR5_status_set":status_category(status_by_report_pid[("AR5",i)]) if i in all_ids["AR5"] else "absent",
            "AR6_status_set":status_category(status_by_report_pid[("AR6",i)]),
            "AR5_occurrences":len(by_report_pid[("AR5",i)]),"AR6_occurrences":len(by_report_pid[("AR6",i)])})
    old_partition_counts=Counter((r["AR5_status_set"],r["AR6_status_set"]) for r in old_partition)
    partition_summary=[{"AR5_status_set":x,"AR6_status_set":y,"IDs":n} for (x,y),n in sorted(old_partition_counts.items())]
    eligibility=[]
    for earlier,later,cutoffs in [("AR4","AR5",[2006,2007,2008,2012,2013,2014]),("AR5","AR6",[2012,2013,2014])]:
        for cutoff in cutoffs:
            eligible={i for i in matched[later] if year(papers[i]["year_best"]) is not None and year(papers[i]["year_best"])<=cutoff}
            for label,prior in [("earlier_matched",matched[earlier]),("any_earlier_extracted",all_ids[earlier])]:
                shared=eligible&prior
                eligibility.append({"earlier":earlier,"later":later,"working_group":"All","cutoff_year":cutoff,
                    "cutoff_basis":"fixed_preferred_publication_year","earlier_membership":label,
                    "later_matched_total":len(matched[later]),"temporally_eligible_later_ids":len(eligible),
                    "shared_eligible_ids":len(shared),"eligible_absent_ids":len(eligible-prior),
                    "conditional_reuse":len(shared)/len(eligible) if eligible else None})
        for wg in WGS:
            sy=source_years[(earlier,wg)]
            check(f"single actual source year for {earlier} {wg}",len(sy),1)
            cutoff=next(iter(sy));later_ids=group_matched[(later,wg)]
            eligible={i for i in later_ids if year(papers[i]["year_best"]) is not None and year(papers[i]["year_best"])<=cutoff}
            earlier_any_wg={r["paper_id"] for r in by_report[earlier] if r["wg_label"]==wg and r["paper_id"] is not None}
            for label,prior in [("same_group_earlier_matched",group_matched[(earlier,wg)]),("same_group_any_earlier_extracted",earlier_any_wg)]:
                n=len(eligible&prior)
                eligibility.append({"earlier":earlier,"later":later,"working_group":wg,"cutoff_year":cutoff,
                    "cutoff_basis":"earlier_working_group_source_publication_year","earlier_membership":label,
                    "later_matched_total":len(later_ids),"temporally_eligible_later_ids":len(eligible),
                    "shared_eligible_ids":n,"eligible_absent_ids":len(eligible-prior),
                    "conditional_reuse":n/len(eligible) if eligible else None})
    old=lambda ids:{i for i in ids if year(papers[i]["year_best"]) is not None and year(papers[i]["year_best"])<=2013}
    same_vintage_sets={"reused_matched_through_2013":old(reused),"present_any_AR5_through_2013":old(present),
                       "actually_absent_AR5_through_2013":old(absent),"new_to_matched_AR5_through_2013":old(new)}
    profiles=[];group_profiles=[]
    for name,ids in same_vintage_sets.items():
        rr=[{**age_record(r),"paper_id":r["paper_id"]} for r in by_report["AR6"] if r["paper_id"] in ids and r["match_status"] in MATCHED]
        js,_=journal_summary(ids,papers,name)
        profiles.append({"cohort":name,**year_summary(ids,papers),**age_summary(rr),
                         **{k:v for k,v in js.items() if k not in ["cohort","distinct_papers"]}})
        for wg in WGS:
            ss=ids&group_matched[("AR6",wg)]
            rrr=[age_record(r) for r in by_report["AR6"] if r["paper_id"] in ss and r["wg_label"]==wg and r["match_status"] in MATCHED]
            gs,_=journal_summary(ss,papers,name)
            group_profiles.append({"cohort":name,"working_group":wg,"paper_ids":len(ss),
                "share_cohort_ids":len(ss)/len(ids) if ids else None,**age_summary(rrr),
                "hhi_known_journals":gs["hhi_known_journals"],"named_journals":gs["named_journals"]})
    later_shared={i for i in reused if year(papers[i]["year_best"]) is not None and year(papers[i]["year_best"])>2013}
    missing={i for i in new if year(papers[i]["year_best"]) is None}
    check("later-dated shared IDs",len(later_shared),14);check("missing-year incoming IDs",len(missing),5)
    check("same-vintage primary components",len(same_vintage_sets["reused_matched_through_2013"])+len(old_incoming),5810)
    anomalies=[]
    for i in sorted(later_shared|missing|switches):
        label="shared_later_than_2013" if i in later_shared else "incoming_missing_year" if i in missing else "AR5_unmatched_AR6_matched"
        for r in by_pid[i]:anomalies.append({"review_group":label,**r,**audit_by_occ[r["occurrence_id"]]})
    sample_frame=defaultdict(list)
    for i in sorted(a|b):
        membership="shared_matched" if i in reused else "AR5_only_matched" if i in a else "AR6_only_matched"
        report="AR6" if i in b else "AR5"
        ss=set().union(*(status_by_report_pid[(report2,i)] for report2 in ["AR5","AR6"]))
        status=status_category(ss)
        groups=sorted({r["wg_label"] for r in by_report_pid[(report,i)] if r["match_status"] in MATCHED and r["wg_label"] in WGS})
        wg_group=groups[0] if len(groups)==1 else "multiple_groups" if len(groups)>1 else "other_units"
        stratum=f"{membership}|{status}|{wg_group}";sample_frame[stratum].append(i)
    sample_ids=set();sample_info={};strata=[]
    for label,ids in sorted(sample_frame.items()):
        rank=lambda i:hashlib.sha256(f"{seed}|{label}|{i}".encode()).hexdigest()
        picked=sorted(ids,key=rank)[:per_stratum];sample_ids.update(picked)
        strata.append({"stratum":label,"population_ids":len(ids),"sampled_ids":len(picked),
                       "inclusion_probability":len(picked)/len(ids)})
        for i in picked:sample_info[i]={"stratum":label,"selection":"stratified_hash_sample"}
    for i in sorted(later_shared|missing|switches):
        if i not in sample_ids:
            sample_ids.add(i);sample_info[i]={"stratum":"targeted_review","selection":"targeted_supplement_not_probability_sample"}
    sample_rows=[];sample_papers=[]
    for i in sorted(sample_ids):
        rr=[r for r in by_pid[i] if r["report_id"] in ["AR5","AR6"]]
        audits=[audit_by_occ[r["occurrence_id"]] for r in rr]
        all_raw_dois={d for x in audits for d in x["raw_dois"].split(" | ") if d}
        distinct_raw={text_key(r["raw_reference"]) for r in rr}
        sample_papers.append({"paper_id":i,**sample_info[i],**papers[i],
            "AR5_status_set":status_category(status_by_report_pid[("AR5",i)]) if i in all_ids["AR5"] else "absent",
            "AR6_status_set":status_category(status_by_report_pid[("AR6",i)]) if i in all_ids["AR6"] else "absent",
            "occurrences_reviewed":len(rr),"normalized_raw_variants":len(distinct_raw),"distinct_raw_dois":len(all_raw_dois),
            **{k:sum(bool(x[k]) for x in audits) for k in ["single_raw_doi_equals_paper_doi","raw_doi_disagrees_with_paper",
                       "raw_year_gap_over_one","preferred_year_after_source","raw_entry_over_900_characters","raw_first_author_disagrees"]},
            "human_label":"unreviewed", "automated_screen":"rule_based_consistency_screen_only"})
        for r in rr:
            w=wos.get(r["wos_record_id"],{})
            sample_rows.append({"sample_stratum":sample_info[i]["stratum"],"selection":sample_info[i]["selection"],
                **r,**audit_by_occ[r["occurrence_id"]],"wos_title":w.get("Article Title"),
                "wos_doi":w.get("DOI"),"wos_year":w.get("Publication Year"),
                "human_identity_label":"unreviewed"})
    sampled_probability={i for i in sample_ids if sample_info[i]["selection"]=="stratified_hash_sample"}
    check("nonoverlapping strata frame",sum(len(v) for v in sample_frame.values()),len(a|b))
    check("unique sample IDs",len(sample_papers),len(sample_ids))
    check("probability sample selected size",len(sampled_probability),sum(s["sampled_ids"] for s in strata))
    check("sample raw-entry uniqueness",len({r["occurrence_id"] for r in sample_rows}),len(sample_rows))
    sample_stats=[]
    for label,ids in sorted(sample_frame.items()):
        chosen=[r for r in sample_papers if r["paper_id"] in sampled_probability and r["stratum"]==label]
        sample_stats.append({"stratum":label,"population_ids":len(ids),"sampled_ids":len(chosen),
            **{k+"_flagged_papers":sum(r[k]>0 for r in chosen) for k in ["raw_doi_disagrees_with_paper","raw_year_gap_over_one",
                           "preferred_year_after_source","raw_entry_over_900_characters","raw_first_author_disagrees"]},
            "human_validated_precision":None,"human_validated_false_merge_rate":None})
    # The date-based exclusion is a stress test, not a corrected population.
    sensitivities=[]
    for name,excluded in [("exclude_all_shared_later_than_2013",later_shared)]:
        aa,bb=a-excluded,b-excluded
        sensitivities.append({"scenario":name,"excluded_union_ids":len((a|b)&excluded),
                              **compare(aa,bb),"interpretation":"Stress test; no corrected-identity or error-rate claim"})
    srcrows=[{"report":r,"working_group":wg,"source_year_label":sy,"occurrences":n}
             for (r,wg,sy),n in sorted(source_count.items())]
    pipeline=inspect_pipeline(Path(pipeline_root).expanduser().resolve() if pipeline_root else ROOT.parents[1],
                              build_info,retrieval,rows)
    files={
      "status_coverage":coverage,"status_combinations":categories,"transitions":transitions,
      "independent_raw_baselines":raw_baselines,"independent_raw_eligible_reuse":raw_eligibility,
      "raw_DOI_paper_crosswalk":raw_doi_crosswalk,"raw_year_occurrences":raw_year_age_rows,"raw_year_age_summaries":raw_age_summaries,
      "raw_year_eligible_reuse":raw_year_eligibility,
      "earlier_incoming_partition":old_partition,"earlier_incoming_partition_summary":partition_summary,
      "eligible_reuse":eligibility,"same_vintage_profiles":profiles,"same_vintage_groups":group_profiles,
      "anomalous_entries":anomalies,"audit_sample_papers":sample_papers,"audit_sample_entries":sample_rows,
      "audit_strata":strata,"audit_sample_summary":sample_stats,"raw_consistency_census":consistency,
      "flagged_sensitivity":sensitivities,"source_years":srcrows}
    for name,data in files.items():write_csv(result_dir/f"revision_identity_{name}.csv",data)
    end_hash=digest(database);check("database unchanged",end_hash,start_hash)
    summary={
      "provenance":{"database_sha256":start_hash,"database_bytes":database.stat().st_size,
        "database_read_only":True,"database_unchanged":True,"script_sha256":digest(Path(__file__)),"query":SQL.strip()},
      "definitions":{"unit_of_analysis":"Unique reference occurrence; report-specific paper ID set; stratified sample paper ID",
        "link_cardinality":"references_occurrence many-to-one chapters, papers and sources; validated unique occurrence IDs and invariant paper metadata",
        "null_paper_id_exclusions_by_report":dict(drops),"source_population_occurrences":len(rows),
        "matching_status":"Recorded workflow result, not validated original-reference identity",
        "DOI_status":"At least one matched_doi occurrence in the selected report, including enriched DOIs",
        "paper_DOI_verified_workflow_label":"paper.verification_status == doi_verified; excludes wos_matched papers. Builder assigns this label to valid-DOI records without a WoS bridge. It differs from matched_doi occurrence status.",
        "symmetric_presence":"An AR6 matched ID is present earlier if any AR5 extracted occurrence has the same recorded ID, whatever its status",
        "conditional_reuse":"Shared preferred-year-eligible IDs / later matched IDs with valid preferred years at or before cutoff",
        "cutoff_scope":"Preferred publication-year eligibility, not reconstructed literature-search cutoff; in-press records and bad enriched dates require separate examination",
        "same_vintage":"Valid preferred year through 2013; no guarantee of correct preferred metadata",
        "raw_doi_screen":"Fresh unfragmented DOI regex in preserved raw_reference; missing or split DOI is not evidence of an error",
        "independent_raw_DOI_baseline":"Exactly one usable DOI freshly extracted from the raw bibliography string; literal DOI is the identity key, with no enriched paper DOI or assigned paper ID used. All-status and matched-occurrence populations are separate.",
        "punctuation_repaired_raw_DOI_baseline":"Fresh raw text only. Unicode dash variants and whitespace immediately around / . - are normalized, with case-sensitive lowercase/digit continuation after punctuation. Strings with multiple usable DOI tokens are excluded. This recovers some typography differences, still misses other fragmentation and needs adjudication.",
        "independent_raw_text_baseline":"Exact text_key-normalized complete reference string, at least 100 normalized characters and an available raw leading year; identity key has no enriched metadata. Formatting/versions can separate a real common document.",
        "raw_year_screen":"First plausible four-digit year in first 300 characters; mismatch >1 year is a review candidate, not a validated error",
        "raw_length_screen":"More than 900 characters is an extraction-review cue; it does not prove concatenation",
        "raw_author_screen":"First alphabetic name/organization token in raw entry compared with stored first-author surname after accent/punctuation normalization. Truncation, corporate names and incomplete authors can disagree without establishing an error.",
        "age_and_outlets":"Original exclusive quartiles, occurrence weights, source years, ages 0–60 and casefold/whitespace-only named outlet labels",
        "inference":"Descriptive census and rule sensitivity. No sampling p-values, linkage precision or population error rate are inferred."},
      "main_counts":{"matched_AR5":len(a),"matched_AR6":len(b),"shared_matched":len(reused),
        "AR6_matched_present_in_any_AR5":len(present),"AR6_matched_absent_from_any_AR5":len(absent),
        "matching_status_switch_ids":sorted(switches),"incoming_through_2013":len(old_incoming),
        "incoming_through_2013_present_any_AR5":len(old_incoming&all_ids["AR5"]),
        "incoming_through_2013_absent_any_AR5":len(old_incoming-all_ids["AR5"]),
        "shared_later_than_2013_ids":sorted(later_shared),"incoming_missing_year_ids":sorted(missing)},
      "transitions":transitions,"shared_status_combinations":categories,"earlier_incoming_partition":partition_summary,
      "independent_raw_baselines":raw_baselines,"independent_raw_conditional_reuse":raw_eligibility,
      "raw_leading_year_age_summaries":raw_age_summaries,
      "raw_leading_year_matched_ID_conditional_reuse":raw_year_eligibility,
      "raw_DOI_crosswalk_conflicts":{"report_DOI_keys":len(raw_doi_crosswalk),"keys_assigned_to_multiple_paper_IDs":sum(r["distinct_assigned_paper_ids"]>1 for r in raw_doi_crosswalk)},
      "conditional_reuse":eligibility,"same_vintage_profiles":profiles,"same_vintage_groups":group_profiles,
      "flagged_case_sensitivities":sensitivities,
      "sample":{"seed":seed,"ranking":"SHA256(seed|stratum|paper_id), smallest hashes within each mutually exclusive stratum",
        "target_per_stratum":per_stratum,"strata":strata,"frame_size":len(a|b),
        "probability_sample_ids":len(sampled_probability),"targeted_supplement_ids":len(sample_ids-sampled_probability),
        "all_sample_ids":len(sample_ids),"raw_entries_exported":len(sample_rows),
        "human_labels_completed":0,"independent_human_review":False,
        "use":"Review worksheet and consistency screen. Sampling weights are recorded, but no weighted precision is estimated without validated labels."},
      "pipeline":pipeline,"quality_checks":{"all_passed":True,"count":len(checks),"checks":checks},
      "human_validation_still_needed":["Two independent reviewers check sampled original PDF references and authoritative candidate records.",
        "Separate same-document, different-version, false-merge, false-split, extraction-concatenation and unresolved labels.",
        "Reconcile disagreements before estimating stratum-weighted linkage precision and false-merge or false-split rates.",
        "Use adjudicated crosswalks to recompute recurrence; current flags do not supply corrected IDs."]}
    json_write(result_dir/"revision_identity_summary.json",summary)
    json_write(qc_dir/"revision_identity_pipeline_audit.json",pipeline)
    json_write(qc_dir/"revision_identity_checks.json",summary["quality_checks"])
    produced=[result_dir/f"revision_identity_{name}.csv" for name in files]
    produced += [result_dir/"revision_identity_summary.json",qc_dir/"revision_identity_pipeline_audit.json",qc_dir/"revision_identity_checks.json"]
    produced=sorted(produced)
    json_write(qc_dir/"revision_identity_output_manifest.json",{"script_sha256":digest(Path(__file__)),
       "files":[{"path":str(p.relative_to(output)),"bytes":p.stat().st_size,"sha256":digest(p)} for p in produced]})
    print(json.dumps({"quality_checks":len(checks),"all_passed":True,"files":len(produced)+1,
       "main_counts":summary["main_counts"],"sample":summary["sample"]["all_sample_ids"]}))
    return summary


def inspect_pipeline(root, build_info, retrieval, rows):
    locations={"configuration":"run_all.py","extraction":"ipccdb/refextract.py","extraction_driver":"ipccdb/pipeline.py",
               "citation_similarity":"ipccdb/cite.py","enrichment":"ipccdb/enrich.py","WoS_acceptance":"ipccdb/wos.py",
               "database_entity_builder":"ipcc_litdb/database/build_litdb.py","enrichment_cache_runner":"tools/run_enrich.py"}
    file_records=[]
    for role,name in locations.items():
        path=root/name
        file_records.append({"role":role,"workspace_relative_path":name,"exists":path.exists(),"sha256":digest(path) if path.exists() else None})
    return {"scope":"Inspection of available upstream code and immutable snapshot fields; code has no archived executed-version identifier in build_info",
       "files":file_records,"snapshot_build_info":build_info,"source_retrieval_dates":retrieval,
       "occurrence_as_of_counts":dict(sorted(Counter(r["as_of"] for r in rows).items())),
       "stored_confidence_range":[{"status":s,"count":len(v),"min":min(v) if v else None,"max":max(v) if v else None}
          for s in ["matched_doi","matched_title"] for v in [[float(r["match_confidence"]) for r in rows if r["match_status"]==s and r["match_confidence"]]]],
       "extraction":{"splitters":["PDF text blocks","hanging indent","year/author regex"],
          "selection":"QC score penalizes missing-year and duplicate rates and subtracts 12 per flag. Hanging-indent strategy overrides when confidence >=0.5 and median gap >=3.0.",
          "QC_thresholds":{"missing_year_rate_max":0.25,"duplicate_rate_max":0.15,"references_per_page_min":1.5,
            "references_per_page_max":45,"median_raw_length_range":[90,900],"non_reference_start_rate_max":0.35},
          "deduplication":"Within a splitter result, repeated normalized complete raw strings are removed. A retry can choose an alternative with more items.",
          "OCR":"Current configuration enables OCR copies and QC retries; it does not prove the executed method for every source."},
       "enrichment":{"sources":["Crossref","OpenAlex"],"title_to_DOI_acceptance_similarity":0.90,
          "candidate_year_gap_penalty":"Subtract 0.05 when candidate publication year differs by more than one year.",
          "metadata_overwrite":"enrich.apply replaces Article Title, Authors, Publisher and Year with enriched metadata before WoS acceptance; recovered DOI is inserted when no DOI is present.",
          "cache":"tools/run_enrich.py warms DOI and title caches; run_all can reuse title cache without live title enrichment."},
       "WoS_acceptance":{"exact_DOI":"Normalized candidate DOI equals occurrence DOI; accepted with confidence 1.0.",
          "crossref_backfilled_DOI":"Additional title similarity >=0.85; the compared title may already have been replaced by enrichment.",
          "title_match":"Best normalized similarity >=0.92, |publication-year difference| <=1 where both values are usable, first-author surname equality where both are available.",
          "similarity":"Maximum token-set Jaccard and RapidFuzz token_set_ratio where RapidFuzz is installed; Jaccard fallback otherwise.",
          "source_asymmetry":"The special DOI-title check is keyed to doi_source == crossref; OpenAlex-backfilled DOI does not take that branch.",
          "interpretation":"Confidence is a rule output. An enriched DOI hit cannot independently validate the DOI's correspondence to the original raw reference."},
       "paper_ID_assignment":{"first_pass":"Group valid normalized DOI-bearing occurrences by DOI.",
          "second_pass":"Attach DOI-less occurrences by exact normalized title when selected group's year is within one year if both years exist; otherwise create title/year or occurrence-only identity.",
          "title_normalization":"NFKD decomposition, combining-mark removal, lowercase ASCII letters/digits/spaces after markup removal, whitespace collapse.",
          "preferred_metadata":"Most common occurrence title, year, journal and author strings; selection inherits enriched fields.",
          "validation_status":"A paper with a WoS record is wos_matched; a syntactically valid DOI without WoS is labelled doi_verified by builder. That latter label is not an independent resolver check."},
       "historical_limit":"Snapshot dates and saved statuses document the collection/build state. Current source thresholds are inspectable; exact executed software versions and original candidate sets need archived logs/cache provenance for complete reconstruction."}


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database",type=Path,required=True)
    parser.add_argument("--output",type=Path,default=ROOT)
    parser.add_argument("--pipeline-root",type=Path)
    parser.add_argument("--seed",type=int,default=20261011)
    parser.add_argument("--per-stratum",type=int,default=12)
    args=parser.parse_args()
    if args.per_stratum<1:parser.error("--per-stratum must be positive")
    run(args.database,args.output,args.pipeline_root,args.seed,args.per_stratum)
