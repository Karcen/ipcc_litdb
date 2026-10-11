"""Build citation networks with explicit populations, filters, and display limits."""
from __future__ import annotations

from collections import Counter
import math
from pathlib import Path
import sqlite3

from backend.config import sqlite_database_path
from analytics.graph_analysis import summarise

DEFAULT_MIN_WEIGHT = {
    "chapter-chapter": 0.25,
    "report-report": 0.02,
    "journal-chapter": 0.0,
    "author-author": 0.0,
    "paper-chapter": 0.0,
}

KINDS = {
    "paper-chapter": "papers and the chapters that cite them",
    "chapter-chapter": "chapters linked by shared literature (Jaccard)",
    "report-report": "assessment reports linked by shared literature",
    "journal-chapter": "journals and the chapters that cite them",
    "author-author": "co-authorship among the cited works",
}


def _con(db):
    path = Path(db).expanduser().resolve()
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def build(kind: str, db: Path | str | None = None, *, min_weight: float | None = None,
          limit: int = 300) -> dict:
    if kind not in KINDS:
        raise ValueError(f"unknown network: {kind}")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("network limit must be a positive integer")
    if min_weight is None:
        min_weight = DEFAULT_MIN_WEIGHT[kind]
    if kind in {"chapter-chapter", "report-report"}:
        if not math.isfinite(min_weight) or not 0 <= min_weight <= 1:
            raise ValueError("Jaccard threshold must be between 0 and 1")
    con = _con(sqlite_database_path() if db is None else db)
    try:
        if kind == "chapter-chapter":
            return _chapter_chapter(con, min_weight, limit)
        if kind == "report-report":
            return _report_report(con, min_weight, limit)
        if kind == "journal-chapter":
            return _journal_chapter(con, limit)
        if kind == "author-author":
            return _author_author(con, limit)
        return _paper_chapter(con, limit)
    finally:
        con.close()


def _pack(node_index, eligible_edges, limit, *, provenance, **params) -> dict:
    """Apply the display cap after graph filters and keep its endpoints."""
    connected = {edge[key] for edge in eligible_edges for key in ("source", "target")}
    edges = eligible_edges[:limit]
    displayed = {edge[key] for edge in edges for key in ("source", "target")}
    nodes = [node_index[node_id] for node_id in sorted(displayed)]
    payload = {
        "nodes": nodes,
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "parameters": {
            **params,
            "limit": limit,
            "eligible_edge_count": len(eligible_edges),
            "truncated": len(eligible_edges) > limit,
            "omitted_edge_count": len(eligible_edges) - len(edges),
            "node_population": len(node_index),
            "isolates_after_filters": len(node_index) - len(connected),
            "omitted_connected_nodes": len(connected) - len(displayed),
        },
        "provenance": {
            "graph_type": "simple undirected" if params["kind"] in {
                "chapter-chapter", "report-report", "author-author"
            } else "simple undirected bipartite",
            "node_selection": "Endpoints of displayed edges; isolates are counted but omitted from the view.",
            "limit_applies_to": "Eligible edges after all graph filters.",
            "isolate_definition": "Population nodes with no eligible edge after filters, before the display limit.",
            **provenance,
        },
    }

    payload["analysis"] = summarise(payload)
    return payload


def _chapter_nodes(con):
    return {
        row["chapter_id"]: {
            "id": row["chapter_id"],
            "label": f"{row['report_id']} {row['chapter_label']}",
            "group": row["report_id"],
            "size": 0,
            "kind": "chapter",
            "report_id": row["report_id"],
            "wg": row["wg_label"],
            "chapter_label": row["chapter_label"],
        }
        for row in con.execute("""
            SELECT c.chapter_id, c.report_id, c.chapter_label, wg.wg_label
            FROM chapters c LEFT JOIN working_groups wg ON wg.wg_id = c.wg_id
            ORDER BY c.chapter_id
        """)
    }


