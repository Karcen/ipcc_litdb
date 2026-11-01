# Portable replay copy: algorithms and frozen labels preserved; paths adapted.
import type_paths as P
"""Independent recomputation of the four base rates and the sample flow.

Reads the frozen analysis database once. Produces the sample-flow table, the
four rates at report and working-group level, the availability-conditional
reuse, and the 1970-2013 matched-vs-extracted selection comparison.
No source data are modified.

Run: $PY CJE_revision/analysis/00_audit_base_rates.py
"""
import os, sqlite3, json
import pandas as pd, numpy as np

ROOT = str(P.OUTPUT)
DB = str(P.DB)
RES = str(P.DOI_RESOLUTION)
OUT = str(P.TABLES)
PROV = str(P.PROVENANCE)
os.makedirs(OUT, exist_ok=True); os.makedirs(PROV, exist_ok=True)

CUTOFFS = {"WG1": "2013-03-15", "WG2": "2013-08-31", "WG3": "2013-10-03"}
GROUPS  = ("WG1", "WG2", "WG3")
MATCHED = ("matched_doi", "matched_title")

def sha256(path, blocks=1 << 20):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(blocks)
            if not b: break
            h.update(b)
    return h.hexdigest()

# ---------------- load ----------------
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
occ = pd.read_sql("""
  SELECT r.occurrence_id, r.paper_id, c.report_id, w.wg_label, r.match_status, r.chapter_id
  FROM references_occurrence r
  JOIN chapters c ON c.chapter_id = r.chapter_id
  LEFT JOIN working_groups w ON w.wg_id = c.wg_id
  WHERE c.report_id IN ('AR4','AR5','AR6') AND r.paper_id IS NOT NULL
""", con)
papers = pd.read_sql("SELECT paper_id, doi_norm, year_best, journal_best, identity_method, "
                     "verification_status, metadata_source FROM papers", con)
con.close()
occ["is_matched"] = occ.match_status.isin(MATCHED).astype(int)

res = pd.read_csv(RES, low_memory=False)
res = res[res.resolution == "exact_unique"].drop_duplicates("requested_doi")
res["pubdate"] = pd.to_datetime(res["publication_date"], errors="coerce")
res["doi_k"] = res["requested_doi"].fillna("").str.lower().str.strip()

# ---------------- identity x report membership ----------------
def membership(rep):
    s = occ[occ.report_id == rep]
    any_m = s.groupby("paper_id").size()
    mat   = s[s.is_matched == 1].groupby("paper_id").size()
    return set(any_m.index), set(mat.index)

ext, mat = {}, {}
for rep in ("AR4", "AR5", "AR6"):
    ext[rep], mat[rep] = membership(rep)

# per working group within report
def wg_cohort(rep, wg):
    s = occ[(occ.report_id == rep) & (occ.wg_label == wg)]
    return set(s.paper_id.unique()), set(s.loc[s.is_matched == 1, "paper_id"].unique())

wext, wmat = {}, {}
for rep in ("AR4", "AR5", "AR6"):
    for wg in GROUPS:
        wext[(rep, wg)], wmat[(rep, wg)] = wg_cohort(rep, wg)

# ---------------- availability-conditional cohort ----------------
papers["doi_k"] = papers["doi_norm"].fillna("").str.lower().str.strip()
pd_map = papers.merge(res[["doi_k", "pubdate"]], on="doi_k", how="left").set_index("paper_id")

def ci(k, n, z=1.96):
    if n == 0: return (np.nan, np.nan, np.nan)
    p = k / n; se = np.sqrt(p * (1 - p) / n)
    return (100 * p, 100 * max(0, p - z * se), 100 * min(1, p + z * se))

flow, rates, avail, sel = [], [], [], []

# ---------------- report level ----------------
for rep in ("AR4", "AR5", "AR6"):
    flow.append(dict(level="report", report=rep,
                     extracted_identities=len(ext[rep]), matched_identities=len(mat[rep]),
                     identity_coverage_pct=round(100 * len(mat[rep]) / len(ext[rep]), 2)))
for wg in GROUPS:
    for rep in ("AR5", "AR6"):
        flow.append(dict(level=f"report_{wg}", report=rep,
                         extracted_identities=len(wext[(rep, wg)]), matched_identities=len(wmat[(rep, wg)]),
                         identity_coverage_pct=round(100 * len(wmat[(rep, wg)]) / len(wext[(rep, wg)]), 2)))
pd.DataFrame(flow).to_csv(os.path.join(OUT, "AUDIT_sample_flow.csv"), index=False)

def add_rate(scope, R_M, R_X, U_M, J):
    rates.append(dict(scope=scope, R_M_n=len(R_M), R_M_d=len(mat["AR5"]) if scope == "report" else None))
