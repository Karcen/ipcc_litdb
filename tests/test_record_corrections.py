"""Correction replay, immutable citations and chronological review boundaries."""
from copy import deepcopy
from contextlib import closing
import json
import sqlite3

import pytest

from scripts.correct_record_links import (
    add_year_review_flags,
    apply_corrections,
    digest,
    raw_occurrence_digest,
    raw_reference_year,
    unambiguous_source_year,
    validate_database,
    year,
)


TIMESTAMP = "2026-10-11T00:00:00Z"


@pytest.mark.parametrize("value,expected", [
    ("March 2014", 2014), ("2014 / 2014", 2014),
    ("2013 / 2014", None), (None, None), ("undated", None),
])
def test_source_dates_require_one_unambiguous_year(value, expected):
    assert unambiguous_source_year(value) == expected


@pytest.mark.parametrize("raw,expected", [
    ("Author (2013a). Printed title.", 2013),
    ("Author (2014c). Printed title.", 2014),
    ("Author (2012). Printed title.", 2012),
    ("x" * 301 + " (2013a). Printed title.", None),
    (None, None),
])
def test_raw_year_suffixes_leave_preferred_and_source_parsing_strict(raw, expected):
    assert raw_reference_year(raw) == expected
    assert year("2013a") is None
    assert unambiguous_source_year("2013a") is None


@pytest.fixture
def connection():
    """A small independent schema exercises the structural contract."""
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript("""
            CREATE TABLE sources (source_id TEXT PRIMARY KEY, publication_year TEXT);
            CREATE TABLE chapters (
                chapter_id TEXT PRIMARY KEY, report_id TEXT,
                source_id TEXT REFERENCES sources(source_id));
            CREATE TABLE papers (
                paper_id INTEGER PRIMARY KEY, year_best TEXT, doi_norm TEXT,
                n_occurrences INTEGER, n_chapters INTEGER, n_reports INTEGER);
            CREATE TABLE references_occurrence (
                occurrence_id TEXT PRIMARY KEY,
                chapter_id TEXT NOT NULL REFERENCES chapters(chapter_id),
                paper_id INTEGER REFERENCES papers(paper_id),
                source_id TEXT, source_page INTEGER, raw_reference TEXT,
                match_status TEXT NOT NULL);
            CREATE TABLE chapter_references (
                chapter_id TEXT REFERENCES chapters(chapter_id),
                occurrence_id TEXT REFERENCES references_occurrence(occurrence_id),
                paper_id INTEGER REFERENCES papers(paper_id),
                PRIMARY KEY(chapter_id,occurrence_id));
            CREATE TABLE data_quality_flags (
                flag_id INTEGER PRIMARY KEY, entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL, flag_type TEXT NOT NULL,
                severity TEXT NOT NULL, detail TEXT, detected_at TEXT);
            CREATE TABLE manual_review (
                review_id INTEGER PRIMARY KEY,
                occurrence_id TEXT REFERENCES references_occurrence(occurrence_id),
                status TEXT NOT NULL DEFAULT 'open', assigned_to TEXT,
                reason TEXT, note TEXT, created_at TEXT, updated_at TEXT);
            INSERT INTO sources VALUES ('source','2014');
            INSERT INTO chapters VALUES ('chapter','AR5','source');
            INSERT INTO papers VALUES (1,'2012','10.1234/fixture',1,1,1);
            INSERT INTO references_occurrence VALUES
                ('citation','chapter',1,'source',85,'Author (2012). Printed title.','matched_doi');
            INSERT INTO chapter_references VALUES ('chapter','citation',1);
        """)
        yield connection


