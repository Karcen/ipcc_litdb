"""Network populations, weights, and display limits on small SQLite fixtures."""
import sqlite3

import pytest

from analytics import networks


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "networks.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE assessment_reports (report_id TEXT PRIMARY KEY);
        CREATE TABLE working_groups (wg_id TEXT PRIMARY KEY, wg_label TEXT);
        CREATE TABLE chapters (
            chapter_id TEXT PRIMARY KEY, report_id TEXT, wg_id TEXT, chapter_label TEXT
        );
        CREATE TABLE papers (
            paper_id INTEGER PRIMARY KEY, title_best TEXT, n_reports INTEGER,
            n_chapters INTEGER, journal_best TEXT
        );
        CREATE TABLE references_occurrence (paper_id INTEGER, chapter_id TEXT);
        CREATE TABLE authors (author_id INTEGER PRIMARY KEY, surname TEXT, given TEXT);
        CREATE TABLE paper_authors (paper_id INTEGER, author_id INTEGER);
        INSERT INTO working_groups VALUES ('WG1', 'WG1');
    """)
    yield path, connection
    connection.close()


def add_chapters(database, names):
    connection = database[1]
    for chapter, report in names:
        connection.execute("INSERT OR IGNORE INTO assessment_reports VALUES (?)", (report,))
        connection.execute("INSERT INTO chapters VALUES (?, ?, 'WG1', ?)", (chapter, report, chapter))


def add_papers(database, rows):
    database[1].executemany("INSERT INTO papers VALUES (?, ?, ?, ?, ?)", rows)


def build(database, kind, **parameters):
    database[1].commit()
    return networks.build(kind, db=database[0], **parameters)


def assert_only_edge_endpoints(graph):
    endpoints = {edge[key] for edge in graph["edges"] for key in ("source", "target")}
    assert {node["id"] for node in graph["nodes"]} == endpoints
    assert graph["node_count"] == len(endpoints)
    assert graph["edge_count"] == len(graph["edges"])


def test_strongest_coauthor_pair_is_retained_and_limits_are_reported(database):
    add_papers(database, [(i, f"Paper {i}", 2, 2, None) for i in range(1, 7)])
    database[1].executemany("INSERT INTO authors VALUES (?, ?, NULL)", [(i, f"Author {i}") for i in range(1, 5)])
    database[1].executemany("INSERT INTO paper_authors VALUES (?, ?)", [
        (1, 1), (1, 2), (2, 1), (2, 2), (3, 1), (3, 2),
        (4, 2), (4, 3), (5, 2), (5, 3), (6, 4),
    ])
    graph = build(database, "author-author", limit=1)
    assert graph["edges"] == [{"source": "1", "target": "2", "weight": 3}]
    parameters = graph["parameters"]
    assert parameters["limit"] == 1
    assert parameters["eligible_edge_count"] == 2
    assert parameters["omitted_edge_count"] == 1
    assert parameters["truncated"] is True
    assert parameters["node_population"] == 4
    assert parameters["isolates_after_filters"] == 1
    assert parameters["omitted_connected_nodes"] == 1
    assert {node["id"]: node["size"] for node in graph["nodes"]} == {"1": 3, "2": 5}
    assert "Distinct coauthored paper IDs" in graph["provenance"]["weight_definition"]
    assert_only_edge_endpoints(graph)


def test_all_authors_in_large_papers_are_included(database):
    add_papers(database, [(1, "Large paper", 2, 2, None), (2, "Another large paper", 2, 2, None)])
    database[1].executemany("INSERT INTO authors VALUES (?, ?, NULL)", [(i, f"Author {i}") for i in range(1, 27)])
    database[1].executemany("INSERT INTO paper_authors VALUES (?, ?)", [(paper, author) for paper in (1, 2) for author in range(1, 27)])
    graph = build(database, "author-author", limit=1000)
    assert graph["edge_count"] == 26 * 25 // 2
    assert graph["node_count"] == 26
    assert all(edge["weight"] == 2 for edge in graph["edges"])
    assert all(node["size"] == 50 for node in graph["nodes"])
    assert graph["parameters"]["truncated"] is False
    assert graph["parameters"]["isolates_after_filters"] == 0
    assert_only_edge_endpoints(graph)


def test_duplicate_author_rows_do_not_inflate_coauthorship(database):
    add_papers(database, [(1, "One paper", 2, 2, None)])
    database[1].executemany("INSERT INTO authors VALUES (?, ?, NULL)", [(1, "A"), (2, "B")])
    database[1].executemany("INSERT INTO paper_authors VALUES (?, ?)", [(1, 1), (1, 1), (1, 2), (1, 2)])
    graph = build(database, "author-author", limit=5)
    assert graph["edges"] == []  # One paper does not meet the two-paper filter.
    assert graph["parameters"]["node_population"] == 2
    assert graph["parameters"]["isolates_after_filters"] == 2


@pytest.mark.parametrize("kind", ["chapter-chapter", "report-report"])
def test_overlap_uses_distinct_papers_and_omits_only_counted_nodes(database, kind):
    add_chapters(database, [("A", "R1"), ("B", "R2"), ("C", "R3"), ("D", "R4")])
    database[1].executemany("INSERT INTO references_occurrence VALUES (?, ?)", [
        (1, "A"), (1, "A"), (2, "A"), (2, "B"), (1, "C"),
    ])
    graph = build(database, kind, min_weight=0.4, limit=1)
    source, target = ("A", "B") if kind == "chapter-chapter" else ("R1", "R2")
    assert graph["edges"] == [{"source": source, "target": target, "weight": 0.5, "shared": 1}]
    assert graph["parameters"]["eligible_edge_count"] == 2
    assert graph["parameters"]["node_population"] == 4
    assert graph["parameters"]["isolates_after_filters"] == 1
    assert graph["parameters"]["omitted_connected_nodes"] == 1
    assert {node["id"]: node["size"] for node in graph["nodes"]} == {source: 2, target: 1}
    assert_only_edge_endpoints(graph)


def test_empty_overlap_keeps_catalogue_population_in_provenance(database):
    add_chapters(database, [("A", "R1"), ("B", "R2")])
    graph = build(database, "chapter-chapter", limit=5)
    assert graph["nodes"] == graph["edges"] == []
    assert graph["parameters"]["node_population"] == 2
    assert graph["parameters"]["isolates_after_filters"] == 2
    assert graph["parameters"]["omitted_connected_nodes"] == 0
    assert graph["parameters"]["truncated"] is False


def test_journal_filter_runs_before_display_limit(database):
    add_chapters(database, [("A", "R1"), ("B", "R1"), ("C", "R1")])
    add_papers(database, [(1, "Multi-chapter journal", 1, 2, "J1"), (2, "Single-chapter journal", 1, 1, "J2")])
    database[1].executemany("INSERT INTO references_occurrence VALUES (?, ?)", [(1, "A")] * 10 + [(1, "B")] + [(2, "A")] * 9)
    graph = build(database, "journal-chapter", limit=1)
    assert graph["edges"] == [{"source": "J::J1", "target": "A", "weight": 10}]
    assert graph["parameters"]["eligible_edge_count"] == 2
    assert graph["parameters"]["node_population"] == 5
    assert graph["parameters"]["isolates_after_filters"] == 2  # C and filtered journal J2.
    assert graph["parameters"]["omitted_connected_nodes"] == 1
    assert {node["id"]: node["size"] for node in graph["nodes"]} == {"J::J1": 11, "A": 10}
    assert "occurrences" in graph["provenance"]["weight_definition"]
    assert_only_edge_endpoints(graph)


def test_paper_links_have_no_hidden_chapter_cap_and_are_deduplicated(database):
    add_chapters(database, [(f"C{i:02}", "R1") for i in range(1, 15)])
    add_papers(database, [(1, "Many chapters", 1, 13, None)])
    database[1].executemany("INSERT INTO references_occurrence VALUES (?, ?)", [(1, f"C{i:02}") for i in range(1, 14)] + [(1, "C01")])
    graph = build(database, "paper-chapter", limit=20)
    assert graph["edge_count"] == 13
    assert graph["node_count"] == 14
    assert graph["parameters"]["node_population"] == 15
    assert graph["parameters"]["isolates_after_filters"] == 1
    assert graph["parameters"]["truncated"] is False
    assert all(node["size"] == 1 for node in graph["nodes"] if node["kind"] == "chapter")
    assert_only_edge_endpoints(graph)


def test_paper_population_is_not_selected_by_a_hidden_paper_limit(database):
    add_chapters(database, [("A", "R1"), ("B", "R1"), ("C", "R1")])
    add_papers(database, [(i, f"Paper {i}", 1, 3, None) for i in range(1, 5)])
    database[1].executemany("INSERT INTO references_occurrence VALUES (?, ?)", [(paper, chapter) for paper in range(1, 5) for chapter in ("A", "B", "C")])
    graph = build(database, "paper-chapter", limit=20)
    assert graph["edge_count"] == 12
    assert graph["node_count"] == 7
    limited = build(database, "paper-chapter", limit=2)
    assert limited["parameters"]["eligible_edge_count"] == 12
    assert limited["parameters"]["omitted_edge_count"] == 10
    assert limited["parameters"]["omitted_connected_nodes"] == 4
    assert_only_edge_endpoints(limited)


@pytest.mark.parametrize("limit", [0, -1, 1.5, True])
def test_invalid_edge_limits_are_rejected(database, limit):
    with pytest.raises(ValueError, match="positive integer"):
        build(database, "chapter-chapter", limit=limit)


@pytest.mark.parametrize("threshold", [-0.1, 1.1, float("nan"), float("inf")])
def test_invalid_jaccard_thresholds_are_rejected(database, threshold):
    with pytest.raises(ValueError, match="between 0 and 1"):
        build(database, "chapter-chapter", min_weight=threshold)
