"""C_identity_error_sensitivity.py -- Task C (re-scoped by Lead, 27 Oct).

(a) Verify the three-frame comparison (Frame W = Web of Science record; Frame D = any
    verified metadata (WoS or resolved DOI); Frame A = all extracted identities) and
    the Wald intervals on the WGIII-minus-WGII difference.
(b) Identity-rule sensitivity of the FRAME-DEPENDENT LEVELS: rebuild the WG levels and
    the W->A swing under alternative identity rules reconstructible from the data
    (exact DOI key; title+year key; exact raw-string key; literal vs enriched DOI;
    excluding fuzzy matched_title matches).
(c) HYPOTHETICAL break-even error budget for the WGIII>WGII ordering: the minimum
    number of identity mis-merges / mis-splits, in a given group and direction, that
    would flip the ordering. This is a break-even arithmetic exercise, NOT an estimate
    of the true error rate; the observed fuzzy/flag counts are reported only to give a
    plausibility scale.

Unit: occurrence for reconstruction; assigned identity (papers.paper_id) for the
shipped frames. Deterministic. No source data modified.
Outputs: CJE_revision/tables/C_frame_verification.csv, C_sensitivity.csv, C_break_even.csv
"""
import re
import sqlite3
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import BCD_lib as L

DB = L.DB
ROWS = []
BE = []


def add(**kw):
    ROWS.append(kw)


def wald_diff(k1, n1, k2, n2, z=1.96):
    p1, p2 = k1 / n1, k2 / n2
    se = np.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    d = 100 * (p1 - p2)
    return d, d - z * 100 * se, d + z * 100 * se, 100 * se


def load_occ():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    q = """
    SELECT r.occurrence_id, c.report_id, w.wg_label, r.paper_id, r.match_status,
           r.doi_norm, r.doi_source, r.parsed_title, r.parsed_year, r.raw_reference,
           p.verification_status, p.metadata_source, p.year_best, p.journal_best
    FROM references_occurrence r
    JOIN chapters c ON c.chapter_id = r.chapter_id
    LEFT JOIN working_groups w ON w.wg_id = c.wg_id
    LEFT JOIN papers p ON p.paper_id = r.paper_id
    WHERE c.report_id IN ('AR5','AR6') AND w.wg_label IN ('WG1','WG2','WG3')
    """
    d = pd.read_sql(q, con)
    con.close()
    d["is_wos"] = d["match_status"].isin(L.MATCHED).astype(int)
    d["is_verified"] = d["verification_status"].isin(("wos_matched", "doi_verified")).astype(int)
    d["year"] = pd.to_numeric(d["parsed_year"], errors="coerce")
    return d


def shipped_frames(d):
    """Reproduce Frame W / D / A exactly as the Lead's 05_frame_alternatives.py."""
    out = {}
    for frame in ("W", "D", "A"):
        out[frame] = {}
        for rep in ("AR5", "AR6"):
            for g in L.GROUPS:
                s = d[(d.report_id == rep) & (d.wg_label == g)]
                if frame == "W":
                    ids = set(s.loc[s.is_wos == 1, "paper_id"])
                elif frame == "D":
                    ids = set(s.loc[s.is_verified == 1, "paper_id"])
                else:
                    ids = set(s.paper_id)
                out[frame][(rep, g)] = ids
    rows = []
    for frame in ("W", "D", "A"):
        for g in L.GROUPS:
            a5 = out[frame][("AR5", g)]; a6 = out[frame][("AR6", g)]
            sh = a5 & a6
            n, k = len(a5), len(sh)
            p, lo, hi = L.wilson(k, n)
            rows.append(dict(frame=frame, group=g, earlier_n=n, later_n=len(a6), shared=k,
                             retention_pct=p, wilson_lo=lo, wilson_hi=hi))
        # report level: union over WGs
        a5 = set().union(*[out[frame][("AR5", g)] for g in L.GROUPS])
        a6 = set().union(*[out[frame][("AR6", g)] for g in L.GROUPS])
        sh = a5 & a6
        p, lo, hi = L.wilson(len(sh), len(a5))
        rows.append(dict(frame=frame, group="report", earlier_n=len(a5), later_n=len(a6),
                         shared=len(sh), retention_pct=p, wilson_lo=lo, wilson_hi=hi))
    return pd.DataFrame(rows), out


