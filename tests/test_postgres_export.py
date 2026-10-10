"""PostgreSQL dump format, source preservation and schema coverage."""
import hashlib
import io
import re
import sqlite3
import subprocess
import sys

import pytest

from database.export_for_postgres import export_database


def test_copy_preserves_null_empty_strings_and_escaped_text(tmp_path):
    source = tmp_path / "snapshot with spaces.sqlite"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE build_info (key TEXT PRIMARY KEY, value TEXT)")
        connection.executemany("INSERT INTO build_info VALUES (?, ?)", [
            ("empty", ""), ("null", None), ("literal", r"\N"),
            ("controls", "line 1\nline 2\rtab\tbackslash\\"),
            ("unicode", "Climate – café"), ("terminator", r"\."),
        ])
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    output = io.StringIO()
    export_database(source, output)
    dump = output.getvalue()
    assert 'COPY "build_info" ("key", "value") FROM stdin;\n' in dump
    assert "empty\t\n" in dump
    assert "null\t\\N\n" in dump
    assert "literal\t\\\\N\n" in dump
    assert "controls\tline 1\\nline 2\\rtab\\tbackslash\\\\\n" in dump
    assert "unicode\tClimate – café\n" in dump
    assert "terminator\t\\\\.\n" in dump
    assert dump.count("\n\\.\n") == 1
    assert digest == hashlib.sha256(source.read_bytes()).hexdigest()


def test_parent_tables_load_before_children_and_sequences_are_reset(tmp_path):
    source = tmp_path / "relations.sqlite"
    with sqlite3.connect(source) as connection:
        connection.executescript("""
            CREATE TABLE working_groups (
                wg_id INTEGER PRIMARY KEY,
                report_id TEXT REFERENCES assessment_reports(report_id)
            );
            CREATE TABLE assessment_reports (report_id TEXT PRIMARY KEY);
            CREATE TABLE analysis_results (
                analysis_id INTEGER PRIMARY KEY,
                run_id INTEGER REFERENCES analysis_runs(run_id)
            );
            CREATE TABLE analysis_runs (run_id INTEGER PRIMARY KEY);
            CREATE TABLE insights (insight_id INTEGER PRIMARY KEY);
            INSERT INTO assessment_reports VALUES ('AR6');
            INSERT INTO working_groups VALUES (43, 'AR6');
            INSERT INTO analysis_runs VALUES (5);
            INSERT INTO analysis_results VALUES (12, 5);
        """)
    output = io.StringIO()
    export_database(source, output)
    dump = output.getvalue()
    assert dump.index('COPY "assessment_reports"') < dump.index('COPY "working_groups"')
    assert dump.index('COPY "analysis_runs"') < dump.index('COPY "analysis_results"')
    assert 'COPY "insights" ("insight_id") FROM stdin;\n\\.\n' in dump
    assert dump.count("SELECT setval(pg_get_serial_sequence(") == 4
    assert "GREATEST(COALESCE(MAX(\"insight_id\"), 0), 1)" in dump
    assert "session_replication_role" not in dump
    assert "DISABLE TRIGGER" not in dump
    assert "BEGIN;" in dump and dump.endswith("COMMIT;\n")
    assert "\\set ON_ERROR_STOP on" in dump


def test_unknown_tables_fail_before_writing_a_partial_dump(tmp_path):
    source = tmp_path / "unknown.sqlite"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE unregistered_data (value TEXT)")
    output = io.StringIO()
    with pytest.raises(ValueError, match="unregistered_data"):
        export_database(source, output)
    assert output.getvalue() == ""


def test_nul_text_fails_instead_of_changing_data(tmp_path):
    source = tmp_path / "nul.sqlite"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE build_info (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO build_info VALUES (?, ?)", ("nul", "a\x00b"))
    output = io.StringIO()
    with pytest.raises(ValueError, match="NUL"):
        export_database(source, output)
    assert not output.getvalue().endswith("COMMIT;\n")


def test_explicit_nul_conversion_keeps_source_unchanged(tmp_path):
    source = tmp_path / "nul.sqlite"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE build_info (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO build_info VALUES (?, ?)", ("nul", "a\x00b\x00"))
    output = io.StringIO()
    assert export_database(source, output, strip_nul=True) == 2
    assert "nul\tab\n" in output.getvalue()
    with sqlite3.connect(source) as connection:
        assert connection.execute("SELECT value FROM build_info").fetchone()[0] == "a\x00b\x00"


def test_cli_reports_missing_database_without_creating_it(tmp_path, repo):
    missing = tmp_path / "missing.sqlite"
    result = subprocess.run(
        [sys.executable, str(repo / "database" / "export_for_postgres.py"),
         "--database", str(missing)], capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "Export failed:" in result.stderr
    assert result.stdout == ""
    assert not missing.exists()


def test_postgres_schema_covers_every_snapshot_column(db_path, repo):
    schema = (repo / "database" / "schema" / "schema_postgres.sql").read_text()
    migration = (repo / "database" / "migrations" / "001_init.sql").read_text()
    assert schema == migration
    declared = {}
    for table, body in re.findall(
        r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", schema, flags=re.S
    ):
        columns = []
        for line in body.splitlines():
            match = re.match(r'\s*(?:"([^"]+)"|(\w+))\s+', line)
            if match:
                name = match.group(1) or match.group(2)
                if name not in {"PRIMARY", "UNIQUE", "CONSTRAINT", "CHECK", "FOREIGN"}:
                    columns.append(name)
        declared[table] = columns
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )]
        actual = {table: [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
                  for table in tables}
    assert declared == actual