@pytest.mark.parametrize("preferred,source,raw,status,reasons", [
    ("2015", "2014", "Author (2015). Title.", "matched_doi", {"preferred_year_after_source"}),
    ("2015", "2014", "Author (2014). Title.", "matched_doi", {"preferred_year_after_source"}),
    ("2015", "2013 / 2014", "Author (2015). Title.", "matched_doi", set()),
    ("2015", "2021", "Author (2012). Title.", "matched_doi", {"preferred_raw_year_gap_over_one"}),
    ("2015", "2021", "Author (2013a). Title.", "matched_doi", {"preferred_raw_year_gap_over_one"}),
    ("2015", "2021", "Author (2014c). Title.", "matched_doi", set()),
    ("2015", "2014", "Author (2012). Title.", "matched_doi", {
        "preferred_year_after_source", "preferred_raw_year_gap_over_one"}),
    ("2015", "2021", "Author (2014). Title.", "matched_doi", set()),
    ("2013", "2013", "Author (2013). Title.", "matched_doi", set()),
    ("2015", "2021", None, "matched_doi", set()),
    (None, "2014", "Author (2012). Title.", "matched_doi", set()),
    ("2015", None, "Author (2012). Title.", "matched_doi", {"preferred_raw_year_gap_over_one"}),
    ("2015", "2014", "Author (2012). Title.", "matched_title", set()),
    ("2015", "2014", "Author (2012). Title.", "unmatched", set()),
    ("2015", "2021", "x" * 301 + " (2012). Title.", "matched_doi", set()),
])
def test_chronology_rules_queue_cases_without_reclassifying_them(
        connection, preferred, source, raw, status, reasons):
    connection.execute("UPDATE papers SET year_best=?", (preferred,))
    connection.execute("UPDATE sources SET publication_year=?", (source,))
    connection.execute("UPDATE references_occurrence SET raw_reference=?,match_status=?", (raw, status))
    before = connection.execute("SELECT * FROM references_occurrence").fetchall()
    assert add_year_review_flags(connection, TIMESTAMP) == bool(reasons)
    flags = connection.execute("SELECT flag_type,detail FROM data_quality_flags").fetchall()
    assert len(flags) == bool(reasons)
    if reasons:
        assert flags[0][0] == "matched_doi_chronology_review"
        detail = json.loads(flags[0][1])
        assert set(detail["reasons"]) == reasons
        assert "Review candidate" in detail["interpretation"]
        assert connection.execute("SELECT status,assigned_to FROM manual_review").fetchall() == [("open", None)]
    else:
        assert connection.execute("SELECT COUNT(*) FROM manual_review").fetchone()[0] == 0
    assert connection.execute("SELECT * FROM references_occurrence").fetchall() == before
    assert connection.execute("SELECT year_best FROM papers").fetchone()[0] == preferred


def test_review_flags_are_idempotent_and_preserve_prior_adjudication(connection):
    connection.execute("UPDATE papers SET year_best='2015'")
    assert add_year_review_flags(connection, TIMESTAMP, {"citation"}) == 1
    rows = connection.execute("SELECT * FROM manual_review").fetchall()
    assert rows[0][2] == "resolved"
    assert rows[0][3] == "Codex"
    assert add_year_review_flags(connection, "2026-10-12T00:00:00Z") == 1
    assert connection.execute("SELECT * FROM manual_review").fetchall() == rows
    assert connection.execute("SELECT COUNT(*) FROM data_quality_flags").fetchone()[0] == 1


def test_raw_digest_includes_nulls_controls_and_provenance_but_excludes_metadata(connection):
    connection.execute("UPDATE references_occurrence SET raw_reference=?", ('Title, "quoted"\nwith\x00NUL.',))
    original = raw_occurrence_digest(connection)
    connection.execute("UPDATE papers SET year_best='2015'")
    connection.execute("UPDATE references_occurrence SET match_status='unmatched'")
    assert raw_occurrence_digest(connection) == original
    connection.execute("UPDATE references_occurrence SET raw_reference=NULL")
    null_digest = raw_occurrence_digest(connection)
    assert null_digest != original
    connection.execute("UPDATE references_occurrence SET raw_reference=''")
    assert raw_occurrence_digest(connection) not in {original, null_digest}
    connection.execute("UPDATE references_occurrence SET raw_reference=?", ('Title, "quoted"\nwith\x00NUL.',))
    connection.execute("UPDATE references_occurrence SET source_page=86")
    assert raw_occurrence_digest(connection) != original


