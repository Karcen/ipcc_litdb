"""Portable data layer for the submitted CJE comparison.

Memberships and core metadata are rebuilt from the shipped read-only SQLite
snapshot and frozen DOI-resolution response table. The package contains no API
client and makes no network requests. Outputs and disposable caches are written
only under output/. Eligible-reuse and standardisation definitions below are
unchanged from the source analysis.
"""
import os
import pandas as pd
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "inputs"
CJE = ROOT / "output"
OUT = CJE / "tables"
CACHE = CJE / "cache"
DB = CJE / "inputs/ipcc_litdb.sqlite"
RESOLUTION = CJE / "inputs/doi_resolution.csv"
OUT.mkdir(parents=True, exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)

GROUPS = ("WG1", "WG2", "WG3")
REPORTS = ("AR4", "AR5", "AR6")
CUTOFF = {"WG1": "2013-03-15", "WG2": "2013-08-31", "WG3": "2013-10-03"}
MATCHED = ("matched_doi", "matched_title")


def load_resolution():
    """Frozen OpenAlex exact-unique DOI resolution. One row per requested DOI."""
    res = pd.read_csv(RESOLUTION, low_memory=False)
    res = res[res.resolution == "exact_unique"].drop_duplicates("requested_doi").copy()
    res["doi_key"] = res["requested_doi"].fillna("").str.lower().str.strip()
    res["pubdate"] = pd.to_datetime(res["publication_date"], errors="coerce")
    return res[["doi_key", "pubdate", "publication_year", "source_name", "source_type", "issn_l"]].drop_duplicates("doi_key")


def core_frame():
    """Rebuild per-identity/report/group counts from the read-only snapshot."""
    import sqlite3
    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as con:
        return pd.read_sql("""
          SELECT r.paper_id, c.report_id, w.wg_label, COUNT(*) AS n_occ,
                 SUM(r.match_status IN ('matched_doi','matched_title')) AS n_matched
          FROM references_occurrence r
          JOIN chapters c ON c.chapter_id=r.chapter_id
          LEFT JOIN working_groups w ON w.wg_id=c.wg_id
          WHERE r.paper_id IS NOT NULL
          GROUP BY r.paper_id,c.report_id,w.wg_label
        """, con)


def identity_frame(force=False):
    """Rebuild the core analysis frame from SQLite and frozen DOI resolution.

    SQL empty strings and numeric year fields are read with the same semantics
    as the historical CSV input. All report/group memberships are rebuilt from
    occurrence counts. Unused fields from the old regression workflow are not
    included in this analysis frame.
    """
    import sqlite3
    fp = CACHE / "identity_frame.pkl"
    if fp.exists() and not force:
        return pd.read_pickle(fp)
    cols = ["paper_id", "doi_norm", "year_best", "journal_best", "identity_method",
            "verification_status", "metadata_source", "n_occurrences", "n_reports"]
    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as con:
        m = pd.read_sql("SELECT " + ",".join(cols) + " FROM papers ORDER BY paper_id", con)
    # pandas.read_csv interpreted these original empty text fields as missing.
    for col in ("doi_norm", "journal_best", "identity_method", "verification_status", "metadata_source"):
        m[col] = m[col].replace("", np.nan)
    m["year_best"] = pd.to_numeric(m["year_best"], errors="coerce").astype(float)
    m["doi_key"] = m["doi_norm"].fillna("").str.lower().str.strip()
    m = m.merge(load_resolution(), on="doi_key", how="left", validate="many_to_one")
    m["year"] = m["year_best"].where(m["year_best"].notna(), m["pubdate"].dt.year)
    m["venue"] = m["source_name"].fillna(m["journal_best"])
    m["year_s"] = m["year"].astype("Int64").astype(str)
    core = core_frame()
    for rep in REPORTS:
        for g in GROUPS:
            s = core[(core.report_id == rep) & (core.wg_label == g)]
            mm = s.groupby("paper_id")["n_matched"].max()
            aa = s.groupby("paper_id")["n_occ"].max()
            m[f"m{rep[-1]}_{g}"] = (m["paper_id"].map(mm) > 0).fillna(False)
            m[f"a{rep[-1]}_{g}"] = (m["paper_id"].map(aa) > 0).fillna(False)
        s = core[core.report_id == rep]
        mm = s.groupby("paper_id")["n_matched"].max()
        aa = s.groupby("paper_id")["n_occ"].max()
        m[f"m_{rep}"] = (m["paper_id"].map(mm) > 0).fillna(False)
        m[f"a_{rep}"] = (m["paper_id"].map(aa) > 0).fillna(False)
    m = m.set_index("paper_id", drop=True)
    m.to_pickle(fp)
    return m


