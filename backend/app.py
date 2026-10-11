#!/usr/bin/env python3
"""
IPCC Literature Database - internal API.

Read endpoints serve bibliography records and analysis results. Manual review
changes record the old and new values in change_log.

Run:  uvicorn app:app --host 127.0.0.1 --port 8077
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Annotated, Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
from config import (ALLOW_WRITES, CHANGED_BY, FRONTEND_DIR, MAX_PAGE_SIZE,
                    REPO_ROOT, sqlite_database_path)

app = FastAPI(title="IPCC Literature Database API", version="1.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"],
                   expose_headers=["X-Total-Count", "X-Row-Count", "X-Export-Limit",
                                   "X-Export-Truncated"])


@app.middleware("http")
async def revalidate_frontend_assets(request, call_next):
    """Stop browsers serving a stale dashboard after the files change.

    Starlette's StaticFiles sets no Cache-Control header, so browsers fall back to
    heuristic caching and can keep an old locale file for hours. "no-cache" means
    revalidate before use, not "do not store": with the ETag Starlette already
    sends, an unchanged file costs one 304.
    """
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response

NOW = lambda: dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- helpers
def page_args(page: int, size: int) -> tuple[int, int]:
    page = max(1, page)
    size = max(1, min(size, MAX_PAGE_SIZE))
    return size, (page - 1) * size


PAPER_ORDERS = {
    "n_reports": "p.n_reports DESC, p.n_chapters DESC, p.paper_id ASC",
    "year": "p.year_best ASC, p.paper_id ASC",
    "title": "p.title_best ASC, p.paper_id ASC",
    "occurrences": "p.n_occurrences DESC, p.paper_id ASC",
}
REFERENCE_ORDER = "r.chapter_id, r.source_page, r.occurrence_id"
UNRESOLVED_ORDER = "c.report_id, wg.wg_label, c.chapter_label, r.source_page, r.occurrence_id"


def normalized_doi(value: str | None) -> str | None:
    if value is None:
        return None
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "",
                  value.strip(), flags=re.I).lower()


def working_group_filter(wg: str | None, working_group: str | None) -> str | None:
    if wg and working_group and wg != working_group:
        raise HTTPException(400, "wg and working_group must identify the same working group")
    return working_group or wg


def paper_filters(*, q=None, doi=None, year_from=None, year_to=None, report_id=None,
                  wg=None, chapter_id=None, status=None, min_reports=None) -> tuple[str, dict]:
    """Use the same paper selection for pagination and every export format."""
    where, params = [], {}
    if q:
        where.append("(LOWER(p.title_best) LIKE :q OR LOWER(p.authors_best) LIKE :q "
                     "OR LOWER(p.journal_best) LIKE :q OR p.doi_norm LIKE :q)")
        params["q"] = f"%{q.lower()}%"
    if doi:
        where.append("p.doi_norm = :doi"); params["doi"] = normalized_doi(doi)
    if year_from is not None or year_to is not None:
        where.append("p.year_best IS NOT NULL AND TRIM(p.year_best) <> ''")
    if year_from is not None:
        where.append("p.year_best >= :year_from"); params["year_from"] = year_from
    if year_to is not None:
        where.append("p.year_best <= :year_to"); params["year_to"] = year_to
    if status:
        where.append("p.verification_status = :status"); params["status"] = status
    if min_reports is not None:
        where.append("p.n_reports >= :min_reports"); params["min_reports"] = min_reports
    if report_id or wg or chapter_id:
        citation = ["r.paper_id = p.paper_id"]
        if report_id:
            citation.append("c.report_id = :report_id"); params["report_id"] = report_id
        if wg:
            citation.append("wg.wg_label = :wg"); params["wg"] = wg
        if chapter_id:
            citation.append("c.chapter_id = :chapter_id"); params["chapter_id"] = chapter_id
        where.append("EXISTS (SELECT 1 FROM references_occurrence r "
                     "JOIN chapters c ON c.chapter_id = r.chapter_id "
                     "JOIN working_groups wg ON wg.wg_id = c.wg_id "
                     "WHERE " + " AND ".join(citation) + ")")
    return (" WHERE " + " AND ".join(where) if where else ""), params


def occurrence_filters(*, report_id=None, wg=None, chapter_id=None, status=None,
                       q=None, only_doi_less=False, unresolved_only=False) -> tuple[str, dict]:
    where, params = [], {}
    if unresolved_only:
        where.append("r.match_status = 'unmatched'")
    if report_id:
        where.append("c.report_id = :report_id"); params["report_id"] = report_id
    if wg:
        where.append("wg.wg_label = :wg"); params["wg"] = wg
    if chapter_id:
        where.append("r.chapter_id = :chapter_id"); params["chapter_id"] = chapter_id
    if status:
        where.append("r.match_status = :status"); params["status"] = status
    if q:
        where.append("(LOWER(r.raw_reference) LIKE :q OR LOWER(r.parsed_title) LIKE :q)")
        params["q"] = f"%{q.lower()}%"
    if only_doi_less:
        where.append("(r.doi_norm IS NULL OR r.doi_norm = '')")
    return (" WHERE " + " AND ".join(where) if where else ""), params


def audit(entity_type: str, entity_id: str, field: str, old: Any, new: Any,
          reason: str) -> None:
    db.execute(
        """INSERT INTO change_log (entity_type, entity_id, field, old_value,
                                   new_value, changed_by, changed_at, reason)
           VALUES (:et, :eid, :f, :o, :n, :by, :at, :r)""",
        et=entity_type, eid=str(entity_id), f=field,
        o=None if old is None else str(old), n=None if new is None else str(new),
        by=CHANGED_BY, at=NOW(), r=reason)


def require_writes() -> None:
    if not ALLOW_WRITES:
        raise HTTPException(403, "write API disabled (ALLOW_WRITES=false)")


# ---------------------------------------------------------------- health
@app.get("/api/health")
def health() -> dict:
    one = db.query_one("SELECT COUNT(*) AS n FROM papers")
    return {"status": "ok", "papers": one["n"] if one else 0, "time": NOW()}


# ---------------------------------------------------------------- statistics
@app.get("/api/statistics")
def statistics() -> dict:
    """Every number here is computed from the database at request time."""
    tot: dict[str, Any] = {}
    scalar = {
        "papers": "SELECT COUNT(*) FROM papers",
        "references": "SELECT COUNT(*) FROM references_occurrence",
        "reports": "SELECT COUNT(*) FROM assessment_reports",
        "working_groups": "SELECT COUNT(*) FROM working_groups",
        "chapters": "SELECT COUNT(*) FROM chapters",
        "matched": "SELECT COUNT(*) FROM references_occurrence WHERE match_status IN ('matched_doi','matched_title')",
        "matched_doi": "SELECT COUNT(*) FROM references_occurrence WHERE match_status='matched_doi'",
        "matched_title": "SELECT COUNT(*) FROM references_occurrence WHERE match_status='matched_title'",
        "unmatched": "SELECT COUNT(*) FROM references_occurrence WHERE match_status='unmatched'",
        "unique_doi": "SELECT COUNT(*) FROM papers WHERE doi_norm IS NOT NULL AND doi_norm <> ''",
        "missing_doi": "SELECT COUNT(*) FROM references_occurrence WHERE doi_norm IS NULL OR doi_norm=''",
        "wos_records": "SELECT COUNT(*) FROM wos_records",
        "papers_wos": "SELECT COUNT(*) FROM papers WHERE verification_status='wos_matched'",
        "papers_doi_verified": "SELECT COUNT(*) FROM papers WHERE verification_status='doi_verified'",
        "papers_unresolved": "SELECT COUNT(*) FROM papers WHERE verification_status='unresolved'",
        "multi_chapter_papers": "SELECT COUNT(*) FROM papers WHERE n_chapters > 1",
        "multi_report_papers": "SELECT COUNT(*) FROM papers WHERE n_reports > 1",
        "quality_flags": "SELECT COUNT(*) FROM data_quality_flags",
        "manual_review_required": "SELECT COUNT(*) FROM references_occurrence WHERE match_status='unmatched'",
        "sources": "SELECT COUNT(*) FROM sources",
        "sources_present": "SELECT COUNT(*) FROM sources WHERE file_present = 1",
    }
    for k, sql in scalar.items():
        row = db.query_one(sql)
        tot[k] = list(row.values())[0] if row else 0
    dup = db.query_one("""SELECT COUNT(*) AS n FROM (
            SELECT title_norm FROM papers WHERE LENGTH(title_norm) > 25
            GROUP BY title_norm HAVING COUNT(*) > 1) t""")
    tot["duplicate_title_groups"] = dup["n"] if dup else 0
    tot["completion_rate"] = round(100 * tot["matched"] / max(tot["references"], 1), 2)
    tot["traceability_rate"] = round(
        100 * (tot["papers_wos"] + tot["papers_doi_verified"]) / max(tot["papers"], 1), 2)

    by_report = db.query("""
        SELECT ar.report_id AS report_id, ar.report_label,
               COUNT(r.occurrence_id)                                        AS n_references,
               SUM(CASE WHEN r.match_status IN ('matched_doi','matched_title')
                        THEN 1 ELSE 0 END)                                   AS matched,
               SUM(CASE WHEN r.match_status='unmatched' THEN 1 ELSE 0 END)   AS unmatched,
               SUM(CASE WHEN r.occurrence_id IS NOT NULL AND (r.doi_norm IS NULL OR r.doi_norm='') THEN 1 ELSE 0 END) AS missing_doi,
               COUNT(DISTINCT c.chapter_id)                                  AS n_chapters
        FROM assessment_reports ar
        LEFT JOIN chapters c ON c.report_id = ar.report_id
        LEFT JOIN references_occurrence r ON r.chapter_id = c.chapter_id
        GROUP BY ar.report_id, ar.report_label ORDER BY ar.report_id""")

    by_wg = db.query("""
        SELECT ar.report_id, wg.wg_label,
               COUNT(r.occurrence_id)                                        AS n_references,
               SUM(CASE WHEN r.match_status IN ('matched_doi','matched_title')
                        THEN 1 ELSE 0 END)                                   AS matched,
               SUM(CASE WHEN r.match_status='unmatched' THEN 1 ELSE 0 END)   AS unmatched,
               SUM(CASE WHEN r.occurrence_id IS NOT NULL AND (r.doi_norm IS NULL OR r.doi_norm='') THEN 1 ELSE 0 END) AS missing_doi
        FROM working_groups wg
        JOIN assessment_reports ar ON ar.report_id = wg.report_id
        LEFT JOIN chapters c ON c.wg_id = wg.wg_id
        LEFT JOIN references_occurrence r ON r.chapter_id = c.chapter_id
        GROUP BY ar.report_id, wg.wg_label ORDER BY ar.report_id, wg.wg_label""")

    by_year = db.query("""
        SELECT year_best AS year, COUNT(*) AS n_papers FROM papers
        WHERE year_best IS NOT NULL AND year_best <> ''
        GROUP BY year_best ORDER BY year_best""")

    quality = db.query("""
        SELECT flag_type, severity, COUNT(*) AS n FROM data_quality_flags
        GROUP BY flag_type, severity ORDER BY n DESC""")

    completeness = {
        "doi": round(100 * (1 - tot["missing_doi"] / max(tot["references"], 1)), 2),
        "title": round(100 * (db.query_one(
            "SELECT COUNT(*) AS n FROM references_occurrence WHERE parsed_title <> ''")["n"]
            / max(tot["references"], 1)), 2),
        "author": round(100 * (db.query_one(
            "SELECT COUNT(*) AS n FROM references_occurrence WHERE parsed_authors <> ''")["n"]
            / max(tot["references"], 1)), 2),
        "journal": round(100 * (db.query_one(
            "SELECT COUNT(*) AS n FROM references_occurrence WHERE parsed_journal <> ''")["n"]
            / max(tot["references"], 1)), 2),
        "year": round(100 * (db.query_one(
            "SELECT COUNT(*) AS n FROM references_occurrence WHERE parsed_year IS NOT NULL AND TRIM(parsed_year) <> ''")["n"]
            / max(tot["references"], 1)), 2),
    }
    return {"totals": tot, "by_report": by_report, "by_working_group": by_wg,
            "by_year": by_year, "quality_flags": quality, "completeness": completeness,
            "generated_at": NOW()}


# ---------------------------------------------------------------- dimensions
@app.get("/api/reports")
def reports() -> list[dict]:
    return db.query("""SELECT report_id, report_label, report_kind, first_published
                       FROM assessment_reports ORDER BY report_id""")


@app.get("/api/working-groups")
def working_groups(report_id: Optional[str] = None) -> list[dict]:
    sql = """SELECT wg.wg_id, wg.report_id, wg.wg_label,
                    COUNT(DISTINCT c.chapter_id) AS n_chapters
             FROM working_groups wg
             LEFT JOIN chapters c ON c.wg_id = wg.wg_id
             {where} GROUP BY wg.wg_id, wg.report_id, wg.wg_label
             ORDER BY wg.report_id, wg.wg_label"""
    if report_id:
        return db.query(sql.format(where="WHERE wg.report_id = :rid"), rid=report_id)
    return db.query(sql.format(where=""))


@app.get("/api/chapters")
def chapters(report_id: Optional[str] = None, wg: Optional[str] = None,
             limit: int = 500, offset: int = 0) -> list[dict]:
    where, params = [], {}
    if report_id:
        where.append("c.report_id = :rid"); params["rid"] = report_id
    if wg:
        where.append("wg.wg_label = :wg"); params["wg"] = wg
    params.update(limit=min(limit, 1000), offset=offset)
    sql = f"""
        SELECT c.chapter_id, c.report_id, wg.wg_label, c.chapter_label, c.chapter_title,
               c.extraction_status, c.extraction_method, c.ref_start_page, c.ref_end_page,
               COUNT(r.occurrence_id) AS n_references,
               SUM(CASE WHEN r.match_status IN ('matched_doi','matched_title')
                        THEN 1 ELSE 0 END) AS matched,
               SUM(CASE WHEN r.match_status='unmatched' THEN 1 ELSE 0 END) AS unmatched,
               SUM(CASE WHEN r.occurrence_id IS NOT NULL AND (r.doi_norm IS NULL OR r.doi_norm='') THEN 1 ELSE 0 END) AS missing_doi
        FROM chapters c
        JOIN working_groups wg ON wg.wg_id = c.wg_id
        LEFT JOIN references_occurrence r ON r.chapter_id = c.chapter_id
        {'WHERE ' + ' AND '.join(where) if where else ''}
        GROUP BY c.chapter_id, c.report_id, wg.wg_label, c.chapter_label, c.chapter_title,
                 c.extraction_status, c.extraction_method, c.ref_start_page, c.ref_end_page
        ORDER BY c.report_id, wg.wg_label, c.chapter_label
        LIMIT :limit OFFSET :offset"""
    return db.query(sql, **params)


# ---------------------------------------------------------------- papers
@app.get("/api/papers")
def papers(q: Optional[str] = None, doi: Optional[str] = None, year_from: Optional[int] = None,
           year_to: Optional[int] = None, report_id: Optional[str] = None,
           wg: Optional[str] = None, chapter_id: Optional[str] = None,
           status: Optional[str] = None, min_reports: Optional[int] = None,
           sort: str = "n_reports", page: int = 1, size: int = 25,
           working_group: Optional[str] = None) -> dict:
    size, offset = page_args(page, size)
    wg = working_group_filter(wg, working_group)
    clause, params = paper_filters(q=q, doi=doi, year_from=year_from, year_to=year_to,
                                   report_id=report_id, wg=wg, chapter_id=chapter_id,
                                   status=status, min_reports=min_reports)
    order = PAPER_ORDERS.get(sort, PAPER_ORDERS["n_reports"])
    total = db.query_one(f"SELECT COUNT(*) AS n FROM papers p {clause}", **params)["n"]
    params.update(limit=size, offset=offset)
    rows = db.query(f"""
        SELECT p.paper_id, p.doi_norm, p.title_best, p.year_best, p.journal_best,
               p.authors_best, p.identity_method, p.n_occurrences, p.n_chapters, p.n_reports,
               p.metadata_source, p.verification_status, p.wos_record_id
        FROM papers p {clause}
        ORDER BY {order} LIMIT :limit OFFSET :offset""", **params)
    return {"total": total, "page": page, "size": size, "items": rows}


@app.get("/api/papers/{paper_id}")
def paper_detail(paper_id: int) -> dict:
    p = db.query_one("SELECT * FROM papers WHERE paper_id = :id", id=paper_id)
    if not p:
        raise HTTPException(404, "paper not found")
    authors = db.query("""SELECT a.author_id, a.surname, a.given, a.full_name, pa.author_pos
                          FROM paper_authors pa JOIN authors a ON a.author_id = pa.author_id
                          WHERE pa.paper_id = :id ORDER BY pa.author_pos""", id=paper_id)
    wos = None
    if p.get("wos_record_id"):
        wos = db.query_one("SELECT * FROM wos_records WHERE record_id = :rid",
                           rid=p["wos_record_id"])
    return {"paper": p, "authors": authors, "wos_record": wos}


@app.get("/api/papers/{paper_id}/citations")
def paper_citations(paper_id: int) -> list[dict]:
    """Which IPCC chapters cite this paper - the core multi-generation query."""
    return db.query("""
        SELECT r.occurrence_id, r.chapter_id, c.report_id, wg.wg_label,
               c.chapter_label, c.chapter_title, r.source_page, r.match_status,
               r.raw_reference
        FROM references_occurrence r
        JOIN chapters c ON c.chapter_id = r.chapter_id
        JOIN working_groups wg ON wg.wg_id = c.wg_id
        WHERE r.paper_id = :id
        ORDER BY c.report_id, wg.wg_label, c.chapter_label""", id=paper_id)


# ---------------------------------------------------------------- references
@app.get("/api/references")
def references(chapter_id: Optional[str] = None, status: Optional[str] = None,
               report_id: Optional[str] = None, q: Optional[str] = None,
               page: int = 1, size: int = 25, wg: Optional[str] = None,
               working_group: Optional[str] = None, only_doi_less: bool = False) -> dict:
    size, offset = page_args(page, size)
    wg = working_group_filter(wg, working_group)
    clause, params = occurrence_filters(report_id=report_id, wg=wg, chapter_id=chapter_id,
                                        status=status, q=q, only_doi_less=only_doi_less)
    total = db.query_one(f"""SELECT COUNT(*) AS n FROM references_occurrence r
                             JOIN chapters c ON c.chapter_id = r.chapter_id
                             JOIN working_groups wg ON wg.wg_id = c.wg_id {clause}""",
                         **params)["n"]
    params.update(limit=size, offset=offset)
    rows = db.query(f"""
        SELECT r.occurrence_id, r.chapter_id, c.report_id, wg.wg_label, c.chapter_label,
               c.chapter_title, r.paper_id, r.source_page, r.parsed_authors, r.parsed_title,
               r.parsed_journal, r.parsed_year, r.doi_norm, r.match_status, r.match_confidence,
               r.wos_uid, r.raw_reference
        FROM references_occurrence r
        JOIN chapters c ON c.chapter_id = r.chapter_id
        JOIN working_groups wg ON wg.wg_id = c.wg_id
        {clause} ORDER BY {REFERENCE_ORDER}
        LIMIT :limit OFFSET :offset""", **params)
    return {"total": total, "page": page, "size": size, "items": rows}


@app.get("/api/unresolved")
def unresolved(report_id: Optional[str] = None, chapter_id: Optional[str] = None,
               only_doi_less: bool = False, page: int = 1, size: int = 25,
               wg: Optional[str] = None, working_group: Optional[str] = None,
               q: Optional[str] = None, status: Optional[str] = None) -> dict:
    size, offset = page_args(page, size)
    wg = working_group_filter(wg, working_group)
    clause, params = occurrence_filters(report_id=report_id, wg=wg, chapter_id=chapter_id,
                                        status=status, q=q, only_doi_less=only_doi_less,
                                        unresolved_only=True)
    total = db.query_one(f"""SELECT COUNT(*) AS n FROM references_occurrence r
                             JOIN chapters c ON c.chapter_id = r.chapter_id
                             JOIN working_groups wg ON wg.wg_id = c.wg_id {clause}""",
                         **params)["n"]
    mr = {x["occurrence_id"]: x for x in db.query("SELECT * FROM manual_review")}
    params.update(limit=size, offset=offset)
    rows = db.query(f"""
        SELECT r.occurrence_id, r.chapter_id, c.report_id, wg.wg_label, c.chapter_label,
               c.chapter_title, r.source_page, r.raw_reference, r.parsed_authors,
               r.parsed_title, r.parsed_journal, r.parsed_year, r.doi_norm, r.doi_source,
               r.notes, r.paper_id
        FROM references_occurrence r
        JOIN chapters c ON c.chapter_id = r.chapter_id
        JOIN working_groups wg ON wg.wg_id = c.wg_id
        {clause} ORDER BY {UNRESOLVED_ORDER}
        LIMIT :limit OFFSET :offset""", **params)
    for r in rows:
        m = mr.get(r["occurrence_id"])
        r["review_status"] = m["status"] if m else "not_reviewed"
        r["review_note"] = m["note"] if m else ""
    return {"total": total, "page": page, "size": size, "items": rows}


# ---------------------------------------------------------------- quality / dupes
@app.get("/api/data-quality")
def data_quality() -> dict:
    counts = db.query("""SELECT flag_type, severity, COUNT(*) AS n
                         FROM data_quality_flags GROUP BY flag_type, severity
                         ORDER BY n DESC""")
    summary = {
        "manual_review_required": db.query_one(
            "SELECT COUNT(*) AS n FROM references_occurrence WHERE match_status='unmatched'")["n"],
        "invalid_doi": sum(c["n"] for c in counts if c["flag_type"] == "invalid_doi"),
        "suspicious_year": sum(c["n"] for c in counts if c["flag_type"] == "suspicious_year"),
        "missing_doi": sum(c["n"] for c in counts if c["flag_type"] == "missing_doi"),
        "missing_title": sum(c["n"] for c in counts if c["flag_type"] == "missing_title"),
        "missing_year": sum(c["n"] for c in counts if c["flag_type"] == "missing_year"),
        "missing_journal": sum(c["n"] for c in counts if c["flag_type"] == "missing_journal"),
        "missing_author": sum(c["n"] for c in counts if c["flag_type"] == "missing_author"),
        "possible_duplicate_title": sum(c["n"] for c in counts
                                        if c["flag_type"] == "possible_duplicate_title"),
    }
    return {"summary": summary, "by_type": counts}


@app.get("/api/data-quality/records")
def data_quality_records(flag_type: str, severity: Optional[str] = None,
                         page: int = 1, size: int = 25) -> dict:
    size, offset = page_args(page, size)
    where = ["f.flag_type = :ft"]
    params: dict[str, Any] = {"ft": flag_type}
    if severity:
        where.append("f.severity = :sev"); params["sev"] = severity
    clause = "WHERE " + " AND ".join(where)
    total = db.query_one(f"SELECT COUNT(*) AS n FROM data_quality_flags f {clause}",
                         **params)["n"]
    params.update(limit=size, offset=offset)
    rows = db.query(f"""
        SELECT f.flag_id, f.entity_type, f.entity_id, f.flag_type, f.severity, f.detail,
               r.chapter_id, r.parsed_title, r.parsed_authors, r.parsed_year, r.doi_norm,
               r.raw_reference, r.match_status
        FROM data_quality_flags f
        LEFT JOIN references_occurrence r
               ON f.entity_type = 'occurrence' AND r.occurrence_id = f.entity_id
        {clause} ORDER BY f.severity, f.flag_id LIMIT :limit OFFSET :offset""", **params)
    return {"total": total, "page": page, "size": size, "items": rows}


@app.get("/api/duplicates")
def duplicates(limit: int = 50, offset: int = 0) -> dict:
    """Groups of distinct papers sharing an identical normalised title."""
    # same >=25-character threshold the flag generator uses: short generic
    # titles such as "Introduction" or "Index" are not duplicates.
    total = db.query_one("""SELECT COUNT(*) AS n FROM (
            SELECT title_norm FROM papers WHERE LENGTH(title_norm) > 25
            GROUP BY title_norm HAVING COUNT(*) > 1) t""")["n"]
    groups = db.query("""
        SELECT title_norm, COUNT(*) AS n, MIN(year_best) AS year_min, MAX(year_best) AS year_max
        FROM papers WHERE LENGTH(title_norm) > 25
        GROUP BY title_norm HAVING COUNT(*) > 1
        ORDER BY n DESC, title_norm LIMIT :limit OFFSET :offset""",
        limit=min(limit, 200), offset=offset)
    for g in groups:
        g["papers"] = db.query("""SELECT paper_id, title_best, year_best, journal_best,
                                         doi_norm, n_occurrences, verification_status
                                  FROM papers WHERE title_norm = :tn""", tn=g["title_norm"])
    return {"total_groups": total, "groups": groups}


@app.get("/api/collection-progress")
def collection_progress() -> dict:
    """Chapter extraction coverage and recorded collection assignments."""
    rows = db.query("""
        SELECT c.chapter_id, c.report_id, wg.wg_label, c.chapter_label, c.chapter_title,
               c.extraction_status, COUNT(r.occurrence_id) AS n_references,
               SUM(CASE WHEN r.match_status IN ('matched_doi','matched_title')
                        THEN 1 ELSE 0 END) AS matched,
               SUM(CASE WHEN r.match_status='unmatched' THEN 1 ELSE 0 END) AS unresolved
        FROM chapters c
        JOIN working_groups wg ON wg.wg_id = c.wg_id
        LEFT JOIN references_occurrence r ON r.chapter_id = c.chapter_id
        GROUP BY c.chapter_id, c.report_id, wg.wg_label, c.chapter_label, c.chapter_title,
                 c.extraction_status
        ORDER BY c.report_id, wg.wg_label, c.chapter_label""")
    assigned = db.query("SELECT * FROM collection_tasks")
    return {
        "researcher_assignments": assigned,
        "assignment_data_available": bool(assigned),
        "note": ("The table shows extraction coverage for each chapter. "
                 "Collector assignments are available when recorded in collection_tasks.")
                if assigned else
                "The table shows extraction coverage for each chapter. Collector assignments have not been recorded.",
        "chapters": rows,
    }


@app.get("/api/changelog")
def changelog(limit: int = 100) -> list[dict]:
    return db.query("""SELECT * FROM change_log ORDER BY change_id DESC LIMIT :l""",
                    l=min(limit, 500))


# ---------------------------------------------------------------- write surface
class ReferencePatch(BaseModel):
    parsed_title: Optional[str] = None
    parsed_authors: Optional[str] = None
    parsed_journal: Optional[str] = None
    parsed_year: Optional[str] = None
    doi_norm: Optional[str] = None
    notes: Optional[str] = None
    reason: str = Field(min_length=3, description="why the change is made")


class FlagIn(BaseModel):
    flag_type: str
    severity: str = "medium"
    detail: str = ""
    reason: str = Field(min_length=3)


class ResolveIn(BaseModel):
    status: str = "resolved"
    assigned_to: Optional[str] = None
    note: str = ""
    reason: str = Field(min_length=3)


@app.patch("/api/references/{occurrence_id}")
def patch_reference(occurrence_id: str, body: ReferencePatch) -> dict:
    require_writes()
    cur = db.query_one("SELECT * FROM references_occurrence WHERE occurrence_id = :id",
                       id=occurrence_id)
    if not cur:
        raise HTTPException(404, "occurrence not found")
    allowed = {"parsed_title", "parsed_authors", "parsed_journal", "parsed_year",
               "doi_norm", "notes"}
    updates = {k: v for k, v in body.model_dump(exclude={"reason"}).items()
               if k in allowed and v is not None}
    if not updates:
        raise HTTPException(400, "no updatable field supplied")
    for k, v in updates.items():                      # audit BEFORE writing
        audit("occurrence", occurrence_id, k, cur.get(k), v, body.reason)
    sets = ", ".join(f"{k} = :{k}" for k in updates)
    db.execute(f"UPDATE references_occurrence SET {sets} WHERE occurrence_id = :oid",
               oid=occurrence_id, **updates)
    return {"updated": occurrence_id, "fields": list(updates), "audited": True}


@app.post("/api/references/{occurrence_id}/flag")
def flag_reference(occurrence_id: str, body: FlagIn) -> dict:
    require_writes()
    if body.severity not in ("high", "medium", "low"):
        raise HTTPException(400, "severity must be high|medium|low")
    if not db.query_one("SELECT 1 AS x FROM references_occurrence WHERE occurrence_id = :id",
                        id=occurrence_id):
        raise HTTPException(404, "occurrence not found")
    db.execute("""INSERT INTO data_quality_flags
                  (entity_type, entity_id, flag_type, severity, detail, detected_at)
                  VALUES ('occurrence', :eid, :ft, :sev, :d, :at)""",
               eid=occurrence_id, ft=body.flag_type, sev=body.severity,
               d=body.detail, at=NOW())
    audit("occurrence", occurrence_id, "flag", None, body.flag_type, body.reason)
    return {"flagged": occurrence_id, "flag_type": body.flag_type}


@app.post("/api/references/{occurrence_id}/resolve")
def resolve_reference(occurrence_id: str, body: ResolveIn) -> dict:
    require_writes()
    if body.status not in ("open", "in_progress", "resolved", "wont_fix"):
        raise HTTPException(400, "invalid status")
    if not db.query_one("SELECT 1 AS x FROM references_occurrence WHERE occurrence_id = :id",
                        id=occurrence_id):
        raise HTTPException(404, "occurrence not found")
    existing = db.query_one("SELECT * FROM manual_review WHERE occurrence_id = :id",
                            id=occurrence_id)
    if existing:
        audit("manual_review", occurrence_id, "status", existing["status"], body.status,
              body.reason)
        db.execute("""UPDATE manual_review SET status = :st, assigned_to = :a,
                      note = :n, updated_at = :at WHERE occurrence_id = :id""",
                   st=body.status, a=body.assigned_to, n=body.note, at=NOW(),
                   id=occurrence_id)
    else:
        audit("manual_review", occurrence_id, "status", None, body.status, body.reason)
        db.execute("""INSERT INTO manual_review
                      (occurrence_id, status, assigned_to, reason, note,
                       created_at, updated_at)
                      VALUES (:id, :st, :a, :r, :n, :at, :at)""",
                   id=occurrence_id, st=body.status, a=body.assigned_to,
                   r=body.reason, n=body.note, at=NOW())
    return {"occurrence_id": occurrence_id, "review_status": body.status, "audited": True}


# ---------------------------------------------------------------- dashboard
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(str(FRONTEND_DIR / "index.html"))


# =========================================================================== #
# Semantic layer / BI
# =========================================================================== #
import sys as _sys
_sys.path.insert(0, str(REPO_ROOT))
from analytics import metrics as _metrics                      # noqa: E402
from analytics import networks as _networks                    # noqa: E402
import ai as _ai                                               # noqa: E402
import exports as _exports                                     # noqa: E402
from config import AI_ENABLED, SQL_ROW_LIMIT, SQL_TIMEOUT_SECONDS  # noqa: E402


@app.get("/api/metrics")
def metric_catalog() -> dict:
    """The explicit definition of every metric and dimension (the semantic layer)."""
    return {"metrics": _metrics.metric_catalog(), "dimensions": _metrics.dimension_catalog(),
            "note": ("Every KPI in the UI is produced by one of these definitions. "
                     "The SQL behind each is returned by /api/bi/query.")}


@app.get("/api/bi/query")
def bi_query(metric: str, dimension: Optional[str] = None,
             report_id: Optional[str] = None, working_group: Optional[str] = None,
             chapter_id: Optional[str] = None, match_status: Optional[str] = None,
             year_from: Optional[int] = None, year_to: Optional[int] = None,
             limit: int = 200) -> dict:
    """BI explorer: one metric, optionally grouped by one dimension, with filters."""
    filters = {"report": report_id or "", "working_group": working_group or "",
               "chapter": chapter_id or "", "match_status": match_status or "",
               "year_from": year_from or "", "year_to": year_to or ""}
    try:
        sql = _metrics.build_query(metric, dimension, filters, limit)
    except ValueError as e:
        raise HTTPException(400, str(e))
    try:
        rows = db.query(sql)
    except Exception as e:
        raise HTTPException(400, f"query failed: {e}")
    m = _metrics.METRICS[metric]
    # Select the chart type from the grouping dimension.
    if dimension is None:
        chart = "kpi"
    elif dimension in ("publication_year", "decade"):
        chart = "line"
    elif dimension in ("journal", "chapter", "chapter_label"):
        chart = "hbar"
    else:
        chart = "bar"
    return {"metric": metric, "dimension": dimension, "filters": filters,
            "sql": sql, "rows": rows, "chart": chart,
            "label": m.get("label", {}), "unit": m.get("unit"),
            "description": m.get("description", {})}


# =========================================================================== #
# Deterministic analysis + insights
# =========================================================================== #
@app.get("/api/analysis")
def analysis_index() -> list[dict]:
    """Stored analysis results, newest run first. Reproducible: each row keeps
    the SQL and parameters that produced it."""
    return db.query("""
        SELECT analysis_id, run_id, analysis_type, parameters, created_at, software_version
        FROM analysis_results ORDER BY analysis_id DESC""")


@app.get("/api/analysis/run")
def analysis_runs() -> list[dict]:
    return db.query("SELECT * FROM analysis_runs ORDER BY run_id DESC LIMIT 50")


@app.get("/api/analysis/{analysis_type}")
def analysis_detail(analysis_type: str) -> dict:
    row = db.query_one("""SELECT * FROM analysis_results WHERE analysis_type = :t
                          ORDER BY analysis_id DESC LIMIT 1""", t=analysis_type)
    if not row:
        raise HTTPException(404, f"no stored result for {analysis_type}")
    import json as _json
    row["output"] = _json.loads(row["output"])
    row["parameters"] = _json.loads(row["parameters"] or "{}")
    return row


@app.get("/api/insights")
def insights() -> list[dict]:
    """Automated insight feed. Every item carries the evidence that produced it
    and a link to the page where the underlying records can be inspected."""
    import json as _json
    rows = db.query("SELECT * FROM insights ORDER BY "
                    "CASE severity WHEN 'warning' THEN 0 WHEN 'critical' THEN 0 ELSE 1 END, insight_id")
    out = []
    for r in rows:
        out.append({"insight_id": r["insight_id"], "category": r["category"],
                    "severity": r["severity"],
                    "title": r["title_en"],
                    "body": r["body_en"],
                    "evidence": _json.loads(r["evidence"] or "{}"),
                    "link": r["link"], "created_at": r["created_at"]})
    return out


# =========================================================================== #
# Networks
# =========================================================================== #
@app.get("/api/networks")
def network_kinds() -> dict:
    return {"kinds": [{"key": k, "description": v} for k, v in _networks.KINDS.items()],
            "defaults": _networks.DEFAULT_MIN_WEIGHT}


@app.get("/api/networks/{kind}")
def network(kind: str, min_weight: Optional[float] = None,
            limit: Annotated[int, Query(ge=1, le=2000)] = 300) -> dict:
    try:
        database = sqlite_database_path()
    except ValueError as error:
        raise HTTPException(501, str(error))
    try:
        return _networks.build(kind, db=database, min_weight=min_weight, limit=limit)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/networks/{kind}/export")
def network_export(kind: str, fmt: str = "graphml", min_weight: Optional[float] = None,
                   limit: Annotated[int, Query(ge=1, le=2000)] = 300):
    """Export the same filtered, capped graph used by the network view."""
    from analytics.graph_analysis import graph_download

    if fmt not in {"graphml", "gexf", "csv"}:
        raise HTTPException(400, "fmt must be graphml, gexf, or csv")
    data = network(kind=kind, min_weight=min_weight, limit=limit)
    content, mime, extension = graph_download(data, fmt)
    return Response(content=content, media_type=mime, headers={
        "Content-Disposition": f'attachment; filename="ipcc_network_{kind}.{extension}"',
        "X-Row-Count": str(data["edge_count"]),
        "X-Total-Count": str(data["parameters"]["eligible_edge_count"]),
        "X-Export-Limit": str(limit),
        "X-Export-Truncated": str(data["parameters"]["truncated"]).lower(),
    })


@app.get("/api/data-quality/audit")
def quality_audit(sample_limit: Annotated[int, Query(ge=1, le=50)] = 10) -> dict:
    """Recalculate bibliographic and relationship checks from the working database."""
    from analytics.research import quality_audit as run_audit

    try:
        database = sqlite_database_path()
    except ValueError as error:
        raise HTTPException(501, str(error))
    try:
        return run_audit(db=database, sample_limit=sample_limit)
    except ValueError as error:
        raise HTTPException(400, str(error))


@app.get("/api/research/compare")
def research_compare(report_a: str, report_b: str,
                     top: Annotated[int, Query(ge=1, le=100)] = 20) -> dict:
    """Compare distinct works cited by two report cohorts."""
    from analytics.research import compare_reports

    try:
        database = sqlite_database_path()
    except ValueError as error:
        raise HTTPException(501, str(error))
    try:
        return compare_reports(report_a, report_b, db=database, top=top)
    except ValueError as error:
        raise HTTPException(400, str(error))


# =========================================================================== #
# AI analysis (optional; degrades gracefully with no key)
# =========================================================================== #
class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    lang: str = "en"  # Accepted for compatibility; responses use English.


@app.get("/api/ai/status")
def ai_status() -> dict:
    return _ai.status()


@app.post("/api/ai/ask")
def ai_ask(body: AskIn) -> dict:
    """Read-only natural-language analytics.

    The model proposes SQL; the guard validates it; it runs on a read-only
    connection; only then is a summary produced.  The SQL is always returned."""
    try:
        db_path = sqlite_database_path()
    except ValueError as error:
        return {"configured": _ai.is_configured(),
                "error": str(error),
                "sql": None, "rows": [], "answer": None}
    return _ai.answer(body.question, "en", db_path=db_path)


# =========================================================================== #
# Export
# =========================================================================== #
@app.get("/api/export/{kind}.{fmt}")
def export(kind: str, fmt: str, report_id: Optional[str] = None,
           working_group: Optional[str] = None, chapter_id: Optional[str] = None,
           status: Optional[str] = None, q: Optional[str] = None,
           doi: Optional[str] = None, year_from: Optional[int] = None,
           year_to: Optional[int] = None, min_reports: Optional[int] = None,
           sort: str = "n_reports", only_doi_less: bool = False,
           wg: Optional[str] = None, limit: int = Query(5000, ge=1, le=100000)):
    """Export the matching rows in result order, recording limits and provenance."""
    from fastapi.responses import Response
    fmt = fmt.lower()
    if fmt not in ("csv", "json", "xlsx", "bibtex", "ris"):
        raise HTTPException(400, "format must be csv|json|xlsx|bibtex|ris")
    group = working_group_filter(wg, working_group)
    if kind == "papers":
        clause, params = paper_filters(q=q, doi=doi, year_from=year_from, year_to=year_to,
                                       report_id=report_id, wg=group, chapter_id=chapter_id,
                                       status=status, min_reports=min_reports)
        selected_sort = sort if sort in PAPER_ORDERS else "n_reports"
        order = PAPER_ORDERS[selected_sort]
        source = "FROM papers p"
        columns = """p.paper_id, p.title_best, p.authors_best, p.year_best,
                     p.journal_best, p.doi_norm, p.n_reports, p.n_chapters,
                     p.n_occurrences, p.verification_status, p.metadata_source"""
    elif kind in ("references", "unresolved"):
        clause, params = occurrence_filters(report_id=report_id, wg=group, chapter_id=chapter_id,
                                            status=status, q=q, only_doi_less=only_doi_less,
                                            unresolved_only=kind == "unresolved")
        selected_sort = "chapter_page" if kind == "references" else "report_chapter_page"
        order = REFERENCE_ORDER if kind == "references" else UNRESOLVED_ORDER
        source = """FROM references_occurrence r
                    JOIN chapters c ON c.chapter_id = r.chapter_id
                    JOIN working_groups wg ON wg.wg_id = c.wg_id"""
        columns = """r.occurrence_id, r.chapter_id, c.report_id, wg.wg_label,
                     c.chapter_label, c.chapter_title, r.source_page,
                     r.parsed_title AS title_best, r.parsed_authors AS authors_best,
                     r.parsed_year AS year_best, r.parsed_journal AS journal_best,
                     r.doi_norm, r.match_status, r.raw_reference"""
    else:
        raise HTTPException(400, "kind must be papers|references|unresolved")
    total = db.query_one(f"SELECT COUNT(*) AS n {source} {clause}", **params)["n"]
    rows = db.query(f"SELECT {columns} {source} {clause} ORDER BY {order} LIMIT :limit",
                    **params, limit=limit)
    filters = {"q": q, "report_id": report_id, "working_group": group,
               "chapter_id": chapter_id, "status": status}
    if kind == "papers":
        filters.update(doi=normalized_doi(doi), year_from=year_from,
                       year_to=year_to, min_reports=min_reports)
    elif only_doi_less:
        filters["only_doi_less"] = True
    filters = {key: value for key, value in filters.items() if value is not None and value != ""}
    filters["sort"] = selected_sort
    desc = "; ".join(f"{key}={value}" for key, value in filters.items())
    metadata = {"total_matches": total, "limit": limit, "truncated": total > len(rows)}
    if fmt == "csv":
        data, mime, ext = _exports.to_csv(rows, filters=desc, metadata=metadata), "text/csv", "csv"
    elif fmt == "json":
        data, mime, ext = _exports.to_json(rows, filters=desc, metadata=metadata), "application/json", "json"
    elif fmt == "xlsx":
        data, mime, ext = _exports.to_xlsx(rows, filters=desc, metadata=metadata), \
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    elif fmt == "bibtex":
        data, mime, ext = _exports.to_bibtex(rows, filters=desc, metadata=metadata), "application/x-bibtex", "bib"
    else:
        data, mime, ext = _exports.to_ris(rows, filters=desc, metadata=metadata), "application/x-research-info-systems", "ris"
    fname = f"ipcc_{kind}.{ext}"
    return Response(content=data, media_type=mime,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"',
                             "X-Row-Count": str(len(rows)), "X-Total-Count": str(total),
                             "X-Export-Limit": str(limit),
                             "X-Export-Truncated": str(total > len(rows)).lower()})