def test_valid_fixture_passes_every_invariant(connection):
    assert not any(validate_database(connection).values())


@pytest.mark.parametrize("sql", [
    "UPDATE papers SET n_occurrences=2",
    "UPDATE papers SET n_chapters=2",
    "UPDATE papers SET n_reports=2",
    "UPDATE papers SET n_occurrences=NULL",
    "UPDATE papers SET n_chapters=NULL",
    "UPDATE papers SET n_reports=NULL",
    "DELETE FROM chapter_references",
    "UPDATE references_occurrence SET paper_id=NULL",
    "INSERT INTO papers VALUES (2,'2012',NULL,0,0,0)",
])
def test_invalid_counts_crosswalks_and_identities_are_rejected(connection, sql):
    connection.execute(sql)
    with pytest.raises(ValueError, match="invariant"):
        validate_database(connection)


def test_duplicate_nonblank_doi_is_rejected(connection):
    connection.execute("INSERT INTO papers VALUES (2,'2012','10.1234/fixture',1,1,1)")
    connection.execute("INSERT INTO references_occurrence VALUES "
                       "('other','chapter',2,'source',86,'Author (2012). Other title.','unmatched')")
    connection.execute("INSERT INTO chapter_references VALUES ('chapter','other',2)")
    with pytest.raises(ValueError, match="duplicate DOI"):
        validate_database(connection)


@pytest.mark.parametrize("target", ["source", "destination", "existing_report"])
def test_report_never_replaces_an_input_database_or_existing_file(tmp_path, target):
    source = tmp_path / "input.sqlite"
    source.write_bytes(b"immutable source fixture")
    destination = tmp_path / "corrected.sqlite"
    existing_report = tmp_path / "report.json"
    existing_report.write_text("Retained report", encoding="utf-8")
    report = {"source": source, "destination": destination, "existing_report": existing_report}[target]
    with pytest.raises(ValueError, match="new report file"):
        apply_corrections(source, destination, {}, report)
    assert source.read_bytes() == b"immutable source fixture"
    assert existing_report.read_text(encoding="utf-8") == "Retained report"
    assert not destination.exists()


def test_versioned_ledger_preserves_raw_fields_and_documents_source_evidence(repo):
    ledger = json.loads((repo / "reproducibility/corrections/2026-10-11.json").read_text(encoding="utf-8"))
    assert ledger["version"] == "v1.2.0"
    assert ledger["source_sha256"] == "faa7fc711ae4607acfa9833739ec8f202f8aefd6d929b474a12fceb589c5b85d"
    assert "AI-assisted" in ledger["audit_method"]
    assert "unestimated" in ledger["audit_method"]
    reviewed = ledger["reviewed_occurrence_ids"]
    assert len(reviewed) == len(set(reviewed))
    assert set(reviewed) <= {oid for case in ledger["cases"] for oid in case["occurrence_ids"]}
    for edit in ledger["edits"]:
        assert edit["reason"] and edit["evidence_urls"]
        assert all(url.startswith("https://") for url in edit["evidence_urls"])
        if edit["table"] != "papers":
            assert not {"occurrence_id", "chapter_id", "source_id", "source_page", "raw_reference"} & edit["fields"].keys()
    assert {paper["paper_id"] for paper in ledger["new_papers"]} == {113592, 113593, 113594, 113595, 113596}
    assert set(ledger["remove_empty_papers"]) == {7174, 35431}
    classifications = {case["original_paper_id"]: case["classification"] for case in ledger["cases"]}
    assert classifications[28347] == "correct_shared_identity_wrong_metadata"
    assert classifications[29105] == "correct_shared_identity_wrong_metadata"
    for paper_id in (32928, 33835, 35438):
        assert classifications[paper_id] == "legitimate_shared_identity_online_print_year_difference"