def eligible_reuse(ident, g):
    """Availability-conditional reuse denominator for group g (validated definition).

    Identity matched in AR6 group g, with an exact resolved publication date not
    later than the AR5 literature cutoff of that group. Returns a DataFrame with
    column y = identity had a matched AR5 occurrence in the same group.
    """
    cut = pd.Timestamp(CUTOFF[g])
    e = ident[ident[f"m6_{g}"] & ident["pubdate"].notna() & (ident["pubdate"] <= cut)].copy()
    e["y"] = e[f"m5_{g}"].astype(int)
    e["grp"] = g
    return e


def common_support(ident, g1, g3, keys):
    """Direct standardisation of the g1-vs-g3 gap on common-support strata.

    Returns the three quantities the revision must keep separate:
      crude_full_gap_pp      : all eligible g1 vs g3 records
      crude_common_gap_pp    : same crude contrast restricted to shared strata
      standardised_common_pp : pooled-count-weighted within-stratum gap on shared strata
    plus support counts and support share. keys=[] gives the crude comparison.
    """
    A = eligible_reuse(ident, g1)
    B = eligible_reuse(ident, g3)
    out = dict(spec="crude" if not keys else "_".join(keys),
               n_g1_full=len(A), n_g3_full=len(B),
               y_g1_full=int(A.y.sum()), y_g3_full=int(B.y.sum()),
               crude_full_gap_pp=100.0 * (A.y.mean() - B.y.mean()))
    if not keys:
        out.update(common_strata=1, n_g1_common=len(A), n_g3_common=len(B),
                   y_g1_common=int(A.y.sum()), y_g3_common=int(B.y.sum()),
                   crude_common_gap_pp=out["crude_full_gap_pp"],
                   standardised_common_gap_pp=out["crude_full_gap_pp"],
                   support_shift_pp=0.0, composition_common_pp=0.0,
                   support_share_pct=100.0, support_share_g1_pct=100.0,
                   support_share_g3_pct=100.0)
        return out
    x = A.groupby(keys).agg(n1=("y", "size"), y1=("y", "sum"))
    y = B.groupby(keys).agg(n3=("y", "size"), y3=("y", "sum"))
    z = x.join(y, how="outer").fillna(0)
    z = z[(z.n1 > 0) & (z.n3 > 0)]
    if len(z) == 0:
        return None
    crude_common = 100.0 * (z.y1.sum() / z.n1.sum() - z.y3.sum() / z.n3.sum())
    w = (z.n1 + z.n3) / (z.n1.sum() + z.n3.sum())
    p1 = z.y1 / z.n1
    p3 = z.y3 / z.n3
    std = 100.0 * float((w * (p1 - p3)).sum())
    out.update(common_strata=len(z),
               n_g1_common=int(z.n1.sum()), n_g3_common=int(z.n3.sum()),
               y_g1_common=int(z.y1.sum()), y_g3_common=int(z.y3.sum()),
               crude_common_gap_pp=crude_common,
               standardised_common_gap_pp=std,
               support_shift_pp=out["crude_full_gap_pp"] - crude_common,
               composition_common_pp=crude_common - std,
               support_share_pct=100.0 * (z.n1.sum() + z.n3.sum()) / (len(A) + len(B)),
               support_share_g1_pct=100.0 * z.n1.sum() / len(A),
               support_share_g3_pct=100.0 * z.n3.sum() / len(B))
    return out


def wilson(k, n, z=1.96):
    """Wilson score interval, in percent, for a binomial reference interval.
    Conditional on this fixed corpus; not a sampling-confidence interval."""
    if n == 0:
        return (np.nan, np.nan, np.nan)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (100 * p, 100 * max(0, centre - half), 100 * min(1, centre + half))

def top_venues(ident, n=25):
    """Top-n venue labels by identity count on the full identity frame (legacy rule)."""
    vc = ident["venue"].value_counts()
    return list(vc.head(n).index)

def year_bin(y, width=5):
    if width != 5:
        raise ValueError("year_bin supports width=5")
    bins = list(range(1970, 2025, width)) + [2026]
    labels = [f"{a}-{a+width-1}" for a in range(1970, 2020, width)] + ["2020+"]
    return pd.cut(y, bins=bins, right=False, labels=labels)
