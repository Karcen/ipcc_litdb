# Portable replay copy: algorithms and frozen labels preserved; paths adapted.
import type_paths as P
"""Turn the completed adjudication sheet into the numbers the paper needs.

NOTE: the shipped verdicts come from analysis/13_independent_adjudication.py, an AI
adjudicator, unless a human coder has overwritten the `verdict` column. The results
file records which adjudicator produced them.

Usage:
    python3 analysis/11_analyse_validation.py            # reads human_validation/coding_sheet.csv
    python3 analysis/11_analyse_validation.py --dry-run  # proves the pipeline works on fake codes

Applies the decision rule pre-registered in human_validation/analysis_plan.md.
Outputs: human_validation/results.md, tables/HV_error_rates.csv, tables/HV_frame_bound.csv
"""
import hashlib, os, sys
import numpy as np, pandas as pd

ROOT = str(P.OUTPUT)
HV = str(P.VALIDATION / "coding_worksheet.csv")
OUT = str(P.OUTPUT / "human_validation")
TAB = str(P.TABLES)
DRY = False
if "--dry-run" in sys.argv:
    raise SystemExit("Synthetic-label runs are excluded from this offline replay.")
SUF  = "_DRYRUN" if DRY else ""

AMBIG = ("flagged_invalid_doi", "flagged_matched_doi_chronology_review",
         "flagged_missing_year", "same_title_multiple_identities")
POP   = ("boundary_WG2", "boundary_WG3")

def wilson(k, n, z=1.96):
    if n == 0: return (np.nan, np.nan, np.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * p, 100 * max(0, c - h), 100 * min(1, c + h))

d = pd.read_csv(HV)
if DRY:
    rng = np.random.default_rng(20261027)
    d["verdict"] = rng.choice(["same", "distinct", "uncertain"], size=len(d), p=[0.90, 0.05, 0.05])
    d["confidence_1to3"] = rng.integers(1, 4, len(d))
    print("DRY RUN: verdicts are synthetic. Pipeline test only.\n")
else:
    if "verdict" not in d or d["verdict"].fillna("").eq("").all():
        sys.exit("coding_sheet.csv has no verdicts yet. Code the sheet with label_tool.py, then re-run.")
    d = d[d["verdict"].fillna("").isin(["same", "distinct", "uncertain"])].copy()
    print(f"coded cases: {len(d)}\n")

d["conf"] = pd.to_numeric(d.get("confidence_1to3"), errors="coerce").fillna(2).astype(int)

def by_pool(frame, uncertain_as=None):
    out = []
    for pool, g in frame.groupby("pool"):
        h = g.copy()
        if uncertain_as is not None:
            h["verdict"] = h["verdict"].where(h["verdict"] != "uncertain", uncertain_as)
        h = h[h["verdict"].isin(["same", "distinct"])]
        k, n = int((h["verdict"] == "distinct").sum()), len(h)
        p, lo, hi = wilson(k, n)
        out.append(dict(pool=pool, kind="ambiguity" if pool in AMBIG else "population",
                        n=n, errors=k, rate_pct=p, lo=lo, hi=hi,
                        uncertain_treatment=uncertain_as or "excluded"))
    return pd.DataFrame(out)

R = pd.concat([by_pool(d, None), by_pool(d, "distinct")], ignore_index=True)
R.to_csv(os.path.join(TAB, f"HV_error_rates{SUF}.csv"), index=False)

pop_excl = R[(R.kind == "population") & (R.uncertain_treatment == "excluded")]
pop_cons = R[(R.kind == "population") & (R.uncertain_treatment == "distinct")]
pop_err  = float(pop_excl.rate_pct.mean()) if len(pop_excl) else np.nan
pop_cons_err = float(pop_cons.rate_pct.mean()) if len(pop_cons) else np.nan

# ---- confidence diagnostics (secondary, per the plan amendment) -------
conf_dist = d["conf"].value_counts().sort_index().to_dict()
conf_rows = []
for lvl in (1, 2, 3):
    g = d[d["pool"].isin(POP) & (d["conf"] == lvl)]
    g = g[g["verdict"].isin(["same", "distinct"])]
    k, n = int((g["verdict"] == "distinct").sum()), len(g)
    p, lo, hi = wilson(k, n)
    conf_rows.append(dict(confidence=lvl, n=n, errors=k, rate_pct=p, lo=lo, hi=hi))
CONF = pd.DataFrame(conf_rows)
dc = d.copy()
dc.loc[(dc["verdict"] == "same") & (dc["conf"] == 1), "verdict"] = "uncertain"
gp = dc[dc["pool"].isin(POP) & dc["verdict"].isin(["same", "distinct"])]
k_c, n_c = int((gp["verdict"] == "distinct").sum()), len(gp)
p_c, lo_c, hi_c = wilson(k_c, n_c)

# ---- bounded sensitivity on the frame result -------------------------
fr = pd.read_csv(os.path.join(TAB, "AUDIT_frame_alternatives.csv"))
def cell(frame, group):
    r = fr[(fr.frame == frame) & (fr.group == group)].iloc[0]
    return int(r.shared), int(r.earlier_n)