# report level rates
sh_m = mat["AR5"] & mat["AR6"]; sh_x = ext["AR5"] & ext["AR6"]
rM = ci(len(sh_m), len(mat["AR5"])); rX = ci(len(sh_x), len(ext["AR5"]))
uM = ci(len(sh_m), len(mat["AR6"])); jac = len(sh_m) / len(mat["AR5"] | mat["AR6"])
rates.append(dict(scope="report", R_M=len(sh_m), R_M_den=len(mat["AR5"]), R_M_pct=round(rM[0], 2),
                  R_X=len(sh_x), R_X_den=len(ext["AR5"]), R_X_pct=round(rX[0], 2),
                  U_M=len(sh_m), U_M_den=len(mat["AR6"]), U_M_pct=round(uM[0], 2),
                  jaccard_pct=round(100 * jac, 2)))
for wg in GROUPS:
    a = wmat[("AR5", wg)]; b = wmat[("AR6", wg)]
    ea = wext[("AR5", wg)]; eb = wext[("AR6", wg)]
    s_m = a & b; s_x = ea & eb
    r1 = ci(len(s_m), len(a)); r2 = ci(len(s_x), len(ea)); r3 = ci(len(s_m), len(b))
    rates.append(dict(scope=wg, R_M=len(s_m), R_M_den=len(a), R_M_pct=round(r1[0], 2),
                      R_X=len(s_x), R_X_den=len(ea), R_X_pct=round(r2[0], 2),
                      U_M=len(s_m), U_M_den=len(b), U_M_pct=round(r3[0], 2),
                      jaccard_pct=round(100 * len(s_m) / len(a | b), 2)))
pd.DataFrame(rates).to_csv(os.path.join(OUT, "AUDIT_base_rates.csv"), index=False)

# ---------------- availability-conditional reuse ----------------
for wg in GROUPS:
    cut = pd.Timestamp(CUTOFFS[wg])
    B = pd_map.loc[list(wmat[("AR6", wg)])].dropna(subset=["pubdate"])
    B = B[B.pubdate <= cut]
    k = B.index.isin(wmat[("AR5", wg)]).sum(); n = len(B)
    p = ci(int(k), n)
    avail.append(dict(group=wg, cutoff=CUTOFFS[wg],
                      ar6_matched=len(wmat[("AR6", wg)]),
                      ar6_missing_date=int(len(wmat[("AR6", wg)]) - len(pd_map.loc[list(wmat[("AR6", wg)])].dropna(subset=["pubdate"]))),
                      eligible=n, shared=int(k), reuse_pct=round(p[0], 2),
                      lo=round(p[1], 2), hi=round(p[2], 2)))
pd.DataFrame(avail).to_csv(os.path.join(OUT, "AUDIT_availability_reuse.csv"), index=False)

# ---------------- 1970-2013 selection comparison ----------------
# NOTE: pandas aligns fillna on the index. papers has a RangeIndex and pd_map has
# paper_id, so the fallback must be mapped explicitly rather than aligned.
_fallback = pd_map["pubdate"].dt.year
papers["year"] = pd.to_numeric(papers["year_best"], errors="coerce")
papers["year"] = papers["year"].where(papers["year"].notna(),
                                      papers["paper_id"].map(_fallback))
assert papers["year"].notna().sum() > 100000, "year construction failed"
yr = papers.set_index("paper_id")["year"]
for wg in GROUPS:
    a_m = wmat[("AR5", wg)]; a_x = wext[("AR5", wg)]
    out6 = wext[("AR6", wg)]           # any occurrence in same AR6 WG
    for label, cohort in (("matched", a_m), ("extracted", a_x)):
        ids = [p for p in cohort if pd.notna(yr.get(p, np.nan)) and 1970 <= yr.get(p) <= 2013]
        k = sum(1 for p in ids if p in out6)
        p = ci(k, len(ids))
        sel.append(dict(group=wg, cohort=label, n=len(ids), recurrent=k,
                        rate_pct=round(p[0], 2), lo=round(p[1], 2), hi=round(p[2], 2),
                        year_window="1970-2013"))
S = pd.DataFrame(sel)
piv = S.pivot(index="group", columns="cohort", values="rate_pct")
piv["ratio_matched_over_extracted"] = (piv["matched"] / piv["extracted"]).round(3)
piv["overstatement_pp"] = (piv["matched"] - piv["extracted"]).round(2)
S.to_csv(os.path.join(OUT, "AUDIT_selection_comparison.csv"), index=False)
piv.reset_index().to_csv(os.path.join(OUT, "AUDIT_selection_ratio.csv"), index=False)

# ---------------- provenance ----------------
json.dump(dict(
    database=DB, database_sha256=sha256(DB),
    doi_resolution=RES, doi_resolution_sha256=sha256(RES),
    occurrence_frame="AR4/AR5/AR6, paper_id not null: %d rows" % len(occ),
    generated="CJE_revision/analysis/00_audit_base_rates.py",
), open(os.path.join(PROV, "audit_hashes.json"), "w"), indent=2)

print("=== SAMPLE FLOW ===");      print(pd.DataFrame(flow).to_string(index=False))
print("\n=== BASE RATES ===");     print(pd.DataFrame(rates).to_string(index=False))
print("\n=== AVAILABILITY ===");   print(pd.DataFrame(avail).to_string(index=False))
print("\n=== SELECTION RATIO ===");print(piv.to_string())
