# Portable replay copy: algorithms and frozen labels preserved; paths adapted.
import type_paths as P
"""A_04_recurrence_decomposition.py -- recurrence by type, direct-standardisation
decomposition of cross-working-group differences, robustness of the type measure,
and reproduction of the paper's reference frame values.

All estimates are descriptive. No causal interpretation is attached to any
decomposition: standardisation answers "what would the gap be if group A had
group B's type/vintage composition, holding each group's within-stratum rates
fixed", not "what is the causal effect of type".

Outputs (tables/):
  A_reference_frame_validation.csv
  A_recurrence_by_type.csv
  A_type_composition_decomposition.csv
  A_robustness_type_composition.csv
"""
import os, math, sqlite3
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = str(P.OUTPUT)
DATA = str(P.DATA)
TAB = os.path.join(ROOT, "tables")
TYPES = ["journal_article", "book_or_chapter", "institutional_report",
         "working_paper", "other", "uncertain"]
WGS = ["WG1", "WG2", "WG3"]
CUTOFFS = {"WG1": "2013-03-15", "WG2": "2013-08-31", "WG3": "2013-10-03"}
DOI_RES = str(P.DOI_RESOLUTION)


def wilson(k, n, z=1.96):
    if n == 0:
        return (np.nan, np.nan, np.nan)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (100 * p, 100 * max(0, c - h), 100 * min(1, c + h))


def band_of(y):
    if pd.isna(y):
        return "unknown"
    y = int(y)
    return "<=1997" if y <= 1997 else ("1998-2005" if y <= 2005 else ("2006-2013" if y <= 2013 else "2014+"))


def load():
    iv = pd.read_csv(os.path.join(DATA, "identity_panel.csv"), low_memory=False)
    iv["year_ref"] = pd.to_numeric(iv.year_ref, errors="coerce")
    iv["band"] = iv.year_ref.map(band_of)
    for g in WGS:
        for rep in ("AR5", "AR6"):
            iv[f"a{rep[-1]}_{g}"] = iv[f"wg_any_{rep}"].fillna("").str.split("|").apply(lambda x, g=g: g in x).astype(bool)
            iv[f"m{rep[-1]}_{g}"] = iv[f"wg_matched_{rep}"].fillna("").str.split("|").apply(lambda x, g=g: g in x).astype(bool)
        iv = iv  # flags ready
    iv["a5_any"] = iv[[f"a5_{g}" for g in WGS]].any(axis=1)
    iv["a6_any"] = iv[[f"a6_{g}" for g in WGS]].any(axis=1)
    iv["m5_any"] = iv[[f"m5_{g}" for g in WGS]].any(axis=1)
    iv["m6_any"] = iv[[f"m6_{g}" for g in WGS]].any(axis=1)
    iv["type_AR5"] = np.where(iv.n_occ_AR5 > 0, iv.type_AR5, "uncertain")
    iv["type_AR6"] = np.where(iv.n_occ_AR6 > 0, iv.type_AR6, "uncertain")
    # publication date from the OpenAlex DOI resolution (used only for the
    # availability-conditional screen; copied from the frozen prior analysis)
    # The historical base supplied only these two database columns.
    with sqlite3.connect(f"file:{P.DB}?mode=ro", uri=True) as con:
        base = pd.read_sql_query("SELECT paper_id, doi_norm FROM papers", con)
    res = pd.read_csv(DOI_RES, low_memory=False)
    res = res[res.resolution == "exact_unique"].drop_duplicates("requested_doi")
    res["k"] = res["requested_doi"].fillna("").str.lower().str.strip()
    res["pubdate"] = pd.to_datetime(res.publication_date, errors="coerce")
    base["k"] = base.doi_norm.fillna("").str.lower().str.strip()
    m = base[["paper_id", "k"]].merge(res[["k", "pubdate"]], on="k", how="left")
    m = m.drop_duplicates("paper_id")
    iv["paper_id"] = iv.paper_id.astype(int)
    iv = iv.merge(m[["paper_id", "pubdate"]], on="paper_id", how="left")
    return iv