def _overlap_edges(sets, min_weight):
    keys = sorted(sets)
    edges = []
    for i, source in enumerate(keys):
        for target in keys[i + 1:]:
            shared = len(sets[source] & sets[target])
            if not shared:
                continue
            weight = shared / len(sets[source] | sets[target])
            if weight >= min_weight:
                edges.append({"source": source, "target": target, "weight": weight, "shared": shared})
    return sorted(edges, key=lambda edge: (-edge["weight"], edge["source"], edge["target"]))


def _chapter_chapter(con, min_weight: float, limit: int) -> dict:
    nodes = _chapter_nodes(con)
    sets = {chapter_id: set() for chapter_id in nodes}
    for row in con.execute("""
        SELECT r.chapter_id, r.paper_id FROM references_occurrence r
        JOIN chapters c ON c.chapter_id = r.chapter_id WHERE r.paper_id IS NOT NULL
    """):
        sets[row["chapter_id"]].add(row["paper_id"])
    for chapter_id, papers in sets.items():
        nodes[chapter_id]["size"] = len(papers)
    return _pack(nodes, _overlap_edges(sets, min_weight), limit,
                 kind="chapter-chapter", min_weight=min_weight, provenance={
                     "population_scope": "All chapters in the chapter catalogue, including chapters with no extracted paper links.",
                     "edge_definition": "A pair of chapters sharing at least one paper and meeting the Jaccard threshold.",
                     "weight_definition": "Shared distinct paper IDs divided by the union of distinct paper IDs.",
                     "node_size_definition": "Distinct paper IDs linked to the chapter before edge filters.",
                     "edge_ranking": "Jaccard descending, then chapter IDs ascending.",
                 })


def _report_report(con, min_weight: float, limit: int) -> dict:
    sets = {row[0]: set() for row in con.execute("SELECT report_id FROM assessment_reports ORDER BY report_id")}
    for row in con.execute("""
        SELECT c.report_id, r.paper_id FROM chapters c
        JOIN references_occurrence r ON r.chapter_id = c.chapter_id WHERE r.paper_id IS NOT NULL
    """):
        sets[row["report_id"]].add(row["paper_id"])
    nodes = {report_id: {"id": report_id, "label": report_id, "group": report_id, "size": len(papers)}
             for report_id, papers in sets.items()}
    return _pack(nodes, _overlap_edges(sets, min_weight), limit,
                 kind="report-report", min_weight=min_weight, provenance={
                     "population_scope": "All assessment reports in the report catalogue, including reports with no extracted paper links.",
                     "edge_definition": "A pair of reports sharing at least one paper and meeting the Jaccard threshold.",
                     "weight_definition": "Shared distinct paper IDs divided by the union of distinct paper IDs.",
                     "node_size_definition": "Distinct paper IDs linked to the report before edge filters.",
                     "edge_ranking": "Jaccard descending, then report IDs ascending.",
                 })


def _journal_chapter(con, limit: int) -> dict:
    rows = con.execute("""
        SELECT p.journal_best AS journal, r.chapter_id, COUNT(*) AS n
        FROM references_occurrence r
        JOIN papers p ON p.paper_id = r.paper_id
        JOIN chapters c ON c.chapter_id = r.chapter_id
        WHERE p.journal_best IS NOT NULL AND TRIM(p.journal_best) <> ''
        GROUP BY p.journal_best, r.chapter_id
        ORDER BY n DESC, p.journal_best, r.chapter_id
    """).fetchall()
    nodes = _chapter_nodes(con)
    chapter_counts = Counter(row["journal"] for row in rows)
    edges = []
    for row in rows:
        journal_id = f"J::{row['journal']}"
        nodes.setdefault(journal_id, {"id": journal_id, "label": row["journal"][:48],
                                      "group": "journal", "size": 0, "kind": "journal"})
        if chapter_counts[row["journal"]] >= 2:
            edges.append({"source": journal_id, "target": row["chapter_id"], "weight": row["n"]})
            nodes[journal_id]["size"] += row["n"]
            nodes[row["chapter_id"]]["size"] += row["n"]
    return _pack(nodes, edges, limit, kind="journal-chapter", min_chapters=2, provenance={
        "population_scope": "All catalogue chapters and named journals linked to extracted references.",
        "edge_definition": "A journal cited by a chapter; journals must occur in at least two chapters before the display limit.",
        "weight_definition": "Reference occurrences for the journal in the chapter; repeated occurrences count separately.",
        "node_size_definition": "Sum of reference-occurrence weights on eligible edges before the display limit.",
        "edge_ranking": "Reference occurrences descending, then journal name and chapter ID ascending.",
    })


