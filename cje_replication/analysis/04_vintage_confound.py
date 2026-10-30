# Portable replay copy: algorithms and frozen labels preserved; paths adapted.
import type_paths as P
"""Quantify how much of the matched-versus-extracted recurrence gap is vintage
composition rather than within-vintage difference.

The matched frame cannot contain any work published before 1998 (the WoS export
window), so within the 1970-2013 comparison window the two cohorts differ in
vintage. This script decomposes the gap.

Output: CJE_revision/tables/AUDIT_vintage_confound.csv
"""
import os, sqlite3
import numpy as np, pandas as pd

ROOT = str(P.OUTPUT)
DB = str(P.DB)
OUT = str(P.TABLES / "AUDIT_vintage_confound.csv")
GROUPS = ("WG1", "WG2", "WG3")

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
occ = pd.read_sql("""
  SELECT r.paper_id, c.report_id, w.wg_label, r.match_status
  FROM references_occurrence r
  JOIN chapters c ON c.chapter_id=r.chapter_id
  LEFT JOIN working_groups w ON w.wg_id=c.wg_id
  WHERE c.report_id IN ('AR5','AR6') AND r.paper_id IS NOT NULL
""", con)
papers = pd.read_sql("SELECT paper_id, year_best, metadata_source FROM papers", con)
con.close()
occ["ism"] = occ.match_status.isin(("matched_doi", "matched_title")).astype(int)
papers["year"] = pd.to_numeric(papers["year_best"], errors="coerce")
yr = papers.set_index("paper_id")["year"]

rows = []
for g in GROUPS:
    a5 = occ[(occ.report_id == "AR5") & (occ.wg_label == g)]
    a6 = occ[(occ.report_id == "AR6") & (occ.wg_label == g)]
    M = set(a5.loc[a5.ism == 1, "paper_id"])          # earlier matched cohort
    X = set(a5.paper_id)                              # earlier extracted cohort
    U = X - M                                         # earlier unmatched
    out6 = set(a6.paper_id)                           # recurrence outcome
    keep = lambda s: {p for p in s if pd.notna(yr.get(p, np.nan)) and 1970 <= yr.get(p) <= 2013}
    M, U, X = keep(M), keep(U), keep(X)
    r = lambda s: sum(p in out6 for p in s) / len(s) if s else np.nan
    r_m, r_u, r_x = r(M), r(U), r(X)
    w_u = len(U) / len(X)

    # vintage-standardise the unmatched cohort to the matched cohort's year distribution
    mv = pd.Series({p: yr[p] for p in M}); uv = pd.Series({p: yr[p] for p in U})
    common = sorted(set(mv) & set(uv))
    if len(common) >= 3:
        mw = mv.value_counts(normalize=True).reindex(common).fillna(0)
        rows_u = pd.DataFrame({"year": uv.values, "rec": [1 if p in out6 else 0 for p in U]})
        gu = rows_u.groupby("year").rec.mean()
        r_u_std = float((mw * gu.reindex(common)).sum() / mw.sum())
        vintage_share = (r_u_std - r_u) / (r_m - r_u) if r_m != r_u else np.nan
    else:
        r_u_std, vintage_share = np.nan, np.nan

    # pre-1998 stratum in the extracted cohort
    pre = {p for p in X if yr[p] < 1998}
    post = X - pre
    rows.append(dict(group=g,
        n_matched=len(M), n_unmatched=len(U), n_extracted=len(X),
        unmatched_share=round(w_u, 4),
        rate_matched=round(100 * r_m, 3), rate_unmatched=round(100 * r_u, 3),
        rate_extracted=round(100 * r_x, 3),
        gap_matched_minus_extracted_pp=round(100 * (r_m - r_x), 3),
        rate_unmatched_std_to_matched_vintage=round(100 * r_u_std, 3) if r_u_std == r_u_std else np.nan,
        vintage_share_of_gap=round(100 * vintage_share, 1) if vintage_share == vintage_share else np.nan,
        n_pre1998_in_extracted=len(pre), rate_pre1998=round(100 * r(pre), 3) if pre else np.nan,
        n_post1998_extracted=len(post), rate_post1998=round(100 * r(post), 3)))

df = pd.DataFrame(rows)
df.to_csv(OUT, index=False)
print("=== matched vs extracted: vintage decomposition (1970-2013 window) ===")
print(df.to_string(index=False))
print("\nReading: 'vintage_share_of_gap' is the share of the matched-minus-unmatched rate")
print("difference that disappears once the unmatched cohort is reweighted to the matched")
print("cohort's publication-year distribution. The remainder is within-vintage.")
