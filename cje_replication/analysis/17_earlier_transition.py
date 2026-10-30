# Portable replay copy: algorithms and frozen labels preserved; paths adapted.
import type_paths as P
"""Frame comparison on the earlier transition (AR4 -> AR5).

The paper's main comparison is AR5 -> AR6. This script repeats the retention
estimand on the earlier transition, which needs no literature cutoff: retention
only requires the two adjacent reports' reference lists. (The availability-
conditional reuse rate does need a documented cutoff, which does not exist for
AR4, and that rate is therefore not computed here.)

AR4 Working Group I has a documented extraction gap and is reported but excluded
from interpretation.

Output: tables/AUDIT_earlier_transition.csv
"""
import os, sqlite3
import pandas as pd

ROOT = str(P.OUTPUT)
DB = str(P.DB)
OUT = str(P.TABLES / "AUDIT_earlier_transition.csv")

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
occ = pd.read_sql("""SELECT r.paper_id, c.report_id, w.wg_label, r.match_status
  FROM references_occurrence r JOIN chapters c ON c.chapter_id=r.chapter_id
  LEFT JOIN working_groups w ON w.wg_id=c.wg_id
  WHERE c.report_id IN ('AR4','AR5','AR6') AND r.paper_id IS NOT NULL""", con)
con.close()
occ["wos"] = occ.match_status.isin(("matched_doi", "matched_title")).astype(int)

def cohort(rep, wg, frame):
    s = occ[(occ.report_id == rep) & (occ.wg_label == wg)]
    return set(s.paper_id) if frame == "A" else set(s.loc[s.wos == 1, "paper_id"])

rows = []
for t0, t1, tag in (("AR4", "AR5", "earlier"), ("AR5", "AR6", "later")):
    for wg in ("WG1", "WG2", "WG3"):
        w0, a0 = cohort(t0, wg, "W"), cohort(t0, wg, "A")
        w1, a1 = cohort(t1, wg, "W"), cohort(t1, wg, "A")
        rw = len(w0 & w1) / len(w0) if w0 else float("nan")
        ra = len(a0 & a1) / len(a0) if a0 else float("nan")
        rows.append(dict(transition=f"{t0}->{t1}", tag=tag, group=wg,
                         w_earlier_n=len(w0), w_shared=len(w0 & w1), w_rate_pct=round(100*rw, 2),
                         a_earlier_n=len(a0), a_shared=len(a0 & a1), a_rate_pct=round(100*ra, 2),
                         gap_pp=round(100*(rw-ra), 2),
                         interpretable=int(wg != "WG1" or t0 != "AR4")))
out = pd.DataFrame(rows)
out.to_csv(OUT, index=False)
print(out.to_string(index=False))
print()
usable = out[(out.interpretable == 1) & (out.tag == "earlier")]
print(f"usable replication cells (Groups II and III, earlier transition): {len(usable)}, "
      f"positive: {int((usable.gap_pp > 0).sum())}")
