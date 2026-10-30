# Portable replay copy: algorithms and frozen labels preserved; paths adapted.
import type_paths as P
"""Decompose the frame W -> frame A change in measured retention.

The referee asks for three separate quantities. Earlier work answered (1), the
cross-group coverage gap. This script answers (2): how much of the change in
retention between the narrow and the wide frame is attributable to the document-type
composition of the identities the wide frame adds, and how much to recurrence
differences within type.

R_A - R_W = sum_t (w_t^A - w_t^W) r_t^W      composition term
          + sum_t  w_t^A (r_t^A - r_t^W)     within-type rate term
where w_t is the type share of the cohort and r_t the recurrence rate within type.
"""
import os
import pandas as pd

ROOT = str(P.OUTPUT)
PANEL = os.path.join(ROOT, "analysis/data/identity_panel.csv")
OUT = os.path.join(ROOT, "tables/HV_frame_effect_decomposition.csv")

d = pd.read_csv(PANEL)

def in_wg(series, wg):
    return series.fillna("").str.contains(wg)

rows = []
for wg, lab in (("WG1", "WGI"), ("WG2", "WGII"), ("WG3", "WGIII")):
    w = d[in_wg(d.wg_matched_AR5, wg)].copy()
    w["recur"] = in_wg(w.wg_matched_AR6, wg).astype(int)
    a = d[in_wg(d.wg_any_AR5, wg) & d.type_AR5.notna()].copy()
    a["recur"] = in_wg(a.wg_any_AR6, wg).astype(int)
    w = w[w.type_AR5.notna()]

    types = sorted(set(w.type_AR5) | set(a.type_AR5))
    Rw = w.recur.mean(); Ra = a.recur.mean()
    comp = rate = comp_a = rate_w = 0.0
    for t in types:
        wt = (w.type_AR5 == t).mean() if len(w) else 0.0
        wa = (a.type_AR5 == t).mean() if len(a) else 0.0
        rw = w.loc[w.type_AR5 == t, "recur"].mean() if (w.type_AR5 == t).any() else 0.0
        ra = a.loc[a.type_AR5 == t, "recur"].mean() if (a.type_AR5 == t).any() else 0.0
        if pd.isna(rw): rw = 0.0
        if pd.isna(ra): ra = 0.0
        comp += (wa - wt) * rw          # composition, evaluated at frame-W within-type rates
        rate += wa * (ra - rw)          # within-type shift, evaluated at frame-A mix
        comp_a += (wa - wt) * ra        # same split evaluated at frame-A rates
        rate_w += wt * (ra - rw)
        rows.append(dict(group=lab, doc_type=t, share_W=round(100*wt, 2), share_A=round(100*wa, 2),
                         recur_W=round(100*rw, 2), recur_A=round(100*ra, 2),
                         composition_pp=round(100*(wa-wt)*rw, 3), rate_pp=round(100*wa*(ra-rw), 3),
                         composition_pp_atA=round(100*(wa-wt)*ra, 3), rate_pp_atW=round(100*wt*(ra-rw), 3)))
    rows.append(dict(group=lab, doc_type="TOTAL", share_W=100.0, share_A=100.0,
                     recur_W=round(100*Rw, 2), recur_A=round(100*Ra, 2),
                     composition_pp=round(100*comp, 3), rate_pp=round(100*rate, 3),
                     composition_pp_atA=round(100*comp_a, 3), rate_pp_atW=round(100*rate_w, 3)))

out = pd.DataFrame(rows)
out.to_csv(OUT, index=False)
print(out[out.doc_type == "TOTAL"].to_string(index=False))
print()
print(out[out.doc_type != "TOTAL"].to_string(index=False))
