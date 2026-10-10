"""Read-only SQL guard for the AI layer.

The AI never receives write access.  Everything a model produces passes through
`validate_sql()` before it is executed, and execution itself runs on a
read-only SQLite connection with a row limit and a timeout.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

# statements/keywords that must never appear in AI-generated SQL
FORBIDDEN = [
    "insert", "update", "delete", "drop", "create", "alter", "truncate",
    "replace", "attach", "detach", "pragma", "vacuum", "reindex", "grant",
    "revoke", "begin", "commit", "rollback", "savepoint", "analyze",
]

# every table/column name the model is allowed to read
ALLOWED_TABLES = {
    "assessment_reports", "working_groups", "sources", "chapters", "papers",
    "references_occurrence", "authors", "paper_authors", "metadata_sources",
    "wos_records", "data_quality_flags", "manual_review", "collection_tasks",
    "analysis_results", "analysis_runs", "insights", "build_info",
}

_COMMENT = re.compile(r"(--[^\n]*|/\*.*?\*/)", re.S)
_TABLE = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.I)


class SqlRejected(ValueError):
    """Raised when generated SQL is not safe to execute."""


def validate_sql(sql: str, row_limit: int = 500) -> str:
    """Return a safe, row-limited SQL string or raise SqlRejected."""
    if not sql or not sql.strip():
        raise SqlRejected("empty SQL")

    cleaned = _COMMENT.sub(" ", sql).strip().rstrip(";").strip()

    # single statement only
    if ";" in cleaned:
        raise SqlRejected("multiple statements are not allowed")

    low = cleaned.lower()
    if not (low.startswith("select") or low.startswith("with")):
        raise SqlRejected("only SELECT / WITH queries are allowed")

    # word-boundary keyword scan (so 'created_at' does not trip 'create')
    for kw in FORBIDDEN:
        if re.search(rf"\b{kw}\b", low):
            raise SqlRejected(f"forbidden keyword: {kw}")

    # names introduced by a WITH clause are legitimate local relations
    cte_names = {m.lower() for m in
                 re.findall(r"(?:with|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s+as\s*\(", low)}

    # every referenced table must be on the allowlist (or be a CTE)
    for t in _TABLE.findall(cleaned):
        if t.lower() not in ALLOWED_TABLES and t.lower() not in cte_names:
            raise SqlRejected(f"table not allowed: {t}")

    # force a row limit
    if not re.search(r"\blimit\s+\d+", low):
        cleaned = f"{cleaned} LIMIT {int(row_limit)}"
    else:
        cleaned = re.sub(r"\blimit\s+(\d+)",
                         lambda m: f"LIMIT {min(int(m.group(1)), int(row_limit))}",
                         cleaned, flags=re.I)
    return cleaned


def execute_readonly(db_path, sql: str, row_limit: int = 500,
                     timeout: float = 15.0) -> dict:
    """Run validated SQL on a read-only connection with a timeout."""
    safe = validate_sql(sql, row_limit)
    path = Path(db_path).expanduser().resolve()
    con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=timeout)
    con.row_factory = sqlite3.Row
    try:
        con.execute(f"PRAGMA query_only = ON")
        rows = con.execute(safe).fetchall()
        cols = list(rows[0].keys()) if rows else (
            [d[0] for d in con.execute(safe).description] if False else [])
        return {"sql": safe, "columns": cols,
                "rows": [dict(r) for r in rows][:row_limit],
                "row_count": len(rows), "truncated": len(rows) >= row_limit}
    finally:
        con.close()
