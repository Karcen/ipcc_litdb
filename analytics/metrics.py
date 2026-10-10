"""Shared metric definitions for the BI explorer and dashboard.

Each metric declares its SQL, supported dimensions, and English display metadata.
"""
from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Base table aliases used by the metric expressions.
#   r = references_occurrence       p = papers
#   c = chapters                     wg = working_groups
#   ar = assessment_reports          wr = wos_records
# --------------------------------------------------------------------------- #
BASE_FROM = """
    FROM references_occurrence r
    JOIN chapters c        ON c.chapter_id = r.chapter_id
    JOIN working_groups wg ON wg.wg_id = c.wg_id
    JOIN assessment_reports ar ON ar.report_id = c.report_id
    LEFT JOIN papers p     ON p.paper_id = r.paper_id
"""

PAPER_FROM = """
    FROM papers p
"""

METRICS: dict[str, dict[str, Any]] = {
    "reference_count": {
        "sql": "COUNT(*)",
        "source": "occurrence",
        "label": {"en": "Reference occurrences"},
        "description": {
            "en": "One row per citation printed in an IPCC chapter.",
        },
    },
    "unique_papers": {
        "sql": "COUNT(DISTINCT r.paper_id)",
        "source": "occurrence",
        "label": {"en": "Unique papers"},
        "description": {
            "en": "Distinct scholarly works behind those citations.",
        },
    },
    "matched": {
        "sql": "SUM(CASE WHEN r.match_status IN ('matched_doi','matched_title') THEN 1 ELSE 0 END)",
        "source": "occurrence",
        "label": {"en": "Matched references"},
        "description": {
            "en": "References matched to a Web of Science record.",
        },
    },
    "unmatched": {
        "sql": "SUM(CASE WHEN r.match_status = 'unmatched' THEN 1 ELSE 0 END)",
        "source": "occurrence",
        "label": {"en": "Unmatched references"},
        "description": {
            "en": "References awaiting a Web of Science match.",
        },
    },
    "missing_doi": {
        "sql": "SUM(CASE WHEN r.doi_norm IS NULL OR r.doi_norm = '' THEN 1 ELSE 0 END)",
        "source": "occurrence",
        "label": {"en": "Missing DOI"},
    },
    "match_rate": {
        "sql": ("ROUND(100.0 * SUM(CASE WHEN r.match_status IN ('matched_doi','matched_title') "
                "THEN 1 ELSE 0 END) / NULLIF(COUNT(*),0), 2)"),
        "source": "occurrence",
        "unit": "%",
        "label": {"en": "Match rate"},
        "description": {
            "en": "Matched references ÷ all references.",
        },
    },
    "missing_doi_rate": {
        "sql": ("ROUND(100.0 * SUM(CASE WHEN r.doi_norm IS NULL OR r.doi_norm = '' "
                "THEN 1 ELSE 0 END) / NULLIF(COUNT(*),0), 2)"),
        "source": "occurrence",
        "unit": "%",
        "label": {"en": "Missing DOI rate"},
    },
    "papers_in_multiple_reports": {
        "sql": "COUNT(DISTINCT CASE WHEN p.n_reports > 1 THEN r.paper_id END)",
        "source": "occurrence",
        "label": {"en": "Papers cited in ≥2 reports"},
        "description": {
            "en": "Papers cited in more than one IPCC assessment report.",
        },
    },
}

# dimensions the BI explorer may group by
DIMENSIONS: dict[str, dict[str, Any]] = {
    "report":      {"sql": "ar.report_id",     "source": "occurrence",
                    "label": {"en": "Assessment Report"}},
    "working_group": {"sql": "wg.wg_label",    "source": "occurrence",
                    "label": {"en": "Working Group"}},
    "chapter":     {"sql": "c.chapter_id",     "source": "occurrence",
                    "label": {"en": "Chapter"}},
    "chapter_label": {"sql": "c.chapter_label", "source": "occurrence",
                    "label": {"en": "Chapter label"}},
    "match_status": {"sql": "r.match_status",  "source": "occurrence",
                    "label": {"en": "Match status"}},
    "publication_year": {"sql": "p.year_best", "source": "occurrence",
                    "label": {"en": "Publication year"}},
    "journal":     {"sql": "p.journal_best",   "source": "occurrence",
                    "label": {"en": "Journal"}},
    "verification_status": {"sql": "p.verification_status", "source": "occurrence",
                    "label": {"en": "Verification status"}},
    "decade":      {"sql": "CAST(CAST(p.year_best AS INTEGER)/10 AS INTEGER)*10",
                    "source": "occurrence",
                    "label": {"en": "Decade"}},
}

# metrics that only make sense on the occurrence grain
PAPER_METRICS = {"unique_papers", "papers_in_multiple_reports"}


def metric_catalog() -> list[dict]:
    return [{"key": k, **{kk: vv for kk, vv in v.items() if kk != "sql"}} for k, v in METRICS.items()]


def dimension_catalog() -> list[dict]:
    return [{"key": k, **{kk: vv for kk, vv in v.items() if kk != "sql"}} for k, v in DIMENSIONS.items()]


def build_query(metric: str, dimension: str | None, filters: dict[str, str],
                limit: int = 500) -> str:
    """Build an aggregate query from validated metric, dimension, and filter inputs."""
    if metric not in METRICS:
        raise ValueError(f"unknown metric: {metric}")
    if dimension is not None and dimension not in DIMENSIONS:
        raise ValueError(f"unknown dimension: {dimension}")

    m = METRICS[metric]
    where = []
    for k, v in filters.items():
        if k == "report" and v:
            where.append(f"ar.report_id = '{_lit(v)}'")
        elif k == "working_group" and v:
            where.append(f"wg.wg_label = '{_lit(v)}'")
        elif k == "chapter" and v:
            where.append(f"c.chapter_id = '{_lit(v)}'")
        elif k == "match_status" and v:
            where.append(f"r.match_status = '{_lit(v)}'")
        elif k == "year_from" and v:
            where.append(f"CAST(p.year_best AS INTEGER) >= {int(v)}")
        elif k == "year_to" and v:
            where.append(f"CAST(p.year_best AS INTEGER) <= {int(v)}")
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    if dimension:
        dim_sql = DIMENSIONS[dimension]["sql"]
        return (f"SELECT {dim_sql} AS dim, {m['sql']} AS value{BASE_FROM}{clause} "
                f"GROUP BY {dim_sql} ORDER BY value DESC LIMIT {int(limit)}")
    return f"SELECT {m['sql']} AS value{BASE_FROM}{clause}"


def _lit(v: str) -> str:
    """Escape a SQL string literal and limit its length."""
    return str(v).replace("'", "''")[:200]