def normalise_title(s):
    s = str(s or "").lower()
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalise_raw(s):
    s = str(s or "").lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def rule_levels(d, keycol, frame, min_len=0, year_filter=None):
    """Retention per WG under an identity key and a frame filter.

    earlier cohort = distinct keys present in AR5 occurrences in the WG
    recurrence     = same key present in AR6 occurrences in the WG
    Keys missing/too short are dropped from numerator AND denominator (documented).
    """
    dd = d.copy()
    if frame == "W":
        dd = dd[dd.is_wos == 1]
    dd = dd[dd[keycol].notna() & (dd[keycol].astype(str).str.len() > 0)]
    if min_len:
        dd = dd[dd[keycol].astype(str).str.len() >= min_len]
    if year_filter is not None:
        dd = dd[dd[keycol + "_year"].notna() & (dd[keycol + "_year"] >= 1900) & (dd[keycol + "_year"] <= 2026)]
    rows = []
    for g in L.GROUPS:
        s = dd[dd.wg_label == g]
        a5 = set(s.loc[s.report_id == "AR5", keycol]); a6 = set(s.loc[s.report_id == "AR6", keycol])
        sh = a5 & a6
        n, k = len(a5), len(sh)
        p, lo, hi = L.wilson(k, n)
        rows.append(dict(frame=frame, group=g, earlier_n=n, later_n=len(a6), shared=k,
                         retention_pct=p, wilson_lo=lo, wilson_hi=hi))
    return pd.DataFrame(rows)


