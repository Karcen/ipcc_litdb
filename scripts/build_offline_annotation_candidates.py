#!/usr/bin/env python3
"""Create offline candidate-retrieval evidence, never labels or error estimates.

Only this script's output directory is written. Database and sample inputs are
opened read-only. Every retained query is executed and records full results.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys


EXPECTED_DB = "a887a535947ae3b9bab7656070cc038eb3a7a5161494afcb2c3df2245f5ff0ec"
YEAR_RE = re.compile(r"\b(1[6-9]\d{2}|20\d{2})[a-z]?\b")
AUTHOR_RE = re.compile(r"\s*([A-Za-z\u00c0-\u00ff'’-]+)")
STOP = set("a an the of in on for and or to by with from at as is are was were be been being this that these those its their it into within across through using use toward towards under over between among based study studies analysis assessment report reports new some all part chapter volume edition pp et al de van von der den la le da di du del des".split())


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_csv(path):
    with Path(path).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def stable_ids(values):
    return sorted(set(int(v) for v in values))


def run(repo, output):
    repo, output = Path(repo).resolve(), Path(output).resolve()
    source = repo / "revision_preparation" / "annotation" / "inputs"
    db = repo / "database" / "ipcc_litdb.sqlite"
    before = digest(db)
    if before != EXPECTED_DB:
        raise ValueError("Snapshot hash is not the immutable corrected v1.2.0 snapshot")
    sample_papers = read_csv(source / "revision_identity_audit_sample_papers.csv")
    sample_entries = read_csv(source / "revision_identity_audit_sample_entries.csv")
    if len(sample_papers) != 331 or len(sample_entries) != 801:
        raise ValueError("Expected 331 sampled IDs and 801 source entries")
    if len({x["paper_id"] for x in sample_papers}) != 331:
        raise ValueError("Duplicate sample ID")
    if len({x["occurrence_id"] for x in sample_entries}) != 801:
        raise ValueError("Duplicate source occurrence")
    source_hashes = {p.name: digest(p) for p in sorted(source.iterdir()) if p.is_file() and not p.name.startswith("._")}

    sys.path.insert(0, str(repo / "reproducibility"))
    import revision_identity as canonical
    text_key = canonical.text_key

    c = sqlite3.connect(db.as_uri() + "?mode=ro&immutable=1", uri=True)
    c.row_factory = sqlite3.Row
    try:
        papers = {int(r["paper_id"]): dict(r) for r in c.execute("SELECT * FROM papers ORDER BY paper_id")}
        sql = """
        SELECT r.*, c.report_id, c.chapter_label, c.chapter_title,
               w.wg_label, s.file_url AS source_file_url,
               s.page_url AS source_page_url, s.publication_year AS source_year
        FROM references_occurrence r
        JOIN chapters c ON c.chapter_id=r.chapter_id
        LEFT JOIN working_groups w ON w.wg_id=c.wg_id
        LEFT JOIN sources s ON s.source_id=c.source_id
        ORDER BY r.occurrence_id
        """
        records = [dict(r) for r in c.execute(sql)]
    finally:
        c.close()
    by_oid = {r["occurrence_id"]: r for r in records}
    if len(by_oid) != len(records):
        raise ValueError("Occurrence join multiplied rows")
    sample_by_pid = defaultdict(list)
    for r in sample_entries:
        actual = by_oid.get(r["occurrence_id"])
        if actual is None or actual["raw_reference"] != r["raw_reference"] or int(actual["paper_id"]) != int(r["paper_id"]):
            raise ValueError("Frozen sample entry differs from snapshot: " + r["occurrence_id"])
        sample_by_pid[int(r["paper_id"])].append(r)

    # Index the complete saved occurrence inventory, with no matching filter.
    raw_tokens = defaultdict(set)
    raw_author_year = defaultdict(set)
    literal_dois = defaultdict(set)
    repaired_dois = defaultdict(set)
    by_pid = defaultdict(list)
    for i, r in enumerate(records):
        raw = str(r["raw_reference"] or "")
        if r["paper_id"] is not None:
            by_pid[int(r["paper_id"])].append(i)
        for token in set(text_key(raw).split()):
            raw_tokens[token].add(i)
        author = AUTHOR_RE.match(raw)
        year = YEAR_RE.search(raw[:300])
        if author and year:
            raw_author_year[(text_key(author.group(1)), int(year.group(1)))].add(i)
        for doi in {canonical.normalized_doi(x) for x in canonical.DOI_RE.findall(raw)}:
            if canonical.usable_raw_doi(doi):
                literal_dois[doi].add(i)
        for doi in canonical.punctuation_repaired_raw_dois(raw):
            repaired_dois[doi].add(i)

    preferred_tokens = defaultdict(set)
    preferred_author_year = defaultdict(set)
    preferred_dois = defaultdict(set)
    for pid, p in papers.items():
        for token in set(text_key(p["title_best"]).split()):
            preferred_tokens[token].add(pid)
        author = text_key(p["first_author_surname"])
        year = str(p["year_best"] or "")
        if author and re.fullmatch(r"[12]\d{3}", year):
            preferred_author_year[(author, int(year))].add(pid)
        doi = canonical.normalized_doi(p["doi_norm"])
        if doi:
            preferred_dois[doi].add(pid)

    def intersect(index, tokens):
        if not tokens:
            return set()
        sets = [index.get(t, set()) for t in tokens]
        sets.sort(key=len)
        result = set(sets[0])
        for values in sets[1:]:
            result.intersection_update(values)
        return result

    def fragments(text):
        # Query generation is heuristic only; no inferred title becomes a label.
        tokens = list(dict.fromkeys(t for t in text_key(text).split() if len(t) >= 3 and t not in STOP and not t.isdigit()))
        tokens = tokens[:24]
        if len(tokens) < 3:
            return []
        k = min(4, len(tokens))
        options = [tokens[:k]]
        if len(tokens) >= 2*k:
            options.append(tokens[k:2*k])
        # Four globally least-common tokens in the first 24 significant tokens.
        options.append(sorted(tokens, key=lambda t: (len(raw_tokens.get(t, set())), t))[:k])
        return [list(f) for f in dict.fromkeys(tuple(x) for x in options)]

    def raw_title_span(raw):
        found = YEAR_RE.search(raw[:300])
        if not found:
            return None
        after = raw[found.end():].lstrip(").,:; \t\r\n")
        # Bound query generation to title-leading text; do not claim validated parsing.
        return after[:350] or None

    all_queries = 0
    result_packets = []
    method_counts = Counter()
    candidate_union = set()
    for source_paper in sample_papers:
        pid = int(source_paper["paper_id"])
        original = sample_by_pid[pid]
        queries = []
        query_index = {}
        candidate_evidence = defaultdict(list)

        def add_query(method, params, source_basis, match_indices=None, match_pids=None):
            nonlocal all_queries
            signature = json.dumps([method, params], sort_keys=True, ensure_ascii=False)
            if signature in query_index:
                q = queries[query_index[signature]]
                if source_basis not in q["source_basis"]:
                    q["source_basis"].append(source_basis)
                return
            if match_indices is not None:
                match_indices = sorted(match_indices)
                hits = stable_ids(records[i]["paper_id"] for i in match_indices if records[i]["paper_id"] is not None)
                evidence_oids = [records[i]["occurrence_id"] for i in match_indices]
            else:
                hits = stable_ids(match_pids or ())
                evidence_oids = []
            candidate_ids = [v for v in hits if v != pid]
            qid = len(queries) + 1
            q = {"query_id": qid, "method": method, "parameters": params,
                 "source_basis": [source_basis], "executed": True,
                 "scope": "All snapshot reports and all matching statuses",
                 "matched_paper_ids_including_self": hits,
                 "candidate_ids_excluding_self": candidate_ids,
                 "matched_occurrence_ids": evidence_oids,
                 "total_matched_ids": len(hits), "total_matched_occurrences": len(evidence_oids),
                 "results_truncated": False, "candidate_limit": None}
            query_index[signature] = len(queries)
            queries.append(q)
            all_queries += 1
            method_counts[method] += 1
            for candidate in candidate_ids:
                candidate_evidence[candidate].append(qid)

        for entry in original:
            raw = str(entry["raw_reference"] or "")
            basis = {"kind": "original_reference", "occurrence_id": entry["occurrence_id"]}
            literal = sorted({canonical.normalized_doi(x) for x in canonical.DOI_RE.findall(raw) if canonical.usable_raw_doi(canonical.normalized_doi(x))})
            repaired = canonical.punctuation_repaired_raw_dois(raw)
            for doi in sorted(set(literal + repaired)):
                doi_basis = {**basis, "raw_token_literal": doi in literal, "raw_token_punctuation_repaired": doi in repaired}
                add_query("original_doi_to_original_literal", {"doi": doi}, doi_basis, match_indices=literal_dois.get(doi, set()))
                add_query("original_doi_to_original_repaired", {"doi": doi}, doi_basis, match_indices=repaired_dois.get(doi, set()))
                add_query("original_doi_to_preferred_doi_supplement", {"doi": doi}, doi_basis, match_pids=preferred_dois.get(doi, set()))
            span = raw_title_span(raw)
            if span:
                for tokens in fragments(span):
                    title_basis = {**basis, "heuristic_title_span": span,
                        "span_rule": "After first year in first 300 characters; trim year suffix/punctuation; take next 350 characters; title not externally validated"}
                    add_query("original_title_fragment_to_original_text", {"all_significant_tokens": tokens}, title_basis, match_indices=intersect(raw_tokens, tokens))
                    add_query("original_title_fragment_to_preferred_title_supplement", {"all_significant_tokens": tokens}, title_basis, match_pids=intersect(preferred_tokens, tokens))
            author = AUTHOR_RE.match(raw)
            year = YEAR_RE.search(raw[:300])
            if author and year:
                name, y = text_key(author.group(1)), int(year.group(1))
                years = [y-1, y, y+1]
                raw_hits = set().union(*(raw_author_year.get((name, yy), set()) for yy in years))
                pref_hits = set().union(*(preferred_author_year.get((name, yy), set()) for yy in years))
                params = {"first_author_surname": name, "leading_year": y, "year_tolerance": 1, "searched_years": years}
                add_query("original_first_author_year_to_original_text", params, basis, match_indices=raw_hits)
                add_query("original_first_author_year_to_preferred_metadata_supplement", params, basis, match_pids=pref_hits)
        preferred = papers[pid]
        pref_basis = {"kind": "preferred_metadata_supplement", "paper_id": pid}
        for tokens in fragments(preferred["title_best"] or ""):
            add_query("preferred_title_fragment_to_original_text_supplement", {"all_significant_tokens": tokens}, pref_basis, match_indices=intersect(raw_tokens, tokens))
            add_query("preferred_title_fragment_to_preferred_title_supplement", {"all_significant_tokens": tokens}, pref_basis, match_pids=intersect(preferred_tokens, tokens))
        pref_author, pref_year = text_key(preferred["first_author_surname"]), str(preferred["year_best"] or "")
        if pref_author and re.fullmatch(r"[12]\d{3}", pref_year):
            y = int(pref_year)
            years = [y-1, y, y+1]
            params = {"first_author_surname": pref_author, "preferred_year": y, "year_tolerance": 1, "searched_years": years}
            add_query("preferred_author_year_to_original_text_supplement", params, pref_basis,
                      match_indices=set().union(*(raw_author_year.get((pref_author, yy), set()) for yy in years)))
            add_query("preferred_author_year_to_preferred_metadata_supplement", params, pref_basis,
                      match_pids=set().union(*(preferred_author_year.get((pref_author, yy), set()) for yy in years)))
        candidate_union.update(candidate_evidence)
        candidates = []
        for cid in sorted(candidate_evidence):
            pair_entries = [records[i] for i in by_pid[cid] if records[i]["report_id"] in {"AR5", "AR6"}]
            matched_oids = set().union(*(set(queries[q-1]["matched_occurrence_ids"]) for q in candidate_evidence[cid]))
            other_matching = [records[i] for i in by_pid[cid] if records[i]["report_id"] not in {"AR5", "AR6"} and records[i]["occurrence_id"] in matched_oids]
            candidates.append({"paper_id": cid, "query_ids": candidate_evidence[cid], "preferred_metadata": papers.get(cid),
                "ar5_ar6_original_entries": pair_entries, "other_report_matching_entries": other_matching,
                "all_snapshot_occurrence_count": len(by_pid[cid]), "ar5_ar6_occurrence_count": len(pair_entries),
                "entries_truncated": False})
        result_packets.append({"paper_id": pid, "frozen_sample_row": source_paper,
            "preferred_metadata": preferred, "all_801_scope_sample_entries_for_this_id": original,
            "queries": queries, "candidate_ids": sorted(candidate_evidence), "candidates": candidates,
            "candidate_search_truncated": False, "generated_labels": False,
            "limitations": "Candidate retrieval only. Token and author/year similarity are not identity judgments. Preferred metadata may be erroneous. Offline absence of a candidate is not proof of no missed merger. No external metadata or primary-source validity was checked."})
    after = digest(db)
    if before != after:
        raise ValueError("Snapshot changed during read-only candidate generation")
    if {p.name: digest(p) for p in sorted(source.iterdir()) if p.is_file() and not p.name.startswith("._")} != source_hashes:
        raise ValueError("Frozen source inputs changed during candidate generation")
    result = {"schema_version": "offline-ai-review-candidates-v1", "created_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Offline retrieval evidence for per-ID AI-assisted initial review; no human labels or accuracy estimates",
        "database": {"path": str(db), "sha256_before": before, "sha256_after": after, "read_only": True,
                     "all_paper_rows": len(papers), "all_joined_reference_occurrences": len(records)},
        "source_input_hashes": source_hashes, "sample_id_count": len(sample_papers), "sample_entry_count": len(sample_entries),
        "source_values_preserved": True, "random_seed": 20261011,
        "generator_script_sha256": digest(__file__),
        "randomness_used": False, "all_query_count": all_queries, "method_query_counts": dict(sorted(method_counts.items())),
        "unique_candidate_paper_ids": len(candidate_union), "result_limits": None,
        "canonical_doi_parser": {"path": str(Path(canonical.__file__).resolve()), "sha256": digest(canonical.__file__)},
        "search_specification": {"scope": "Complete saved SQLite snapshot, all reports and all match statuses",
             "title_normalization": "Canonical NFKD accent fold, lowercase, ASCII alphanumeric tokenization",
             "title_fragment_generation": "First 24 unique significant tokens of a 350-character raw post-year span, or preferred title; first four, next four if available, and four globally least-common tokens. At least three tokens. Tokens combined by AND without order constraints.",
             "stopwords": sorted(STOP), "author_year": "Canonical leading surname regex plus first year in first 300 raw characters; exact surname and year +/- 1. Preferred variants explicitly marked supplemental.",
             "doi": "Canonical literal and conservative punctuation-repaired DOI tokens; original-text inverted indexes, with preferred-DOI lookup as a separate supplement",
             "truncation": "None: every matched candidate and every candidate AR5/AR6 occurrence retained. Other-report original entries retained where they directly matched a query."},
        "packets": result_packets}
    output.mkdir(parents=True, exist_ok=True)
    destination = output / "candidate_packets.json"
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    packet_folder = output / "packets"
    packet_folder.mkdir(exist_ok=True)
    index = []
    for packet in result_packets:
        name = str(packet["paper_id"]) + ".json"
        file = packet_folder / name
        file.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        index.append({"paper_id": packet["paper_id"], "file": "packets/" + name, "sha256": digest(file),
            "sample_entries": len(packet["all_801_scope_sample_entries_for_this_id"]),
            "executed_queries": len(packet["queries"]), "candidate_ids": len(packet["candidate_ids"]),
            "candidate_search_truncated": False})
    (output / "candidate_packet_index.json").write_text(json.dumps({"bundle_sha256": digest(destination),
        "database_sha256": before, "labels_generated": False, "packets": index}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(destination), "sha256": digest(destination), "bytes": destination.stat().st_size,
                      "sample_ids": len(sample_papers), "sample_entries": len(sample_entries), "queries": all_queries,
                      "candidate_ids": len(candidate_union), "database_unchanged": before == after,
                      "labels_generated": False}, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1],
                    help="Repository containing the frozen database and annotation inputs")
    ap.add_argument("--output", type=Path, required=True,
                    help="New evidence directory; no annotation labels are generated")
    args = ap.parse_args()
    run(args.repo, args.output)
