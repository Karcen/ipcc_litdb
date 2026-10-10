"""Configured database paths shared by the API, analytics, and CLI."""
import sqlite3
import runpy
from pathlib import Path

import pytest
from fastapi import HTTPException

import app
import config
from analytics import cli, networks
from analytics.engine import Engine
from backend import config as analytics_config


@pytest.fixture
def selected_database(tmp_path, monkeypatch):
    database = tmp_path / "selected database #1.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE working_groups (wg_id TEXT, wg_label TEXT);
            CREATE TABLE chapters (chapter_id TEXT, report_id TEXT, wg_id TEXT, chapter_label TEXT);
            CREATE TABLE references_occurrence (chapter_id TEXT, paper_id INTEGER);
            CREATE TABLE analysis_results (
                analysis_id INTEGER, analysis_type TEXT, created_at TEXT,
                software_version TEXT, output TEXT, query TEXT, parameters TEXT
            );
            INSERT INTO working_groups VALUES ('selected-wg', 'WG1');
            INSERT INTO chapters VALUES ('selected-a', 'selected-report', 'selected-wg', 'A');
            INSERT INTO chapters VALUES ('selected-b', 'selected-report', 'selected-wg', 'B');
            INSERT INTO references_occurrence VALUES ('selected-a', 1);
            INSERT INTO references_occurrence VALUES ('selected-b', 1);
            INSERT INTO analysis_results VALUES (
                1, 'selected_analysis', '2026-10-11', 'test', '{"selected": true}',
                'SELECT 1', '{}'
            );
        """)
    database_url = f"sqlite:///{database}"
    monkeypatch.setattr(config, "DATABASE_URL", database_url)
    monkeypatch.setattr(analytics_config, "DATABASE_URL", database_url)
    return database


def test_engine_reads_configured_alternate_database_readonly(selected_database):
    engine = Engine()
    try:
        assert engine.db_path == selected_database
        assert engine.q1("SELECT COUNT(*) FROM references_occurrence") == 2
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            engine.con.execute("CREATE TABLE unintended_write (id INTEGER)")
    finally:
        engine.con.close()


@pytest.mark.parametrize("builder", [networks.build, app.network])
def test_networks_use_the_configured_database(selected_database, builder):
    graph = builder("chapter-chapter", min_weight=0.25, limit=10)
    assert {node["id"] for node in graph["nodes"]} == {"selected-a", "selected-b"}
    assert graph["edges"] == [{"source": "selected-a", "target": "selected-b", "weight": 1.0, "shared": 1}]


def test_cli_list_and_show_use_the_configured_database(selected_database, capsys):
    assert cli.cmd_list() == 0
    assert "selected_analysis" in capsys.readouterr().out
    assert cli.cmd_show("selected_analysis") == 0
    output = capsys.readouterr().out
    assert '"selected": true' in output
    assert "SELECT 1" in output


def test_ai_endpoint_passes_the_configured_path(selected_database, monkeypatch):
    paths = []

    def answer(question, language, db_path):
        paths.append(db_path)
        return {"configured": True, "rows": []}

    monkeypatch.setattr(app._ai, "answer", answer)
    app.ai_ask(app.AskIn(question="Count references"))
    assert paths == [selected_database]


def test_relative_sqlite_path_resolves_against_repository(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    assert config.sqlite_database_path("sqlite:///./state/database.sqlite") == tmp_path / "state/database.sqlite"
    assert config.sqlite_database_path("sqlite:///state/database.sqlite") == tmp_path / "state/database.sqlite"


@pytest.mark.parametrize("environment_override", [False, True])
def test_config_loads_repository_env_with_environment_precedence(tmp_path, monkeypatch, environment_override):
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    copied_config = backend_dir / "config.py"
    copied_config.write_text(Path(config.__file__).read_text())
    (tmp_path / ".env").write_text("DATABASE_URL=sqlite:///./state/from-env.sqlite\n")
    external_database = tmp_path / "state/from-environment.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{external_database}")
    if not environment_override:
        monkeypatch.delenv("DATABASE_URL")
    namespace = runpy.run_path(str(copied_config))
    expected = external_database if environment_override else tmp_path / "state/from-env.sqlite"
    assert namespace["sqlite_database_path"]() == expected


def test_missing_database_is_not_created(tmp_path, monkeypatch):
    database = tmp_path / "missing.sqlite"
    monkeypatch.setattr(analytics_config, "DATABASE_URL", f"sqlite:///{database}")
    with pytest.raises(sqlite3.OperationalError):
        cli.cmd_list()
    with pytest.raises(sqlite3.OperationalError):
        Engine()
    with pytest.raises(sqlite3.OperationalError):
        networks.build("chapter-chapter")
    assert not database.exists()


def test_postgres_network_and_ai_requests_report_sqlite_requirement(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://user:password@localhost/example")
    with pytest.raises(HTTPException) as error:
        app.network("chapter-chapter")
    assert error.value.status_code == 501
    assert "SQLite" in error.value.detail
    result = app.ai_ask(app.AskIn(question="Count references"))
    assert "SQLite" in result["error"]
    assert result["sql"] is None


@pytest.mark.parametrize("url", ["sqlite://", "sqlite:///:memory:", "postgresql://localhost/example"])
def test_analytics_require_a_sqlite_file(monkeypatch, url):
    monkeypatch.setattr(analytics_config, "DATABASE_URL", url)
    with pytest.raises(ValueError, match="SQLite"):
        Engine()
    with pytest.raises(ValueError, match="SQLite"):
        networks.build("chapter-chapter")
