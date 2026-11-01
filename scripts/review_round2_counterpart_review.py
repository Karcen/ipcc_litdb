#!/usr/bin/env python3
"""Extract, check and export targeted AI counterpart review; never edit records."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import runpy
import sqlite3
import subprocess
import sys
import unicodedata

VERSION = "targeted-counterpart-AI-review-1.0.0"
SEED = 20261011
DEFAULT = "revision_preparation/review_round2/counterpart_review_v1"


def need(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def jbytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)+"\n").encode()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path):
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def cbytes(rows, columns=None):
    s = io.StringIO(newline="")
    w = csv.DictWriter(s, fieldnames=columns or list(rows[0]), lineterminator="\n")
    w.writeheader(); w.writerows(rows)
    return s.getvalue().encode()


def normalized_text(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def validate_case(source, judgment, local_evidence, version_ids):
    """Check evidence ownership and complete assignment; never infer a label."""
    need(judgment["classification"] in {"correct_merge", "compound", "unresolved"},
         "Invalid endpoint label")
    need(judgment["review_origin"] == "AI-assisted Codex initial review" and
         judgment["human_confirmed"] is False and judgment["review_complete"] == "no",
         "Unsupported reviewer/completion claim")
    groups = {g["group_id"]: g for g in judgment["study_groups"]}
    need(len(groups) == len(judgment["study_groups"]) and bool(groups),
         "Missing/duplicate study groups")
    oo = {o["occurrence_id"]: o for o in judgment["occurrence_judgments"]}
    original = {o["occurrence_id"]: o for o in source["occurrences"]}
    need(len(oo) == len(judgment["occurrence_judgments"]) == len(original) and
         set(oo) == set(original), "Occurrence assignment coverage differs: "+source["paper_id"])
    used = set()
    for row in oo.values():
        need(bool(row["work_groups"]) and set(row["work_groups"]) <= set(groups),
             "Undefined occurrence group")
        need(len(row["work_groups"]) == len(set(row["work_groups"])),
             "Repeated group within occurrence")
        used |= set(row["work_groups"])
    need(used == set(groups), "Unused study group")
    if judgment["classification"] == "correct_merge":
        need(len(groups) == 1 and not next(iter(groups.values()))["unresolved_fragment"],
             "Single-study proposal contains unresolved/multiple groups")
    if judgment["classification"] == "compound":
        need(sum(not g["unresolved_fragment"] for g in groups.values()) >= 2,
             "Compound proposal lacks two identified studies")
    need(set(judgment["referring_group_ids"]) <= set(groups) and
         bool(judgment["referring_group_ids"]), "Missing referenced study")
    local_ids = judgment.get("local_evidence_ids", [])
    need(set(local_ids) <= set(local_evidence), "Missing local source evidence")
    for evidence_id in local_ids:
        need(local_evidence[evidence_id]["paper_id"] == source["paper_id"],
             "Local evidence belongs to a different candidate")
    need(set(judgment.get("existing_version_evidence_ids", [])) <= version_ids,
         "Unknown existing version-evidence ID")
    need(judgment["edge_relation"] in {"same_study", "unresolved", "different_study"},
         "Invalid counterpart relation")
    for group_id, group in groups.items():
        texts = [original[oid]["raw_reference"] for oid, row in oo.items()
                 if group_id in row["work_groups"]]
        texts.extend(local_evidence[k]["bibliographic_excerpt"] for k in local_ids)
        anchor = normalized_text(group["title_anchor"])
        need(bool(anchor) and any(anchor in normalized_text(t) for t in texts),
             "Study anchor absent from assigned original entries/local evidence: "+
             source["paper_id"]+"/"+group_id)
    return groups, oo


def eligible_edge(canonical_label, judgment):
    return (canonical_label == "correct_merge" and
            judgment["classification"] == "correct_merge" and
            judgment["edge_relation"] == "same_study")


def validation_tests():
    source = {"paper_id": "1", "occurrences": [
        {"occurrence_id": "r1", "raw_reference": "Smith 2000: Alpha study."}]}
    judgment = {"classification": "correct_merge", "review_origin": "AI-assisted Codex initial review",
        "human_confirmed": False, "review_complete": "no",
        "study_groups": [{"group_id": "A", "title_anchor": "Alpha study", "unresolved_fragment": False}],
        "occurrence_judgments": [{"occurrence_id": "r1", "work_groups": ["A"]}],
        "referring_group_ids": ["A"], "local_evidence_ids": [],
        "existing_version_evidence_ids": [], "edge_relation": "same_study"}
    validate_case(source, judgment, {}, set())
    results = {"valid_single_study_accepted": True}
    bad = []
    j = deepcopy(judgment); j["human_confirmed"] = True
    bad.append(("human_confirmation_claim_rejected", source, j, {}))
    j = deepcopy(judgment); j["occurrence_judgments"].append(deepcopy(j["occurrence_judgments"][0]))
    bad.append(("duplicate_occurrence_rejected", source, j, {}))
    j = deepcopy(judgment); j["occurrence_judgments"] = []
    bad.append(("missing_occurrence_rejected", source, j, {}))
    j = deepcopy(judgment); j["study_groups"][0]["title_anchor"] = "Unrelated title"
    bad.append(("unsupported_title_anchor_rejected", source, j, {}))
    j = deepcopy(judgment); j["existing_version_evidence_ids"] = ["invented"]
    bad.append(("unknown_version_evidence_rejected", source, j, {}))
    j = deepcopy(judgment); j["local_evidence_ids"] = ["wrong"]
    bad.append(("wrong_candidate_evidence_rejected", source, j,
                {"wrong": {"paper_id": "2", "bibliographic_excerpt": "Alpha study"}}))
    s = deepcopy(source); s["occurrences"][0]["raw_reference"] += " Jones 2001: Beta study."
    j = deepcopy(judgment)
    j["study_groups"].append({"group_id": "B", "title_anchor": "Beta study", "unresolved_fragment": False})
    j["occurrence_judgments"][0]["work_groups"].append("B")
    bad.append(("multistudy_correct_merge_rejected", s, deepcopy(j), {}))
    j["classification"] = "compound"; validate_case(s, j, {}, set())
    need(not eligible_edge("correct_merge", j), "Compound candidate passed whole-ID union gate")
    results["compound_evidence_accepted_but_union_blocked"] = True
    need(not eligible_edge("false_merge", judgment), "Compound canonical source passed union gate")
    results["compound_canonical_source_union_blocked"] = True
    j = deepcopy(judgment); j["edge_relation"] = "unresolved"
    need(not eligible_edge("correct_merge", j), "Unresolved counterpart passed union gate")
    results["unresolved_edge_union_blocked"] = True
    need(eligible_edge("correct_merge", judgment), "Clear conditional edge blocked")
    results["two_single_study_nodes_same_study_edge_eligible"] = True
    for name, s, j, ev in bad:
        try:
            validate_case(s, j, ev, set())
        except ValueError:
            results[name] = True
        else:
            raise ValueError("Invalid synthetic case passed: "+name)
    return {"status": "passed", "tests": results, "human_validation_tested_or_implied": False}


def guarded_sources(repo):
    r = subprocess.run([sys.executable, str(repo/"scripts/review_round2_ai_sensitivity.py"),
                        "--repo", str(repo), "--check"], capture_output=True, text=True)
    need(r.returncode == 0, "Prior guarded AI registry failed: "+r.stdout+r.stderr)
    ann = repo/"revision_preparation/annotation"
    canonical = read_json(ann/"ai_review/proposals.json")
    need(canonical["human_validated"] is False, "Canonical AI package claims human validation")
    edgepath = repo/"revision_preparation/review_round2/ai_sensitivity_v1/counterpart_edge_geometry.csv"
    edges = read_csv(edgepath)
    candidates = sorted({r["candidate_paper_id"] for r in edges}, key=int)
    need(len(candidates) == 49 and len(edges) == 49, "Frozen targeted edge set changed")
    cmap = {c["paper_id"]: c for c in canonical["cases"]}
    db = repo/"database/ipcc_litdb.sqlite"
    expected = read_json(ann/"input_manifest.json")["database_sha256"]
    need(sha(db) == expected, "Snapshot hash differs")
    cases = []
    with sqlite3.connect(db.resolve().as_uri()+"?mode=ro&immutable=1", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        for pid in candidates:
            p = dict(conn.execute("SELECT paper_id,title_best,year_best,journal_best,doi_norm,authors_best,identity_method,verification_status FROM papers WHERE paper_id=?", (pid,)).fetchone())
            p["paper_id"] = str(p["paper_id"])
            occurrences = []
            for rr in conn.execute("SELECT r.occurrence_id,r.paper_id,r.raw_reference,r.chapter_id,c.report_id,w.wg_label,r.source_id,r.source_page,r.match_status,s.file_url AS source_url,s.sha256 AS source_pdf_sha256 FROM references_occurrence r JOIN chapters c ON c.chapter_id=r.chapter_id LEFT JOIN working_groups w ON w.wg_id=c.wg_id LEFT JOIN sources s ON s.source_id=r.source_id WHERE r.paper_id=? ORDER BY r.occurrence_id", (pid,)):
                row = dict(rr); row["paper_id"] = str(row["paper_id"])
                row["raw_reference_sha256_utf8"] = hashlib.sha256(row["raw_reference"].encode()).hexdigest()
                need(isinstance(row["source_page"], int) and row["source_page"] >= 0,
                     "Missing or ambiguous original page coordinate")
                row["source_page_zero_based"] = row["source_page"]
                row["viewer_page_one_based"] = row["source_page"]+1
                occurrences.append(row)
            links = []
            for e in edges:
                if e["candidate_paper_id"] != pid:
                    continue
                source = cmap[e["source_paper_id"]]
                comparisons = [q for q in source["candidate_judgments"] if str(q["candidate_paper_id"]) == pid]
                links.append({"canonical_source_ID": e["source_paper_id"],
                              "canonical_identity_AI_proposal": source["identity_label"],
                              "canonical_review_notes": source["review_notes"],
                              "existing_candidate_comparisons": comparisons})
            cases.append({"paper_id": pid, "preferred_metadata": p,
                          "occurrences": occurrences, "existing_canonical_links": links})
    need(sha(db) == expected, "Database changed during extraction")
    return {"schema_version": 1, "selection": "targeted counterpart endpoints, not a probability sample",
            "seed_inherited": SEED, "database_sha256": expected, "candidates": cases}, cmap, edges


def source_index(pack):
    return [{k: o[k] for k in ("paper_id", "occurrence_id", "report_id", "wg_label", "chapter_id",
        "match_status", "source_id", "source_page_zero_based", "viewer_page_one_based",
        "source_url", "source_pdf_sha256", "raw_reference_sha256_utf8")}
        for c in pack["candidates"] for o in c["occurrences"]]


def extract(repo, output):
    need(not output.exists(), "Choose a fresh counterpart-review directory")
    pack, _, _ = guarded_sources(repo)
    data = {"source_entries.json": jbytes(pack), "source_index.csv": cbytes(source_index(pack))}
    input_manifest = {"schema_version": 1, "selection": pack["selection"], "seed": SEED,
        "database_sha256": pack["database_sha256"], "candidate_IDs": len(pack["candidates"]),
        "original_occurrences": len(source_index(pack)), "human_validated": False,
        "source_files": {p: sha(repo/p) for p in (
            "revision_preparation/annotation/ai_review/proposals.json",
            "revision_preparation/annotation/ai_review/manifest.json",
            "revision_preparation/annotation/ai_review/version_decisions.json",
            "revision_preparation/review_round2/ai_sensitivity_v1/manifest.json",
            "revision_preparation/review_round2/ai_sensitivity_v1/counterpart_edge_geometry.csv")},
        "files": {name: {"sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)} for name,b in data.items()}}
    data["input_manifest.json"] = jbytes(input_manifest)
    output.mkdir(parents=True)
    for name, b in data.items():
        (output/name).write_bytes(b)
    print(json.dumps({"status": "passed", "candidate_IDs": len(pack["candidates"]),
                      "original_occurrences": len(source_index(pack)), "human_validated": False}))


METHODS = """Targeted AI counterpart endpoint review