def test_corrected_snapshot_restores_specific_document_identities(db_path, repo):
    """Protect bibliographic case decisions separately from aggregate checks."""
    ledger = json.loads((repo / "reproducibility/corrections/2026-10-11.json").read_text(encoding="utf-8"))
    with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as connection:
        version = connection.execute("SELECT value FROM build_info WHERE key='correction_version'").fetchone()
        if version is None:
            pytest.skip("The original snapshot precedes the versioned correction ledger")
        assert version[0] == "v1.2.0"
        for edit in ledger["edits"]:
            key = "paper_id" if edit["table"] == "papers" else "occurrence_id"
            for field, change in edit["fields"].items():
                actual = connection.execute(
                    f'SELECT "{field}" FROM "{edit["table"]}" WHERE "{key}"=?', (edit["key"],)
                ).fetchone()
                assert actual == (change["new"],), (edit["table"], edit["key"], field)
        assert connection.execute(
            "SELECT paper_id,parsed_year,match_status,doi_norm,wos_record_id FROM references_occurrence "
            "WHERE occurrence_id='AR5-WG2-Chapter 16-00085'"
        ).fetchone() == (113593, "2012", "unmatched", None, None)
        assert connection.execute("SELECT title_best,year_best,first_author_surname FROM papers WHERE paper_id=113593").fetchone() == (
            "Socially just adaptation to climate change", "2012", "brisley")
        assert connection.execute("SELECT n_occurrences,n_chapters,n_reports FROM papers WHERE paper_id=113592").fetchone() == (9, 9, 5)
        assert connection.execute("SELECT n_occurrences FROM papers WHERE paper_id=26271").fetchone() == (3,)
        assert connection.execute("SELECT year_best,doi_norm,n_occurrences FROM papers WHERE paper_id=28347").fetchone() == ("2000", None, 4)
        assert connection.execute("SELECT year_best,doi_norm,n_occurrences FROM papers WHERE paper_id=29105").fetchone() == ("2011", None, 2)
        for paper_id in (7174, 35431):
            assert connection.execute("SELECT paper_id FROM papers WHERE paper_id=?", (paper_id,)).fetchone() is None
        assert connection.execute("SELECT year_best,doi_norm,n_occurrences,n_reports FROM papers WHERE paper_id=70675").fetchone() == (
            "2015", "10.1016/j.techfore.2013.09.021", 6, 3)
        authors = connection.execute(
            "SELECT surname FROM paper_authors JOIN authors USING(author_id) WHERE paper_id=70675 ORDER BY author_pos"
        ).fetchall()
        assert len(authors) == 24 and authors[0] == ("Kriegler",) and ("Rosen",) not in authors
        for paper_id in (32928, 33835, 35438):
            assert connection.execute("SELECT year_best FROM papers WHERE paper_id=?", (paper_id,)).fetchone() == ("2015",)
            reports = connection.execute(
                "SELECT DISTINCT c.report_id FROM references_occurrence o JOIN chapters c USING(chapter_id) "
                "WHERE o.paper_id=? AND o.match_status='matched_doi'", (paper_id,)
            ).fetchall()
            assert {"AR5", "AR6"} <= {row[0] for row in reports}
        assert connection.execute("SELECT COUNT(*) FROM references_occurrence").fetchone()[0] == 160029
        assert not any(validate_database(connection).values())
        for oid in ledger["reviewed_occurrence_ids"]:
            assert connection.execute(
                "SELECT status,assigned_to,reason FROM manual_review WHERE occurrence_id=?", (oid,)
            ).fetchall() == [("resolved", "Codex", "targeted_source_inspection")]


