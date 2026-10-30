# Portable replay copy: algorithms and frozen labels preserved; paths adapted.
import type_paths as P
"""A_02_tables.py -- Task A main tables (document type x working group x cycle).

Unit of analysis, stated once and kept separate throughout:
  U1 occurrence : one bibliography entry (references_occurrence row).
  U2 identity   : one assigned identity (papers.paper_id = one cited document).

Frames:
  - occurrence frame: all extracted occurrences of a report/working group.
  - identity frame  : identities with >=1 occurrence (extracted frame) or with
    >=1 matched occurrence (matched frame) in a report/working group.
Vintage: raw-string citation year (see A_01); bands <=1997 / 1998-2005 /
  2006-2013 / 2014+ / unknown. The 1998 cut is the Web of Science export window.

All rates are reported with numerator and denominator. No causal language.

Outputs (tables/):
  A_document_type_distribution.csv
  A_type_by_vintage.csv
  A_match_rate_by_type.csv
  A_recurrence_by_type.csv
  A_type_composition_decomposition.csv
  A_reference_frame_validation.csv
"""
import os, csv, math, sqlite3
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = str(P.OUTPUT)
DATA = str(P.DATA)
TAB = os.path.join(ROOT, "tables")
os.makedirs(TAB, exist_ok=True)

TYPES = ["journal_article", "book_or_chapter", "institutional_report",
         "working_paper", "other", "uncertain"]
BANDS = ["<=1997", "1998-2005", "2006-2013", "2014+", "unknown"]
CUTOFFS = {"WG1": "2013-03-15", "WG2": "2013-08-31", "WG3": "2013-10-03"}
WGS = ["WG1", "WG2", "WG3"]
DB = str(P.DB)
DOI_RES = str(P.DOI_RESOLUTION)


def wilson(k, n, z=1.96):
    if n == 0:
        return (np.nan, np.nan, np.nan)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (100 * p, 100 * max(0, centre - half), 100 * min(1, centre + half))


def band_of(y):
    if pd.isna(y):
        return "unknown"
    y = int(y)
    if y <= 1997:
        return "<=1997"
    if y <= 2005:
        return "1998-2005"
    if y <= 2013:
        return "2006-2013"
    return "2014+"


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------
def load():
    iv = pd.read_csv(os.path.join(DATA, "identity_panel.csv"), low_memory=False)
    ov = pd.read_csv(os.path.join(DATA, "occurrence_vintage.csv"), low_memory=False)
    d = pd.read_csv(os.path.join(DATA, "occurrences_all.csv"), low_memory=False)
    # merge raw year into occurrences with type (occurrence_vintage already has both)
    iv["year_ref"] = pd.to_numeric(iv.year_ref, errors="coerce")
    iv["band"] = iv.year_ref.map(band_of)
    ov = ov.rename(columns={"ref_year": "year_ref"})
    ov["year_ref"] = pd.to_numeric(ov.year_ref, errors="coerce")
    ov["band"] = ov.year_ref.map(band_of)
    ov["matched"] = ov.match_status.isin(["matched_doi", "matched_title"]).astype(int)

    # ---- identity x report x WG flags (unit U2, matched/extracted frames)
    for g in WGS:
        for rep in ("AR5", "AR6"):
            iv[f"a{rep[-1]}_{g}"] = iv[f"wg_any_{rep}"].fillna("").str.split("|").apply(lambda x, g=g: g in x)
            iv[f"m{rep[-1]}_{g}"] = iv[f"wg_matched_{rep}"].fillna("").str.split("|").apply(lambda x, g=g: g in x)
        iv[f"a5_{g}"] = iv[f"a5_{g}"].astype(bool)
        iv[f"a6_{g}"] = iv[f"a6_{g}"].astype(bool)
        iv[f"m5_{g}"] = iv[f"m5_{g}"].astype(bool)
        iv[f"m6_{g}"] = iv[f"m6_{g}"].astype(bool)
    iv["a5_any"] = iv[[f"a5_{g}" for g in WGS]].any(axis=1)
    iv["a6_any"] = iv[[f"a6_{g}" for g in WGS]].any(axis=1)
    iv["m5_any"] = iv[[f"m5_{g}" for g in WGS]].any(axis=1)
    iv["m6_any"] = iv[[f"m6_{g}" for g in WGS]].any(axis=1)
    # AR5-cycle-observed identity type (preferred where available)
    iv["type_use"] = np.where((iv.n_occ_AR5 > 0) & iv.type_AR5.isin(TYPES), iv.type_AR5,
                      np.where((iv.n_occ_AR6 > 0) & iv.type_AR6.isin(TYPES), iv.type_AR6,
                               iv.identity_type))
    return iv, ov, d


# ---------------------------------------------------------------------------
# T1: document-type distribution (occurrence and identity frames)
# ---------------------------------------------------------------------------
def t_distribution(iv, ov):
    rows = []
    # occurrence frame
    for rep in ("AR5", "AR6"):
        for wg in ["ALL"] + WGS + ["SYR"]:
            sub = ov[ov.report_id == rep]
            if wg != "ALL":
                sub = sub[sub.wg_label == wg]
            N = len(sub)
            vc = sub.category.value_counts()
            for t in TYPES:
                rows.append(dict(unit="occurrence", cycle=rep, wg=wg, category=t,
                                 n=int(vc.get(t, 0)), denominator=N,
                                 share_pct=round(100 * vc.get(t, 0) / N, 3) if N else np.nan))
    # identity frame (membership = any occurrence in cycle/WG)
    for rep in ("AR5", "AR6"):
        col = f"a{rep[-1]}"
        for wg in ["ALL"] + WGS + ["SYR"]:
            if wg == "ALL":
                mask = iv[f"{col}_any"]
            elif wg == "SYR":
                mask = iv[f"wg_any_{rep}"].fillna("").str.split("|").apply(lambda x: "SYR" in x)
            else:
                mask = iv[f"{col}_{wg}"]
            sub = iv[mask]
            N = len(sub)
            if N == 0:
                continue
            vc = sub.type_use.value_counts()
            for t in TYPES:
                rows.append(dict(unit="identity", cycle=rep, wg=wg, category=t,
                                 n=int(vc.get(t, 0)), denominator=N,
                                 share_pct=round(100 * vc.get(t, 0) / N, 3) if N else np.nan))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(TAB, "A_document_type_distribution.csv"), index=False)
    return df


