"""Open chronology candidates and recurrence stress with fixed denominators."""
from contextlib import closing
from pathlib import Path
import sqlite3
import sys

import pytest

# The analysis scripts use sibling imports when executed from their directory.
script_directory = str(Path(__file__).resolve().parents[1] / "reproducibility")
sys.path.insert(0, script_directory)
try:
    from reproducibility.revision_identity import (
        fixed_denominator_chronology_stress,
        select_open_shared_chronology,
    )
finally:
    sys.path.remove(script_directory)


@pytest.fixture
def chronology_fixture():
    """Independent identities include resolved, duplicated and ineligible cases."""
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript("""
            CREATE TABLE sources (source_id TEXT PRIMARY KEY, publication_year TEXT);
            CREATE TABLE working_groups (wg_id INTEGER PRIMARY KEY, wg_label TEXT);
            CREATE TABLE chapters (
                chapter_id TEXT PRIMARY KEY, report_id TEXT, source_id TEXT,
                wg_id INTEGER, chapter_label TEXT, chapter_title TEXT);
            CREATE TABLE papers (
                paper_id INTEGER PRIMARY KEY, year_best TEXT, doi_norm TEXT, title_best TEXT);
            CREATE TABLE references_occurrence (
                occurrence_id TEXT PRIMARY KEY, paper_id INTEGER, chapter_id TEXT,
                match_status TEXT, raw_reference TEXT, doi_norm TEXT, doi_source TEXT);
            CREATE TABLE data_quality_flags (
                flag_id INTEGER PRIMARY KEY, entity_type TEXT, entity_id TEXT,
                flag_type TEXT, detail TEXT);
            CREATE TABLE manual_review (
                review_id INTEGER PRIMARY KEY, occurrence_id TEXT, status TEXT, reason TEXT);
            INSERT INTO working_groups VALUES (1,'WG1');
        """)
        for report, source_year in [("AR4", "2007"), ("AR5", "2013"), ("AR6", "2021"), ("SR15", "2018")]:
            connection.execute("INSERT INTO sources VALUES (?,?)", (report, source_year))
            connection.execute("INSERT INTO chapters VALUES (?,?,?,1,'Chapter 1','Fixture')", (report, report, report))
        papers = {
            i: {"year_best": None if i == 8 else "2015" if i == 2 else "2012"}
            for i in range(1, 11)
        }
        for pid, meta in papers.items():
            connection.execute("INSERT INTO papers VALUES (?,?,?,?)", (pid, meta["year_best"], f"10.1234/{pid}", f"Paper {pid}"))
        for pid in range(1, 11):
            reports = ["AR5"] if pid == 4 else ["AR6"] if pid == 10 else ["AR5", "AR6"]
            for report in reports:
                status = "unmatched" if (pid, report) == (5, "AR6") else "matched_title" if (pid, report) == (1, "AR6") else "matched_doi"
                connection.execute("INSERT INTO references_occurrence VALUES (?,?,?,?,?,?,?)", (
                    f"p{pid}-{report}", pid, report, status, f"Author (2012a). Paper {pid}.", f"10.1234/{pid}", "embedded"))
        for pid, report in [(1, "AR5"), (7, "AR4"), (8, "SR15")]:
            connection.execute("INSERT INTO references_occurrence VALUES (?,?,?,?,?,?,?)", (
                f"p{pid}-{report}-extra", pid, report, "matched_doi", f"Author (2012a). Paper {pid}.", f"10.1234/{pid}", "embedded"))
        flags = [
            (1, "occurrence", "p1-AR5", "matched_doi_chronology_review", "Candidate one"),
            (2, "occurrence", "p1-AR5", "matched_doi_chronology_review", "Candidate one"),
            (3, "occurrence", "p2-AR6", "matched_doi_chronology_review", "Post-cutoff preferred year"),
            (4, "occurrence", "p3-AR5", "matched_doi_chronology_review", "Resolved candidate"),
            (5, "occurrence", "p4-AR5", "matched_doi_chronology_review", "Not shared"),
            (6, "occurrence", "p5-AR5", "matched_doi_chronology_review", "Only one side matched"),
            (7, "occurrence", "p6-AR6", "other_rule", "Wrong flag type"),
            (8, "occurrence", "p7-AR4-extra", "matched_doi_chronology_review", "Earlier report flag"),
            (9, "occurrence", "p8-SR15-extra", "matched_doi_chronology_review", "Missing preferred year"),
            (10, "occurrence", "p9-AR6", "matched_doi_chronology_review", "Wont-fix candidate"),
            (11, "paper", "1", "matched_doi_chronology_review", "Wrong entity type"),
        ]
        connection.executemany("INSERT INTO data_quality_flags VALUES (?,?,?,?,?)", flags)
        reason = "matched_doi_chronology_review"
        reviews = [
            (1, "p1-AR5", "open", reason), (2, "p1-AR5", "in_progress", reason),
            (3, "p1-AR5", "resolved", reason), (4, "p2-AR6", "open", reason),
            (5, "p3-AR5", "resolved", reason), (6, "p4-AR5", "open", reason),
            (7, "p5-AR5", "open", reason), (8, "p6-AR6", "open", reason),
            (9, "p7-AR4-extra", "in_progress", reason), (10, "p8-SR15-extra", "open", reason),
            (11, "p9-AR6", "wont_fix", reason),
            (12, "p3-AR5", "open", "unrelated_review_rule"),
        ]
        connection.executemany("INSERT INTO manual_review VALUES (?,?,?,?)", reviews)
        yield connection, papers