def main():
    d = load_occ()
    print(f"AR5/AR6 WG1-3 occurrences: {len(d):,}")

    # ---------- (a) verify the shipped three-frame comparison ----------
    F, fr = shipped_frames(d)
    print("\n(a) frame verification (shipped identities)")
    print(F.pivot(index="group", columns="frame", values="retention_pct").round(3).to_string())
    for r in F.itertuples():
        add(task="C_frame_level", quantity="retention_pct", frame=r.frame, group=r.group,
            numerator=int(r.shared), denominator=int(r.earlier_n), estimate=float(r.retention_pct),
            ci_lo=float(r.wilson_lo), ci_hi=float(r.wilson_hi),
            note="Frame W = has a WoS record; Frame D = any verified metadata (WoS or resolved DOI); Frame A = all extracted identities. Reference interval conditional on this fixed corpus.")
    for frame in ("W", "D", "A"):
        s = F[(F.frame == frame) & (F.group != "report")].set_index("group")
        k2, n2 = int(s.loc["WG2", "shared"]), int(s.loc["WG2", "earlier_n"])
        k3, n3 = int(s.loc["WG3", "shared"]), int(s.loc["WG3", "earlier_n"])
        dm, lo, hi, se = wald_diff(k3, n3, k2, n2)
        add(task="C_ordering", quantity="WGIII_minus_WGII_pp", frame=frame, group="WG3_vs_WG2",
            numerator=k3, denominator=n3, estimate=float(dm), ci_lo=float(lo), ci_hi=float(hi),
            note=f"Wald 95% reference interval; WGII {k2}/{n2}, WGIII {k3}/{n3}. Ordering is not identified if interval includes 0.")
        print(f"  frame {frame}: WGIII-WGII = {dm:+.3f} pp [{lo:+.3f}, {hi:+.3f}]  "
              f"(WGII {100*k2/n2:.3f}, WGIII {100*k3/n3:.3f})")
    F.to_csv(L.OUT / "C_frame_verification.csv", index=False)
    print("wrote tables/C_frame_verification.csv")

    # ---------- (b) identity-rule sensitivity of the frame-dependent levels ----------
    d["doi_key"] = d["doi_norm"].fillna("").str.lower().str.strip()
    d["doi_key"] = d["doi_key"].where(d["doi_key"].str.len() > 0)
    d["doi_lit_key"] = np.where(d["doi_source"].isin(("embedded", "embedded_normalized")),
                                d["doi_key"], np.nan)
    d["title_key"] = [normalise_title(x) for x in d["parsed_title"]]
    d["title_key"] = d["title_key"].where(d["title_key"].str.len() >= 15)
    d["title_key"] = np.where(d["title_key"].notna(),
                              d["title_key"].astype(str) + "|" + d["year"].astype("Int64").astype(str), np.nan)
    d["raw_key"] = [normalise_raw(x) for x in d["raw_reference"]]
    d["raw_key"] = d["raw_key"].where(d["raw_key"].str.len() >= 40)
    d["doi_key_year"] = d["year"]; d["doi_lit_key_year"] = d["year"]
    d["title_key_year"] = d["year"]; d["raw_key_year"] = d["year"]

    rules = [("exact_DOI_all", "doi_key", None),
             ("exact_DOI_literal_only", "doi_lit_key", None),
             ("title_plus_year", "title_key", None),
             ("exact_raw_string", "raw_key", None)]
    levels = []
    for name, key, yf in rules:
        for frame in ("W", "A"):
            R = rule_levels(d, key, frame, year_filter=yf)
            levels.append(R.assign(rule=name))
    # Degenerate rules are recorded, not used for inference: the literal-DOI rule
    # leaves only 7 WGIII earlier identities in the W frame, and the exact-raw-string
    # rule cannot detect recurrence at all (WG1/WG3 = 0 shared).
    DEGENERATE = {"exact_DOI_literal_only": "not estimable for WGIII (earlier_n=7 in frame W; literal embedded-DOI coverage is WG1-specific)",
                  "exact_raw_string": "degenerate: exact raw-string identity detects essentially no cross-report recurrence (WG1 0/6390, WG3 0/2266 in frame W)"}
    LV = pd.concat(levels, ignore_index=True)
    LV.to_csv(L.OUT / "C_identity_rule_levels.csv", index=False)
    print("\n(b) identity-rule levels (matched-retention style, same-WG)")
    print(LV.pivot_table(index=["rule", "frame"], columns="group", values="retention_pct").round(3).to_string())
    for r in LV.itertuples():
        add(task="C_identity_rule_level", quantity="retention_pct", frame=r.frame, group=r.group,
            numerator=int(r.shared), denominator=int(r.earlier_n), estimate=float(r.retention_pct),
            ci_lo=float(r.wilson_lo), ci_hi=float(r.wilson_hi),
            note=f"Reconstructed identity rule = {r.rule}; frame {r.frame}; keys missing/too short dropped from numerator and denominator. Shipped-identity baseline: see C_frame_level.")
    for rule in LV.rule.unique():
        s = LV[(LV.rule == rule)].pivot(index="group", columns="frame", values="retention_pct")
        swing = s["A"] - s["W"]
        for g in L.GROUPS:
            note = f"Rule {rule}: Frame A level minus Frame W level. Negative = matched frame raises the measured level."
            if rule in DEGENERATE:
                note += " DEGENERATE / NOT ESTIMABLE - do not use for inference."
            add(task="C_identity_rule_swing", quantity="W_to_A_level_swing_pp", frame="W_to_A",
                group=g, numerator=None, denominator=None, estimate=float(swing[g]),
                ci_lo=np.nan, ci_hi=np.nan, note=note)
        # ordering under each rule/frame
        for frame in ("W", "A"):
            R = LV[(LV.rule == rule) & (LV.frame == frame)].set_index("group")
            dm, lo, hi, se = wald_diff(int(R.loc["WG3", "shared"]), int(R.loc["WG3", "earlier_n"]),
                                       int(R.loc["WG2", "shared"]), int(R.loc["WG2", "earlier_n"]))
            note = f"Reconstructed identity rule = {rule}."
            if rule in DEGENERATE:
                note += " DEGENERATE / NOT ESTIMABLE - recorded for completeness only; do not use for inference."
            add(task="C_identity_rule_ordering", quantity="WGIII_minus_WGII_pp", frame=frame,
                group="WG3_vs_WG2", numerator=int(R.loc["WG3", "shared"]),
                denominator=int(R.loc["WG3", "earlier_n"]), estimate=float(dm),
                ci_lo=float(lo), ci_hi=float(hi), note=note)
    # WoS frame restricted to exact DOI matches (drop fuzzy matched_title)
    d["is_wos_exact"] = (d.match_status == "matched_doi").astype(int)
    dd = d[d.is_wos_exact == 1]
    rows = []
    for g in L.GROUPS:
        s = dd[dd.wg_label == g]
        a5 = set(s.loc[s.report_id == "AR5", "paper_id"]); a6 = set(s.loc[s.report_id == "AR6", "paper_id"])
        sh = a5 & a6
        p, lo, hi = L.wilson(len(sh), len(a5))
        rows.append(dict(group=g, earlier_n=len(a5), shared=len(sh), retention_pct=p))
        add(task="C_matched_title_exclusion", quantity="retention_pct_exact_DOI_matches_only",
            frame="W_exactDOI", group=g, numerator=len(sh), denominator=len(a5),
            estimate=float(p), ci_lo=float(lo), ci_hi=float(hi),
            note="Frame W retaining only matched_doi occurrences (drops fuzzy matched_title matches).")
    print("\n  Frame W with exact-DOI matches only (drops matched_title):")
    print(pd.DataFrame(rows).round(3).to_string(index=False))

    # ---------- (c) HYPOTHETICAL break-even error budget for the ordering ----------
    W = F[F.frame == "W"].set_index("group")
    k2, n2 = int(W.loc["WG2", "shared"]), int(W.loc["WG2", "earlier_n"])
    k3, n3 = int(W.loc["WG3", "shared"]), int(W.loc["WG3", "earlier_n"])
    p2, p3 = k2 / n2, k3 / n3
    scenarios = [
        ("S1_false_recurrences_WG3", "Mergebased false shared identities in WGIII numerator (mis-merge of distinct later works into an earlier identity); subtract k from WGIII numerator.",
         int(np.ceil(k3 - n3 * p2)), "WG3 numerator -k"),
        ("S2_missed_recurrences_WG2", "Genuine WGII recurrences hidden by mis-splits; add k to WGII numerator.",
         int(np.ceil(p3 * n2 - k2)), "WG2 numerator +k"),
        ("S3_duplicate_earlier_WG3", "Mis-split non-recurring earlier identities added to WGIII denominator; add k to WGIII denominator.",
         int(np.ceil(k3 / p2 - n3)), "WG3 denominator +k"),
        ("S4_duplicate_earlier_WG2", "Merged duplicate earlier identities removed from WGII denominator; subtract k from WGII denominator.",
         int(np.ceil(n2 - k2 / p3)), "WG2 denominator -k"),
        ("S5_symmetric_false_recurrences", "Same number k of false shared identities in BOTH groups; subtract k from each numerator.",
         int(np.ceil((k3 * n2 - k2 * n3) / (n2 - n3))), "both numerators -k"),
    ]
    print("\n(c) HYPOTHETICAL break-even error budget for WGIII>WGII (frame W)")
    for name, desc, k, direction in scenarios:
        BE.append(dict(scenario=name, direction=direction, k_required=k,
                       share_of_shared_numerator_pct=100 * k / k3 if "WG3" in direction or "both" in direction else 100 * k / k2,
                       share_of_earlier_denominator_pct=100 * k / n3 if "WG3" in direction or "both" in direction else 100 * k / n2,
                       description=desc))
        add(task="C_break_even_hypothetical", quantity="k_identities_to_flip_ordering", frame="W",
            group=direction, numerator=None, denominator=None, estimate=float(k),
            ci_lo=np.nan, ci_hi=np.nan,
            note=f"{name}: {desc} This is a BREAK-EVEN arithmetic quantity, not an estimate of the true identity-error rate.")
        print(f"  {name}: k = {k}  ({direction})")
    BEd = pd.DataFrame(BE)
    BEd["baseline_WG2"] = f"{k2}/{n2} = {100*p2:.4f}%"
    BEd["baseline_WG3"] = f"{k3}/{n3} = {100*p3:.4f}%"
    BEd["baseline_gap_pp"] = 100 * (p3 - p2)
    BEd.to_csv(L.OUT / "C_break_even.csv", index=False)
    print("wrote tables/C_break_even.csv")

    # ---------- observed fuzzy-title reliance in the Frame-W ordering ----------
    # matched_title occurrences are fuzzy title matches; an identity whose Frame-W
    # membership in a cycle rests ONLY on matched_title would leave the frame if that
    # fuzzy match were an error. This is an OBSERVED sensitivity, not the hypothetical
    # error budget.
    print("\nobserved fuzzy-title reliance, Frame W ordering")
    frame_ids = {}
    for rep in ("AR5", "AR6"):
        for g in L.GROUPS:
            srep = d[(d.report_id == rep) & (d.wg_label == g)]
            arr = srep.groupby("paper_id").agg(n_wos=("is_wos", "sum"),
                                               n_doi=("match_status", lambda x: (x == "matched_doi").sum()),
                                               n_title=("match_status", lambda x: (x == "matched_title").sum()))
            frame_ids[(rep, g)] = arr
    fuzzy_stats = []
    for g in ("WG2", "WG3"):
        A5 = frame_ids[("AR5", g)]; A6 = frame_ids[("AR6", g)]
        a5 = set(A5.index[A5.n_wos > 0]); a6 = set(A6.index[A6.n_wos > 0])
        shared_ids = a5 & a6
        f5 = {i for i in shared_ids if A5.loc[i, "n_title"] > 0 and A5.loc[i, "n_doi"] == 0}
        f6 = {i for i in shared_ids if A6.loc[i, "n_title"] > 0 and A6.loc[i, "n_doi"] == 0}
        fany = {i for i in shared_ids if A5.loc[i, "n_title"] > 0 or A6.loc[i, "n_title"] > 0}
        fuzzy_stats.append(dict(group=g, earlier_n=len(a5), shared=len(shared_ids),
                                shared_fuzzy_anywhere=len(fany),
                                shared_fuzzy_only_AR5=len(f5), shared_fuzzy_only_AR6=len(f6),
                                shared_fuzzy_only_either=len(f5 | f6)))
        for lab, k in (("shared_fuzzy_anywhere", len(fany)), ("shared_fuzzy_only_either", len(f5 | f6))):
            rate = 100 * (len(shared_ids) - k) / len(a5)
            add(task="C_fuzzy_reliance", quantity="WGIII_retention_pct_after_dropping_" + lab,
                frame="W_numerator_restricted", group=g, numerator=len(shared_ids) - k,
                denominator=len(a5), estimate=rate, ci_lo=np.nan, ci_hi=np.nan,
                note="Observed sensitivity: drop from the shared numerator those identities whose recurrence relies on a fuzzy matched_title occurrence in at least one cycle (fuzzy_only_either) or any cycle (fuzzy_anywhere); earlier denominator unchanged. If fuzzy matches are errors this is the resulting rate; it is NOT an estimate that they are.")
    FS = pd.DataFrame(fuzzy_stats)
    print(FS.to_string(index=False))
    FS.to_csv(L.OUT / "C_fuzzy_reliance.csv", index=False)
    fw2 = FS[FS.group == "WG2"].iloc[0]; fw3 = FS[FS.group == "WG3"].iloc[0]
    for lab in ("shared_fuzzy_anywhere", "shared_fuzzy_only_either"):
        k2 = int(fw2[lab]); k3 = int(fw3[lab])
        r2 = 100 * (fw2.shared - k2) / fw2.earlier_n
        r3 = 100 * (fw3.shared - k3) / fw3.earlier_n
        add(task="C_fuzzy_reliance", quantity="WGIII_minus_WGII_pp_after_dropping_" + lab,
            frame="W_numerator_restricted", group="WG3_vs_WG2",
            numerator=int(fw3.shared - k3), denominator=int(fw3.earlier_n),
            estimate=float(r3 - r2), ci_lo=np.nan, ci_hi=np.nan,
            note=f"Dropping fuzzy-title-reliant shared identities: WGII {fw2.shared-k2}/{fw2.earlier_n}={r2:.4f}%, WGIII {fw3.shared-k3}/{fw3.earlier_n}={r3:.4f}%.")
        print(f"  drop {lab}: WGII {fw2.shared-k2}/{fw2.earlier_n}={r2:.4f}%  WGIII {fw3.shared-k3}/{fw3.earlier_n}={r3:.4f}%  gap {r3-r2:+.4f} pp")

    # ---------- plausibility scale (NOT an error-rate estimate) ----------
    ident = L.identity_frame()
    core = L.core_frame()
    scale_rows = []
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    flags = pd.read_sql("SELECT entity_id, flag_type FROM data_quality_flags WHERE entity_type='occurrence'", con)
    mr = pd.read_sql("SELECT occurrence_id, status FROM manual_review", con)
    con.close()
    for g in ("WG2", "WG3"):
        a5 = set(core[(core.report_id == "AR5") & (core.wg_label == g)].paper_id)
        a6 = set(core[(core.report_id == "AR6") & (core.wg_label == g)].paper_id)
        shared_ids = a5 & a6
        doi = d[(d.wg_label == g) & (d.report_id.isin(["AR5", "AR6"]))]
        cohort_ident = a5 | a6
        cohort_occ = doi[doi.paper_id.isin(cohort_ident)]
        fuzzy_ids = set(doi.loc[doi.match_status == "matched_title", "paper_id"]) & cohort_ident
        occ_ids = set(cohort_occ.occurrence_id)
        fl = flags[flags.entity_id.isin(occ_ids)]
        mro = mr[mr.occurrence_id.isin(occ_ids)]
        scale_rows.append(dict(group=g, earlier_n=len(a5), shared=len(shared_ids),
                               matched_title_identities_in_frame=len(fuzzy_ids),
                               possible_duplicate_title_flags=int((fl.flag_type == "possible_duplicate_title").sum()),
                               chronology_review_flags=int((fl.flag_type == "matched_doi_chronology_review").sum()),
                               invalid_doi_flags=int((fl.flag_type == "invalid_doi").sum()),
                               manual_review_items=len(mro),
                               manual_review_resolved=int((mro.status == "resolved").sum())))
    SC = pd.DataFrame(scale_rows)
    SC.to_csv(L.OUT / "C_error_plausibility_scale.csv", index=False)
    print("\nplausibility scale (observed indicator counts; NOT error-rate estimates):")
    print(SC.to_string(index=False))
    for r in SC.itertuples():
        for col in ("matched_title_identities_in_frame", "possible_duplicate_title_flags",
                    "chronology_review_flags", "invalid_doi_flags", "manual_review_items"):
            add(task="C_error_plausibility_scale", quantity=col, frame="W", group=r.group,
                numerator=int(getattr(r, col)), denominator=int(r.earlier_n + 0),
                estimate=float(getattr(r, col)), ci_lo=np.nan, ci_hi=np.nan,
                note="Observed indicator count over the group's AR5/AR6 frame identities; plaubsibility scale against the break-even k. NOT an error-rate estimate.")

    out = pd.DataFrame(ROWS)
    out = out[["task", "quantity", "frame", "group", "numerator", "denominator",
               "estimate", "ci_lo", "ci_hi", "note"]]
    p = L.OUT / "C_sensitivity.csv"
    out.to_csv(p, index=False)
    print(f"\nwrote {len(out)} rows -> {p}")


if __name__ == "__main__":
    main()