def _author_author(con, limit: int) -> dict:
    by_paper: dict[int, set] = {}
    nodes = {}
    for row in con.execute("""
        SELECT pa.paper_id, a.author_id, a.surname, a.given
        FROM paper_authors pa JOIN authors a ON a.author_id = pa.author_id
        JOIN papers p ON p.paper_id = pa.paper_id
        WHERE p.n_reports > 1 ORDER BY pa.paper_id, a.author_id
    """):
        by_paper.setdefault(row["paper_id"], set()).add(row["author_id"])
        author_id = str(row["author_id"])
        nodes[author_id] = {"id": author_id, "label": f"{row['surname'] or ''} {row['given'] or ''}".strip(),
                            "group": "author", "size": 0}
    pairs = Counter()
    for author_ids in by_paper.values():
        ids = sorted(author_ids)
        for i, source in enumerate(ids):
            for target in ids[i + 1:]:
                pairs[(source, target)] += 1
    ranked = sorted(((pair, weight) for pair, weight in pairs.items() if weight >= 2),
                    key=lambda item: (-item[1], item[0]))
    edges = []
    for (source, target), weight in ranked:
        source, target = str(source), str(target)
        edges.append({"source": source, "target": target, "weight": weight})
        nodes[source]["size"] += weight
        nodes[target]["size"] += weight
    return _pack(nodes, edges, limit, kind="author-author", min_coauthored_papers=2,
                 scope="authors of papers cited in more than one report", provenance={
                     "population_scope": "All recorded authors of papers cited in more than one report; no per-paper author cap.",
                     "edge_definition": "An author pair appearing together on at least two distinct papers in scope.",
                     "weight_definition": "Distinct coauthored paper IDs in scope; duplicate author-paper rows count once.",
                     "node_size_definition": "Sum of coauthored-paper weights on eligible edges before the display limit.",
                     "edge_ranking": "Coauthored papers descending, then numeric author IDs ascending.",
                 })


def _paper_chapter(con, limit: int) -> dict:
    nodes = _chapter_nodes(con)
    for row in con.execute("""
        SELECT paper_id, title_best, n_reports, n_chapters FROM papers
        WHERE n_chapters >= 3 ORDER BY paper_id
    """):
        paper_id = f"P::{row['paper_id']}"
        nodes[paper_id] = {"id": paper_id, "label": (row["title_best"] or "")[:52],
                           "group": "paper", "size": row["n_chapters"], "kind": "paper",
                           "n_reports": row["n_reports"]}
    edges = []
    for row in con.execute("""
        SELECT DISTINCT p.paper_id, r.chapter_id
        FROM papers p JOIN references_occurrence r ON r.paper_id = p.paper_id
        JOIN chapters c ON c.chapter_id = r.chapter_id
        WHERE p.n_chapters >= 3 ORDER BY p.n_chapters DESC, p.paper_id, r.chapter_id
    """):
        edges.append({"source": f"P::{row['paper_id']}", "target": row["chapter_id"], "weight": 1})
        nodes[row["chapter_id"]]["size"] += 1
    return _pack(nodes, edges, limit, kind="paper-chapter", min_chapters_per_paper=3, provenance={
        "population_scope": "All catalogue chapters and papers whose recorded n_chapters is at least three; no per-paper chapter cap.",
        "edge_definition": "A distinct qualifying paper-chapter link; repeated reference occurrences count once.",
        "weight_definition": "One per distinct paper-chapter link.",
        "node_size_definition": "Paper: recorded n_chapters. Chapter: number of qualifying linked papers before the display limit.",
        "edge_ranking": "Recorded chapters per paper descending, then numeric paper ID and chapter ID ascending.",
    })
