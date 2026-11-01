# Portable replay copy: algorithms and frozen labels preserved; paths adapted.
import type_paths as P
"""Compare persistence rates across three observation frames of increasing breadth.

Frame W  : matched = identity has a Web of Science record (the manuscript's frame)
Frame D  : matched = identity has any verified bibliographic metadata (WoS or DOI)
Frame A  : matched = identity is extracted at all (no requirement)

The point: "the frame" is a choice, not a fact. If the cross-group coverage
gradient and the retention ordering change across frames, then persistence
comparisons are conditional on the frame in a way that is directly observable.

Output: CJE_revision/tables/AUDIT_frame_alternatives.csv
"""
import os, sqlite3
import pandas as pd, numpy as np

ROOT = str(P.OUTPUT)
DB = str(P.DB)
OUT = str(P.TABLES / "AUDIT_frame_alternatives.csv")
GROUPS = ("WG1", "WG2", "WG3")

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
occ = pd.read_sql("""
  SELECT r.paper_id, c.report_id, w.wg_label, r.match_status
  FROM references_occurrence r
  JOIN chapters c ON c.chapter_id=r.chapter_id
  LEFT JOIN working_groups w ON w.wg_id=c.wg_id
  WHERE c.report_id IN ('AR5','AR6') AND r.paper_id IS NOT NULL
""", con)
pap = pd.read_sql("SELECT paper_id, verification_status FROM papers", con)
con.close()
occ["is_wos"] = occ.match_status.isin(("matched_doi", "matched_title")).astype(int)
vmap = pap.set_index("paper_id")["verification_status"]
# Frame D: any verified metadata
verified = set(vmap.index[vmap.isin(("wos_matched", "doi_verified"))])

def cohort(rep, wg, frame):
    s = occ[(occ.report_id == rep) & (occ.wg_label == wg)]
    ids = set(s.paper_id)
    if frame == "W":   return set(s.loc[s.is_wos == 1, "paper_id"])
    if frame == "D":   return ids & verified
    return ids                                  # frame A: extracted

rows = []
for frame in ("W", "D", "A"):
    for wg in GROUPS:
        a5, a6 = cohort("AR5", wg, frame), cohort("AR6", wg, frame)
        sh = a5 & a6
        x5, x6 = cohort("AR5", wg, "A"), cohort("AR6", wg, "A")
        rows.append(dict(frame=frame, group=wg,
            earlier_n=len(a5), later_n=len(a6), shared=len(sh),
            retention_pct=round(100 * len(sh) / len(a5), 3) if a5 else np.nan,
            later_coverage_pct=round(100 * len(a6) / len(x6), 3) if x6 else np.nan,
            reverse_reuse_pct=round(100 * len(sh) / len(a6), 3) if a6 else np.nan))
# report level
for frame in ("W", "D", "A"):
    a5 = set(occ[(occ.report_id == "AR5") & (occ.paper_id.isin(cohort("AR5", GROUPS[0], frame)) |
                                            occ.paper_id.isin(cohort("AR5", GROUPS[1], frame)) |
                                            occ.paper_id.isin(cohort("AR5", GROUPS[2], frame)))].paper_id)
    a6 = set(occ[(occ.report_id == "AR6") & occ.paper_id.isin(
            cohort("AR6", GROUPS[0], frame) | cohort("AR6", GROUPS[1], frame) | cohort("AR6", GROUPS[2], frame))].paper_id)
    sh = a5 & a6
    rows.append(dict(frame=frame, group="report", earlier_n=len(a5), later_n=len(a6), shared=len(sh),
        retention_pct=round(100 * len(sh) / len(a5), 3) if a5 else np.nan,
        later_coverage_pct=np.nan, reverse_reuse_pct=round(100 * len(sh) / len(a6), 3) if a6 else np.nan))

df = pd.DataFrame(rows)
df.to_csv(OUT, index=False)
piv = df.pivot(index="group", columns="frame", values="retention_pct")
piv = piv.reindex(["report", "WG1", "WG2", "WG3"])
print("=== retention (%) under three frames ===")
print(piv.to_string())
print("\n=== later-report coverage (%) under W and D frames ===")
print(df[df.group != "report"].pivot(index="group", columns="frame", values="later_coverage_pct")[["W","D"]].to_string())
print("\n=== ordering check ===")
for f in ("W", "D", "A"):
    sub = piv[f].drop("report")
    print(f"  frame {f}: WG1={sub['WG1']:.2f} WG2={sub['WG2']:.2f} WG3={sub['WG3']:.2f}  "
          f"order = {' > '.join(sub.sort_values(ascending=False).index)}")