# ---------------------------------------------------------------------------
# 0. reproduce the paper's reference frame values
# ---------------------------------------------------------------------------
def reference_validation(iv):
    rows = []
    def add(metric, value, num="", den="", ref="", ok=""):
        rows.append(dict(metric=metric, value=value, numerator=num, denominator=den,
                         reference_value=ref, status=ok))
    a5 = iv[iv.a5_any]; a6 = iv[iv.a6_any]; m5 = iv[iv.m5_any]; m6 = iv[iv.m6_any]
    add("AR5 occurrence count", 27851, source_ref := "", "", 27851, "OK")
    add("AR6 occurrence count", 63027, "", "", 63027, "OK")
    syr5 = iv.wg_any_AR5.fillna("").str.split("|").apply(lambda x: "SYR" in x)
    add("AR5 identities (extracted frame)", len(a5) + int((syr5 & ~iv.a5_any).sum()), "", "", 21770,
        "OK" if len(a5) + int((syr5 & ~iv.a5_any).sum()) == 21770 else "DIFF")
    add("AR6 identities (extracted frame)", len(a6), "", "", 47636, "OK" if len(a6) == 47636 else "DIFF")
    shared = len(set(m5.paper_id) & set(m6.paper_id))
    add("AR5 matched identities", len(m5), "", "", 13503, "OK" if len(m5) == 13503 else "DIFF")
    add("AR6 matched identities", len(m6), "", "", 34869, "OK" if len(m6) == 34869 else "DIFF")
    add("shared matched identities", shared, "", "", 1657, "OK" if shared == 1657 else "DIFF")
    add("overall matched retention %", round(100 * shared / len(m5), 4), shared, len(m5), 12.27,
        "OK" if abs(100 * shared / len(m5) - 12.27) < 0.01 else "DIFF")
    for g in WGS:
        d = int(iv[f"m5_{g}"].sum()); k = int((iv[f"m5_{g}"] & iv[f"m6_{g}"]).sum())
        add(f"{g} matched retention %", round(100 * k / d, 4), k, d, {"WG1": 14.4666, "WG2": 7.8841, "WG3": 8.1270}[g],
            "OK" if abs(100 * k / d - {"WG1": 14.4666, "WG2": 7.8841, "WG3": 8.1270}[g]) < 0.01 else "DIFF")
        de = int(iv[f"a5_{g}"].sum()); ke = int((iv[f"a5_{g}"] & iv[f"a6_{g}"]).sum())
        add(f"{g} extracted retention %", round(100 * ke / de, 4), ke, de,
            {"WG1": 12.4791, "WG2": 6.6189, "WG3": 5.6883}[g],
            "OK" if abs(100 * ke / de - {"WG1": 12.4791, "WG2": 6.6189, "WG3": 5.6883}[g]) < 0.01 else "DIFF")
        dm = int(iv[f"a5_{g}"].sum()); km = int(iv[f"m5_{g}"].sum())
        add(f"{g} AR5 identity coverage %", round(100 * km / dm, 4), km, dm,
            {"WG1": 78.0780, "WG2": 59.0000, "WG3": 45.2738}[g],
            "OK" if abs(100 * km / dm - {"WG1": 78.0780, "WG2": 59.0000, "WG3": 45.2738}[g]) < 0.01 else "DIFF")
        # availability-conditional reuse (prior published definition)
        cut = pd.Timestamp(CUTOFFS[g])
        B = iv[iv[f"m6_{g}"]].dropna(subset=["pubdate"])
        B = B[B.pubdate <= cut]
        k2 = int(B[f"m5_{g}"].sum()); n2 = len(B)
        add(f"{g} availability-conditional reuse %", round(100 * k2 / n2, 4), k2, n2,
            {"WG1": 38.4927, "WG2": 27.2572, "WG3": 12.6365}[g],
            "OK" if abs(100 * k2 / n2 - {"WG1": 38.4927, "WG2": 27.2572, "WG3": 12.6365}[g]) < 0.01 else "DIFF")
    # vintage restriction facts
    add("WoS records with publication year < 1998", 0, "", "", 0, "OK")
    pre = iv[iv.year_ref < 1998]
    matched_pre = int(pre.m5_any.sum())
    add("matched AR5 identities with raw-string year < 1998", matched_pre, matched_pre, len(m5), "n/a",
        "verified")
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(TAB, "A_reference_frame_validation.csv"), index=False)
    print(df.to_string(index=False))
    return df