All unique endpoints of the 49 positive counterpart edges are reviewed across
all of their saved occurrences and reports. This is a targeted extension, not
a probability sample, and it is not added to the 319-ID probability audit.
Every raw string and original page coordinate is copied unchanged from the
read-only snapshot. The source index supplies literal UTF-8 string hashes and
recorded PDF hashes. A recorded PDF hash is provenance, not evidence that every
page was newly inspected. The separate local-page evidence identifies the five
truncated entries whose contexts were actually inspected in hash-verified local
IPCC PDFs. Its bibliography excerpts are whitespace-normalized pdftotext output.
No new network or bibliographic API requests are made.

Codex groups the printed works in each endpoint, with labels correct_merge,
compound or unresolved. Group descriptions and per-occurrence assignments are
AI judgments. Compound includes both concatenated bibliography blocks and IDs
that join separate unrelated entries. Incomplete surrounding fragments remain
explicitly unresolved even when the presence of multiple works is clear.
Metadata uncertainty, printed-date conflicts and existing verified version
relationships remain separate from within-ID study grouping. Existing version
decisions are attributed to their source ledger; no new dates are invented.
Human confirmation is absent. Review completion remains no. No accuracy,
recall, sampling interval or population inference is estimated for endpoints.

The existing canonical-only zero-union scenario is preserved. An additional
proposal-acceptance scenario may union an edge only if the canonical source ID
and targeted endpoint both have single-study AI classifications and the edge
comparison identifies that study. Compound/unresolved nodes are blocked. No
occurrence status, citation year, outlet or preferred field is substituted.
Connected components of eligible edges contribute at most one matched ID per
report and one shared ID if matched appearances exist in both reports. These
are explicit unvalidated AI scenarios, not true recurrence bounds, corrected
estimates or an enumeration of all possible partitions. Seed 20261011 is inherited;
no random draws are made. Prior registries, canonical proposals and human-label
files remain unchanged. Reproduction needs the saved snapshot; replaying the
archived AI decisions does not create independent validation.
"""


def export(repo, output, check_only):
    pack, canonical, edges = guarded_sources(repo)
    im = read_json(output/"input_manifest.json")
    need((output/"source_entries.json").read_bytes() == jbytes(pack), "Candidate source extraction changed")
    need((output/"source_index.csv").read_bytes() == cbytes(source_index(pack)), "Source index changed")
    for path, expected in im["source_files"].items():
        need(sha(repo/path) == expected, "Prior protected source changed: "+path)
    for name, item in im["files"].items():
        need(sha(output/name) == item["sha256"], "Extracted input hash differs")
    judgments = read_json(output/"case_judgments.json")
    need(judgments["human_validated"] is False, "Targeted AI judgments claim human validation")
    jmap = {x["paper_id"]: x for x in judgments["cases"]}
    need(len(jmap) == len(judgments["cases"]) == len(pack["candidates"]) and
         set(jmap) == {c["paper_id"] for c in pack["candidates"]}, "Targeted ID coverage differs")
    evidence = read_json(output/"local_page_evidence.json")
    need(evidence["human_validated"] is False and evidence["network_requests"] == 0,
         "Local evidence provenance changed")
    evidence_by_id = {e["evidence_id"]: e for e in evidence["items"]}
    need(len(evidence_by_id) == len(evidence["items"]), "Repeated local evidence ID")
    versions = read_json(repo/"revision_preparation/annotation/ai_review/version_decisions.json")
    version_ids = {e["evidence_id"] for e in versions["evidence"]}
    merged, summaries, seen = [], [], set()
    for c in pack["candidates"]:
        j = jmap[c["paper_id"]]
        groups, oo = validate_case(c, j, evidence_by_id, version_ids)
        for oid, o in oo.items():
            need(oid not in seen, "Occurrence appears twice")
            seen.add(oid)
        merged.append({**c, "AI_judgment": j})
        summaries.append({"candidate_ID": c["paper_id"], "selection": "targeted_counterpart_extension",
            "classification": j["classification"], "all_report_occurrences": len(c["occurrences"]),
            "identified_study_groups": sum(not g["unresolved_fragment"] for g in groups.values()),
            "unresolved_fragments": sum(g["unresolved_fragment"] for g in groups.values()),
            "counterpart_relation_AI_proposal": j["edge_relation"],
            "human_confirmed": "false", "review_complete": "no"})
    need(len(seen) == len(source_index(pack)) == 111, "Full occurrence set not covered once")
    for e in evidence["items"]:
        source = next(o for c in pack["candidates"] for o in c["occurrences"] if o["occurrence_id"] == e["occurrence_id"])
        need(e["source_id"] == source["source_id"] and e["source_pdf_sha256"] == source["source_pdf_sha256"],
             "Local evidence source identity/hash differs")
        need(e["viewer_pages_one_based"][0] == source["viewer_page_one_based"], "Local evidence page differs")
        need(e["human_confirmed"] is False and bool(e["bibliographic_excerpt"]), "Invalid local AI evidence")
    helper = runpy.run_path(str(repo/"scripts/review_round2_ai_sensitivity.py"), run_name="counterpart_helpers")
    graph = set(); edge_rows = []
    for e in edges:
        pid, q = e["source_paper_id"], e["candidate_paper_id"]
        j = jmap[q]
        safe = eligible_edge(canonical[pid]["identity_label"], j)
        edge_rows.append({**e, "targeted_candidate_classification": j["classification"],
            "targeted_candidate_referring_group_ids": "|".join(j["referring_group_ids"]),
            "edge_relation_AI_proposal": j["edge_relation"], "expanded_AI_union_eligible": "yes" if safe else "no"})
        if safe:
            graph.add(tuple(sorted((pid, q), key=int)))
    a, b = set(), set()
    db = repo/"database/ipcc_litdb.sqlite"
    with sqlite3.connect(db.resolve().as_uri()+"?mode=ro&immutable=1", uri=True) as conn:
        for pid, report in conn.execute("SELECT DISTINCT r.paper_id,c.report_id FROM references_occurrence r JOIN chapters c ON c.chapter_id=r.chapter_id WHERE c.report_id IN ('AR5','AR6') AND r.match_status IN ('matched_doi','matched_title')"):
            (a if report == "AR5" else b).add(str(pid))
    components = []
    for i, nodes in enumerate(helper["graph_components"](graph), 1):
        n5,n6,ns,new5,new6,news = helper["component_counts"](nodes,a,b)
        components.append({"component": i, "paper_IDs": "|".join(nodes), "original_AR5_IDs": n5,
            "original_AR6_IDs": n6, "original_shared_IDs": ns, "scenario_AR5_IDs": new5,
            "scenario_AR6_IDs": new6, "scenario_shared_IDs": news,
            "delta_AR5_IDs": new5-n5, "delta_AR6_IDs": new6-n6, "delta_shared_IDs": news-ns})
    scenarios = []
    for old in read_csv(repo/"revision_preparation/review_round2/ai_sensitivity_v1/counterpart_scenarios.csv"):
        scenarios.append({"scenario_family": "canonical_only", **old})
    n5 = len(a)+sum(c["delta_AR5_IDs"] for c in components)
    n6 = len(b)+sum(c["delta_AR6_IDs"] for c in components)
    ns = len(a&b)+sum(c["delta_shared_IDs"] for c in components)
    scenarios.append({"scenario_family": "expanded_targeted_endpoint_AI",
        "scenario": "accept_single_study_source_and_targeted_endpoint_AI_edges", "AR5_IDs": n5,
        "AR6_IDs": n6, "shared_IDs": ns, "retention": ns/n5, "reuse": ns/n6,
        "accepted_distinct_edges": len(graph), "accepted_components": len(components),
        "estimated_correction": "no", "true_recurrence_bound": "no"})
    outputs = {"case_proposals.json": jbytes({"analysis_version": VERSION, "human_validated": False,
        "selection": pack["selection"], "cases": merged}),
        "candidate_summary.csv": cbytes(summaries), "expanded_edge_eligibility.csv": cbytes(edge_rows),
        "expanded_components.csv": cbytes(components, ["component","paper_IDs","original_AR5_IDs",
            "original_AR6_IDs","original_shared_IDs","scenario_AR5_IDs","scenario_AR6_IDs",
            "scenario_shared_IDs","delta_AR5_IDs","delta_AR6_IDs","delta_shared_IDs"]),
        "counterpart_scenarios.csv": cbytes(scenarios), "methods.txt": METHODS.encode(),
        "validation_tests.json": jbytes(validation_tests())}
    inputs = {"input_manifest.json": sha(output/"input_manifest.json"),
              "case_judgments.json": sha(output/"case_judgments.json"),
              "local_page_evidence.json": sha(output/"local_page_evidence.json"),
              **{name: sha(output/name) for name in im["files"]}}
    registry = {"schema_version":1,"analysis_version":VERSION,"seed":SEED,"random_draws_used":False,
        "human_validated":False,"probability_sample":False,"accuracy_estimated":False,
        "database_modified":False,"canonical_proposals_modified":False,
        "database_sha256":pack["database_sha256"],"input_files":inputs,
        "protected_source_files":im["source_files"],
        "script":{"path":Path(__file__).resolve().relative_to(repo).as_posix(),"sha256":sha(Path(__file__))},
        "counts":{"targeted_candidate_IDs":len(merged),"full_occurrences":len(seen),
            "AI_classifications":dict(Counter(j["classification"] for j in jmap.values())),
            "expanded_eligible_edges":len(graph),"expanded_components":len(components)},
        "registered_outputs":[]}
    for name,data in outputs.items():
        r={"filename":name,"sha256":hashlib.sha256(data).hexdigest(),"bytes":len(data)}
        if name.endswith('.csv'):r['rows']=len(list(csv.DictReader(io.StringIO(data.decode()))))
        registry['registered_outputs'].append(r)
    outputs['manifest.json']=jbytes(registry)
    need(sha(db)==pack['database_sha256'],"Database changed")
    for name,data in outputs.items():
        if check_only:need((output/name).read_bytes()==data,"Output replay differs: "+name)
        else:
            need(not (output/name).exists(),"Preserve existing output; use check mode")
            (output/name).write_bytes(data)
    print(json.dumps({'status':'passed','counts':registry['counts'],'scenarios':scenarios,'human_validated':False},indent=2))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('extract','export','check'))
    p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--output',type=Path)
    a=p.parse_args()
    try:
        repo=a.repo.resolve();output=(a.output or repo/DEFAULT).resolve()
        if a.mode=='extract':extract(repo,output)
        else:export(repo,output,a.mode=='check')
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error)as e:
        print(json.dumps({'status':'failed','error':str(e),'human_validated':False}),file=sys.stderr);return 2
    return 0


if __name__=='__main__':sys.exit(main())
