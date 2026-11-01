"""Read-only data-quality checks and comparisons of IPCC report literature."""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import unquote, urlsplit

from backend.config import sqlite_database_path

SOFTWARE_VERSION = "research-1.1.0"
YEAR_MIN = 1000
TIME_LIMIT_SECONDS = 30
MISSING_JOURNAL = "Missing journal"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_limit(value: int, label: str, maximum: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        raise ValueError(f"{label} must be an integer from 1 to {maximum}")


@lru_cache(maxsize=32768)
def normalize_doi(value: str | None) -> str | None:
    """Normalize bare identifiers and resolver URLs while preserving DOI suffixes."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"^doi:\s*", "", text, flags=re.I)
    if re.match(r"^https?://", text, flags=re.I):
        try:
            url = urlsplit(text)
        except ValueError:
            return text.lower()
        if url.hostname and url.hostname.lower() in {"doi.org", "dx.doi.org", "www.doi.org"}:
            text = unquote(url.path.lstrip("/"))
    return text.strip().lower() or None


def valid_doi(value: str | None) -> bool:
    doi = normalize_doi(value)
    return bool(doi and re.fullmatch(r"10\.[0-9]{4,9}/\S+", doi)
                and not any(ord(character) < 33 for character in doi))


def _publication_year(value: str | None, current_year: int) -> int | None:
    text = str(value).strip() if value is not None else ""
    if not re.fullmatch(r"[0-9]{4}", text):
        return None
    year = int(text)
    return year if YEAR_MIN <= year <= current_year + 1 else None


def source_publication_year(value: str | None) -> int | None:
    """Read an unambiguous four-digit year from the source's date string."""
    years = re.findall(r"\b(?:1[0-9]{3}|20[0-9]{2})\b", str(value or ""))
    return int(years[0]) if len(years) == 1 else None


def first_reference_year(value: str | None) -> int | None:
    """Return the first plausible year token as a review cue, not an adjudicated date."""
    match = re.search(r"\b(1[5-9][0-9]{2}|20[0-9]{2})[a-z]?\b", str(value or ""), re.I)
    return int(match.group(1)) if match else None


def _percent(count: int, denominator: int) -> float | None:
    return round(100.0 * count / denominator, 4) if denominator else None


@contextmanager
def _connection(database: Path | str | None):
    path = sqlite_database_path() if database is None else Path(database).expanduser().resolve()
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    deadline = time.monotonic() + TIME_LIMIT_SECONDS
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
    current_year = datetime.now(timezone.utc).year
    connection.create_function("ipcc_normalize_doi", 1, normalize_doi, deterministic=True)
    connection.create_function("ipcc_valid_doi", 1, valid_doi, deterministic=True)
    connection.create_function("ipcc_valid_year", 1,
                               lambda value: _publication_year(value, current_year) is not None,
                               deterministic=True)
    connection.create_function("ipcc_publication_year", 1,
                               lambda value: _publication_year(value, current_year), deterministic=True)
    connection.create_function("ipcc_source_year", 1, source_publication_year, deterministic=True)
    connection.create_function("ipcc_reference_year", 1, first_reference_year, deterministic=True)
    connection.execute("BEGIN")
    try:
        yield connection, path, current_year
    except sqlite3.OperationalError as error:
        if "interrupted" in str(error).lower():
            raise ValueError(f"Research query exceeded {TIME_LIMIT_SECONDS} seconds") from error
        raise
    finally:
        connection.close()


def _rows(connection: sqlite3.Connection, sql: str, parameters: dict | None = None) -> list[dict]:
    return [dict(row) for row in connection.execute(sql, parameters or {})]


def _build_metadata(connection: sqlite3.Connection) -> dict:
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='build_info'").fetchone()
    if not exists:
        return {}
    return {row["key"]: row["value"] for row in connection.execute(
        "SELECT key,value FROM build_info WHERE key IN ('build_timestamp','source','builder')")}


def quality_audit(db: Path | str | None = None, sample_limit: int = 10) -> dict:
    """Count live rule matches, with report-level completeness and bounded examples."""
    _validate_limit(sample_limit, "sample_limit", 100)
    with _connection(db) as (connection, path, current_year):
        populations = {}
        for table in ("references_occurrence", "papers", "chapters", "assessment_reports",
                      "working_groups", "paper_authors", "authors", "sources"):
            populations[table] = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        checks = []

        def add(identifier, label, population_sql, denominator, grain, severity, definition,
                order="entity_id", parameters=None):
            parameters = parameters or {}
            count_sql = f"SELECT COUNT(*) AS count FROM ({population_sql}) audit_rows"
            sample_sql = f"SELECT * FROM ({population_sql}) audit_rows ORDER BY {order} LIMIT :sample_limit"
            count = connection.execute(count_sql, parameters).fetchone()[0]
            samples = _rows(connection, sample_sql, {**parameters, "sample_limit": sample_limit})
            checks.append({
                "id": identifier, "label": label, "count": count, "denominator": denominator,
                "rate": _percent(count, denominator), "rate_unit": "%", "grain": grain,
                "severity": severity, "status": "attention" if count else "pass",
                "definition": definition, "sql": count_sql, "sample_sql": sample_sql,
                "parameters": parameters, "samples": samples,
            })

        fields = {"title": "parsed_title", "authors": "parsed_authors",
                  "journal": "parsed_journal", "year": "parsed_year", "doi": "doi_norm"}
        for field, column in fields.items():
            add(f"missing_{field}", f"Missing {field}",
                f"SELECT occurrence_id AS entity_id, chapter_id, paper_id, {column} AS value "
                f"FROM references_occurrence WHERE TRIM(COALESCE({column},'')) = ''",
                populations["references_occurrence"], "reference_occurrence",
                "low" if field in {"journal", "doi"} else "medium",
                f"Reference occurrences whose {column} is NULL, empty or whitespace-only.")

        add("invalid_year", "Publication years requiring review",
            "SELECT occurrence_id AS entity_id,chapter_id,paper_id,parsed_year AS value "
            "FROM references_occurrence WHERE TRIM(COALESCE(parsed_year,'')) <> '' "
            "AND NOT ipcc_valid_year(parsed_year)", populations["references_occurrence"],
            "reference_occurrence", "medium",
            f"Nonblank parsed years outside {YEAR_MIN}–{current_year + 1}, or not four decimal digits.")
        add("invalid_doi", "DOI identifiers with malformed syntax",
            "SELECT occurrence_id AS entity_id,chapter_id,paper_id,doi_norm AS value,"
            "ipcc_normalize_doi(doi_norm) AS normalized_doi FROM references_occurrence "
            "WHERE TRIM(COALESCE(doi_norm,'')) <> '' AND NOT ipcc_valid_doi(doi_norm)",
            populations["references_occurrence"], "reference_occurrence", "medium",
            "Nonblank identifiers that fail DOI syntax after resolver-URL and doi: prefix normalization.")

        source_columns = {row["name"] for row in connection.execute("PRAGMA table_info(sources)")}
        reference_columns = {row["name"] for row in connection.execute("PRAGMA table_info(references_occurrence)")}
        date_rows = """
            SELECT r.occurrence_id AS entity_id,r.paper_id,r.chapter_id,c.report_id,
                   p.title_best,p.year_best,r.match_status,s.publication_year AS source_date,
                   ipcc_publication_year(p.year_best) AS preferred_year,
                   ipcc_source_year(s.publication_year) AS source_year
            FROM references_occurrence r JOIN papers p ON p.paper_id=r.paper_id
            JOIN chapters c ON c.chapter_id=r.chapter_id
            JOIN sources s ON s.source_id=c.source_id
            WHERE ipcc_publication_year(p.year_best) IS NOT NULL
              AND ipcc_source_year(s.publication_year) IS NOT NULL
        """
        if "publication_year" in source_columns:
            dated_denominator = connection.execute(f"SELECT COUNT(*) FROM ({date_rows}) dated").fetchone()[0]
            add("publication_after_source", "Preferred publication year later than the source date",
                f"SELECT * FROM ({date_rows}) dated WHERE preferred_year > source_year",
                dated_denominator, "reference_occurrence", "high",
                "Linked paper year_best exceeds the chapter source's unambiguous publication year. "
                "Review the printed citation and metadata link; online-first and edition dates can also differ.")
        if "raw_reference" in reference_columns:
            reference_dates = """
                SELECT r.occurrence_id AS entity_id,r.paper_id,r.chapter_id,p.title_best,
                       p.year_best,r.match_status,ipcc_publication_year(p.year_best) AS preferred_year,
                       ipcc_reference_year(r.raw_reference) AS printed_year_token,
                       SUBSTR(r.raw_reference,1,600) AS reference_excerpt
                FROM references_occurrence r JOIN papers p ON p.paper_id=r.paper_id
                WHERE ipcc_publication_year(p.year_best) IS NOT NULL
                  AND ipcc_reference_year(r.raw_reference) IS NOT NULL
            """
            raw_dated_denominator = connection.execute(f"SELECT COUNT(*) FROM ({reference_dates}) dated").fetchone()[0]
            add("reference_year_disagreement", "Printed year token differs from preferred metadata",
                f"SELECT * FROM ({reference_dates}) dated WHERE preferred_year <> printed_year_token",
                raw_dated_denominator, "reference_occurrence", "medium",
                "The first 1500–2099 year token in the printed reference differs from paper.year_best. "
                "This is a review candidate: references can contain several dates, editions and year-like page numbers.")

        duplicate_sql = """
            SELECT ipcc_normalize_doi(doi_norm) AS entity_id,COUNT(*) AS paper_count,
                   MIN(paper_id) AS first_paper_id,MAX(paper_id) AS last_paper_id
            FROM papers WHERE ipcc_valid_doi(doi_norm)
            GROUP BY ipcc_normalize_doi(doi_norm) HAVING COUNT(*) > 1"""
        doi_groups = connection.execute("SELECT COUNT(DISTINCT ipcc_normalize_doi(doi_norm)) "
                                        "FROM papers WHERE ipcc_valid_doi(doi_norm)").fetchone()[0]
        add("duplicate_normalized_doi", "DOIs attached to multiple paper records", duplicate_sql,
            doi_groups, "normalized_doi_group", "high",
            "Groups of structurally valid, normalized DOI identifiers attached to more than one paper record.")

        orphan_rules = [
            ("orphan_occurrence_paper", "References without an existing paper", "references_occurrence", "r",
             "occurrence_id", "paper_id", "papers", "paper_id", False),
            ("orphan_occurrence_chapter", "References without an existing chapter", "references_occurrence", "r",
             "occurrence_id", "chapter_id", "chapters", "chapter_id", False),
            ("orphan_chapter_report", "Chapters without an existing report", "chapters", "c",
             "chapter_id", "report_id", "assessment_reports", "report_id", False),
            ("orphan_chapter_working_group", "Chapters without an existing working group", "chapters", "c",
             "chapter_id", "wg_id", "working_groups", "wg_id", False),
            ("orphan_working_group_report", "Working groups without an existing report", "working_groups", "w",
             "wg_id", "report_id", "assessment_reports", "report_id", False),
            ("orphan_chapter_source", "Chapters pointing to an absent source record", "chapters", "c",
             "chapter_id", "source_id", "sources", "source_id", True),
            ("orphan_paper_author_paper", "Author links without an existing paper", "paper_authors", "pa",
             "paper_id", "paper_id", "papers", "paper_id", False),
            ("orphan_paper_author_author", "Author links without an existing author", "paper_authors", "pa",
             "paper_id", "author_id", "authors", "author_id", False),
        ]
        for identifier, label, table, alias, key, foreign_key, parent, parent_key, optional in orphan_rules:
            condition = f"NOT EXISTS (SELECT 1 FROM {parent} p WHERE p.{parent_key}={alias}.{foreign_key})"
            if optional:
                condition = f"TRIM(COALESCE({alias}.{foreign_key},'')) <> '' AND " + condition
            extra = f",{alias}.author_id" if table == "paper_authors" else ""
            population_sql = (f"SELECT {alias}.{key} AS entity_id,{alias}.{foreign_key} AS linked_id{extra} "
                              f"FROM {table} {alias} WHERE {condition}")
            add(identifier, label, population_sql, populations[table], table, "high",
                f"Rows in {table} whose {foreign_key} has no parent in {parent}."
                + (" Empty optional source links are excluded." if optional else ""),
                order="entity_id,author_id" if table == "paper_authors" else "entity_id")

        actual_counts = """
            WITH actual AS (
                SELECT p.paper_id,COUNT(r.occurrence_id) AS actual_occurrences,
                       COUNT(DISTINCT c.chapter_id) AS actual_chapters,
                       COUNT(DISTINCT ar.report_id) AS actual_reports
                FROM papers p LEFT JOIN references_occurrence r ON r.paper_id=p.paper_id
                LEFT JOIN chapters c ON c.chapter_id=r.chapter_id
                LEFT JOIN assessment_reports ar ON ar.report_id=c.report_id
                GROUP BY p.paper_id
            )"""
        # One aggregate query supplies all three checks without repeated corpus joins.
        counter_query = actual_counts + """
            SELECT p.paper_id,p.title_best,p.n_occurrences,p.n_chapters,p.n_reports,
                   a.actual_occurrences,a.actual_chapters,a.actual_reports,
                   p.n_occurrences IS NOT a.actual_occurrences AS occurrence_mismatch,
                   p.n_chapters IS NOT a.actual_chapters AS chapter_mismatch,
                   p.n_reports IS NOT a.actual_reports AS report_mismatch
            FROM papers p JOIN actual a ON a.paper_id=p.paper_id
            WHERE p.n_occurrences IS NOT a.actual_occurrences
               OR p.n_chapters IS NOT a.actual_chapters OR p.n_reports IS NOT a.actual_reports
            ORDER BY p.paper_id"""
        counter_rows = _rows(connection, counter_query)
        for label, stored, actual, flag in (
            ("occurrence", "n_occurrences", "actual_occurrences", "occurrence_mismatch"),
            ("chapter", "n_chapters", "actual_chapters", "chapter_mismatch"),
            ("report", "n_reports", "actual_reports", "report_mismatch"),
        ):
            matches = [row for row in counter_rows if row[flag]]
            count_sql = actual_counts + f" SELECT COUNT(*) AS count FROM papers p " \
                f"JOIN actual a ON a.paper_id=p.paper_id WHERE p.{stored} IS NOT a.{actual}"
            sample_sql = actual_counts + f" SELECT p.paper_id AS entity_id,p.title_best," \
                f"p.{stored} AS stored,a.{actual} AS actual FROM papers p " \
                f"JOIN actual a ON a.paper_id=p.paper_id WHERE p.{stored} IS NOT a.{actual} " \
                "ORDER BY p.paper_id LIMIT :sample_limit"
            checks.append({
                "id": f"paper_{label}_count_mismatch", "label": f"Stored paper {label} counts that differ from citations",
                "count": len(matches), "denominator": populations["papers"],
                "rate": _percent(len(matches), populations["papers"]), "rate_unit": "%",
                "grain": "paper", "severity": "high", "status": "attention" if matches else "pass",
                "definition": f"Stored {stored} differs from {actual} counted from the current citation links.",
                "sql": count_sql, "sample_sql": sample_sql, "parameters": {},
                "samples": [{"entity_id": row["paper_id"], "title_best": row["title_best"],
                             "stored": row[stored], "actual": row[actual]} for row in matches[:sample_limit]],
            })

        present_expressions = ",".join(
            f"SUM(CASE WHEN r.occurrence_id IS NOT NULL AND TRIM(COALESCE(r.{column},'')) <> '' "
            f"THEN 1 ELSE 0 END) AS {field}_present" for field, column in fields.items())
        report_query = f"""
            SELECT ar.report_id,ar.report_label,COUNT(r.occurrence_id) AS reference_occurrences,
                   {present_expressions}
            FROM assessment_reports ar LEFT JOIN chapters c ON c.report_id=ar.report_id
            LEFT JOIN references_occurrence r ON r.chapter_id=c.chapter_id
            GROUP BY ar.report_id,ar.report_label ORDER BY ar.report_id"""
        by_report = []
        for row in _rows(connection, report_query):
            denominator = row["reference_occurrences"]
            by_report.append({"report_id": row["report_id"], "report_label": row["report_label"],
                              "reference_occurrences": denominator,
                              "fields": {field: {"present": row[f"{field}_present"],
                                                 "missing": denominator - row[f"{field}_present"],
                                                 "denominator": denominator,
                                                 "completeness": _percent(row[f"{field}_present"], denominator)}
                                         for field in fields}})
        unassigned = populations["references_occurrence"] - sum(row["reference_occurrences"] for row in by_report)
        return {
            "generated_at": _now(), "source": path.name, "checks": checks, "by_report": by_report,
            "provenance": {
                "software_version": SOFTWARE_VERSION, "grain": "Reference occurrences unless a check declares another grain.",
                "definitions": {
                    "rate": "100 × rule-matching records / declared denominator; null for an empty population.",
                    "completeness": "100 × nonblank field values / reference occurrences; validity is checked separately.",
                    "doi": "Trim whitespace, remove doi: prefixes, decode recognized resolver URL paths, then lowercase. DOI syntax requires 10., 4–9 digits, a slash and a nonempty suffix.",
                    "paper_counters": "Occurrence rows, distinct existing chapter IDs and distinct existing report IDs linked to each paper.",
                    "severity": "Review priority of the rule; missing bibliographic fields can reflect the source publication type.",
                },
                "queries": {"report_completeness": report_query, "paper_counters": counter_query},
                "sql_functions": {"ipcc_normalize_doi": "analytics.research.normalize_doi",
                                  "ipcc_valid_doi": "analytics.research.valid_doi",
                                  "ipcc_valid_year": f"Four decimal digits from {YEAR_MIN} through {current_year + 1}",
                                  "ipcc_publication_year": f"Parsed integer year from {YEAR_MIN} through {current_year + 1}, otherwise NULL",
                                  "ipcc_source_year": "analytics.research.source_publication_year",
                                  "ipcc_reference_year": "analytics.research.first_reference_year"},
                "parameters": {"sample_limit": sample_limit, "current_year": current_year},
                "table_counts": populations, "unassigned_occurrences": unassigned,
                "build_info": _build_metadata(connection),
            },
        }


COHORT_QUERY = """
    SELECT p.paper_id,p.title_best,p.authors_best,p.year_best,p.journal_best,p.doi_norm,
           p.n_reports,p.n_chapters,p.n_occurrences,
           MAX(CASE WHEN c.report_id=:report_a THEN 1 ELSE 0 END) AS in_a,
           MAX(CASE WHEN c.report_id=:report_b THEN 1 ELSE 0 END) AS in_b
    FROM references_occurrence r JOIN chapters c ON c.chapter_id=r.chapter_id
    JOIN papers p ON p.paper_id=r.paper_id
    WHERE c.report_id IN (:report_a,:report_b)
    GROUP BY p.paper_id ORDER BY p.paper_id"""

REPORT_SUMMARY_QUERY = """
    SELECT ar.report_id,ar.report_label,COUNT(r.occurrence_id) AS reference_occurrences,
           COUNT(DISTINCT p.paper_id) AS unique_papers,COUNT(DISTINCT c.chapter_id) AS chapters,
           SUM(CASE WHEN r.match_status IN ('matched_doi','matched_title') THEN 1 ELSE 0 END) AS matched_occurrences
    FROM assessment_reports ar LEFT JOIN chapters c ON c.report_id=ar.report_id
    LEFT JOIN references_occurrence r ON r.chapter_id=c.chapter_id
    LEFT JOIN papers p ON p.paper_id=r.paper_id
    WHERE ar.report_id IN (:report_a,:report_b)
    GROUP BY ar.report_id,ar.report_label ORDER BY ar.report_id"""


def compare_reports(report_a: str, report_b: str, db: Path | str | None = None, top: int = 20) -> dict:
    """Compare distinct cited works, with complete year and journal distributions."""
    _validate_limit(top, "top", 200)
    if not isinstance(report_a, str) or not isinstance(report_b, str) or not report_a.strip() or not report_b.strip():
        raise ValueError("Two report IDs are required")
    report_a, report_b = report_a.strip(), report_b.strip()
    parameters = {"report_a": report_a, "report_b": report_b}
    with _connection(db) as (connection, path, current_year):
        summaries = {row["report_id"]: row for row in _rows(connection, REPORT_SUMMARY_QUERY, parameters)}
        for report in (report_a, report_b):
            if report not in summaries:
                raise ValueError(f"Unknown report: {report}")
        cohort = _rows(connection, COHORT_QUERY, parameters)
        a = {row["paper_id"] for row in cohort if row["in_a"]}
        b = {row["paper_id"] for row in cohort if row["in_b"]}
        shared, union = a & b, a | b
        summary_a, summary_b = dict(summaries[report_a]), dict(summaries[report_b])
        for summary in (summary_a, summary_b):
            summary["match_rate"] = _percent(summary["matched_occurrences"], summary["reference_occurrences"])
        years_a, years_b, years_shared = Counter(), Counter(), Counter()
        journals_a, journals_b = Counter(), Counter()
        year_missing = {"a": 0, "b": 0}
        for row in cohort:
            year = _publication_year(row["year_best"], current_year)
            journal = str(row["journal_best"]).strip() if row["journal_best"] is not None else ""
            key = journal or None
            if row["in_a"]:
                journals_a[key] += 1
                if year is None:
                    year_missing["a"] += 1
                else:
                    years_a[year] += 1
            if row["in_b"]:
                journals_b[key] += 1
                if year is None:
                    year_missing["b"] += 1
                else:
                    years_b[year] += 1
            if row["paper_id"] in shared and year is not None:
                years_shared[year] += 1
        by_year = [{"year": year, "count_a": years_a[year], "count_b": years_b[year],
                    "shared_count": years_shared[year]} for year in sorted(set(years_a) | set(years_b))]
        journal_distribution = []
        journal_keys = sorted(set(journals_a) | set(journals_b),
                              key=lambda key: (-(journals_a[key] + journals_b[key]), key or ""))
        for journal in journal_keys:
            count_a, count_b = journals_a[journal], journals_b[journal]
            share_a, share_b = _percent(count_a, len(a)), _percent(count_b, len(b))
            journal_distribution.append({
                "journal": journal if journal is not None else MISSING_JOURNAL,
                "missing": journal is None, "count_a": count_a, "count_b": count_b,
                "share_a": share_a, "share_b": share_b,
                "difference_pp": round(share_b - share_a, 4) if share_a is not None and share_b is not None else None,
            })
        top_shared = sorted((row for row in cohort if row["paper_id"] in shared),
                            key=lambda row: (-(row["n_reports"] or 0), -(row["n_chapters"] or 0), row["paper_id"]))[:top]
        top_shared = [{key: value for key, value in row.items() if key not in {"in_a", "in_b"}}
                      for row in top_shared]
        return {
            "generated_at": _now(), "source": path.name, "report_a": summary_a, "report_b": summary_b,
            "overlap": {"shared_papers": len(shared), "only_a_papers": len(a - b),
                        "only_b_papers": len(b - a), "union_papers": len(union),
                        "jaccard": round(len(shared) / len(union), 6) if union else None,
                        "overlap_coefficient": round(len(shared) / min(len(a), len(b)), 6) if a and b else None,
                        "share_of_a": _percent(len(shared), len(a)), "share_of_b": _percent(len(shared), len(b))},
            "by_year": by_year, "journal_distribution": journal_distribution, "top_shared_papers": top_shared,
            "provenance": {
                "software_version": SOFTWARE_VERSION, "grain": "Distinct existing paper IDs cited by each selected report.",
                "definitions": {
                    "cohort": "A paper belongs to a report when at least one occurrence links it to that report's chapters.",
                    "jaccard": "Shared papers / union of paper sets; a fraction from 0 to 1.",
                    "overlap_coefficient": "Shared papers / the smaller cohort; a fraction from 0 to 1.",
                    "shares": "100 × count / distinct papers in the relevant cohort; null for an empty cohort.",
                    "difference_pp": "Report B's journal share minus report A's share, in percentage points.",
                    "year_distribution": f"Counts of distinct papers with four-digit publication years from {YEAR_MIN} through {current_year + 1}; rows sorted by numeric year.",
                    "year_missing": "Cohort papers with a missing or invalid publication year.",
                    "journal_distribution": "One journal value per distinct paper, including a combined category for blank and NULL values. Shares use all cohort papers.",
                },
                "queries": {"cohorts": COHORT_QUERY, "report_summaries": REPORT_SUMMARY_QUERY},
                "parameters": {**parameters, "top": top, "current_year": current_year},
                "year_missing": year_missing, "shared_paper_limit": top,
                "shared_paper_truncated": len(shared) > top, "journal_missing_label": MISSING_JOURNAL,
                "build_info": _build_metadata(connection),
            },
        }