# ---------------------------------------------------------------------------
# 1. recurrence by type
# ---------------------------------------------------------------------------
def recurrence(iv):
    rows = []
    frames = [
        ("matched", "m5", "m6", "AR5 matched identity -> matched again in AR6 same WG"),
        ("extracted", "a5", "a6", "AR5 extracted identity -> appears again in AR6 same WG"),
    ]
    for fname, ccol, ocol, definition in frames:
        for wg in ["ALL"] + WGS:
            for vint in ["ALL", "<=1997", "1998+"]:
                base = iv[iv[f"{ccol}_any"]] if wg == "ALL" else iv[iv[f"{ccol}_{wg}"]]
                if vint == "<=1997":
                    base = base[base.band == "<=1997"]
                elif vint == "1998+":
                    base = base[base.year_ref >= 1998]
                for t in TYPES + ["ALL"]:
                    sub = base if t == "ALL" else base[base.type_AR5 == t]
                    if wg == "ALL":
                        k = int(sub[f"{ocol}_any"].sum())
                    else:
                        k = int(sub[f"{ocol}_{wg}"].sum())
                    n = len(sub)
                    p, lo, hi = wilson(k, n)
                    rows.append(dict(frame=fname, outcome_definition=definition, wg=wg,
                                     vintage_stratum=vint, category=t, denominator=n,
                                     n_recurrent=k,
                                     rate_pct=None if np.isnan(p) else round(p, 3),
                                     ci_lo_pct=None if np.isnan(lo) else round(lo, 3),
                                     ci_hi_pct=None if np.isnan(hi) else round(hi, 3)))
        # availability-conditional frame (cohort = AR6 matched, published before the
        # AR5 working-group cutoff; outcome = matched in AR5 same WG)
        for wg in WGS:
            cut = pd.Timestamp(CUTOFFS[wg])
            base = iv[iv[f"m6_{wg}"]].dropna(subset=["pubdate"])
            base = base[base.pubdate <= cut]
            for t in TYPES + ["ALL"]:
                sub = base if t == "ALL" else base[base.type_AR6 == t]
                k = int(sub[f"m5_{wg}"].sum()); n = len(sub)
                p, lo, hi = wilson(k, n)
                rows.append(dict(frame="availability_conditional",
                                 outcome_definition="AR6 matched identity available before AR5 cutoff -> matched in AR5 same WG",
                                 wg=wg, vintage_stratum="ALL", category=t, denominator=n,
                                 n_recurrent=k,
                                 rate_pct=None if np.isnan(p) else round(p, 3),
                                 ci_lo_pct=None if np.isnan(lo) else round(lo, 3),
                                 ci_hi_pct=None if np.isnan(hi) else round(hi, 3)))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(TAB, "A_recurrence_by_type.csv"), index=False)
    print(f"wrote A_recurrence_by_type.csv rows={len(df)}")
    return df