# ---------------------------------------------------------------------------
# T2: type by vintage (incl. unknown; pooled and within bands)
# ---------------------------------------------------------------------------
def t_type_by_vintage(iv, ov):
    rows = []
    for unit in ("occurrence", "identity"):
        for rep in ("AR5", "AR6"):
            for wg in ["ALL"] + WGS:
                for band in ["ALL"] + BANDS:
                    if unit == "occurrence":
                        sub = ov[ov.report_id == rep]
                        if wg != "ALL":
                            sub = sub[sub.wg_label == wg]
                        if band != "ALL":
                            sub = sub[sub.band == band]
                        vc = sub.category.value_counts()
                        N = len(sub)
                    else:
                        col = f"a{rep[-1]}"
                        sub = iv[iv[f"{col}_any"]] if wg == "ALL" else iv[iv[f"{col}_{wg}"]]
                        if band != "ALL":
                            sub = sub[sub.band == band]
                        vc = sub.type_use.value_counts()
                        N = len(sub)
                    for t in TYPES:
                        rows.append(dict(unit=unit, cycle=rep, wg=wg, vintage=band,
                                         category=t, n=int(vc.get(t, 0)), denominator=N,
                                         share_pct=round(100 * vc.get(t, 0) / N, 3) if N else np.nan))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(TAB, "A_type_by_vintage.csv"), index=False)
    return df


# ---------------------------------------------------------------------------
# T3: match rate by type, with vintage strata visible
# ---------------------------------------------------------------------------
def t_match_rate(iv, ov):
    rows = []
    for unit in ("occurrence", "identity"):
        for rep in ("AR5", "AR6"):
            for wg in ["ALL"] + WGS:
                for vint in ("ALL", "<=1997", "1998+"):
                    if unit == "occurrence":
                        sub = ov[ov.report_id == rep]
                        if wg != "ALL":
                            sub = sub[sub.wg_label == wg]
                        if vint == "<=1997":
                            sub = sub[sub.band == "<=1997"]
                        elif vint == "1998+":
                            sub = sub[sub.year_ref >= 1998]
                        for t in TYPES + ["ALL"]:
                            s2 = sub if t == "ALL" else sub[sub.category == t]
                            N = len(s2); k = int(s2.matched.sum())
                            m, lo, hi = wilson(k, N)
                            rows.append(dict(unit=unit, cycle=rep, wg=wg, vintage_stratum=vint,
                                             category=t, denominator=N, n_matched=k,
                                             match_rate_pct=None if np.isnan(m) else round(m, 3),
                                             ci_lo_pct=None if np.isnan(lo) else round(lo, 3),
                                             ci_hi_pct=None if np.isnan(hi) else round(hi, 3),
                                             share_uncertain_pct=(round(100 * (sub.category == "uncertain").mean(), 3) if t == "ALL" and len(sub) else None)))
                    else:
                        col = f"a{rep[-1]}"; mcol = f"m{rep[-1]}"
                        sub = iv[iv[f"{col}_any"]] if wg == "ALL" else iv[iv[f"{col}_{wg}"]]
                        if vint == "<=1997":
                            sub = sub[sub.band == "<=1997"]
                        elif vint == "1998+":
                            sub = sub[sub.year_ref >= 1998]
                        for t in TYPES + ["ALL"]:
                            s2 = sub if t == "ALL" else sub[sub.type_use == t]
                            N = len(s2); k = int(s2[f"{mcol}_{wg}"].sum()) if wg != "ALL" else int(s2[f"{mcol}_any"].sum())
                            m, lo, hi = wilson(k, N)
                            rows.append(dict(unit=unit, cycle=rep, wg=wg, vintage_stratum=vint,
                                             category=t, denominator=N, n_matched=k,
                                             match_rate_pct=None if np.isnan(m) else round(m, 3),
                                             ci_lo_pct=None if np.isnan(lo) else round(lo, 3),
                                             ci_hi_pct=None if np.isnan(hi) else round(hi, 3),
                                             share_uncertain_pct=(round(100 * (sub.type_use == "uncertain").mean(), 3) if t == "ALL" and len(sub) else None)))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(TAB, "A_match_rate_by_type.csv"), index=False)
    return df


def main():
    iv, ov, d = load()
    t_distribution(iv, ov)
    t_type_by_vintage(iv, ov)
    t_match_rate(iv, ov)
    print("wrote distribution, type-by-vintage, match-rate tables")
    # quick console summary
    dist = pd.read_csv(os.path.join(TAB, "A_document_type_distribution.csv"))
    print(dist[(dist.unit == "identity") & (dist.wg == "ALL") & (dist.category != "uncertain")]
          .pivot_table(index="category", columns="cycle", values="share_pct").round(1).to_string())

if __name__ == "__main__":
    main()