brows = []
for group in ("WG2", "WG3"):
    for frame in ("W", "A"):
        k, n = cell(frame, group)
        e = pop_err / 100 if pop_err == pop_err else 0.0
        lo_k, hi_k = max(0, int(round(k - e * k))), min(n, int(round(k + e * (n - k))))
        p, plo, phi = wilson(k, n)
        brows.append(dict(group=group, frame=frame, shared=k, n=n, rate_pct=round(p, 3),
                          rate_lo=round(plo, 3), rate_hi=round(phi, 3),
                          bound_low_pct=round(100 * lo_k / n, 3),
                          bound_high_pct=round(100 * hi_k / n, 3),
                          error_rate_used_pct=round(pop_err, 2)))
B = pd.DataFrame(brows)
B.to_csv(os.path.join(TAB, f"HV_frame_bound{SUF}.csv"), index=False)
sign_ok = all(B[(B.group == g) & (B.frame == "W")].rate_pct.iloc[0] >
              B[(B.group == g) & (B.frame == "A")].rate_pct.iloc[0] for g in ("WG2", "WG3"))

# ---- report ----------------------------------------------------------
adj = d.get("adjudicator", pd.Series(["unspecified"]*len(d))).iloc[0] if "adjudicator" in d else "unspecified"
L = ["# Independent adjudication: results", "",
     f"Adjudicator: **{adj}**", "",
     f"Sheet hash: `{hashlib.sha256(open(HV, 'rb').read()).hexdigest()[:16]}`",
     f"Cases coded: {len(d)}", "", "## Error rate by pool", "",
     "| Pool | Kind | n | errors | rate % | 95 % interval |", "|---|---|---|---|---|---|"]
for _, r in R[R.uncertain_treatment == "excluded"].iterrows():
    L.append(f"| {r.pool} | {r.kind} | {r.n} | {r.errors} | {r.rate_pct:.1f} | [{r.lo:.1f}, {r.hi:.1f}] |")
L += ["", "With `uncertain` allocated to `distinct` (conservative direction):", "",
      "| Pool | Kind | n | rate % | 95 % interval |", "|---|---|---|---|---|"]
for _, r in R[R.uncertain_treatment == "distinct"].iterrows():
    L.append(f"| {r.pool} | {r.kind} | {r.n} | {r.rate_pct:.1f} | [{r.lo:.1f}, {r.hi:.1f}] |")
L += ["", "## Population error rate (the number the paper reports)", "",
      f"Mean across the two boundary pools, `uncertain` excluded: **{pop_err:.1f} per cent**.",
      f"Same, with `uncertain` counted as an error: **{pop_cons_err:.1f} per cent**.", "",
      "## Confidence diagnostics (secondary, per the plan amendment)", "",
      "Confidence distribution: " + ", ".join(f"{k}: {v}" for k, v in sorted(conf_dist.items())), "",
      "| Stated confidence | n | errors | rate % | 95 % interval |", "|---|---|---|---|---|"]
for _, r in CONF.iterrows():
    L.append(f"| {int(r.confidence)} | {r.n} | {r.errors} | {r.rate_pct:.1f} | [{r.lo:.1f}, {r.hi:.1f}] |")
L += ["", "Conservative variant, treating a low-confidence `same` as an abstention: "
      f"**{p_c:.1f} per cent** [{lo_c:.1f}, {hi_c:.1f}] on n = {n_c}.", "",
      "## Bounded sensitivity of the frame result", "",
      "| Group | Frame | rate % | bound low | bound high |", "|---|---|---|---|---|"]
for _, r in B.iterrows():
    L.append(f"| {r.group} | {r.frame} | {r.rate_pct:.2f} | {r.bound_low_pct:.2f} | {r.bound_high_pct:.2f} |")
L += ["", "## Pre-registered decision", ""]
if pop_err != pop_err:
    verdict = "INCONCLUSIVE: no coded population cases"
elif pop_err <= 5 and sign_ok:
    verdict = ("PASS (rule row 1): population error rate <= 5 % and the frame sign is unchanged "
               "under the bound. Report as validation.")
elif pop_err <= 15 and sign_ok:
    verdict = ("PASS WITH WIDENED RANGE (rule row 2): error rate 5-15 %, frame sign unchanged. "
               "Report the magnitude as a range.")
elif not sign_ok:
    verdict = ("FAIL (rule row 3): the frame sign does not survive the bound. The frame result must "
               "be reported as not robust.")
else:
    verdict = "REVIEW (rule row 4 candidate): error rate above 15 %. Re-read the decision table."
L += [f"**{verdict}**", ""]
hdr = ("> **DRY RUN — SYNTHETIC VERDICTS. NOT A RESULT.** Produced from fabricated codes to test "
       "the pipeline. Do not cite, quote or copy into the manuscript.\n\n") if DRY else ""
fn = "results_DRYRUN.md" if DRY else "results.md"
open(os.path.join(OUT, fn), "w").write(hdr + "\n".join(L) + "\n")
print("\n".join(L[-4:]))
print(f"\nwrote {OUT}/{fn}, tables/HV_error_rates{SUF}.csv, tables/HV_frame_bound{SUF}.csv")
