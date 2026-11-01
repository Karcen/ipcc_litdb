"""D_venue_strata.py -- supporting table for Task D: per-venue-stratum counts under
the validated joint (year x venue) common-support standardisation of the WGI-vs-WGIII
availability-conditional reuse gap. Descriptive only; no institutional reading.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
import BCD_lib as L

ident = L.identity_frame()
top = L.top_venues(ident, 25)
ident["venue_s"] = np.where(ident["venue"].isin(top), ident["venue"], "OTHER")
A = L.eligible_reuse(ident, "WG1"); B = L.eligible_reuse(ident, "WG3")
rows = []
for cat, s in A.groupby("venue_s"):
    rows.append(dict(venue_category=cat, group="WG1", n=len(s), y=int(s.y.sum())))
for cat, s in B.groupby("venue_s"):
    rows.append(dict(venue_category=cat, group="WG3", n=len(s), y=int(s.y.sum())))
T = pd.DataFrame(rows).pivot_table(index="venue_category", columns="group", values=["n", "y"], aggfunc="first").fillna(0)
T.columns = [f"{a}_{b}" for a, b in T.columns]
T = T.reset_index()
T["in_common_support"] = (T.n_WG1 > 0) & (T.n_WG3 > 0)
T["gap_pct"] = np.where(T.in_common_support,
                        100 * (T.y_WG1 / T.n_WG1.where(T.n_WG1 > 0)) - 100 * (T.y_WG3 / T.n_WG3.where(T.n_WG3 > 0)),
                        np.nan)
common = T[T.in_common_support].copy()
common["pooled_weight_pct"] = 100 * (common.n_WG1 + common.n_WG3) / (common.n_WG1 + common.n_WG3).sum()
T = T.merge(common[["venue_category", "pooled_weight_pct"]], on="venue_category", how="left")
T = T.sort_values(["in_common_support", "n_WG1", "n_WG3"], ascending=[False, False, False])
p = L.OUT / "D_venue_strata.csv"
T.to_csv(p, index=False)
print(f"wrote {len(T)} rows -> {p}")
print(T.head(12).round(3).to_string(index=False))
print("\nwithin-long-tail (OTHER) gap vs named-venue weighted gap:")
if "OTHER" in set(T.venue_category):
    o = T[T.venue_category == "OTHER"].iloc[0]
    print(f"OTHER: n_WG1={int(o.n_WG1)}, n_WG3={int(o.n_WG3)}, gap={o.gap_pct:.4f} pp, weight={o.pooled_weight_pct:.2f}%")
