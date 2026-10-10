#!/usr/bin/env python3
"""Write a PostgreSQL COPY dump from a SQLite database.

Apply database/schema/schema_postgres.sql to an empty PostgreSQL database, then
load this dump with psql -v ON_ERROR_STOP=1. The source database is read-only.

The public snapshot contains NUL characters, which PostgreSQL text cannot store.
Pass --strip-nul to remove those characters from the export; the source is unchanged.

    python database/export_for_postgres.py --strip-nul > /tmp/ipcc_litdb.sql
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import TextIO
from urllib.parse import quote

DEFAULT_DB = Path(__file__).resolve().parent / "ipcc_litdb.sqlite"

# Parents precede children so PostgreSQL can check foreign keys during import.
TABLE_ORDER = (
    "assessment_reports", "working_groups", "sources", "chapters", "papers",
    "authors", "paper_authors", "metadata_sources", "references_occurrence",
    "chapter_references", "wos_records", "data_quality_flags", "manual_review",
    "collection_tasks", "change_log", "build_info", "analysis_runs",
    "analysis_results", "insights",
)
SEQUENCE_COLUMNS = {
    "working_groups": "wg_id", "papers": "paper_id", "authors": "author_id",
    "data_quality_flags": "flag_id", "manual_review": "review_id",
    "collection_tasks": "task_id", "change_log": "change_id",
    "analysis_runs": "run_id", "analysis_results": "analysis_id",
    "insights": "insight_id",
}


def identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def copy_value(value: object, strip_nul: bool = False) -> str:
    """Preserve NULL, empty strings and control characters in COPY text format."""
    if value is None:
        return r"\N"
    value = str(value)
    if "\x00" in value:
        if not strip_nul:
            raise ValueError("PostgreSQL text fields cannot contain a NUL character; "
                             "use --strip-nul to remove these characters during export")
        value = value.replace("\x00", "")
    return (value.replace("\\", "\\\\").replace("\b", r"\b")
            .replace("\f", r"\f").replace("\n", r"\n").replace("\r", r"\r")
            .replace("\t", r"\t").replace("\v", r"\v"))


def export_database(database: Path, output: TextIO, strip_nul: bool = False) -> int:
    uri = f"file:{quote(str(database.resolve()))}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.execute("BEGIN")
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )}
        unknown = tables - set(TABLE_ORDER)
        if unknown:
            raise ValueError("No export order defined for tables: " + ", ".join(sorted(unknown)))
        if not tables:
            raise ValueError("The source database contains no project tables")

        output.write("-- IPCC Literature Database: PostgreSQL data import\n")
        output.write("\\set ON_ERROR_STOP on\nBEGIN;\nSET client_encoding = 'UTF8';\n")
        removed_nul = 0
        for table in TABLE_ORDER:
            if table not in tables:
                continue
            name = identifier(table)
            columns = connection.execute(f"PRAGMA table_info({name})").fetchall()
            primary_key = [row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5]]
            order = ", ".join(identifier(column) for column in primary_key)
            query = f"SELECT * FROM {name}" + (f" ORDER BY {order}" if order else "")
            cursor = connection.execute(query)
            names = ", ".join(identifier(column[0]) for column in cursor.description)
            output.write(f"\nCOPY {name} ({names}) FROM stdin;\n")
            for row in cursor:
                if strip_nul:
                    removed_nul += sum(value.count("\x00") for value in row if isinstance(value, str))
                try:
                    output.write("\t".join(copy_value(value, strip_nul) for value in row) + "\n")
                except ValueError as error:
                    raise ValueError(f"{table}: {error}") from error
            output.write("\\.\n")

        # Imported IDs bypass defaults. Advance each sequence before API writes.
        for table, column in SEQUENCE_COLUMNS.items():
            if table not in tables:
                continue
            name, key = identifier(table), identifier(column)
            output.write(
                f"SELECT setval(pg_get_serial_sequence('{name}', '{column}'), "
                f"GREATEST(COALESCE(MAX({key}), 0), 1), "
                f"COALESCE(MAX({key}), 0) >= 1) FROM {name};\n"
            )
        output.write(f"-- NUL characters removed during export: {removed_nul}\nCOMMIT;\n")
        return removed_nul


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB,
                        help="source SQLite database (default: database/ipcc_litdb.sqlite)")
    parser.add_argument("--strip-nul", action="store_true",
                        help="remove NUL characters that PostgreSQL text cannot store")
    args = parser.parse_args()
    try:
        removed = export_database(args.database, sys.stdout, strip_nul=args.strip_nul)
    except (sqlite3.Error, ValueError) as error:
        print(f"Export failed: {error}", file=sys.stderr)
        return 1
    if removed:
        print(f"Removed {removed} NUL characters from PostgreSQL export text", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
