"""Schema, integrity and the paper/reference separation."""
import json
import sqlite3

import pytest


def rows(con, sql, *a):
    return con.execute(sql, a).fetchall()


def test_occurrence_count_matches_upstream(db_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    assert rows(con, "SELECT COUNT(*) FROM references_occurrence")[0][0] == 160029


def test_every_occurrence_has_chapter_and_paper(db_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    assert rows(con, "SELECT COUNT(*) FROM references_occurrence WHERE paper_id IS NULL")[0][0] == 0
    assert rows(con, "SELECT COUNT(*) FROM references_occurrence WHERE chapter_id NOT IN "
                     "(SELECT chapter_id FROM chapters)")[0][0] == 0


def test_no_data_lost_in_import(db_path):
    """Sum of per-paper occurrence counters must equal the occurrence table."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    assert rows(con, "SELECT SUM(n_occurrences) FROM papers")[0][0] == \
           rows(con, "SELECT COUNT(*) FROM references_occurrence")[0][0]


def test_no_orphan_papers(db_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    assert rows(con, "SELECT COUNT(*) FROM papers p WHERE NOT EXISTS "
                     "(SELECT 1 FROM references_occurrence r WHERE r.paper_id = p.paper_id)")[0][0] == 0


def test_foreign_keys_clean(db_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    assert list(con.execute("PRAGMA foreign_key_check")) == []


def test_unique_occurrence_id(db_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    assert rows(con, "SELECT COUNT(*) FROM (SELECT occurrence_id FROM references_occurrence "
                     "GROUP BY occurrence_id HAVING COUNT(*)>1)")[0][0] == 0


def test_unique_non_null_doi(db_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    assert rows(con, "SELECT COUNT(*) FROM (SELECT doi_norm FROM papers "
                     "WHERE doi_norm IS NOT NULL AND doi_norm<>'' GROUP BY doi_norm "
                     "HAVING COUNT(*)>1)")[0][0] == 0


def test_papers_and_references_are_separate_entities(db_path):
    """A paper cited by several chapters must be ONE paper row - this is the
    central modelling requirement."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    distinct_papers = rows(con, "SELECT COUNT(DISTINCT paper_id) FROM references_occurrence")[0][0]
    assert distinct_papers < 160029, "occurrences were not collapsed into papers"
    multi = rows(con, "SELECT COUNT(*) FROM papers WHERE n_chapters > 1")[0][0]
    assert multi > 20000


def test_repeated_citations_are_preserved(db_path):
    """The same paper cited twice in the same chapter must produce two
    occurrences, not one."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dup = rows(con, """SELECT COUNT(*) FROM (SELECT chapter_id, paper_id, COUNT(*) c
                       FROM references_occurrence WHERE paper_id IS NOT NULL
                       GROUP BY chapter_id, paper_id HAVING c > 1)""")[0][0]
    assert dup > 0, "repeat citations within a chapter should exist and be preserved"


def test_unresolved_references_retained(db_path, repo):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    snapshot = json.loads((repo / "database" / "snapshot.json").read_text(encoding="utf-8"))
    assert rows(con, "SELECT COUNT(*) FROM references_occurrence "
                     "WHERE match_status='unmatched'")[0][0] == snapshot["counts"]["unmatched_occurrences"]


def test_matched_plus_unmatched_equals_total(db_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    m = rows(con, "SELECT COUNT(*) FROM references_occurrence "
                  "WHERE match_status IN ('matched_doi','matched_title')")[0][0]
    u = rows(con, "SELECT COUNT(*) FROM references_occurrence "
                  "WHERE match_status='unmatched'")[0][0]
    assert m + u == 160029


def test_collection_tasks_empty_no_invented_assignments(db_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    assert rows(con, "SELECT COUNT(*) FROM collection_tasks")[0][0] == 0


def test_provenance_is_traceable(db_path):
    """Every occurrence must point at a chapter, and every chapter at a source."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    assert rows(con, "SELECT COUNT(*) FROM references_occurrence r JOIN chapters c "
                     "ON c.chapter_id = r.chapter_id WHERE c.source_id IS NULL "
                     "OR c.source_id = ''")[0][0] == 0
