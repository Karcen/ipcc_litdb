"""Publication dates and exclusion counts in occurrence-weighted reference ages."""
import sqlite3

import pytest

from analytics.engine import Engine


@pytest.fixture
def age_result(tmp_path):
    database = tmp_path / "dates.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE assessment_reports (report_id TEXT PRIMARY KEY);
            CREATE TABLE sources (source_id TEXT PRIMARY KEY, publication_year TEXT);
            CREATE TABLE chapters (chapter_id TEXT PRIMARY KEY, report_id TEXT, source_id TEXT);
            CREATE TABLE papers (paper_id INTEGER PRIMARY KEY, year_best TEXT);
            CREATE TABLE references_occurrence (chapter_id TEXT, paper_id INTEGER);
            INSERT INTO assessment_reports VALUES ('AR6'), ('UNKNOWN');
            INSERT INTO sources VALUES
                ('wg1','August 2021'), ('wg2','February 2022'), ('syr','March 2023'),
                ('ambiguous','2021 / 2022');
            INSERT INTO chapters VALUES
                ('wg1','AR6','wg1'), ('wg2','AR6','wg2'), ('syr','AR6','syr'),
                ('fallback','AR6',NULL), ('ambiguous','AR6','ambiguous'),
                ('unknown','UNKNOWN',NULL);
            INSERT INTO papers VALUES (1,'2022'), (2,'2020'), (3,''), (4,'1900');
            INSERT INTO references_occurrence VALUES
                ('wg1',1), ('wg2',1), ('syr',1), ('fallback',2), ('ambiguous',2),
                ('wg2',3), ('wg2',4), ('unknown',2);
        """)
    engine = Engine(database)
    try:
        engine.a_reference_age()
        yield engine.results[-1]
    finally:
        engine.con.close()


def test_dates_follow_each_source_instead_of_one_assessment_year(age_result):
    result = age_result["output"][0]
    assert result["base_years"] == [2021, 2022, 2023]
    assert result["n"] == 4
    assert result["median_age"] == 1
    assert result["mean_age"] == 0.8
    assert result["source_year_count"] == 5
    assert result["fallback_year_count"] == 2


def test_missing_future_and_old_dates_reconcile_to_all_citations(age_result):
    result = age_result["output"][0]
    assert result["excluded_missing_publication_year"] == 1
    assert result["excluded_future_year"] == 1
    assert result["excluded_over_60_years"] == 1
    assert result["citation_count"] == result["n"] + sum(
        value for key, value in result.items() if key.startswith("excluded_")
    )


def test_unknown_report_date_is_visible_without_a_fabricated_age(age_result):
    result = age_result["output"][1]
    assert result["report_id"] == "UNKNOWN"
    assert result["citation_count"] == 1
    assert result["excluded_missing_report_year"] == 1
    assert result["n"] == 0
    assert result["median_age"] is None


def test_reference_age_records_query_and_year_rule(age_result):
    assert "sources s" in age_result["query"]
    assert age_result["parameters"]["grain"] == "reference occurrence"
    assert age_result["parameters"]["age_range"] == [0, 60]