def test_open_selector_counts_occurrences_once_and_includes_other_reports(chronology_fixture):
    connection, _ = chronology_fixture
    before = connection.total_changes
    rows = select_open_shared_chronology(connection)
    assert connection.total_changes == before
    assert {r["paper_id"] for r in rows} == {1, 2, 7, 8}
    assert {r["occurrence_id"] for r in rows} == {"p1-AR5", "p2-AR6", "p7-AR4-extra", "p8-SR15-extra"}
    assert len(rows) == 4
    first = next(r for r in rows if r["paper_id"] == 1)
    assert first["flag_ids"] == "1 | 2"
    assert first["open_review_ids"] == "1 | 2"
    assert first["open_review_statuses"] == "in_progress | open"
    assert first["open_review_reasons"] == "matched_doi_chronology_review"
    assert first["raw_leading_year"] == 2012
    assert {r["paper_id"] for r in rows if r["flag_occurrence_within_transition"]} == {1, 2}
    assert {r["paper_id"] for r in rows if r["preferred_year_eligible"]} == {1, 7}


def test_fixed_denominators_and_actual_eligible_candidate_count(chronology_fixture):
    connection, papers = chronology_fixture
    details = select_open_shared_chronology(connection)
    a, b = {1, 2, 3, 4, 5, 6, 7, 8, 9}, {1, 2, 3, 6, 7, 8, 9, 10}
    # Even a repeated detail row cannot add a candidate ID or occurrence.
    row = fixed_denominator_chronology_stress(a, b, papers, [*details, details[0]],
        scenario="fixture", flag_scope="All reports in snapshot")
    assert (row["papers_a"], row["papers_b"], row["eligible_denominator"]) == (9, 8, 6)
    assert (row["flagged_occurrences"], row["flagged_shared_ids"]) == (4, 4)
    assert row["excluded_union_ids"] == 0
    assert row["shared"] == 3
    assert row["retention"] == pytest.approx(3/9)
    assert row["reuse"] == pytest.approx(3/8)
    assert row["baseline_shared_eligible_ids"] == 5
    assert row["affected_eligible_shared_ids"] == 2
    assert row["stress_shared_eligible_ids"] == 3
    assert row["conditional_reuse"] == pytest.approx(3/6)
    assert row["jaccard"] is None
    assert row["only_a"] is None and row["only_b"] is None
    assert a == {1, 2, 3, 4, 5, 6, 7, 8, 9} and b == {1, 2, 3, 6, 7, 8, 9, 10}


def test_transition_only_scope_changes_candidates_without_changing_denominators(chronology_fixture):
    connection, papers = chronology_fixture
    details = [r for r in select_open_shared_chronology(connection) if r["flag_occurrence_within_transition"]]
    row = fixed_denominator_chronology_stress({1, 2, 3, 4, 5, 6, 7, 8, 9}, {1, 2, 3, 6, 7, 8, 9, 10},
        papers, details, scenario="transition_only", flag_scope="AR5/AR6 flag occurrences")
    assert row["flagged_shared_ids"] == row["flagged_occurrences"] == 2
    assert row["shared"] == 5 and row["eligible_denominator"] == 6
    assert row["affected_eligible_shared_ids"] == 1
    assert row["conditional_reuse"] == pytest.approx(4/6)


def test_stress_rejects_nonshared_candidates():
    with pytest.raises(ValueError, match="both matched report sets"):
        fixed_denominator_chronology_stress({1}, {2}, {1: {"year_best": "2012"}, 2: {"year_best": "2012"}},
            [{"paper_id": 1, "occurrence_id": "not-shared"}], scenario="invalid", flag_scope="fixture")


def test_empty_stress_has_no_division_by_zero():
    row = fixed_denominator_chronology_stress(set(), set(), {}, [], scenario="empty", flag_scope="fixture")
    assert row["shared"] == row["eligible_denominator"] == row["affected_eligible_shared_ids"] == 0
    assert row["retention"] is None and row["reuse"] is None and row["conditional_reuse"] is None


def test_selection_requires_two_distinct_reports(chronology_fixture):
    with pytest.raises(ValueError, match="two different reports"):
        select_open_shared_chronology(chronology_fixture[0], "AR5", "AR5")


def test_packaged_snapshot_scope_and_fixed_stress(db_path):
    with closing(sqlite3.connect(db_path.resolve().as_uri()+"?mode=ro&immutable=1", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        details = select_open_shared_chronology(connection)
        matched = {report: {r[0] for r in connection.execute("""
            SELECT DISTINCT r.paper_id FROM references_occurrence r
            JOIN chapters c ON c.chapter_id=r.chapter_id
            WHERE c.report_id=? AND r.match_status IN ('matched_doi','matched_title')
              AND r.paper_id IS NOT NULL
        """, (report,))} for report in ("AR5", "AR6")}
        papers = {r["paper_id"]: dict(r) for r in connection.execute("SELECT paper_id,year_best FROM papers")}
    assert (len(details), len({r["paper_id"] for r in details})) == (85, 38)
    pair_details = [r for r in details if r["flag_occurrence_within_transition"]]
    assert (len(pair_details), len({r["paper_id"] for r in pair_details})) == (49, 29)
    stress = fixed_denominator_chronology_stress(matched["AR5"], matched["AR6"], papers, details,
        scenario="snapshot", flag_scope="All reports in snapshot")
    assert (stress["papers_a"], stress["papers_b"], stress["shared"]) == (13503, 34869, 1619)
    assert (stress["eligible_denominator"], stress["affected_eligible_shared_ids"], stress["stress_shared_eligible_ids"]) == (5810, 38, 1612)
    assert stress["retention"] == pytest.approx(1619/13503)
    assert stress["reuse"] == pytest.approx(1619/34869)
    assert stress["conditional_reuse"] == pytest.approx(1612/5810)
