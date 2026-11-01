"""Deterministic analysis engine.

Runs a fixed set of documented analyses against the database and persists the
results. Each analysis records its SQL and parameters for reproduction.

Run:  python -m analytics.cli refresh
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from backend.config import sqlite_database_path

SOFTWARE_VERSION = "analytics-1.1.0"

ANALYSIS_TABLES = """
CREATE TABLE IF NOT EXISTS analysis_runs (
    run_id            INTEGER PRIMARY KEY,
    start_time        TEXT,
    end_time          TEXT,
    source            TEXT,
    rows_processed    INTEGER,
    rows_added        INTEGER,
    rows_changed      INTEGER,
    errors            INTEGER,
    warnings          INTEGER,
    analysis_version  TEXT,
    note              TEXT
);
CREATE TABLE IF NOT EXISTS analysis_results (
    analysis_id     INTEGER PRIMARY KEY,
    run_id          INTEGER REFERENCES analysis_runs(run_id),
    analysis_type   TEXT NOT NULL,
    parameters      TEXT,
    query           TEXT,
    input_version   TEXT,
    output          TEXT,
    created_at      TEXT,
    software_version TEXT
);
CREATE INDEX IF NOT EXISTS ix_analysis_type ON analysis_results(analysis_type);
CREATE TABLE IF NOT EXISTS insights (
    insight_id    INTEGER PRIMARY KEY,
    run_id        INTEGER,
    category      TEXT,
    severity      TEXT,
    title_en      TEXT, title_zh TEXT,
    body_en       TEXT, body_zh TEXT,
    evidence      TEXT,
    link          TEXT,
    created_at    TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Engine:
    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path).expanduser().resolve() if db_path is not None else sqlite_database_path()
        self.con = sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True)
        self.con.row_factory = sqlite3.Row
        self.results: list[dict] = []
        self.insights: list[dict] = []
        self.warnings: list[str] = []

    # ------------------------------------------------------------------ utils
    def q(self, sql: str, **p) -> list[dict]:
        return [dict(r) for r in self.con.execute(sql, p)]

    def q1(self, sql: str, **p):
        r = self.con.execute(sql, p).fetchone()
        return (r[0] if r else None)

    def record(self, atype: str, output: Any, query: str = "",
               parameters: dict | None = None) -> None:
        # objects are kept live in memory; serialisation happens in persist()
        self.results.append({
            "analysis_type": atype,
            "parameters": parameters or {},
            "query": query[:4000],
            "output": output,
        })

    def insight(self, category: str, severity: str, title_en: str,
                body_en: str, evidence: dict | None = None,
                link: str = "") -> None:
        self.insights.append({
            "category": category, "severity": severity,
            # Legacy column names remain compatible with existing snapshots.
            "title_en": title_en, "title_zh": title_en,
            "body_en": body_en, "body_zh": body_en,
            "evidence": evidence or {},
            "link": link,
        })

    # ------------------------------------------------------------- analyses
    def a_coverage_by_report(self) -> None:
        sql = """
        SELECT ar.report_id,
               COUNT(*) AS n_references,
               COUNT(DISTINCT r.paper_id) AS unique_papers,
               SUM(CASE WHEN r.match_status IN ('matched_doi','matched_title') THEN 1 ELSE 0 END) AS matched,
               SUM(CASE WHEN r.match_status='unmatched' THEN 1 ELSE 0 END) AS unmatched,
               SUM(CASE WHEN r.doi_norm IS NULL OR r.doi_norm='' THEN 1 ELSE 0 END) AS missing_doi,
               ROUND(100.0*SUM(CASE WHEN r.match_status IN ('matched_doi','matched_title') THEN 1 ELSE 0 END)
                     / NULLIF(COUNT(*),0), 2) AS match_rate
        FROM references_occurrence r
        JOIN chapters c ON c.chapter_id = r.chapter_id
        JOIN assessment_reports ar ON ar.report_id = c.report_id
        GROUP BY ar.report_id ORDER BY n_references DESC"""
        rows = self.q(sql)
        self.record("coverage_by_report", rows, sql)
        if rows:
            worst = min(rows, key=lambda r: r["match_rate"] or 0)
            best = max(rows, key=lambda r: r["match_rate"] or 0)
            self.insight(
                "coverage", "info",
                f"{best['report_id']} has the highest match rate ({best['match_rate']}%)",
                f"{best['report_id']} matches {best['matched']:,} of {best['n_references']:,} "
                f"references ({best['match_rate']}%). At the other end, "
                f"{worst['report_id']} matches {worst['match_rate']}%.",
                {"best": best, "worst": worst}, "#/quality")

    def a_chapter_outliers(self) -> None:
        """Chapters whose unresolved share is a statistical outlier.

        Rule: unresolved rate per chapter, compared with the corpus-wide rate
        using a two-sided binomial tail (normal approximation, |z| >= 3).
        Documented and deterministic.
        """
        rows = self.q("""
            SELECT c.chapter_id, c.report_id, wg.wg_label, c.chapter_label, c.chapter_title,
                   COUNT(*) AS n,
                   SUM(CASE WHEN r.match_status='unmatched' THEN 1 ELSE 0 END) AS unresolved
            FROM references_occurrence r
            JOIN chapters c ON c.chapter_id = r.chapter_id
            JOIN working_groups wg ON wg.wg_id = c.wg_id
            GROUP BY c.chapter_id HAVING COUNT(*) >= 30""")
        total = sum(r["n"] for r in rows)
        base = sum(r["unresolved"] for r in rows) / total if total else 0
        out = []
        for r in rows:
            n, k = r["n"], r["unresolved"]
            p = k / n
            se = math.sqrt(base * (1 - base) / n) if 0 < base < 1 else 0
            z = (p - base) / se if se else 0.0
            if abs(z) >= 3:
                out.append({**r, "rate": round(100 * p, 2), "z": round(z, 2)})
        out.sort(key=lambda r: -r["z"])
        self.record("chapter_unresolved_outliers", out,
                    "-- chapters with |z| >= 3 on the binomial unresolved rate; baseline "
                    f"{round(100*base,2)}% over {total} references", {"min_refs": 30, "z": 3})
        if out:
            w = out[0]
            self.insight(
                "unresolved", "warning",
                f"{w['report_id']} {w['chapter_label']} has an unusually high unresolved rate",
                f"{w['chapter_label']} leaves {w['unresolved']:,} of {w['n']:,} references "
                f"unresolved ({w['rate']}%), against a corpus baseline of "
                f"{round(100*base,2)}% (z = {w['z']}).",
                {"chapter": w, "baseline": round(100 * base, 2)},
                f"#/reports")
        self._chapter_outliers = out

    def a_reference_age(self) -> None:
        """Occurrence-weighted ages, using the publication year of each source."""
        sql = """
            SELECT ar.report_id, p.year_best AS year, s.publication_year AS source_year
            FROM references_occurrence r
            JOIN chapters c ON c.chapter_id = r.chapter_id
            JOIN assessment_reports ar ON ar.report_id = c.report_id
            LEFT JOIN sources s ON s.source_id = c.source_id
            LEFT JOIN papers p ON p.paper_id = r.paper_id"""
        rows = self.q(sql)
        report_year = {
            "AR1": 1990, "AR2": 1995, "AR3": 2001, "AR4": 2007, "AR5": 2014, "AR6": 2021,
            "SRES": 2000, "SREX": 2012, "SRREN": 2011, "SR15": 2018, "SRCCL": 2019,
            "SROCC": 2019, "2019REF": 2019, "2006GL": 2006, "SRCCS": 2005,
        }
        by = {}
        for r in rows:
            group = by.setdefault(r["report_id"], {
                "ages": [], "citation_count": 0, "source_year_count": 0,
                "fallback_year_count": 0, "base_years": set(),
                "excluded_missing_publication_year": 0, "excluded_missing_report_year": 0,
                "excluded_future_year": 0, "excluded_over_60_years": 0,
            })
            group["citation_count"] += 1
            years = re.findall(r"\b([12][0-9]{3})\b", str(r["source_year"] or ""))
            source_year = int(years[0]) if len(years) == 1 else None
            base = source_year or report_year.get(r["report_id"])
            if base is None:
                group["excluded_missing_report_year"] += 1
                continue
            group["base_years"].add(base)
            group["source_year_count" if source_year else "fallback_year_count"] += 1
            year = str(r["year"] or "").strip()
            if not re.fullmatch(r"[12][0-9]{3}", year):
                group["excluded_missing_publication_year"] += 1
                continue
            age = base - int(year)
            if age < 0:
                group["excluded_future_year"] += 1
            elif age > 60:
                group["excluded_over_60_years"] += 1
            else:
                group["ages"].append(age)
        out = []
        for rid, group in sorted(by.items()):
            ages = group.pop("ages")
            group["base_years"] = sorted(group["base_years"])
            out.append({
                "report_id": rid, "n": len(ages), **group,
                "median_age": round(statistics.median(ages), 1) if ages else None,
                "p25": round(statistics.quantiles(ages, n=4)[0], 1) if len(ages) > 3 else None,
                "p75": round(statistics.quantiles(ages, n=4)[2], 1) if len(ages) > 3 else None,
                "mean_age": round(statistics.fmean(ages), 1) if ages else None,
            })
        self.record("reference_age_by_report", out, sql, {
            "year_source": "sources.publication_year; report fallback when absent or ambiguous",
            "fallback_report_years": report_year, "age_range": [0, 60],
            "grain": "reference occurrence",
        })
        dated = [r for r in out if r["n"]]
        if len(dated) >= 2:
            oldest = max(dated, key=lambda r: r["median_age"])
            self.insight(
                "bibliometrics", "info",
                f"{oldest['report_id']} cites the oldest literature (median {oldest['median_age']} years)",
                f"Across {oldest['n']:,} dated references in {oldest['report_id']}, the median "
                f"cited work was published {oldest['median_age']} years before the report.",
                {"report": oldest}, "#/analytics")

    def a_persistence(self) -> None:
        """Cross-generation persistence: papers cited by more than one report."""
        rows = self.q("""
            SELECT p.paper_id, p.title_best, p.year_best, p.journal_best,
                   p.n_reports, p.n_chapters, p.n_occurrences,
                   GROUP_CONCAT(DISTINCT c.report_id) AS reports
            FROM papers p
            JOIN references_occurrence r ON r.paper_id = p.paper_id
            JOIN chapters c ON c.chapter_id = r.chapter_id
            GROUP BY p.paper_id HAVING p.n_reports > 1
            ORDER BY p.n_reports DESC, p.n_chapters DESC LIMIT 200""")
        span = self.q("""
            SELECT p.paper_id, p.title_best,
                   MIN(c.report_id) AS first_report, MAX(c.report_id) AS last_report,
                   COUNT(DISTINCT c.report_id) AS n_reports
            FROM papers p
            JOIN references_occurrence r ON r.paper_id = p.paper_id
            JOIN chapters c ON c.chapter_id = r.chapter_id
            GROUP BY p.paper_id HAVING COUNT(DISTINCT c.report_id) > 1
            ORDER BY n_reports DESC LIMIT 50""")
        n_total = self.q1("SELECT COUNT(*) FROM papers")
        n_multi = self.q1("SELECT COUNT(*) FROM papers WHERE n_reports > 1")
        self.record("cross_generation_persistence",
                    {"top": rows, "span_examples": span, "total_papers": n_total,
                     "multi_report_papers": n_multi},
                    "-- papers appearing in more than one assessment report (descriptive only)")
        if rows:
            t = rows[0]
            self.insight(
                "persistence", "info",
                f"{n_multi:,} papers are cited by more than one IPCC report",
                f"The most widely cited work appears in {t['n_reports']} reports and "
                f"{t['n_chapters']} chapters: “{t['title_best']}”.",
                {"top": t, "multi_report_papers": n_multi}, "#/analytics")

    def a_chapter_overlap(self) -> None:
        """Jaccard similarity between chapters over their paper sets.

        J(A,B) = |A ∩ B| / |A ∪ B|, computed on distinct paper_id per chapter.
        Chapters with fewer than 20 references are excluded.
        """
        rows = self.q("""
            SELECT c.chapter_id, r.paper_id
            FROM references_occurrence r
            JOIN chapters c ON c.chapter_id = r.chapter_id
            WHERE r.paper_id IS NOT NULL""")
        sets: dict[str, set] = {}
        for r in rows:
            sets.setdefault(r["chapter_id"], set()).add(r["paper_id"])
        sets = {k: v for k, v in sets.items() if len(v) >= 20}
        keys = sorted(sets)
        pairs = []
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = sets[keys[i]], sets[keys[j]]
                inter = len(a & b)
                if inter == 0:
                    continue
                jac = inter / len(a | b)
                if jac >= 0.25:
                    pairs.append({"a": keys[i], "b": keys[j], "shared": inter,
                                  "jaccard": round(jac, 3)})
        pairs.sort(key=lambda p: -p["jaccard"])
        self.record("chapter_overlap_jaccard", pairs[:100],
                    "-- Jaccard(J) = |A∩B| / |A∪B| on distinct paper_ids; chapters with "
                    "<20 references excluded; J >= 0.25 reported", {"min_papers": 20, "threshold": 0.25})
        if pairs:
            p = pairs[0]
            self.insight(
                "overlap", "info",
                f"Two chapters share {p['jaccard']*100:.0f}% of their literature",
                f"{p['a']} and {p['b']} share {p['shared']:,} papers (Jaccard {p['jaccard']}).",
                p, "#/networks")

    def a_journal_concentration(self) -> None:
        """Herfindahl-Hirschman index of journal shares, per Working Group."""
        rows = self.q("""
            SELECT wg.wg_label AS wg, p.journal_best AS journal, COUNT(*) AS n
            FROM references_occurrence r
            JOIN chapters c ON c.chapter_id = r.chapter_id
            JOIN working_groups wg ON wg.wg_id = c.wg_id
            JOIN papers p ON p.paper_id = r.paper_id
            WHERE p.journal_best IS NOT NULL AND p.journal_best <> ''
            GROUP BY wg.wg_label, p.journal_best""")
        by_wg: dict[str, list] = {}
        for r in rows:
            by_wg.setdefault(r["wg"], []).append(r)
        out = []
        for wg, items in by_wg.items():
            tot = sum(i["n"] for i in items)
            if tot < 200:
                continue
            shares = [(i["journal"], i["n"] / tot) for i in items]
            hhi = sum(s * s for _, s in shares)
            shares.sort(key=lambda x: -x[1])
            out.append({"working_group": wg, "references": tot,
                        "hhi": round(hhi, 4),
                        "hhi_interpretation": (
                            "concentrated" if hhi > 0.10 else
                            "moderately concentrated" if hhi > 0.05 else "dispersed"),
                        "top_journals": [{"journal": j, "share": round(s * 100, 2)}
                                         for j, s in shares[:10]]})
        out.sort(key=lambda r: -r["hhi"])
        self.record("journal_concentration", out,
                    "-- HHI = sum of squared journal shares per Working Group; "
                    ">0.10 concentrated, 0.05-0.10 moderate, <0.05 dispersed", {"min_refs": 200})
        if out:
            h = out[0]
            self.insight(
                "bibliometrics", "info",
                f"{h['working_group']} literature is the most concentrated by journal",
                f"Across {h['references']:,} references, HHI = {h['hhi']} "
                f"({h['hhi_interpretation']}). Largest share: "
                f"{h['top_journals'][0]['journal']} at {h['top_journals'][0]['share']}%.",
                {"working_group": h["working_group"], "hhi": h["hhi"]}, "#/analytics")

    def a_metadata_completeness(self) -> None:
        sql = """
        SELECT ar.report_id,
               ROUND(100.0*SUM(CASE WHEN r.parsed_title<>'' THEN 1 ELSE 0 END)/COUNT(*),2) AS has_title,
               ROUND(100.0*SUM(CASE WHEN r.parsed_authors<>'' THEN 1 ELSE 0 END)/COUNT(*),2) AS has_authors,
               ROUND(100.0*SUM(CASE WHEN r.parsed_journal<>'' THEN 1 ELSE 0 END)/COUNT(*),2) AS has_journal,
               ROUND(100.0*SUM(CASE WHEN r.parsed_year IS NOT NULL AND r.parsed_year<>'' THEN 1 ELSE 0 END)/COUNT(*),2) AS has_year,
               ROUND(100.0*SUM(CASE WHEN r.doi_norm IS NOT NULL AND r.doi_norm<>'' THEN 1 ELSE 0 END)/COUNT(*),2) AS has_doi
        FROM references_occurrence r
        JOIN chapters c ON c.chapter_id = r.chapter_id
        JOIN assessment_reports ar ON ar.report_id = c.report_id
        GROUP BY ar.report_id ORDER BY ar.report_id"""
        rows = self.q(sql)
        self.record("metadata_completeness", rows, sql)
        if rows:
            worst = min(rows, key=lambda r: r["has_doi"] or 0)
            self.insight(
                "metadata", "warning",
                f"{worst['report_id']} has the weakest DOI coverage ({worst['has_doi']}%)",
                f"Only {worst['has_doi']}% of its references carry a DOI, against "
                f"{max(r['has_doi'] or 0 for r in rows)}% for the best-covered report.",
                worst, "#/quality")

    def a_duplicate_detection(self) -> None:
        """Four-level duplicate detection with explicit confidence.

        L1 exact DOI (should be impossible after import: the DOI key is unique)
        L2 normalised title + year
        L3 fuzzy title similarity (token Jaccard >= 0.85), same year +-1
        L4 title + first-author surname + year
        Nothing is merged; every level only produces review candidates.
        """
        l1 = self.q("""
            SELECT doi_norm, COUNT(*) AS n FROM papers
            WHERE doi_norm IS NOT NULL AND doi_norm <> ''
            GROUP BY doi_norm HAVING COUNT(*) > 1""")
        l2_all = self.q("""
            SELECT title_norm, COUNT(*) AS n, GROUP_CONCAT(paper_id) AS ids
            FROM papers WHERE LENGTH(title_norm) > 25
            GROUP BY title_norm HAVING COUNT(*) > 1
            ORDER BY n DESC""")
        l2 = l2_all
        # L3 fuzzy on candidate pairs inside each title prefix block
        cands = self.q("""
            SELECT a.paper_id AS a_id, b.paper_id AS b_id,
                   a.title_norm AS a_t, b.title_norm AS b_t,
                   a.year_best AS a_y, b.year_best AS b_y,
                   a.authors_best AS a_a, b.authors_best AS b_a
            FROM papers a JOIN papers b
              ON b.paper_id > a.paper_id
             AND SUBSTR(a.title_norm,1,18) = SUBSTR(b.title_norm,1,18)
             AND a.title_norm <> b.title_norm
            WHERE LENGTH(a.title_norm) > 25 AND LENGTH(b.title_norm) > 25
            LIMIT 20000""")
        fuzzy = []
        for c in cands:
            ta, tb = set(c["a_t"].split()), set(c["b_t"].split())
            j = len(ta & tb) / max(len(ta | tb), 1)
            ya, yb = c["a_y"] or "", c["b_y"] or ""
            if j >= 0.85 and (not ya or not yb or abs(int(ya) - int(yb)) <= 1):
                fuzzy.append({"a": c["a_id"], "b": c["b_id"], "jaccard": round(j, 3),
                              "title_a": c["a_t"][:80], "title_b": c["b_t"][:80],
                              "year_a": ya, "year_b": yb, "level": "L3_fuzzy_title"})
        fuzzy.sort(key=lambda x: -x["jaccard"])
        l4 = [{"a": c["a_id"], "b": c["b_id"], "level": "L4_title_author_year",
               "title": c["a_t"][:80], "year": c["a_y"]}
              for c in cands
              if c["a_a"] and c["b_a"]
              and c["a_a"].split(",")[0].strip().lower() == c["b_a"].split(",")[0].strip().lower()
              and c["a_y"] and c["a_y"] == c["b_y"]][:100]
        self.record("duplicate_detection",
                    {"L1_exact_doi": l1, "L2_title_year": l2, "L3_fuzzy_title": fuzzy[:100],
                     "L4_title_author_year": l4},
                    "-- L1 exact DOI | L2 normalised title (>25 chars) | L3 token-Jaccard >= 0.85 "
                    "with |Δyear| <= 1 | L4 title + first-author surname + year. Candidates only.",
                    {"l3_threshold": 0.85})
        n = len(l1) + len(l2) + len(fuzzy) + len(l4)
        if n:
            self.insight(
                "duplicates", "warning",
                f"{n:,} duplicate candidates await review",
                f"L1 exact DOI: {len(l1)} · L2 title+year: {len(l2)} · "
                f"L3 fuzzy title: {len(fuzzy)} · L4 title+author+year: {len(l4)}.",
                {"counts": {"L1": len(l1), "L2": len(l2), "L3": len(fuzzy), "L4": len(l4)}},
                "#/quality")

    def a_year_outliers(self) -> None:
        rows = self.q("""
            SELECT p.year_best AS year, COUNT(*) AS n FROM papers p
            WHERE p.year_best GLOB '[0-9][0-9][0-9][0-9]'
            GROUP BY p.year_best ORDER BY p.year_best""")
        suspicious = [r for r in rows if not (1800 <= int(r["year"]) <= 2026)]
        recent = [r for r in rows if int(r["year"]) >= 1900]
        counts = [r["n"] for r in recent]
        med = statistics.median(counts) if counts else 0
        spikes = [r for r in recent if med and r["n"] > 5 * med]
        self.record("year_outliers",
                    {"implausible_years": suspicious, "spikes": spikes,
                     "median_yearly_papers": med,
                     "rule": "implausible = outside 1800..2026; spike = count > 5 x median"},
                    "-- implausible outside 1800..2026; spikes above 5x the median yearly count")
        if suspicious:
            self.insight(
                "metadata", "warning",
                f"{sum(s['n'] for s in suspicious):,} papers carry an implausible publication year",
                "Years " + ", ".join(f"{s['year']} ({s['n']})" for s in suspicious[:6]) +
                " fall outside the expected 1800 to 2026 range and require review.",
                {"years": suspicious[:10]}, "#/quality")

    def a_wg_crossover(self) -> None:
        rows = self.q("""
            SELECT p.paper_id, p.title_best, p.year_best,
                   COUNT(DISTINCT wg.wg_label) AS n_wg,
                   GROUP_CONCAT(DISTINCT wg.wg_label) AS wgs,
                   COUNT(DISTINCT c.report_id) AS n_reports
            FROM papers p
            JOIN references_occurrence r ON r.paper_id = p.paper_id
            JOIN chapters c ON c.chapter_id = r.chapter_id
            JOIN working_groups wg ON wg.wg_id = c.wg_id
            GROUP BY p.paper_id HAVING COUNT(DISTINCT wg.wg_label) > 1
            ORDER BY n_wg DESC, n_reports DESC LIMIT 100""")
        self.record("working_group_crossover", rows,
                    "-- papers cited by more than one Working Group label")
        if rows:
            t = rows[0]
            self.insight(
                "persistence", "info",
                f"{len(rows):,} sampled papers cross Working Group boundaries",
                f"The broadest is “{t['title_best']}”, cited by {t['n_wg']} Working Groups "
                f"({t['wgs']}) across {t['n_reports']} reports.",
                {"top": t}, "#/analytics")

    def a_duplicate_units(self) -> None:
        """Chapters that are the same content extracted twice.

        Rule: two chapters whose distinct paper_id sets are identical (Jaccard 1.0)
        or whose reference lists overlap by >= 0.9 are reported.  They are NOT
        merged: choosing which extraction is authoritative is a research decision.
        """
        rows = self.q("""
            SELECT c.chapter_id, c.source_id, c.chapter_title, c.chapter_label,
                   c.extraction_method, r.paper_id
            FROM chapters c
            JOIN references_occurrence r ON r.chapter_id = c.chapter_id
            WHERE r.paper_id IS NOT NULL""")
        sets: dict[str, set] = {}
        meta: dict[str, dict] = {}
        for r in rows:
            sets.setdefault(r["chapter_id"], set()).add(r["paper_id"])
            meta.setdefault(r["chapter_id"], r)
        keys = sorted(k for k, v in sets.items() if len(v) >= 10)
        dup = []
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = sets[keys[i]], sets[keys[j]]
                inter = len(a & b)
                if not inter:
                    continue
                jac = inter / len(a | b)
                if jac >= 0.9:
                    dup.append({
                        "chapter_a": keys[i], "chapter_b": keys[j],
                        "source_a": meta[keys[i]]["source_id"],
                        "source_b": meta[keys[j]]["source_id"],
                        "title": (meta[keys[i]]["chapter_title"] or "")[:80],
                        "shared_papers": inter, "jaccard": round(jac, 3),
                        "duplicate_references": inter if jac == 1.0 else None,
                    })
        dup.sort(key=lambda d: -d["jaccard"])
        self.record("duplicate_units", dup,
                    "-- chapter pairs with paper-set Jaccard >= 0.9 (>=10 papers); "
                    "identical sets indicate one unit extracted from two source PDFs",
                    {"threshold": 0.9})
        exact = [d for d in dup if d["jaccard"] == 1.0]
        if exact:
            d = exact[0]
            n = sum(len(sets[x["chapter_a"]]) for x in exact)
            self.insight(
                "duplicates", "warning",
                f"{len(exact)} chapter pair(s) are the same content extracted twice",
                f"{d['chapter_a']} and {d['chapter_b']} have identical paper sets "
                f"({d['shared_papers']} papers) but come from different source PDFs "
                f"({d['source_a']} vs {d['source_b']}). Approximately {n:,} references are "
                f"counted twice. Review the source PDFs to choose the extraction to retain.",
                {"pairs": exact}, "#/quality")
        self._dup_units = dup

    # --------------------------------------------------------------- driver
    def run_all(self) -> dict:
        start = _now()
        analyses: list[Callable[[], None]] = [
            self.a_coverage_by_report,
            self.a_metadata_completeness,
            self.a_chapter_outliers,
            self.a_reference_age,
            self.a_persistence,
            self.a_wg_crossover,
            self.a_chapter_overlap,
            self.a_journal_concentration,
            self.a_duplicate_detection,
            self.a_year_outliers,
            self.a_duplicate_units,
        ]
        errors = 0
        for fn in analyses:
            try:
                fn()
            except Exception as e:                      # keep going, report clearly
                errors += 1
                self.warnings.append(f"{fn.__name__}: {e}")
                print(f"  [warn] {fn.__name__} failed: {e}", file=sys.stderr)
        return {"start": start, "end": _now(), "errors": errors,
                "results": self.results, "insights": self.insights}

    def persist(self) -> dict:
        """Write results into the database (the only write this engine performs)."""
        payload = self.run_all()
        con = sqlite3.connect(self.db_path)
        con.executescript(ANALYSIS_TABLES)
        cur = con.cursor()
        cur.execute("""INSERT INTO analysis_runs
            (start_time,end_time,source,rows_processed,rows_added,rows_changed,
             errors,warnings,analysis_version,note)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (payload["start"], payload["end"], "analytics.engine",
             self.q1("SELECT COUNT(*) FROM references_occurrence"), 0, 0,
             payload["errors"], len(self.warnings), SOFTWARE_VERSION,
             "; ".join(self.warnings)[:500]))
        run_id = cur.lastrowid
        for r in payload["results"]:
            cur.execute("""INSERT INTO analysis_results
                (run_id,analysis_type,parameters,query,input_version,output,created_at,software_version)
                VALUES (?,?,?,?,?,?,?,?)""",
                (run_id, r["analysis_type"],
                 json.dumps(r["parameters"], ensure_ascii=False),
                 r["query"], "ipcc_litdb",
                 json.dumps(r["output"], ensure_ascii=False, default=str),
                 _now(), SOFTWARE_VERSION))
        cur.execute("DELETE FROM insights")
        for i in payload["insights"]:
            cur.execute("""INSERT INTO insights
                (run_id,category,severity,title_en,title_zh,body_en,body_zh,evidence,link,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (run_id, i["category"], i["severity"], i["title_en"], i["title_zh"],
                 i["body_en"], i["body_zh"],
                 json.dumps(i["evidence"], ensure_ascii=False), i["link"], _now()))
        con.commit()
        con.close()
        payload["run_id"] = run_id
        return payload
