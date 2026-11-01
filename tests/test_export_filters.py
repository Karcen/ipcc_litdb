"""Filtered exports reproduce result selections, ordering and truncation."""
import csv
import io
import sqlite3

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import create_engine

import app as api
import db


@pytest.fixture
def client(tmp_path, monkeypatch, db_path):
    """Use the delivery schema with a small corpus containing repeated citations."""
    fixture = tmp_path / "export-fixture.sqlite"
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as source:
        schema = [row[0] for row in source.execute(
            "SELECT sql FROM sqlite_master WHERE type IN ('table','index') "
            "AND sql IS NOT NULL ORDER BY rowid"
        )]
    with sqlite3.connect(fixture) as connection:
        for statement in schema:
            connection.execute(statement)
        connection.executemany("INSERT INTO assessment_reports (report_id,report_label) VALUES (?,?)",
                               [("AR5", "Fifth Assessment"), ("AR6", "Sixth Assessment"),
                                ("EMPTY", "Empty Report")])
        connection.executemany("INSERT INTO working_groups VALUES (?,?,?)",
                               [(1, "AR6", "WG1"), (2, "AR6", "WG2"),
                                (3, "AR5", "WG1"), (4, "EMPTY", "WG1")])
        connection.executemany(
            "INSERT INTO chapters (chapter_id,report_id,wg_id,chapter_label) VALUES (?,?,?,?)",
            [("AR6A", "AR6", 1, "A"), ("AR6B", "AR6", 1, "B"),
             ("AR6C", "AR6", 2, "C"), ("AR5A", "AR5", 3, "A"),
             ("EMPTYA", "EMPTY", 4, "A")],
        )
        papers = [
            (1, "Carbon policy", "Alpha, A", "2018", "Climate Policy", "10.1234/alpha", 2, 3, 4, "wos_matched"),
            (2, "Carbon pricing", "Beta, B", "2020", "Climate Policy", "10.1234/beta", 1, 1, 2, "doi_verified"),
            (3, "Emission pathways", "Gamma, C", "2017", "Energy", "10.1234/gamma", 1, 1, 1, "unresolved"),
            (4, "Year unknown", "Delta, D", "", "Energy", "", 1, 1, 1, "unresolved"),
            (5, "Future report", "Epsilon, E", "2025", "Energy", "10.1234/epsilon", 1, 1, 1, "wos_matched"),
            (6, "Carbon overview", "Zeta, Z", "2019", "Energy", None, 1, 1, 1, "unresolved"),
        ]
        connection.executemany("""
            INSERT INTO papers (paper_id,title_best,authors_best,year_best,journal_best,
                                doi_norm,n_reports,n_chapters,n_occurrences,verification_status)
            VALUES (?,?,?,?,?,?,?,?,?,?)""", papers)
        occurrences = [
            ("r1", "AR6A", 1, 10, "matched_doi"), ("r2", "AR6A", 1, 11, "unmatched"),
            ("r3", "AR6B", 1, 2, "matched_doi"), ("r4", "AR5A", 1, 2, "matched_doi"),
            ("r5", "AR6A", 2, 2, "unmatched"), ("r6", "AR6A", 2, 3, "unmatched"),
            ("r7", "AR6C", 3, 1, "unmatched"), ("r8", "AR6A", 4, 4, "unmatched"),
            ("r9", "AR5A", 5, 5, "matched_title"), ("r10", "AR6C", 6, 6, "unmatched"),
        ]
        for occurrence, chapter, paper, page, status in occurrences:
            row = papers[paper - 1]
            connection.execute("""
                INSERT INTO references_occurrence
                    (occurrence_id,chapter_id,paper_id,source_page,match_status,
                     parsed_title,parsed_authors,parsed_year,parsed_journal,doi_norm,raw_reference)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (occurrence, chapter, paper, page, status, row[1], row[2],
                 None if paper == 6 else row[3], row[4], row[5], "Printed " + row[1]))
        connection.executemany("""INSERT INTO manual_review(occurrence_id,status,reason,note)
            VALUES (?,?,?,?)""", [
            ("r1", "open", "Initial chronology check", "Initial candidate"),
            ("r2", "open", "Printed citation requires comparison", "Source check"),
            ("r3", "resolved", "Online date verified", "Identity retained"),
            ("r4", "wont_fix", "Source retained", "Closed case"),
            ("r7", "in_progress", "Title link requires comparison", "Work in progress"),
            ("r9", "open", "Publication year requires comparison", "Title match candidate"),
            ("r10", "open", "Compound extraction candidate", "Source block"),
            ("r1", "in_progress", "Latest chronology check", "Latest review"),
        ])
    engine = create_engine(f"sqlite:///{fixture}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(db, "_engine", engine)
    with TestClient(api.app) as session:
        yield session
    engine.dispose()


@pytest.mark.parametrize("filters,expected", [
    ({"q": "carbon"}, [1, 2, 6]),
    ({"q": "10.1234/alpha"}, [1]),
    ({"doi": "https://doi.org/10.1234/ALPHA"}, [1]),
    ({"year_from": 2019, "year_to": 2020}, [2, 6]),
    ({"year_to": 2020}, [1, 2, 3, 6]),
    ({"min_reports": 2}, [1]),
    ({"report_id": "AR6", "wg": "WG1"}, [1, 2, 4]),
    ({"report_id": "AR6", "working_group": "WG2"}, [3, 6]),
    ({"chapter_id": "AR6B", "wg": "WG2"}, []),
    ({"chapter_id": "AR6A", "report_id": "AR5"}, []),
    ({"report_id": "AR6", "status": "doi_verified", "year_from": 2020}, [2]),
    ({"q": "carbon", "report_id": "AR6", "wg": "WG1", "year_from": 2018,
      "year_to": 2019, "min_reports": 2, "sort": "title"}, [1]),
])
def test_paper_export_matches_filters_and_result_rows(client, filters, expected):
    listing = client.get("/api/papers", params={**filters, "size": 100}).json()
    response = client.get("/api/export/papers.json", params={**filters, "limit": 100})
    assert response.status_code == 200, response.text
    exported = response.json()
    assert [row["paper_id"] for row in listing["items"]] == expected
    assert [row["paper_id"] for row in exported["rows"]] == expected
    assert exported["provenance"]["total_matches"] == listing["total"] == len(expected)
    assert response.headers["X-Export-Truncated"] == "false"


@pytest.mark.parametrize("sort,expected", [
    ("n_reports", [1, 2, 3, 4, 5, 6]),
    ("occurrences", [1, 2, 3, 4, 5, 6]),
    ("year", [4, 3, 1, 6, 2, 5]),
    ("title", [6, 1, 2, 3, 5, 4]),
])
def test_export_preserves_requested_order(client, sort, expected):
    listing = client.get("/api/papers", params={"sort": sort, "size": 100}).json()
    export = client.get("/api/export/papers.json", params={"sort": sort, "limit": 100}).json()
    assert [row["paper_id"] for row in listing["items"]] == expected
    assert [row["paper_id"] for row in export["rows"]] == expected


def test_paper_export_limits_unique_works_after_sorting(client):
    response = client.get("/api/export/papers.json",
                          params={"report_id": "AR6", "sort": "year", "limit": 2})
    payload = response.json()
    assert [row["paper_id"] for row in payload["rows"]] == [4, 3]
    assert payload["provenance"]["total_matches"] == 5
    assert payload["provenance"]["row_count"] == 2
    assert payload["provenance"]["truncated"] is True
    assert response.headers["X-Total-Count"] == "5"
    assert response.headers["X-Row-Count"] == "2"
    assert response.headers["X-Export-Limit"] == "2"


@pytest.mark.parametrize("kind,filters,expected", [
    ("unresolved", {"only_doi_less": "true"}, ["r8", "r10"]),
    ("unresolved", {"report_id": "AR5"}, []),
    ("unresolved", {"report_id": "AR6", "chapter_id": "AR6A", "only_doi_less": "true"}, ["r8"]),
    ("unresolved", {"working_group": "WG2"}, ["r7", "r10"]),
    ("references", {"chapter_id": "AR6A", "status": "unmatched", "q": "pricing"}, ["r5", "r6"]),
])
def test_occurrence_export_matches_queue_filters_and_order(client, kind, filters, expected):
    listing = client.get(f"/api/{kind}", params={**filters, "size": 100}).json()
    response = client.get(f"/api/export/{kind}.json", params={**filters, "limit": 100})
    assert response.status_code == 200, response.text
    exported = response.json()
    assert [row["occurrence_id"] for row in listing["items"]] == expected
    assert [row["occurrence_id"] for row in exported["rows"]] == expected
    assert exported["provenance"]["total_matches"] == listing["total"] == len(expected)


@pytest.mark.parametrize("limit", [-1, 0, 100001])
def test_invalid_export_limits_are_rejected(client, limit):
    assert client.get("/api/export/papers.json", params={"limit": limit}).status_code == 422


@pytest.mark.parametrize("fmt", ["csv", "json", "xlsx", "bibtex", "ris"])
def test_each_format_reports_filtered_truncation(client, fmt):
    response = client.get(f"/api/export/papers.{fmt}", params={"q": "carbon", "limit": 1})
    assert response.status_code == 200, response.text
    assert response.headers["X-Total-Count"] == "3"
    assert response.headers["X-Row-Count"] == "1"
    assert response.headers["X-Export-Truncated"] == "true"
    if fmt == "csv":
        text = response.content.decode("utf-8-sig")
        assert "total_matches: 3" in text.splitlines()[0]
        assert "truncated: true" in text.splitlines()[0]
        assert len(list(csv.DictReader(io.StringIO(text.split("\n", 1)[1])))) == 1
    elif fmt == "json":
        assert response.json()["provenance"]["truncated"] is True
    elif fmt == "xlsx":
        workbook = load_workbook(io.BytesIO(response.content), read_only=True)
        provenance = dict(workbook["Provenance"].values)
        assert provenance["total_matches"] == 3 and provenance["truncated"] is True
        workbook.close()
    elif fmt == "bibtex":
        assert response.text.startswith("% provenance:")
        assert response.text.count("@article{") == 1
        assert "truncated: true" in response.text
    else:
        assert response.text.count("TY  - ") == 1
        assert "N1  - provenance:" in response.text and "truncated: true" in response.text


def test_empty_chapters_do_not_invent_missing_dois(client):
    statistics = client.get("/api/statistics").json()
    empty_report = next(row for row in statistics["by_report"] if row["report_id"] == "EMPTY")
    empty_group = next(row for row in statistics["by_working_group"] if row["report_id"] == "EMPTY")
    empty_chapter = client.get("/api/chapters", params={"report_id": "EMPTY"}).json()[0]
    for row in (empty_report, empty_group, empty_chapter):
        assert row["n_references"] == 0
        assert row["missing_doi"] == 0
    assert sum(row["missing_doi"] for row in statistics["by_report"]) == statistics["totals"]["missing_doi"]


def test_year_completeness_excludes_empty_and_null_fields(client):
    statistics = client.get("/api/statistics").json()
    assert statistics["totals"]["references"] == 10
    assert statistics["completeness"]["year"] == 80.0


def test_review_queue_counts_distinct_open_cases_separately_from_unmatched(client):
    statistics = client.get("/api/statistics").json()["totals"]
    quality = client.get("/api/data-quality").json()["summary"]
    assert statistics["open_review_cases"] == quality["open_review_cases"] == 5
    assert statistics["manual_review_required"] == quality["manual_review_required"] == 6
    assert statistics["manual_review_required"] == statistics["unmatched"]
    default = client.get("/api/unresolved", params={"size": 100}).json()
    assert default["total"] == 6 and default["review_queue"] is False
    assert all(row["match_status"] == "unmatched" for row in default["items"])


@pytest.mark.parametrize("filters,expected", [
    ({}, ["r9", "r1", "r2", "r7", "r10"]),
    ({"status": "matched_doi"}, ["r1"]),
    ({"status": "matched_title"}, ["r9"]),
    ({"status": "unmatched"}, ["r2", "r7", "r10"]),
    ({"report_id": "AR5"}, ["r9"]),
    ({"working_group": "WG2"}, ["r7", "r10"]),
    ({"chapter_id": "AR6A", "q": "carbon"}, ["r1", "r2"]),
    ({"only_doi_less": "true"}, ["r10"]),
])
def test_open_review_queue_includes_match_statuses_and_preserves_filters_and_exports(client, filters, expected):
    filters = {**filters, "review_queue": "true"}
    listing = client.get("/api/unresolved", params={**filters, "size": 100}).json()
    assert listing["review_queue"] is True
    assert listing["total"] == len(expected)
    assert [row["occurrence_id"] for row in listing["items"]] == expected
    assert all(row["review_status"] in {"open", "in_progress"} and row["review_reason"] for row in listing["items"])
    response = client.get("/api/export/unresolved.json", params={**filters, "limit": 100})
    assert response.status_code == 200, response.text
    exported = response.json()
    assert [row["occurrence_id"] for row in exported["rows"]] == expected
    assert exported["provenance"]["total_matches"] == len(expected)
    assert "review_queue=True" in exported["provenance"]["filters"]
    assert all(row["review_status"] in {"open", "in_progress"} and row["review_reason"] for row in exported["rows"])
    if "r1" in expected:
        latest = next(row for row in listing["items"] if row["occurrence_id"] == "r1")
        assert latest["review_status"] == "in_progress"
        assert latest["review_reason"] == "Latest chronology check"
        assert latest["review_note"] == "Latest review"


def test_review_queue_pagination_counts_occurrences_without_duplicate_reviews(client):
    pages = [client.get("/api/unresolved", params={"review_queue": "true", "page": page, "size": 2}).json()
             for page in range(1, 4)]
    assert all(page["total"] == 5 for page in pages)
    assert [row["occurrence_id"] for page in pages for row in page["items"]] == ["r9", "r1", "r2", "r7", "r10"]