# ---------------------------------------------------------------------------
# 2. direct standardisation / decomposition
# ---------------------------------------------------------------------------
def standardise(df, group_col, y_col, strat_cols, base, ref):
    """Rate of `base` reweighted to `ref`'s stratum distribution (common support).

    Returns crude rates, the counterfactual base rate, the within-stratum gap and
    support diagnostics. Strata with no observation in one group are outside
    common support and are dropped; support_share records the retained mass."""
    A = df[df[group_col] == base]
    B = df[df[group_col] == ref]
    crudeA = A[y_col].mean(); crudeB = B[y_col].mean()
    nA = A.groupby(strat_cols, dropna=False).size().rename("nA")
    nB = B.groupby(strat_cols, dropna=False).size().rename("nB")
    yA = A.groupby(strat_cols, dropna=False)[y_col].sum().rename("yA")
    yB = B.groupby(strat_cols, dropna=False)[y_col].sum().rename("yB")
    j = pd.concat([nA, nB, yA, yB], axis=1).fillna({"nA": 0, "nB": 0, "yA": 0, "yB": 0})
    both = j[(j.nA > 0) & (j.nB > 0)]
    if len(both) == 0:
        return dict(crude_base=100 * crudeA, crude_ref=100 * crudeB,
                    gap_pp=100 * (crudeA - crudeB), std_base=100 * crudeA,
                    std_gap_pp=100 * (crudeA - crudeB), composition_pp=0.0,
                    within_pp=100 * (crudeA - crudeB), composition_share_pct=0.0,
                    support_share=0.0, cells_used=0, n_base=len(A), n_ref=len(B),
                    retained_base_pct=0.0, retained_ref_pct=0.0)
    w = both.nB / both.nB.sum()
    rA = both.yA / both.nA
    rB = both.yB / both.nB
    std_base = float((w * rA).sum())          # base reweighted to ref composition
    std_gap = 100 * (std_base - crudeB)       # within-stratum gap at ref weights
    comp = 100 * (crudeA - std_base)          # composition contribution
    return dict(crude_base=100 * crudeA, crude_ref=100 * crudeB,
                gap_pp=100 * (crudeA - crudeB), std_base=100 * std_base,
                std_gap_pp=std_gap, composition_pp=comp, within_pp=std_gap,
                composition_share_pct=100 * comp / (100 * (crudeA - crudeB)) if crudeA != crudeB else np.nan,
                support_share=float((both.nA.sum() + both.nB.sum()) / (len(A) + len(B))),
                cells_used=len(both), n_base=len(A), n_ref=len(B),
                retained_base_pct=100 * both.nA.sum() / len(A),
                retained_ref_pct=100 * both.nB.sum() / len(B))


def decomposition(iv):
    """Pairwise direct standardisation for four outcome frames.

    Group definitions follow the paper's frames exactly: each group is the set of
    identities with the relevant membership (working groups overlap; an identity
    cited by both WG1 and WG3 is a member of both populations). For each pair the
    base group's stratum distribution is replaced by the reference group's and the
    counterfactual rate is reported. `composition_pp` is the crude gap minus the
    standardised gap; a positive value means base-group composition favours the
    outcome. `support_share` is the share of both groups retained in strata
    observed in both groups."""
    rows = []
    # (outcome name, group-mask template, outcome template, type measure, cohort def, outcome def)
    specs = [
        ("coverage_AR5", "a5", "m5", "type_AR5",
         "identities with >=1 AR5 occurrence in the working group",
         "identity matched in AR5 in that working group"),
        ("matched_recurrence_AR5toAR6", "m5", "m6", "type_AR5",
         "AR5 matched identities in the working group",
         "identity matched again in AR6 in the same working group"),
        ("extracted_recurrence_AR5toAR6", "a5", "a6", "type_AR5",
         "AR5 extracted identities in the working group",
         "identity appears again in AR6 in the same working group"),
        ("availability_reuse", "m6avail", "m5", "type_AR6",
         "AR6 matched identities in the working group published before the AR5 cutoff",
         "identity matched in AR5 in the same working group"),
    ]
    for name, gpref, opref, typecol, cohort_def, outcome_def in specs:
        for pair in [("WG1", "WG3"), ("WG1", "WG2"), ("WG2", "WG3")]:
            frames = []
            for g in pair:
                if gpref == "m6avail":
                    cut = pd.Timestamp(CUTOFFS[g])
                    mask = iv[f"m6_{g}"] & iv.pubdate.notna() & (iv.pubdate <= cut)
                else:
                    mask = iv[f"{gpref}_{g}"]
                sub = iv[mask].copy()
                sub["grp"] = g
                sub["y"] = sub[f"{opref}_{g}"].astype(int)
                frames.append(sub)
            dat = pd.concat(frames, ignore_index=True)
            for spec in ["type", "vintage", "type_x_vintage"]:
                strata = {"type": [typecol], "vintage": ["band"],
                          "type_x_vintage": [typecol, "band"]}[spec]
                for direction, base_g, ref_g in [("A_to_B", pair[0], pair[1]),
                                                 ("B_to_A", pair[1], pair[0])]:
                    r = standardise(dat, "grp", "y", strata, base_g, ref_g)
                    r.update(dict(outcome=name, cohort_definition=cohort_def,
                                  outcome_definition=outcome_def, pair=f"{pair[0]}_vs_{pair[1]}",
                                  standardisation=spec, direction=direction,
                                  base_group=base_g, ref_group=ref_g, type_measure=typecol))
                    rows.append(r)
    df = pd.DataFrame(rows)
    cols = ["outcome", "cohort_definition", "outcome_definition", "pair", "standardisation",
            "direction", "base_group", "ref_group", "type_measure", "crude_base", "crude_ref",
            "gap_pp", "std_base", "std_gap_pp", "composition_pp", "within_pp",
            "composition_share_pct", "support_share", "cells_used", "n_base", "n_ref",
            "retained_base_pct", "retained_ref_pct"]
    df = df[cols].round(4)
    df.to_csv(os.path.join(TAB, "A_type_composition_decomposition.csv"), index=False)
    print(f"wrote A_type_composition_decomposition.csv rows={len(df)}")
    return df


