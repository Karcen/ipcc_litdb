# Portable replay copy: algorithms and frozen labels preserved; paths adapted.
import type_paths as P
"""Independent verification of the document-type distribution and match rates.

Reads the classifier output, joins to the database, and computes the quantities
the manuscript will report. Does not trust the classifier's own summary.

Output: CJE_revision/tables/AUDIT_types_by_group.csv
"""
import os, sqlite3
import pandas as pd, numpy as np

ROOT = str(P.OUTPUT)
DB = str(P.DB)
TYP = str(P.DATA / "occurrence_types.csv")
OUT = str(P.TABLES / "AUDIT_types_by_group.csv")

t = pd.read_csv(TYP)
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
occ = pd.read_sql("""
  SELECT r.occurrence_id, c.report_id, w.wg_label, r.match_status
  FROM references_occurrence r
  JOIN chapters c ON c.chapter_id=r.chapter_id
  LEFT JOIN working_groups w ON w.wg_id=c.wg_id
  WHERE c.report_id IN ('AR5','AR6')""", con)
con.close()
d = occ.merge(t[["occurrence_id", "category"]], on="occurrence_id", how="left")
d["category"] = d["category"].fillna("uncertain")
d["matched"] = d.match_status.isin(("matched_doi", "matched_title")).astype(int)

NONJ = ("book_or_chapter", "institutional_report", "working_paper", "other")
rows = []
for (rep, wg), g in d.groupby(["report_id", "wg_label"]):
    n = len(g)
    nj = int(g.category.isin(NONJ).sum())
    jr = g[g.category == "journal_article"]
    nr = g[g.category.isin(NONJ)]
    unc = int((g.category == "uncertain").sum())
    rows.append(dict(report=rep, wg=wg, n_occurrences=n,
        journal_n=len(jr), journal_share_pct=round(100 * len(jr) / n, 2),
        nonjournal_n=nj, nonjournal_share_pct=round(100 * nj / n, 2),
        uncertain_n=unc, uncertain_share_pct=round(100 * unc / n, 2),
        match_rate_journal_pct=round(100 * jr.matched.mean(), 2) if len(jr) else np.nan,
        match_rate_nonjournal_pct=round(100 * nr.matched.mean(), 2) if len(nr) else np.nan,
        match_rate_all_pct=round(100 * g.matched.mean(), 2)))
df = pd.DataFrame(rows).sort_values(["report", "wg"])
df.to_csv(OUT, index=False)
print("=== AR5/AR6 by working group ===")
print(df[df.wg != "SYR"].to_string(index=False))
print("\n=== pooled AR5+AR6 ===")
p = d[d.report_id.isin(["AR5", "AR6"])]
tab = p.groupby("category").agg(n=("matched", "size"), matched=("matched", "sum"))
tab["share_pct"] = (100 * tab.n / tab.n.sum()).round(2)
tab["match_rate_pct"] = (100 * tab.matched / tab.n).round(2)
print(tab.sort_values("n", ascending=False).to_string())
