"""Research results use declared populations and preserve the source database."""
from datetime import datetime, timezone
import hashlib
import sqlite3

import pytest

from analytics.research import compare_reports, normalize_doi, quality_audit, valid_doi
from analytics.research import first_reference_year, source_publication_year, _connection


@pytest.fixture
def research_db(tmp_path):
    path = tmp_path / "research.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE assessment_reports(report_id TEXT PRIMARY KEY,report_label TEXT);
            CREATE TABLE working_groups(wg_id INTEGER PRIMARY KEY,report_id TEXT);
            CREATE TABLE sources(source_id TEXT PRIMARY KEY);
            CREATE TABLE chapters(chapter_id TEXT PRIMARY KEY,report_id TEXT,wg_id INTEGER,source_id TEXT);
            CREATE TABLE papers(paper_id INTEGER PRIMARY KEY,title_best TEXT,authors_best TEXT,
                year_best TEXT,journal_best TEXT,doi_norm TEXT,n_reports INTEGER,
                n_chapters INTEGER,n_occurrences INTEGER);
            CREATE TABLE references_occurrence(occurrence_id TEXT PRIMARY KEY,chapter_id TEXT,
                paper_id INTEGER,parsed_title TEXT,parsed_authors TEXT,parsed_year TEXT,
                parsed_journal TEXT,doi_norm TEXT,match_status TEXT);
            CREATE TABLE authors(author_id INTEGER PRIMARY KEY);
            CREATE TABLE paper_authors(paper_id INTEGER,author_id INTEGER);
        """)
        connection.executemany("INSERT INTO assessment_reports VALUES (?,?)",
                               [("A", "Report A"), ("B", "Report B"), ("EMPTY", "Empty report")])
        connection.executemany("INSERT INTO working_groups VALUES (?,?)", [(1, "A"), (2, "B"), (3, "absent")])
        connection.execute("INSERT INTO sources VALUES ('source')")
        connection.executemany("INSERT INTO chapters VALUES (?,?,?,?)", [
            ("a1", "A", 1, "source"), ("a2", "A", 1, None), ("b1", "B", 2, ""),
            ("empty", "EMPTY", 1, None), ("bad", "absent", 999, "absent"),
        ])
        connection.executemany("INSERT INTO papers VALUES (?,?,?,?,?,?,?,?,?)", [
            (1, "Shared work", "Author A", "2020", "Journal X", "10.1234/SHARED", 2, 3, 4),
            (2, "A only", "Author B", "2021", "Journal X", "https://doi.org/10.1234/shared?tracking=1", 1, 1, 1),
            (3, "B only", "Author C", "2022", "Journal Y", "doi:10.1234/third", 1, 1, 1),
            (4, "Unknown year", None, " ", None, None, 2, 2, 2),
            (5, "Invalid year", "Author E", "in press", "  ", "10.1234/fifth", 1, 1, 1),
            (6, "Uncited", "Author F", "2023", "Journal Y", None, 0, 0, 0),
            (7, "Wrong stored counters", "Author G", "2024", "Journal Z", None, 3, 8, 12),
        ])
        # Repeated citations of paper 1 count as one cohort paper.
        occurrences = [
            ("r01", "a1", 1, "Shared", "A", "2020", "Journal X", "10.1234/shared", "matched_doi"),
            ("r02", "a1", 1, "Shared", "A", "2020", "Journal X", "https://dx.doi.org/10.1234/SHARED", "matched_doi"),
            ("r03", "a2", 1, "Shared", "A", "2020", "Journal X", "doi: 10.1234/shared", "matched_title"),
            ("r04", "b1", 1, "Shared", "A", "2020", "Journal X", "10.1234/shared", "matched_doi"),
            ("r05", "a1", 2, "Only A", "B", "2021", "Journal X", "10.1234/shared", "unmatched"),
            ("r06", "b1", 3, "Only B", "C", "2022", "Journal Y", "10.1234/third", "unmatched"),
            ("r07", "a1", 4, None, " ", " ", None, " ", "unmatched"),
            ("r08", "b1", 4, " ", None, None, " ", None, "unmatched"),
            ("r09", "b1", 5, "Bad year", "E", "20XX", " ", "https://example.com/10.1234/fifth", "unmatched"),
            ("r10", "absent", 7, "Orphan chapter", "G", "0999", "Journal Z", "not-a-doi", "unmatched"),
            ("r11", "bad", 999, "Orphan paper", "H", "9999", "Journal Z", "10.1234/", "unmatched"),
        ]
        connection.executemany("INSERT INTO references_occurrence VALUES (?,?,?,?,?,?,?,?,?)", occurrences)
        connection.execute("INSERT INTO authors VALUES (1)")
        connection.executemany("INSERT INTO paper_authors VALUES (?,?)", [(1, 1), (999, 1), (1, 999)])
    return path


@pytest.mark.parametrize("raw,expected", [
    (None, None), ("  ", None), (" DOI: 10.1234/ABC ", "10.1234/abc"),
    ("https://doi.org/10.1234/A%28B%29?tracking=x#section", "10.1234/a(b)"),
    ("https://dx.doi.org/10.1234/ABC", "10.1234/abc"),
    ("10.1234/a(b).", "10.1234/a(b)."),
])
def test_doi_normalization_preserves_suffix(raw, expected):
    assert normalize_doi(raw) == expected


@pytest.mark.parametrize("value", ["https://elsewhere.org/10.1234/a", "10.123/a", "10.1234/", "10.1234/a b", "10.1234/a\x00b", "https://[invalid/"])
def test_invalid_doi_syntax(value):
    assert not valid_doi(value)


def test_audit_counts_missing_invalid_duplicates_and_orphans(research_db):
    audit = quality_audit(research_db, sample_limit=1)
    checks = {row["id"]: row for row in audit["checks"]}
    expected = {"missing_title": 2, "missing_authors": 2, "missing_journal": 3,
                "missing_year": 2, "missing_doi": 2, "invalid_year": 3, "invalid_doi": 3,
                "duplicate_normalized_doi": 1, "orphan_occurrence_paper": 1,
                "orphan_occurrence_chapter": 1, "orphan_chapter_report": 1,
                "orphan_chapter_working_group": 1, "orphan_working_group_report": 1,
                "orphan_chapter_source": 1, "orphan_paper_author_paper": 1,
                "orphan_paper_author_author": 1, "paper_occurrence_count_mismatch": 1,
                "paper_chapter_count_mismatch": 1, "paper_report_count_mismatch": 1}
    assert {key: checks[key]["count"] for key in expected} == expected
    assert checks["duplicate_normalized_doi"]["denominator"] == 3
    assert checks["duplicate_normalized_doi"]["grain"] == "normalized_doi_group"
    assert checks["missing_year"]["denominator"] == 11
    assert checks["missing_year"]["rate"] == pytest.approx(18.1818)
    assert checks["paper_occurrence_count_mismatch"]["samples"][0] == {
        "entity_id": 7, "title_best": "Wrong stored counters", "stored": 12, "actual": 1}
    assert all(len(check["samples"]) <= 1 for check in audit["checks"])
    assert audit["provenance"]["unassigned_occurrences"] == 2


def test_live_audit_detects_date_conflicts_with_printed_evidence(research_db):
    with sqlite3.connect(research_db) as connection:
        connection.execute("ALTER TABLE sources ADD COLUMN publication_year TEXT")
        connection.execute("UPDATE sources SET publication_year='March 2020'")
        connection.execute("ALTER TABLE references_occurrence ADD COLUMN raw_reference TEXT")
        connection.executemany("UPDATE references_occurrence SET raw_reference=? WHERE occurrence_id=?", [
            ("Author, 2019: Shared work.", "r01"),
            ("Author, 2017: An edition reprinted in 2020.", "r02"),
            ("Author, 2021: A only.", "r05"),
        ])
    audit = quality_audit(research_db, sample_limit=2)
    checks = {row["id"]: row for row in audit["checks"]}
    future = checks["publication_after_source"]
    disagreement = checks["reference_year_disagreement"]
    assert (future["count"], future["denominator"]) == (1, 3)
    assert future["samples"][0]["preferred_year"] == 2021
    assert future["samples"][0]["source_year"] == 2020
    assert (disagreement["count"], disagreement["denominator"]) == (2, 3)
    assert disagreement["samples"][0]["reference_excerpt"] == "Author, 2019: Shared work."
    assert "review candidate" in disagreement["definition"]
    with _connection(research_db) as (connection, _, _):
        for check in (future, disagreement):
            assert connection.execute(check["sql"], check["parameters"]).fetchone()[0] == check["count"]
            samples = [dict(row) for row in connection.execute(
                check["sample_sql"], {**check["parameters"], "sample_limit": 2})]
            assert samples == check["samples"]


@pytest.mark.parametrize("date,expected", [
    ("July 1990", 1990), ("2021–2022", None), ("undated", None), (None, None),
])
def test_source_date_requires_one_unambiguous_year(date, expected):
    assert source_publication_year(date) == expected


def test_printed_year_is_a_review_cue_with_multiple_dates():
    assert first_reference_year("Author, 1989a: Report. Updated in 2012.") == 1989
    assert first_reference_year("Undated report, p. 1200.") is None


def test_report_completeness_handles_null_blank_and_empty_report(research_db):
    reports = {row["report_id"]: row for row in quality_audit(research_db)["by_report"]}
    assert reports["A"]["reference_occurrences"] == 5
    assert reports["A"]["fields"]["year"] == {"present": 4, "missing": 1, "denominator": 5, "completeness": 80.0}
    assert reports["B"]["fields"]["year"]["completeness"] == 75.0  # Validity is a separate check.
    assert reports["EMPTY"]["reference_occurrences"] == 0
    assert all(field == {"present": 0, "missing": 0, "denominator": 0, "completeness": None}
               for field in reports["EMPTY"]["fields"].values())


def test_audit_sql_reproduces_each_count_and_sample(research_db):
    audit = quality_audit(research_db, sample_limit=2)
    with sqlite3.connect(research_db) as connection:
        connection.row_factory = sqlite3.Row
        connection.create_function("ipcc_normalize_doi", 1, normalize_doi)
        connection.create_function("ipcc_valid_doi", 1, valid_doi)
        current_year = datetime.now(timezone.utc).year
        connection.create_function("ipcc_valid_year", 1, lambda value: str(value or "").strip().isascii()
                                   and str(value or "").strip().isdigit() and len(str(value or "").strip()) == 4
                                   and 1000 <= int(str(value).strip()) <= current_year + 1)
        for check in audit["checks"]:
            assert connection.execute(check["sql"], check["parameters"]).fetchone()[0] == check["count"]
            rows = connection.execute(check["sample_sql"], {**check["parameters"], "sample_limit": 2})
            assert [dict(row) for row in rows] == check["samples"]


def test_report_comparison_counts_distinct_papers_and_complete_distributions(research_db):
    result = compare_reports("A", "B", research_db, top=1)
    assert result["report_a"]["reference_occurrences"] == 5
    assert result["report_a"]["unique_papers"] == 3
    assert result["report_a"]["matched_occurrences"] == 3
    assert result["report_a"]["match_rate"] == 60.0
    assert result["report_b"]["unique_papers"] == 4
    assert result["overlap"] == {"shared_papers": 2, "only_a_papers": 1, "only_b_papers": 2,
                                 "union_papers": 5, "jaccard": 0.4, "overlap_coefficient": 0.666667,
                                 "share_of_a": 66.6667, "share_of_b": 50.0}
    assert result["by_year"] == [
        {"year": 2020, "count_a": 1, "count_b": 1, "shared_count": 1},
        {"year": 2021, "count_a": 1, "count_b": 0, "shared_count": 0},
        {"year": 2022, "count_a": 0, "count_b": 1, "shared_count": 0},
    ]
    assert result["provenance"]["year_missing"] == {"a": 1, "b": 2}
    assert [row["paper_id"] for row in result["top_shared_papers"]] == [1]
    assert result["provenance"]["shared_paper_truncated"] is True
    assert sum(row["count_a"] for row in result["journal_distribution"]) == 3
    assert sum(row["count_b"] for row in result["journal_distribution"]) == 4
    missing = next(row for row in result["journal_distribution"] if row["missing"])
    assert missing["count_a"] == 1 and missing["count_b"] == 2
    assert missing["difference_pp"] == pytest.approx(16.6667)


def test_empty_and_identical_report_cohorts(research_db):
    result = compare_reports("A", "EMPTY", research_db)
    assert result["report_b"]["unique_papers"] == 0
    assert result["report_b"]["match_rate"] is None
    assert result["overlap"]["jaccard"] == 0.0
    assert result["overlap"]["overlap_coefficient"] is None
    assert result["overlap"]["share_of_b"] is None
    assert all(row["share_b"] is None and row["difference_pp"] is None
               for row in result["journal_distribution"])
    empty = compare_reports("EMPTY", "EMPTY", research_db)
    assert empty["by_year"] == [] and empty["journal_distribution"] == []
    assert empty["overlap"]["jaccard"] is None
    same = compare_reports("A", "A", research_db)
    assert same["overlap"]["jaccard"] == same["overlap"]["overlap_coefficient"] == 1.0


@pytest.mark.parametrize("limit", [0, -1, 101, True, 1.5, "10"])
def test_invalid_audit_limits(research_db, limit):
    with pytest.raises(ValueError, match="sample_limit"):
        quality_audit(research_db, limit)


@pytest.mark.parametrize("top", [0, -1, 201, True, 1.5, "20"])
def test_invalid_comparison_limits(research_db, top):
    with pytest.raises(ValueError, match="top"):
        compare_reports("A", "B", research_db, top)


@pytest.mark.parametrize("report", ["MISSING", "", " ", None])
def test_unknown_or_blank_reports(research_db, report):
    with pytest.raises(ValueError):
        compare_reports(report, "A", research_db)


def test_functions_preserve_source_bytes_and_missing_database_is_not_created(research_db, tmp_path):
    before = hashlib.sha256(research_db.read_bytes()).hexdigest()
    quality_audit(research_db)
    compare_reports("A", "B", research_db)
    assert hashlib.sha256(research_db.read_bytes()).hexdigest() == before
    absent = tmp_path / "absent.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        quality_audit(absent)
    assert not absent.exists()