def test_real_snapshot_replay_is_deterministic_and_preserves_source(db_path, tmp_path):
    """Exercise the complete migration and cache refresh on the packaged schema."""
    source_digest = digest(db_path)
    with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as connection:
        paper_id, old_title = connection.execute(
            "SELECT paper_id,title_best FROM papers WHERE title_best IS NOT NULL ORDER BY paper_id LIMIT 1"
        ).fetchone()
        original_raw = raw_occurrence_digest(connection)
        original_count = connection.execute("SELECT COUNT(*) FROM references_occurrence").fetchone()[0]
        original_run = connection.execute("SELECT MAX(run_id) FROM analysis_runs").fetchone()[0]
        original_changes = connection.execute("SELECT COUNT(*) FROM change_log").fetchone()[0]
    title = old_title + " [correction replay fixture]"
    ledger = {
        "source_sha256": source_digest, "version": "test-correction-replay", "timestamp": TIMESTAMP,
        "new_papers": [], "cases": [], "reviewed_occurrence_ids": [],
        "edits": [{"table": "papers", "key": paper_id,
                   "fields": {"title_best": {"old": old_title, "new": title}},
                   "reason": "Exercise expected-value ledger replay in a temporary database.",
                   "evidence_urls": ["https://example.org/correction-fixture"]}],
    }
    destination = tmp_path / "first.sqlite"
    report_path = tmp_path / "report.json"
    result = apply_corrections(db_path, destination, ledger, report_path)
    assert digest(db_path) == source_digest
    assert result["source_sha256"] == source_digest
    assert result["raw_occurrence_sha256"] == original_raw
    assert result["raw_occurrences_preserved"] == original_count == 160029
    assert json.loads(report_path.read_text(encoding="utf-8")) == result
    assert result["cached_analysis_errors"] == 0
    with closing(sqlite3.connect(destination)) as connection:
        assert not any(validate_database(connection).values())
        assert raw_occurrence_digest(connection) == original_raw
        assert connection.execute("SELECT title_best FROM papers WHERE paper_id=?", (paper_id,)).fetchone()[0] == title
        assert connection.execute("SELECT COUNT(*) FROM change_log").fetchone()[0] == original_changes + 1
        latest = connection.execute(
            "SELECT run_id,errors,start_time,end_time FROM analysis_runs ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
        assert latest == (original_run + 1, 0, TIMESTAMP, TIMESTAMP)
        assert connection.execute("SELECT COUNT(*) FROM analysis_results WHERE run_id=?", (latest[0],)).fetchone()[0] == 11
        assert connection.execute("SELECT COUNT(*) FROM insights WHERE run_id<>?", (latest[0],)).fetchone()[0] == 0
    second = tmp_path / "second.sqlite"
    repeated = apply_corrections(db_path, second, ledger)
    assert repeated == result
    assert digest(second) == digest(destination) == result["destination_sha256"]
    destination_digest = digest(destination)
    with pytest.raises(ValueError, match="new destination"):
        apply_corrections(db_path, destination, ledger)
    assert digest(destination) == destination_digest
    with pytest.raises(ValueError, match="new destination"):
        apply_corrections(db_path, db_path, ledger)
    wrong_hash = deepcopy(ledger)
    wrong_hash["source_sha256"] = "0" * 64
    rejected = tmp_path / "rejected.sqlite"
    with pytest.raises(ValueError, match="SHA-256"):
        apply_corrections(db_path, rejected, wrong_hash)
    assert not rejected.exists()
    wrong_value = deepcopy(ledger)
    wrong_value["edits"][0]["fields"]["title_best"]["old"] = "A value absent from the source"
    with pytest.raises(ValueError, match="precondition"):
        apply_corrections(db_path, rejected, wrong_value)
    assert not rejected.exists()
    immutable = deepcopy(ledger)
    immutable["edits"] = [{"table": "references_occurrence", "key": "fixture",
                           "fields": {"raw_reference": {"old": None, "new": "Changed citation"}},
                           "reason": "Must be rejected", "evidence_urls": []}]
    with pytest.raises(ValueError, match="immutable"):
        apply_corrections(db_path, rejected, immutable)
    assert not rejected.exists()
    assert digest(db_path) == source_digest
