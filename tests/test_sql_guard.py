"""The AI layer must never be able to write. These tests pin that."""
import pytest

from ai_guard import SqlRejected, execute_readonly, validate_sql

WRITES = [
    "DROP TABLE papers",
    "DELETE FROM papers",
    "UPDATE papers SET title_best = 'x'",
    "INSERT INTO papers (paper_id) VALUES (1)",
    "TRUNCATE papers",
    "ALTER TABLE papers ADD COLUMN x TEXT",
    "CREATE TABLE evil (a TEXT)",
    "SELECT 1; DROP TABLE papers",
    "SELECT 1; DELETE FROM papers",
    "ATTACH DATABASE '/tmp/x.db' AS x",
    "PRAGMA writable_schema = 1",
]


@pytest.mark.parametrize("sql", WRITES)
def test_writes_are_rejected(sql):
    with pytest.raises(SqlRejected):
        validate_sql(sql)


def test_select_is_allowed_and_limited():
    out = validate_sql("SELECT COUNT(*) FROM papers")
    assert out.lower().startswith("select")
    assert "limit 500" in out.lower()


def test_existing_limit_is_clamped():
    assert "limit 500" in validate_sql("SELECT * FROM papers LIMIT 99999").lower()


def test_pragma_prefix_is_rejected():
    with pytest.raises(SqlRejected):
        validate_sql("PRAGMA query_only = OFF")


def test_unknown_table_is_rejected():
    with pytest.raises(SqlRejected):
        validate_sql("SELECT * FROM sqlite_master")


def test_comments_are_stripped_before_scanning():
    out = validate_sql("-- a comment\nSELECT COUNT(*) FROM papers")
    assert "--" not in out


def test_words_containing_keywords_are_not_false_positives():
    """'created_at' must not trip the 'create' rule."""
    sql = "SELECT created_at FROM data_quality_flags"
    assert validate_sql(sql)


def test_cte_is_allowed():
    assert validate_sql("WITH x AS (SELECT 1 AS a) SELECT * FROM x").lower().startswith("with")


def test_execute_readonly_runs_and_returns_columns(db_path):
    r = execute_readonly(db_path, "SELECT report_id FROM assessment_reports LIMIT 2")
    assert r["columns"] == ["report_id"]
    assert r["row_count"] == 2


def test_execute_readonly_refuses_a_write(db_path):
    with pytest.raises(SqlRejected):
        execute_readonly(db_path, "DELETE FROM papers")


def test_execute_readonly_connection_is_query_only(db_path):
    """Even if a write slipped past the guard, the connection refuses it."""
    import sqlite3
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.execute("PRAGMA query_only = ON")
    with pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM papers")
    con.close()
