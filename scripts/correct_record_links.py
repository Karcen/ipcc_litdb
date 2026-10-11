#!/usr/bin/env python3
"""Build a corrected snapshot from an immutable input and an explicit field ledger.

Every edit has an expected old value. Raw reference occurrences remain intact.
The ledger records targeted, source-based inspection, not a probability audit.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys

REPO = Path(__file__).resolve().parents[1]
TABLE_KEYS = {"papers": "paper_id", "references_occurrence": "occurrence_id",
              "chapter_references": "occurrence_id"}
IMMUTABLE_FIELDS = {"occurrence_id", "raw_reference", "chapter_id", "source_id", "source_page"}


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def raw_occurrence_digest(connection):
    value = hashlib.sha256()
    for row in connection.execute("SELECT occurrence_id,chapter_id,source_id,source_page,raw_reference "
                                  "FROM references_occurrence ORDER BY occurrence_id"):
        encoded = json.dumps(list(row), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        value.update(len(encoded).to_bytes(8, "big")); value.update(encoded)
    return value.hexdigest()


def validate_database(connection):
    if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise ValueError("SQLite integrity failure")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("Unresolved foreign keys")
    checks = {
        "occurrences without paper identities": "SELECT COUNT(*) FROM references_occurrence WHERE paper_id IS NULL",
        "chapter/reference crosswalk": """SELECT COUNT(*) FROM chapter_references x
            JOIN references_occurrence o USING(occurrence_id)
            WHERE x.chapter_id<>o.chapter_id OR x.paper_id<>o.paper_id""",
        "missing chapter/reference crosswalk": """SELECT COUNT(*) FROM references_occurrence o
            WHERE NOT EXISTS(SELECT 1 FROM chapter_references x WHERE x.occurrence_id=o.occurrence_id
                             AND x.chapter_id=o.chapter_id AND x.paper_id=o.paper_id)""",
        "extra chapter/reference crosswalk": """SELECT COUNT(*) FROM chapter_references x
            WHERE NOT EXISTS(SELECT 1 FROM references_occurrence o WHERE o.occurrence_id=x.occurrence_id
                             AND o.chapter_id=x.chapter_id AND o.paper_id=x.paper_id)""",
        "chapter/reference crosswalk row count": """SELECT ABS(
            (SELECT COUNT(*) FROM chapter_references)-(SELECT COUNT(*) FROM references_occurrence))""",
        "orphan papers": """SELECT COUNT(*) FROM papers p WHERE NOT EXISTS
            (SELECT 1 FROM references_occurrence o WHERE o.paper_id=p.paper_id)""",
        "missing paper counters": """SELECT COUNT(*) FROM papers
            WHERE n_occurrences IS NULL OR n_chapters IS NULL OR n_reports IS NULL""",
        "paper occurrence counters": """SELECT COUNT(*) FROM papers p WHERE p.n_occurrences<>
            (SELECT COUNT(*) FROM references_occurrence o WHERE o.paper_id=p.paper_id)""",
        "paper chapter counters": """SELECT COUNT(*) FROM papers p WHERE p.n_chapters<>
            (SELECT COUNT(DISTINCT chapter_id) FROM references_occurrence o WHERE o.paper_id=p.paper_id)""",
        "paper report counters": """SELECT COUNT(*) FROM papers p WHERE p.n_reports<>
            (SELECT COUNT(DISTINCT c.report_id) FROM references_occurrence o
             JOIN chapters c USING(chapter_id) WHERE o.paper_id=p.paper_id)""",
        "duplicate DOI": """SELECT COUNT(*) FROM (SELECT doi_norm FROM papers
            WHERE doi_norm IS NOT NULL AND doi_norm<>'' GROUP BY doi_norm HAVING COUNT(*)>1)""",
    }
    result = {name: connection.execute(query).fetchone()[0] for name, query in checks.items()}
    if any(result.values()):
        raise ValueError("Correction invariant failed: " + json.dumps(result))
    return result


def year(value):
    match = re.search(r"\b(1[6-9]\d{2}|20\d{2})\b", str(value or ""))
    return int(match.group(1)) if match else None


def unambiguous_source_year(value):
    """Leave multi-year source dates unresolved rather than choosing a year."""
    years = set(re.findall(r"\b(?:1[6-9]\d{2}|20\d{2})\b", str(value or "")))
    return int(next(iter(years))) if len(years) == 1 else None


def raw_reference_year(value):
    """Read the leading printed year, including author-year citation suffixes."""
    match = re.search(r"\b(1[6-9]\d{2}|20\d{2})[a-z]?\b", str(value or "")[:300])
    return int(match.group(1)) if match else None


def add_year_review_flags(connection, timestamp, reviewed_occurrence_ids=()):
    """Queue chronological conflicts. Advance-online dates need adjudication."""
    reviewed = set(reviewed_occurrence_ids)
    rows = connection.execute("""SELECT o.occurrence_id,o.raw_reference,p.year_best,s.publication_year
        FROM references_occurrence o JOIN papers p USING(paper_id)
        JOIN chapters c USING(chapter_id) LEFT JOIN sources s ON s.source_id=c.source_id
        WHERE o.match_status='matched_doi' ORDER BY o.occurrence_id""").fetchall()
    count = 0
    for occurrence_id, raw, preferred, source in rows:
        preferred_year, source_year = year(preferred), unambiguous_source_year(source)
        raw_year = raw_reference_year(raw)
        reasons = []
        if preferred_year and source_year and preferred_year > source_year:
            reasons.append("preferred_year_after_source")
        if preferred_year and raw_year and abs(preferred_year - raw_year) > 1:
            reasons.append("preferred_raw_year_gap_over_one")
        if not reasons:
            continue
        detail = json.dumps({"preferred_year": preferred_year, "source_year": source_year,
                             "raw_leading_year": raw_year, "reasons": reasons,
                             "interpretation": "Review candidate; online and print dates can differ."},
                            sort_keys=True)
        exists = connection.execute("SELECT 1 FROM data_quality_flags WHERE entity_type='occurrence' "
            "AND entity_id=? AND flag_type='matched_doi_chronology_review'", (occurrence_id,)).fetchone()
        if not exists:
            connection.execute("INSERT INTO data_quality_flags(entity_type,entity_id,flag_type,severity,detail,detected_at) "
                "VALUES('occurrence',?,'matched_doi_chronology_review','high',?,?)", (occurrence_id, detail, timestamp))
        prior = connection.execute("SELECT 1 FROM manual_review WHERE occurrence_id=?", (occurrence_id,)).fetchone()
        if not prior:
            status = "resolved" if occurrence_id in reviewed else "open"
            note = "Targeted source inspection is recorded in the correction ledger." if status == "resolved" else detail
            connection.execute("INSERT INTO manual_review(occurrence_id,status,assigned_to,reason,note,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)", (occurrence_id, status, "Codex" if status == "resolved" else None,
                "matched_doi_chronology_review", note, timestamp, timestamp))
        count += 1
    return count


def apply_corrections(source, destination, manifest, report=None):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    ledger = json.loads(Path(manifest).read_text(encoding="utf-8")) if isinstance(manifest, (str, Path)) else manifest
    if source == destination or destination.exists():
        raise ValueError("Choose a new destination; the input and existing files are preserved")
    if report:
        report = Path(report).resolve()
        if report in (source, destination) or report.exists():
            raise ValueError("Choose a new report file separate from both databases")
        report.parent.mkdir(parents=True, exist_ok=True)
    before_hash = digest(source)
    if before_hash != ledger["source_sha256"]:
        raise ValueError("Input SHA-256 does not match the correction ledger")
    timestamp, actor = ledger["timestamp"], "Codex: targeted primary-source inspection"
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as old:
        before_raw = raw_occurrence_digest(old)
        before_count = old.execute("SELECT COUNT(*) FROM references_occurrence").fetchone()[0]
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    edits = []
    try:
        with closing(sqlite3.connect(destination)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            with connection:
                for paper in ledger.get("new_papers", []):
                    fields = list(paper)
                    if not set(fields) <= {r[1] for r in connection.execute("PRAGMA table_info(papers)")}:
                        raise ValueError("Unknown new-paper field")
                    connection.execute("INSERT INTO papers(" + ",".join(fields) + ") VALUES(" +
                                       ",".join("?" for _ in fields) + ")", [paper[f] for f in fields])
                    connection.execute("INSERT INTO change_log(entity_type,entity_id,field,old_value,new_value,changed_by,changed_at,reason) "
                        "VALUES('paper',?,'created',NULL,?,?,?,?)", (str(paper["paper_id"]), json.dumps(paper, sort_keys=True),
                        actor, timestamp, "Split a confirmed distinct work; see the versioned correction ledger."))
                for edit in ledger.get("edits", []):
                    table, key = edit["table"], edit["key"]
                    if table not in TABLE_KEYS:
                        raise ValueError("Unsupported correction table")
                    pk = TABLE_KEYS[table]
                    available = {r[1] for r in connection.execute("PRAGMA table_info(" + table + ")")}
                    for field, change in edit["fields"].items():
                        if field not in available or field == pk or (table != "papers" and field in IMMUTABLE_FIELDS):
                            raise ValueError("Unknown or immutable correction field: " + field)
                        row = connection.execute(f'SELECT "{field}" FROM "{table}" WHERE "{pk}"=?', (key,)).fetchone()
                        if row is None or row[0] != change["old"]:
                            raise ValueError(f"Correction precondition differs at {table}/{key}/{field}")
                        if change["old"] == change["new"]:
                            continue
                        connection.execute(f'UPDATE "{table}" SET "{field}"=? WHERE "{pk}"=?', (change["new"], key))
                        connection.execute("INSERT INTO change_log(entity_type,entity_id,field,old_value,new_value,changed_by,changed_at,reason) "
                            "VALUES(?,?,?,?,?,?,?,?)", ("paper" if table == "papers" else "occurrence", str(key), table + "." + field,
                            json.dumps(change["old"]), json.dumps(change["new"]), actor, timestamp,
                            edit["reason"] + " Sources: " + " ; ".join(edit["evidence_urls"])))
                        edits.append({"table": table, "key": key, "field": field, **change})
                    if table == "references_occurrence" and "paper_id" in edit["fields"]:
                        change = edit["fields"]["paper_id"]
                        connection.execute("UPDATE chapter_references SET paper_id=? WHERE occurrence_id=? AND paper_id=?",
                                           (change["new"], key, change["old"]))
                        connection.execute("INSERT INTO change_log(entity_type,entity_id,field,old_value,new_value,changed_by,changed_at,reason) "
                            "VALUES('occurrence',?,'chapter_references.paper_id',?,?,?,?,?)", (str(key),
                            json.dumps(change["old"]), json.dumps(change["new"]), actor, timestamp,
                            "Mirror the occurrence identity correction in the chapter crosswalk."))
                for paper_id in ledger.get("remove_empty_papers", []):
                    if connection.execute("SELECT 1 FROM references_occurrence WHERE paper_id=?", (paper_id,)).fetchone():
                        raise ValueError("A cited paper cannot be removed")
                    row = connection.execute("SELECT * FROM papers WHERE paper_id=?", (paper_id,)).fetchone()
                    if not row:
                        raise ValueError("Missing merged paper")
                    connection.execute("DELETE FROM paper_authors WHERE paper_id=?", (paper_id,))
                    connection.execute("DELETE FROM papers WHERE paper_id=?", (paper_id,))
                    connection.execute("INSERT INTO change_log(entity_type,entity_id,field,old_value,new_value,changed_by,changed_at,reason) "
                        "VALUES('paper',?,'merged_record_removed',?,NULL,?,?,?)", (str(paper_id), json.dumps(list(row)),
                        actor, timestamp, "All occurrences moved to an existing identity; see the correction ledger."))
                # Author relationships are replaced only for inspected identities.
                for paper_id, authors in ledger.get("paper_authors", {}).items():
                    previous_authors = connection.execute("SELECT a.surname,a.given,a.full_name FROM paper_authors x "
                        "JOIN authors a USING(author_id) WHERE paper_id=? ORDER BY author_pos", (int(paper_id),)).fetchall()
                    connection.execute("DELETE FROM paper_authors WHERE paper_id=?", (int(paper_id),))
                    for pos, author in enumerate(authors, 1):
                        values = (author["surname"], author["given"], author["full_name"])
                        existing = connection.execute("SELECT author_id FROM authors WHERE surname=? AND given=?", values[:2]).fetchone()
                        author_id = existing[0] if existing else connection.execute(
                            "INSERT INTO authors(surname,given,full_name) VALUES(?,?,?)", values).lastrowid
                        connection.execute("INSERT INTO paper_authors VALUES(?,?,?)", (int(paper_id), author_id, pos))
                    connection.execute("INSERT INTO change_log(entity_type,entity_id,field,old_value,new_value,changed_by,changed_at,reason) "
                        "VALUES('paper',?,'paper_authors',?,?,?,?,?)", (paper_id, json.dumps(previous_authors),
                        json.dumps(authors, sort_keys=True), actor, timestamp,
                        "Replace authors of the inspected work using the correction ledger."))
                connection.execute("""UPDATE papers SET
                    n_occurrences=(SELECT COUNT(*) FROM references_occurrence o WHERE o.paper_id=papers.paper_id),
                    n_chapters=(SELECT COUNT(DISTINCT chapter_id) FROM references_occurrence o WHERE o.paper_id=papers.paper_id),
                    n_reports=(SELECT COUNT(DISTINCT c.report_id) FROM references_occurrence o JOIN chapters c USING(chapter_id)
                               WHERE o.paper_id=papers.paper_id)""")
                reviewed_ids = ledger.get("reviewed_occurrence_ids", [])
                for oid in reviewed_ids:
                    case = next(c for c in ledger["cases"] if oid in c["occurrence_ids"])
                    connection.execute("INSERT INTO manual_review(occurrence_id,status,assigned_to,reason,note,created_at,updated_at) "
                        "VALUES(?,'resolved','Codex','targeted_source_inspection',?,?,?)", (oid,
                        case["decision"] + " Evidence: " + " ; ".join(case["evidence_urls"]), timestamp, timestamp))
                flags = add_year_review_flags(connection, timestamp, reviewed_ids)
                for flag in ledger.get("additional_flags", []):
                    connection.execute("INSERT INTO data_quality_flags(entity_type,entity_id,flag_type,severity,detail,detected_at) "
                        "VALUES('occurrence',?,?,?,?,?)", (flag["occurrence_id"], flag["flag_type"],
                        flag["severity"], flag["detail"], timestamp))
                    connection.execute("INSERT INTO manual_review(occurrence_id,status,reason,note,created_at,updated_at) "
                        "VALUES(?,'open',?,?,?,?)", (flag["occurrence_id"], flag["flag_type"], flag["detail"], timestamp, timestamp))
                connection.execute("INSERT OR REPLACE INTO build_info VALUES('correction_version',?)", (ledger["version"],))
                connection.execute("INSERT OR REPLACE INTO build_info VALUES('correction_source_sha256',?)", (before_hash,))
                connection.execute("INSERT OR REPLACE INTO build_info VALUES('correction_timestamp',?)", (timestamp,))
            invariant_checks = validate_database(connection)
            after_raw = raw_occurrence_digest(connection)
            after_count = connection.execute("SELECT COUNT(*) FROM references_occurrence").fetchone()[0]
            if before_raw != after_raw or before_count != after_count:
                raise ValueError("Raw occurrence provenance changed")
        # Refresh cached application analyses with the ledger's fixed timestamp.
        sys.path.insert(0, str(REPO))
        import analytics.engine as engine
        previous_now = engine._now
        try:
            engine._now = lambda: timestamp
            analysis = engine.Engine(destination)
            refreshed = analysis.persist()
            analysis.con.close()
        finally:
            engine._now = previous_now
        if refreshed["errors"]:
            raise ValueError("Derived application analysis refresh failed")
        if digest(source) != before_hash:
            raise ValueError("Input database changed during correction")
        result = {"version": ledger["version"], "source_sha256": before_hash, "destination_sha256": digest(destination),
                  "raw_occurrence_sha256": before_raw, "raw_occurrences_preserved": before_count,
                  "targeted_ids_inspected": len(ledger["cases"]), "field_edits": edits,
                  "chronology_review_candidates": flags, "invariant_checks": invariant_checks,
                  "cached_analysis_errors": refreshed["errors"],
                  "audit_scope": "Targeted source inspection; no population linkage accuracy is estimated."}
        if report:
            with report.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(result, indent=2) + "\n")
        return result
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--corrections", type=Path, default=REPO / "reproducibility/corrections/2026-10-11.json")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = apply_corrections(args.source, args.destination, args.corrections, args.report)
    print(json.dumps({key: value for key, value in result.items() if key != "field_edits"}, indent=2))


if __name__ == "__main__":
    main()
