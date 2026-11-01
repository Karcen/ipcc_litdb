"""D_decomposition_recheck.py -- Task D of the CJE revision.

Three quantities are kept strictly separate for the WGI-vs-WGIII
availability-conditional reuse gap (the 1970-2013 window / AR5 literature cutoffs):
  (1) full-sample crude gap;
  (2) common-support crude gap;
  (3) common-support standardised gap (pooled-count weights within shared strata).
Sample-support shares are reported alongside. Long-tail venue sensitivity:
top-25 venue categories vs pooled remainder, and a leave-one-category-out range.
The within-stratum residual is an UNEXPLAINED DESCRIPTIVE DIFFERENCE. It is not
estimated institutional behaviour and carries no causal interpretation.

Also recomputes the 1970-2013-window selection-into-matching comparison
(matched vs extracted earlier cohort, same later outcome definition) that the
manuscript uses as a "matched-frame bias" contrast.

Unit: assigned identity (papers.paper_id). Deterministic; no sampling.
Outputs: CJE_revision/tables/D_decomposition.csv (+ printed audit trail).
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import BCD_lib as L

ROWS = []


def add(**kw):
    ROWS.append(kw)


def sel_1970_2013(ident):
    """Matched vs extracted AR5 earlier-cohort comparison, 1970-2013 window,
    outcome = any AR6 occurrence in the same working group (fixed outcome)."""
    out = []
    for g in L.GROUPS:
        base = ident[ident["year"].notna() & (ident["year"] >= 1970) & (ident["year"] <= 2013)]
        mt = base[base[f"m5_{g}"]]
        ex = base[base[f"a5_{g}"]]
        un = base[base[f"a5_{g}"] & ~base[f"m5_{g}"]]
        for label, s in (("matched_earlier", mt), ("extracted_earlier", ex),
                         ("extracted_only_earlier", un)):
            k = int(s[f"a6_{g}"].sum())
            n = len(s)
            p, lo, hi = L.wilson(k, n)
            out.append(dict(group=g, cohort=label, n=n, recurrent=k, rate_pct=p,
                            lo_pct=lo, hi_pct=hi))
    R = pd.DataFrame(out)
    piv = R.pivot(index="group", columns="cohort", values="rate_pct")
    cnt = R.pivot(index="group", columns="cohort", values=["n", "recurrent"])
    for g in L.GROUPS:
        add(task="D1_selection_1970_2013", group=g, quantity="matched_cohort_rate_pct",
            numerator=int(cnt.loc[g, ("recurrent", "matched_earlier")]),
            denominator=int(cnt.loc[g, ("n", "matched_earlier")]),
            estimate=float(piv.loc[g, "matched_earlier"]), spec="outcome=any AR6 occ, same WG; years 1970-2013",
            interpretation="Matched earlier cohort: AR5 identity with >=1 database-matched occurrence; outcome is later recurrence as any assigned identity.")
        add(task="D1_selection_1970_2013", group=g, quantity="extracted_cohort_rate_pct",
            numerator=int(cnt.loc[g, ("recurrent", "extracted_earlier")]),
            denominator=int(cnt.loc[g, ("n", "extracted_earlier")]),
            estimate=float(piv.loc[g, "extracted_earlier"]), spec="outcome=any AR6 occ, same WG; years 1970-2013",
            interpretation="Extracted earlier cohort: all AR5 assigned identities in the group; same outcome definition.")
        add(task="D1_selection_1970_2013", group=g, quantity="extracted_only_cohort_rate_pct",
            numerator=int(cnt.loc[g, ("recurrent", "extracted_only_earlier")]),
            denominator=int(cnt.loc[g, ("n", "extracted_only_earlier")]),
            estimate=float(piv.loc[g, "extracted_only_earlier"]), spec="outcome=any AR6 occ, same WG; years 1970-2013",
            interpretation="Extracted-only earlier cohort: AR5 identity whose AR5 occurrences were all unmatched. Selection contrast, not a causal effect of matching.")
        add(task="D1_selection_1970_2013", group=g, quantity="selection_gap_pp_matched_minus_extracted",
            numerator=None, denominator=None,
            estimate=float(piv.loc[g, "matched_earlier"] - piv.loc[g, "extracted_earlier"]),
            spec="pp", interpretation="Observed frame contrast under a fixed outcome and year window. Not a bias in a true recurrence rate.")
        add(task="D1_selection_1970_2013", group=g, quantity="selection_ratio_matched_over_extracted",
            numerator=None, denominator=None,
            estimate=float(piv.loc[g, "matched_earlier"] / piv.loc[g, "extracted_earlier"]),
            spec="ratio", interpretation="Ratio used in the draft as a 'bias' multiplier; it is an observational frame contrast.")
    # Year-standardised contrast: matched vs extracted-only, weights from the
    # matched cohort's year distribution (only years where matched can exist).
    for g in L.GROUPS:
        base = ident[ident["year"].notna() & (ident["year"] >= 1970) & (ident["year"] <= 2013)].copy()
        base["yb"] = base["year"].astype(int)
        mt = base[base[f"m5_{g}"]]
        un = base[base[f"a5_{g}"] & ~base[f"m5_{g}"]]
        cells = mt.groupby("yb").agg(nm=("year", "size"), ym=(f"a6_{g}", "sum")).join(
            un.groupby("yb").agg(nu=("year", "size"), yu=(f"a6_{g}", "sum")), how="outer").fillna(0)
        cells = cells[(cells.nm > 0) & (cells.nu > 0)]
        if len(cells):
            w = cells.nm / cells.nm.sum()
            std_gap = 100 * float((w * (cells.ym / cells.nm - cells.yu / cells.nu)).sum())
            cr = 100 * (mt[f"a6_{g}"].mean() - un[f"a6_{g}"].mean())
            add(task="D1_selection_1970_2013", group=g, quantity="selection_gap_pp_year_standardised",
                numerator=int(cells.ym.sum()), denominator=int(cells.nm.sum()),
                estimate=std_gap, spec=f"matched-weight direct standardisation over {len(cells)} common single-year cells ({int(cells.index.min())}-{int(cells.index.max())})",
                interpretation="Contrast after holding publication-year composition at the matched cohort's distribution. Spans only years where both cohorts exist (eff. 1998+).")
            add(task="D1_selection_1970_2013", group=g, quantity="selection_gap_pp_crude_same_window",
                numerator=None, denominator=None, estimate=cr,
                spec=f"matched minus extracted-only, 1970-2013, {len(cells)} y-bins overlap",
                interpretation="Crude contrast before year standardisation.")
    # Coverage boundary of the matched frame (observational, not IPCC behaviour).
    for rep in ("AR5", "AR6"):
        for g in L.GROUPS:
            s = ident[ident[f"a{rep[-1]}_{g}"]]
            pre = s[(s["year"] < 1998)]
            add(task="D1_coverage_boundary", group=f"{rep}_{g}", quantity="matched_frame_pre1998_identities",
                numerator=int(pre[f"m{rep[-1]}_{g}"].sum()), denominator=len(pre),
                estimate=float(pre[f"m{rep[-1]}_{g}"].mean() * 100) if len(pre) else np.nan,
                spec="identities with year<1998", interpretation="Exact share of pre-1998 identities that can enter the matched frame; local bibliographic snapshot covers publication years 1998-2026.")
    return R


def decomposition(ident):
    ident = ident.copy()
    top = L.top_venues(ident, 25)
    ident["venue_s"] = np.where(ident["venue"].isin(top), ident["venue"], "OTHER")
    specs = {"year": ["year_s"], "venue": ["venue_s"], "year_x_venue": ["year_s", "venue_s"]}
    res = {}
    for name, keys in specs.items():
        r = L.common_support(ident, "WG1", "WG3", keys)
        res[name] = r
        add(task="D2_gap", quantity="full_sample_crude_gap_pp", group="WG1_vs_WG3",
            numerator=r["y_g1_full"], denominator=r["n_g1_full"], estimate=r["crude_full_gap_pp"],
            spec="all eligible: AR6 matched in group, exact date <= AR5 cutoff; y = matched AR5 same group",
            interpretation="Full-sample crude gap; numerator/denominator refer to WGI. WGIII full: %d/%d." % (r["y_g3_full"], r["n_g3_full"]))
        for q in ("crude_common_gap_pp", "standardised_common_gap_pp", "support_shift_pp",
                  "composition_common_pp", "support_share_pct"):
            add(task="D2_gap", quantity=q, group="WG1_vs_WG3",
                numerator=r["y_g1_common"] if q in ("crude_common_gap_pp",) else None,
                denominator=r["n_g1_common"] if q in ("crude_common_gap_pp",) else None,
                estimate=float(r[q]), spec=f"spec={name}; strata={r['common_strata']}; "
                f"common n WGI={r['n_g1_common']} WGIII={r['n_g3_common']}; full n WGI={r['n_g1_full']} WGIII={r['n_g3_full']}",
                interpretation="Common-support crude gap (support-restricted); standardised gap (pooled common-support weights); support shift = full crude - common crude; composition = common crude - standardised (unexplained within-stratum difference); support share = (n_common WGI+WGIII)/(n_full WGI+WGIII).")
        add(task="D2_gap", quantity="sample_support_share_g1_pct", group="WG1_vs_WG3",
            numerator=r["n_g1_common"], denominator=r["n_g1_full"], estimate=r["support_share_g1_pct"],
            spec=f"spec={name}", interpretation="Share of eligible WGI records in jointly represented strata.")
        add(task="D2_gap", quantity="sample_support_share_g3_pct", group="WG1_vs_WG3",
            numerator=r["n_g3_common"], denominator=r["n_g3_full"], estimate=r["support_share_g3_pct"],
            spec=f"spec={name}", interpretation="Share of eligible WGIII records in jointly represented strata.")
        add(task="D2_gap", quantity="unexplained_within_stratum_residual_pp", group="WG1_vs_WG3",
            numerator=None, denominator=None, estimate=float(r["composition_common_pp"]),
            spec=f"spec={name}", interpretation="Residual within-stratum difference after common-support standardisation: UNEXPLAINED and DESCRIPTIVE. Not institutional behaviour, not a causal effect.")
    return res


def long_tail(ident):
    ident = ident.copy()
    top_all = L.top_venues(ident, 25)
    real = ident["venue"].notna()
    A = L.eligible_reuse(ident, "WG1")
    B = L.eligible_reuse(ident, "WG3")
    E = pd.concat([A, B])
    top_elig = list(E.loc[E["venue"].notna(), "venue"].value_counts().head(25).index)

    variants = {}
    for label, top in (("top25_full_frame_plus_other", top_all), ("top25_eligible_plus_other", top_elig)):
        x = ident.copy()
        x["venue_s"] = np.where(x["venue"].isin(top), x["venue"], "OTHER")
        variants[label] = x
    # top-25 only (pooled remainder dropped)
    x = ident.copy()
    x["venue_s"] = pd.Series(np.where(x["venue"].isin(top_all), x["venue"], None),
                              index=x.index, dtype=object)
    variants["top25_only_drop_remainder"] = x
    # pooled remainder only
    x = ident.copy()
    x["venue_s"] = pd.Series(np.where(x["venue"].isin(top_all), None, "OTHER"),
                              index=x.index, dtype=object)
    variants["remainder_only"] = x

    for label, x in variants.items():
        for spec, keys in (("venue_s", ["venue_s"]), ("year_x_venue", ["year_s", "venue_s"])):
            r = L.common_support(x, "WG1", "WG3", keys)
            if r is None:
                add(task="D3_long_tail", quantity="standardised_gap_pp", group="WG1_vs_WG3",
                    numerator=None, denominator=None, estimate=np.nan, spec=f"{label}/{spec}",
                    interpretation="No common support under this venue construction.")
                continue
            add(task="D3_long_tail", quantity=f"standardised_gap_pp[{spec}]", group="WG1_vs_WG3",
                numerator=None, denominator=None, estimate=float(r["standardised_common_gap_pp"]),
                spec=f"{label}; strata={r['common_strata']}; support_share={r['support_share_pct']:.2f}%",
                interpretation=f"Long-tail venue sensitivity variant {label}.")
            add(task="D3_long_tail", quantity=f"common_support_crude_gap_pp[{spec}]", group="WG1_vs_WG3",
                numerator=r["y_g1_common"], denominator=r["n_g1_common"],
                estimate=float(r["crude_common_gap_pp"]),
                spec=f"{label}; strata={r['common_strata']}",
                interpretation="Common-support crude gap under this venue construction.")

    # Leave-one-category-out on the joint (year x venue) standardisation.
    x = ident.copy()
    x["venue_s"] = np.where(x["venue"].isin(top_all), x["venue"], "OTHER")
    cats = sorted(x["venue_s"].dropna().unique())
    loo = []
    for c in cats:
        y = x[x["venue_s"] != c]
        r = L.common_support(y, "WG1", "WG3", ["year_s", "venue_s"])
        if r is None:
            continue
        loo.append(dict(dropped_venue=c, standardised_gap_pp=r["standardised_common_gap_pp"],
                        crude_common_gap_pp=r["crude_common_gap_pp"],
                        support_share_pct=r["support_share_pct"],
                        n_g1_common=r["n_g1_common"], n_g3_common=r["n_g3_common"]))
    LOO = pd.DataFrame(loo).sort_values("standardised_gap_pp")
    for _, r in LOO.iterrows():
        add(task="D3_leave_one_out", quantity="joint_standardised_gap_pp", group="WG1_vs_WG3",
            numerator=None, denominator=None, estimate=float(r.standardised_gap_pp),
            spec=f"dropped venue category={r.dropped_venue}; strata support_share={r.support_share_pct:.2f}%; "
                 f"n_common WGI={int(r.n_g1_common)} WGIII={int(r.n_g3_common)}",
            interpretation="Joint year x venue direct standardisation after dropping one venue category.")
    add(task="D3_leave_one_out", quantity="joint_standardised_gap_range_pp", group="WG1_vs_WG3",
        numerator=None, denominator=None,
        estimate=float(LOO.standardised_gap_pp.max() - LOO.standardised_gap_pp.min()),
        spec=f"min={LOO.standardised_gap_pp.min():.6f} (drop {LOO.iloc[0].dropped_venue}); "
             f"max={LOO.standardised_gap_pp.max():.6f} (drop {LOO.iloc[-1].dropped_venue})",
        interpretation="Range of the joint standardised gap over leave-one-venue-category-out replications. Descriptive sensitivity range, not a confidence interval.")
    return LOO


def main():
    ident = L.identity_frame()
    print(f"identity frame: {ident.shape[0]:,} identities")
    S = sel_1970_2013(ident)
    print("\n[D1] 1970-2013 selection comparison (matched vs extracted earlier cohort, "
          "outcome = any AR6 occurrence in same WG)")
    print(S.pivot(index="group", columns="cohort", values="rate_pct").round(4).to_string())
    print(S.pivot(index="group", columns="cohort", values="n").to_string())
    print(S.pivot(index="group", columns="cohort", values="recurrent").to_string())

    print("\n[D2] WGI vs WGIII availability-conditional reuse, three quantities")
    G = decomposition(ident)
    tab = pd.DataFrame({k: {"crude_full": v["crude_full_gap_pp"],
                            "crude_common": v["crude_common_gap_pp"],
                            "standardised_common": v["standardised_common_gap_pp"],
                            "support_share_pct": v["support_share_pct"],
                            "strata": v["common_strata"],
                            "n_g1_common": v["n_g1_common"], "n_g3_common": v["n_g3_common"]}
                       for k, v in G.items()}).T
    print(tab.round(4).to_string())

    print("\n[D3] long-tail venue sensitivity (joint year x venue standardised gap)")
    LOO = long_tail(ident)
    T = pd.DataFrame(ROWS)
    T = T[T.task == "D3_long_tail"]
    print(T[["quantity", "estimate", "spec"]].to_string(index=False, max_colwidth=90))
    print("\nLeave-one-venue-category-out (joint standardised gap), "
          f"n={len(LOO)} replications, range {LOO.standardised_gap_pp.min():.4f} to "
          f"{LOO.standardised_gap_pp.max():.4f} pp")
    print(LOO.head(4).round(4).to_string(index=False))
    print("...")
    print(LOO.tail(4).round(4).to_string(index=False))

    out = pd.DataFrame(ROWS)
    out = out[["task", "group", "quantity", "numerator", "denominator", "estimate", "spec", "interpretation"]]
    p = L.OUT / "D_decomposition.csv"
    out.to_csv(p, index=False)
    print(f"\nwrote {len(out)} rows -> {p}")
    # provenance companions
    LOO.to_csv(L.OUT / "D_leave_one_venue_out.csv", index=False)
    print(f"wrote {len(LOO)} rows -> {L.OUT / 'D_leave_one_venue_out.csv'}")


if __name__ == "__main__":
    main()