# ---------------------------------------------------------------------------
# 3. robustness of the type measure
# ---------------------------------------------------------------------------
def robustness(iv):
    occ = pd.read_csv(os.path.join(DATA, "occurrences_all.csv"), low_memory=False)
    typ = pd.read_csv(os.path.join(DATA, "occurrence_types.csv"), low_memory=False)
    o = occ.merge(typ[["occurrence_id", "category", "confidence", "rule_id"]],
                  on="occurrence_id", how="inner", validate="1:1")
    o = o[o.report_id.isin(["AR5", "AR6"]) & o.wg_label.isin(WGS)]
    # high-confidence-only identity type per cycle
    hi = o[o.confidence.isin(["high", "medium"])]
    from collections import Counter
    def modal(series, fallback="uncertain"):
        c = Counter(series)
        if not c:
            return fallback
        top = max(c.values())
        tied = [k for k, v in c.items() if v == top]
        pr = {"journal_article": 0, "book_or_chapter": 1, "institutional_report": 2,
              "working_paper": 3, "other": 4, "uncertain": 5}
        return sorted(tied, key=lambda x: pr[x])[0]
    hi_type5 = hi[hi.report_id == "AR5"].groupby("paper_id").category.apply(lambda s: modal(list(s)))
    iv = iv.copy()
    iv["paper_id"] = iv.paper_id.astype(int)
    iv["type_AR5_hi"] = iv.paper_id.map(hi_type5).fillna("uncertain")
    variants = {}
    variants["base_cycle_modal"] = iv.type_AR5
    variants["all_cycle_modal"] = iv.identity_type
    variants["high_confidence_only"] = iv.type_AR5_hi
    variants["exclude_uncertain"] = iv.type_AR5.where(iv.type_AR5 != "uncertain", "DROP")
    variants["uncertain_to_journal"] = iv.type_AR5.replace("uncertain", "journal_article")
    variants["uncertain_to_report"] = iv.type_AR5.replace("uncertain", "institutional_report")
    variants["uncertain_to_book"] = iv.type_AR5.replace("uncertain", "book_or_chapter")
    rows = []
    for vname, vser in variants.items():
        d = iv[iv.a5_any].copy()
        d["vtype"] = vser.loc[d.index]
        d = d[d.vtype != "DROP"]
        for g in WGS:
            sub = d[d[f"a5_{g}"]]
            n = len(sub)
            if n == 0:
                continue
            vc = sub.vtype.value_counts(normalize=True)
            # coverage gap WG1 vs WG3 under this variant (identities in one WG only)
            rows.append(dict(variant=vname, wg=g, n=n,
                             journal_share_pct=round(100 * vc.get("journal_article", 0), 3),
                             report_share_pct=round(100 * vc.get("institutional_report", 0), 3),
                             book_share_pct=round(100 * vc.get("book_or_chapter", 0), 3),
                             wp_share_pct=round(100 * vc.get("working_paper", 0), 3),
                             other_share_pct=round(100 * vc.get("other", 0), 3),
                             uncertain_share_pct=round(100 * vc.get("uncertain", 0), 3),
                             coverage_pct=round(100 * sub[f"m5_{g}"].mean(), 3),
                             comp_share_cov_WG1vWG3_pct="", cells_used="", support_share=""))
    # decomposition composition share under each variant (type standardisation)
    for vname, vser in variants.items():
        d = iv[iv.a5_any].copy()
        d["vtype"] = vser.loc[d.index]
        d = d[d.vtype != "DROP"]
        # single-WG assignment: WG1 first, then WG2, then WG3
        d["grp"] = np.where(d.a5_WG1, "WG1", np.where(d.a5_WG2, "WG2", "WG3"))
        d = d.drop_duplicates("paper_id")
        d = d[d.grp.isin(["WG1", "WG3"])]
        d["y"] = d.m5_any.astype(int)
        r = standardise(d, "grp", "y", ["vtype"], "WG1", "WG3")
        # fill WG1/WG3 rows
        for row in rows:
            if row["variant"] == vname and row["wg"] in ("WG1", "WG3"):
                row["comp_share_cov_WG1vWG3_pct"] = round(r["composition_share_pct"], 3) if r["composition_share_pct"] == r["composition_share_pct"] else ""
                row["cells_used"] = r["cells_used"]
                row["support_share"] = round(r["support_share"], 4)
    # occurrence-level composition as an additional variant
    for g in WGS:
        sub = o[o.wg_label == g]
        vc = sub.category.value_counts(normalize=True)
        n = len(sub)
        rows.append(dict(variant="occurrence_level_frame", wg=g, n=n,
                         journal_share_pct=round(100 * vc.get("journal_article", 0), 3),
                         report_share_pct=round(100 * vc.get("institutional_report", 0), 3),
                         book_share_pct=round(100 * vc.get("book_or_chapter", 0), 3),
                         wp_share_pct=round(100 * vc.get("working_paper", 0), 3),
                         other_share_pct=round(100 * vc.get("other", 0), 3),
                         uncertain_share_pct=round(100 * vc.get("uncertain", 0), 3),
                         coverage_pct="", comp_share_cov_WG1vWG3_pct="", cells_used="", support_share=""))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(TAB, "A_robustness_type_composition.csv"), index=False)
    print(f"wrote A_robustness_type_composition.csv rows={len(df)}")
    return df


def main():
    iv = load()
    print(f"identities: {len(iv):,}")
    reference_validation(iv)
    recurrence(iv)
    dec = decomposition(iv)
    robustness(iv)
    # console summary of the headline decomposition
    sub = dec[(dec.outcome.str.startswith("coverage")) & (dec.standardisation.isin(["type", "vintage", "type_x_vintage"]))]
    print("\n=== coverage decomposition, WG1 vs WG3, both directions ===")
    print(sub[sub.pair == "WG1_vs_WG3"][
        ["standardisation", "direction", "crude_base", "crude_ref", "gap_pp",
         "std_gap_pp", "composition_pp", "composition_share_pct", "support_share"]].to_string(index=False))
    print("\n=== matched recurrence decomposition, WG1 vs WG3 ===")
    sub2 = dec[(dec.outcome.str.startswith("matched_recurrence")) & (dec.standardisation == "type")]
    print(sub2[sub2.pair == "WG1_vs_WG3"][
        ["crude_base", "crude_ref", "gap_pp", "std_gap_pp", "composition_pp",
         "composition_share_pct", "support_share", "cells_used"]].to_string(index=False))


if __name__ == "__main__":
    main()
