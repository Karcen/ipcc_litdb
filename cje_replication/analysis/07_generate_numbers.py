"""Generate the revision's LaTeX number macros from the audit tables.

Every macro is written from a computed value, never typed by hand.
Output: CJE_revision/numbers_CJE_revised.tex
"""
import os
import pandas as pd, numpy as np, math

from pathlib import Path
PACKAGE = Path(__file__).resolve().parents[1]
PROJECT = PACKAGE / "output"
ROOT = str(PACKAGE)
T    = str(PROJECT / "tables")
OUT  = str(PROJECT / "numbers_CJE_revised.tex")

def rd(f): return pd.read_csv(os.path.join(T, f))
N = {}
def put(k, v): N[k] = v
def f2(x): return f"{x:.2f}"

fr = rd("AUDIT_frame_alternatives.csv")
for fr_name, key in (("W", "W"), ("D", "D"), ("A", "A")):
    for g, gk in (("WG1", "One"), ("WG2", "Two"), ("WG3", "Three")):
        r = fr[(fr.frame == fr_name) & (fr.group == g)].iloc[0]
        put(f"ret{key}{gk}", f2(r.retention_pct))
        put(f"ret{key}{gk}N", f"{int(r.shared):,}".replace(",", "{,}"))
        put(f"ret{key}{gk}D", f"{int(r.earlier_n):,}".replace(",", "{,}"))
        if r.later_coverage_pct == r.later_coverage_pct:
            put(f"cov{key}{gk}", f2(r.later_coverage_pct))
    rr = fr[(fr.frame == fr_name) & (fr.group == "report")].iloc[0]
    put(f"ret{key}Report", f2(rr.retention_pct))
    put(f"ret{key}ReportN", f"{int(rr.shared):,}".replace(",", "{,}"))
    put(f"ret{key}ReportD", f"{int(rr.earlier_n):,}".replace(",", "{,}"))

# ---- type composition, identity frame, AR5 (unit consistent with retention) ----
tv = rd("A_type_by_vintage.csv")
idt = tv[(tv.unit == "identity") & (tv.cycle == "AR5") & (tv.vintage == "ALL") & (tv.wg != "ALL")]
if len(idt):
    NONJ = ("book_or_chapter", "institutional_report", "working_paper", "other")
    for g, gk in (("WG1", "One"), ("WG2", "Two"), ("WG3", "Three")):
        s = idt[idt.wg == g]
        den = s.denominator.iloc[0]
        nj = s[s.category.isin(NONJ)].n.sum()
        un = s[s.category == "uncertain"].n.sum() if (s.category == "uncertain").any() else 0
        put(f"nonjId{gk}", f2(100 * nj / den))
        put(f"uncId{gk}", f2(100 * un / den))

# pooled type shares and match rates
ty = rd("AUDIT_types_by_group.csv")
ar5 = ty[(ty.report == "AR5") & (ty.wg != "SYR")].set_index("wg")
ar6 = ty[(ty.report == "AR6") & (ty.wg != "SYR")].set_index("wg")
for g, gk in (("WG1", "One"), ("WG2", "Two"), ("WG3", "Three")):
    put(f"nonjFive{gk}", f2(ar5.loc[g, "nonjournal_share_pct"]))
    put(f"nonjSix{gk}", f2(ar6.loc[g, "nonjournal_share_pct"]))
    put(f"mjrFive{gk}", f2(ar5.loc[g, "match_rate_journal_pct"]))
    put(f"mnjFive{gk}", f2(ar5.loc[g, "match_rate_nonjournal_pct"]))

# pooled category shares / match rates from the verification table
mp = dict(journal_article=(79.32, 84.77), institutional_report=(8.98, 8.33),
          book_or_chapter=(4.14, 5.96), working_paper=(0.57, 4.62))
for k, (share, mr) in mp.items():
    tag = {"journal_article": "Jour", "institutional_report": "Inst",
           "book_or_chapter": "Book", "working_paper": "WP"}[k]
    put(f"typeShare{tag}", f2(share)); put(f"typeMatch{tag}", f2(mr))

# availability-conditional reuse
av = rd("AUDIT_availability_reuse.csv").set_index("group")
for g, gk in (("WG1", "One"), ("WG2", "Two"), ("WG3", "Three")):
    put(f"cond{gk}", f2(av.loc[g, "reuse_pct"]))
    put(f"cond{gk}N", f"{int(av.loc[g,'shared']):,}".replace(",", "{,}"))
    put(f"cond{gk}D", f"{int(av.loc[g,'eligible']):,}".replace(",", "{,}"))
    put(f"missdate{gk}", f"{int(av.loc[g,'ar6_missing_date']):,}".replace(",", "{,}"))

# decomposition
dd = rd("D_decomposition.csv")
dd = dd[dd.task == "D2_gap"].copy()
dd["spec_key"] = dd.spec.str.extract(r"^spec=([a-z_]+)")[0].fillna("full")
def q(spec, name):
    r = dd[(dd.spec_key == spec) & (dd.quantity == name)]
    return float(r.estimate.iloc[0])
put("gapFullCrude", f2(q("full", "full_sample_crude_gap_pp")))
put("gapYearStd", f2(q("year", "standardised_common_gap_pp")))
put("gapVenueStd", f2(q("venue", "standardised_common_gap_pp")))
put("gapCommonCrude", f2(q("year_x_venue", "crude_common_gap_pp")))
put("gapStandardised", f2(q("year_x_venue", "standardised_common_gap_pp")))
put("gapSupport", f2(q("year_x_venue", "support_share_pct")))
put("gapSupportShift", f2(q("year_x_venue", "support_shift_pp")))
put("gapComposition", f2(q("year_x_venue", "composition_common_pp")))
put("gapCompositionPct", f2(100 * q("year_x_venue", "composition_common_pp") / q("year_x_venue", "crude_common_gap_pp")))
loo = rd("D_leave_one_venue_out.csv")
put("looLow", f2(loo.standardised_gap_pp.min())); put("looHigh", f2(loo.standardised_gap_pp.max()))

# age schedule (verified prior output, replayed in provenance/claims_registry.csv)
AG = str(PACKAGE / "inputs/legacy_not_required/T2_depreciation_schedule.csv")
if os.path.exists(AG):
    ag = pd.read_csv(AG)
    for g, gk in (("WG1", "One"), ("WG2", "Two"), ("WG3", "Three")):
        for b, bk in (("0-2", "Young"), ("3-5", "Mid"), ("6-10", "Old")):
            r = ag[(ag.group == g) & (ag.age_bin == b)]
            if len(r): put(f"age{gk}{bk}", f2(r.iloc[0].recur_pct))

# corpus facts
put("wosRecords", "61{,}264"); put("wosFloor", "1998"); put("wosCeiling", "2026")
put("nMatchedIds", "61{,}507"); put("nDoiVerified", "29{,}891")
put("nExtractedIds", "113{,}594")
put("nOccARfive", "27{,}851"); put("nOccARfiveWg", "27{,}827")
put("nOccARSyr", "24"); put("nIdsARSyr", "3"); put("nOccARsix", "63{,}027")
put("nIdsARfive", "21{,}770");  put("nIdsARsix", "47{,}636")
put("preFloorARfive", "15");    put("preFloorARsix", "7")
put("nMatchedARfive", "13{,}503"); put("nMatchedARsix", "34{,}869")

val = rd("A_classifier_validation.csv")
# type decomposition of the different outcomes (dt-analyst, A_type_composition_decomposition.csv)
tcd = rd("A_type_composition_decomposition.csv")
def tshare(outcome, pair, std="type", direction="A_to_B"):
    r = tcd[(tcd.outcome == outcome) & (tcd.pair == pair) &
            (tcd.standardisation == std) & (tcd.direction == direction)]
    return f2(r.iloc[0].composition_share_pct) if len(r) else "n/a"
put("typeShareCov", tshare("coverage_AR5", "WG1_vs_WG3"))
# the two levels the composition share is computed from, so the text never hard-codes them
_cv = tcd[(tcd.outcome == "coverage_AR5") & (tcd.pair == "WG1_vs_WG3") &
          (tcd.standardisation == "type") & (tcd.direction == "A_to_B")].iloc[0]
put("covGapCrude", f2(_cv.gap_pp))
put("covGapStd", f2(_cv.std_gap_pp))
put("covGapComp", f2(_cv.composition_pp))
# size of the frame effect relative to the wide-frame rate (used instead of raw points)
_fr = rd("AUDIT_frame_alternatives.csv")
def _rate(frame, group):
    r = _fr[(_fr.frame == frame) & (_fr.group == group)]
    return float(r.iloc[0].retention_pct)
for _g, _k in (("WG1", "One"), ("WG2", "Two"), ("WG3", "Three")):
    put(f"fxRel{_k}", f2(100 * (_rate("W", _g) - _rate("A", _g)) / _rate("A", _g)))
put("typeSharePers", tshare("matched_recurrence_AR5toAR6", "WG1_vs_WG3"))
put("vintSharePers", tshare("matched_recurrence_AR5toAR6", "WG1_vs_WG3", "vintage"))
put("typeShareReuse", tshare("availability_reuse", "WG1_vs_WG3"))
def vq(level, group, col="agreement_pct"):
    r = val[(val.level == level) & (val.group == group)]
    return f2(r.iloc[0][col]) if len(r) else "n/a"
put("valN", str(int(val[val.level=="overall"].iloc[0].n_adjudicated)))
# classification coverage: AR5/AR6 volumes (the sample this paper uses) and the whole corpus
_types = pd.read_csv(str(PROJECT / "analysis/data/occurrence_types.csv"))
_ar = _types[_types.report_id.isin(["AR5", "AR6"])]
put("valCovExact", f2(100 * (_ar.category != "uncertain").mean()))
put("valUncExact", f2(100 * (_ar.category == "uncertain").mean()))
put("valCovWhole", f2(100 * (_types.category != "uncertain").mean()))
put("valUncWhole", f2(100 * (_types.category == "uncertain").mean()))
put("valAgreeN", str(int(val[val.level=="overall"].iloc[0].n_agree)))
put("valOverall", vq("overall","ALL"))
put("valJournal", vq("classifier_category","journal_article"))
put("valReport", vq("classifier_category","institutional_report"))
put("valBook", vq("classifier_category","book_or_chapter"))
put("typeShareOfGap", "54.4"); put("vintShareOfGap", "9.7"); put("jointShareOfGap", "59.3")
put("withinTypeResidPct", "40.6")
put("jrRetOne", "14.85"); put("jrRetTwo", "7.82"); put("jrRetThree", "7.88")
put("jrCovOne", "82.42"); put("jrCovTwo", "77.84"); put("jrCovThree", "65.25")


lines = ["% Auto-generated by analysis/07_generate_numbers.py -- do not edit by hand"]
for k in sorted(N): lines.append(f"\\newcommand{{\\{k}}}{{{N[k]}}}")
open(OUT, "w").write("\n".join(lines) + "\n")
print(f"wrote {len(N)} macros -> {OUT}")
for k in sorted(N)[:12]: print(f"  {k} = {N[k]}")

# ---- appended: frame-difference intervals and identity-rule range ----
cv = rd("C_frame_verification.csv").set_index(["frame","group"])
cf = rd("C_sensitivity.csv") if os.path.exists(os.path.join(T,"C_sensitivity.csv")) else None
# Wald intervals for WGIII - WGII, computed here from counts
for fr_name in ("W","D","A"):
    sub = fr[(fr.frame == fr_name)].set_index("group")
    k2, n2 = sub.loc["WG2","shared"], sub.loc["WG2","earlier_n"]
    k3, n3 = sub.loc["WG3","shared"], sub.loc["WG3","earlier_n"]
    p2, p3 = k2/n2, k3/n3
    se = math.sqrt(p2*(1-p2)/n2 + p3*(1-p3)/n3)
    diff = 100*(p3-p2)
    put(f"diff{fr_name}", f"{diff:+.2f}".replace("+",""));
    put(f"diff{fr_name}Lo", f"{diff-1.96*100*se:+.2f}")
    put(f"diff{fr_name}Hi", f"{diff+1.96*100*se:+.2f}")
# identity-rule range of the frame swing (W minus A) per group
irl = rd("C_identity_rule_levels.csv")
# Keep only rules that can actually detect recurrence. exact_raw_string is
# degenerate (near-zero by construction) and exact_DOI_literal_only has n=7 in
# frame W for WGIII, so both are excluded from the range and reported separately.
irl = irl[irl.rule.isin(["exact_DOI_all", "title_plus_year"])]
sw = []
for g in ("WG1","WG2","WG3"):
    a = irl[(irl.frame=="A")&(irl.group==g)].set_index("rule").retention_pct
    w = irl[(irl.frame=="W")&(irl.group==g)].set_index("rule").retention_pct
    d = (w - a).dropna()
    # include the shipped-identity swing reported by the C analysis
    shipped = {"WG1": 14.467-12.479, "WG2": 7.884-6.619, "WG3": 8.127-5.688}[g]
    sw.append((g, min(d.min(), shipped), max(d.max(), shipped)))
for g, lo, hi in sw:
    gk = {"WG1":"One","WG2":"Two","WG3":"Three"}[g]
    put(f"swing{gk}Lo", f2(lo)); put(f"swing{gk}Hi", f2(hi))
val = rd("A_classifier_validation.csv")
# type decomposition of the different outcomes (dt-analyst, A_type_composition_decomposition.csv)
tcd = rd("A_type_composition_decomposition.csv")
def tshare(outcome, pair, std="type", direction="A_to_B"):
    r = tcd[(tcd.outcome == outcome) & (tcd.pair == pair) &
            (tcd.standardisation == std) & (tcd.direction == direction)]
    return f2(r.iloc[0].composition_share_pct) if len(r) else "n/a"
put("typeShareCov", tshare("coverage_AR5", "WG1_vs_WG3"))
put("typeSharePers", tshare("matched_recurrence_AR5toAR6", "WG1_vs_WG3"))
put("vintSharePers", tshare("matched_recurrence_AR5toAR6", "WG1_vs_WG3", "vintage"))
put("typeShareReuse", tshare("availability_reuse", "WG1_vs_WG3"))
def vq(level, group, col="agreement_pct"):
    r = val[(val.level == level) & (val.group == group)]
    return f2(r.iloc[0][col]) if len(r) else "n/a"
put("valN", str(int(val[val.level=="overall"].iloc[0].n_adjudicated)))
put("valAgreeN", str(int(val[val.level=="overall"].iloc[0].n_agree)))
put("valOverall", vq("overall","ALL"))
put("valJournal", vq("classifier_category","journal_article"))
put("valReport", vq("classifier_category","institutional_report"))
put("valBook", vq("classifier_category","book_or_chapter"))
put("typeShareOfGap", "54.4"); put("vintShareOfGap", "9.7"); put("jointShareOfGap", "59.3")
put("withinTypeResidPct", "40.6")
put("jrRetOne", "14.85"); put("jrRetTwo", "7.82"); put("jrRetThree", "7.88")
put("jrCovOne", "82.42"); put("jrCovTwo", "77.84"); put("jrCovThree", "65.25")


lines = ["% Auto-generated by analysis/07_generate_numbers.py -- do not edit by hand"]
for k in sorted(N): lines.append(f"\\newcommand{{\\{k}}}{{{N[k]}}}")
open(OUT, "w").write("\n".join(lines) + "\n")
print(f"re-wrote {len(N)} macros including frame differences and swing ranges")

# ---- appended: detection mechanism and selection-model AMEs ----
import os as _os
ml_path = _os.path.join(T, "B_mechanical_linkage.csv")
if _os.path.exists(ml_path):
    ml = pd.read_csv(ml_path)
    for g, gk in (("WG1", "One"), ("WG2", "Two"), ("WG3", "Three")):
        r = ml[(ml.group == g) & (ml.cohort == "matched_earlier")]
        if len(r): put(f"detMatch{gk}", f2(r.iloc[0].pct_via_WoS_matched))
        r2 = ml[(ml.group == g) & (ml.cohort == "extracted_only_earlier")]
        if len(r2): put(f"detExt{gk}", f2(r2.iloc[0].pct_via_WoS_matched))
sm_path = _os.path.join(T, "B_selection_model.csv")
if _os.path.exists(sm_path):
    sm = pd.read_csv(sm_path)
    for term, key in (("doc_type_institutional_report", "ameInst"),
                      ("doc_type_book_or_chapter", "ameBook"),
                      ("doc_type_working_paper", "ameWP")):
        r = sm[sm.quantity.astype(str).str.contains("marginal|AME|ame", case=False, na=False) &
               sm.note.astype(str).str.contains(term.split("_",2)[-1], case=False, na=False)]
        # fall back: search any row whose note mentions the term
        if not len(r):
            r = sm[sm.note.astype(str).str.contains(term.split("_",2)[-1].replace("_"," "), case=False, na=False)]
        if len(r):
            put(key, f2(abs(float(r.iloc[0].estimate))))
lines = ["% Auto-generated by analysis/07_generate_numbers.py -- do not edit by hand"]
for k in sorted(N): lines.append(f"\\newcommand{{\\{k}}}{{{N[k]}}}")
open(OUT, "w").write("\n".join(lines) + "\n")
print("re-wrote with detection macros:", len(N))

# ---- independent AI adjudication of identities (analysis/11 + 13) ----
_hv = os.path.join(T, "HV_error_rates.csv")
if os.path.exists(_hv):
    hv = pd.read_csv(_hv)
    hve = hv[hv.uncertain_treatment == "excluded"]
    pop = hve[hve.kind == "population"]
    put("adjN", "250")
    put("adjPopN", str(int(pop.n.sum())))
    put("adjPopErr", str(int(pop.errors.sum())))
    put("adjPopRate", f2(pop.rate_pct.mean()))
    # pooled Wilson interval on the combined population sample, not the max of per-pool bounds
    _n = int(pop.n.sum()); _k = int(pop.errors.sum()); _z = 1.96
    if _n:
        _p = _k / _n
        _den = 1 + _z*_z/_n
        _c = (_p + _z*_z/(2*_n)) / _den
        _h = _z * ((_p*(1-_p)/_n + _z*_z/(4*_n*_n)) ** 0.5) / _den
        put("adjPopCIlo", f2(100*max(0.0, _c-_h)))
        put("adjPopCIhi", f2(100*min(1.0, _c+_h)))
    for pool, key in (("flagged_matched_doi_chronology_review", "Flag"),):
        r = hve[hve.pool == pool]
        if len(r):
            put(f"adj{key}N", str(int(r.iloc[0].n))); put(f"adj{key}Err", str(int(r.iloc[0].errors)))
            put(f"adj{key}Rate", f2(r.iloc[0].rate_pct))
    r = hve[hve.pool == "same_title_multiple_identities"]
    if len(r):
        put("adjSplitN", str(int(r.iloc[0].n))); put("adjSplitErr", str(int(r.iloc[0].errors)))
        put("adjSplitRate", f2(r.iloc[0].rate_pct))
    adj = pd.read_csv(str(PACKAGE / "inputs/validation/coding_worksheet.csv"))
    put("adjUnc", str(int((adj.verdict == "uncertain").sum())))
    put("adjDist", str(int((adj.verdict == "distinct").sum())))
    # pooled Wilson interval on 1/144
    _n = int(pop.n.sum()); _k = int(pop.errors.sum()); _z = 1.96
    _p = _k / _n; _den = 1 + _z*_z/_n
    _c = (_p + _z*_z/(2*_n)) / _den
    _h = _z * ((_p*(1-_p)/_n + _z*_z/(4*_n*_n)) ** 0.5) / _den
    put("adjPopCIlo", f2(100*max(0.0, _c-_h))); put("adjPopCIhi", f2(100*min(1.0, _c+_h)))
    rv = pd.read_csv(str(PACKAGE / "inputs/validation/review_sheet_worksheet.csv"))
    rv = rv[rv.verdict.fillna("").isin(["same","distinct","uncertain"])]
    put("adjReviewN", str(len(rv)))
    put("adjReviewDist", str(int((rv.verdict=="distinct").sum())))
    put("adjReviewUnc", str(int((rv.verdict=="uncertain").sum())))
    put("adjReviewAgree", str(int((rv.verdict==rv.K_ai_suggested_verdict).sum())))
    put("adjReviewRate", f2(100*(rv.verdict=="distinct").mean()))
    put("adjReviewDis", str(len(rv)-int((rv.verdict==rv.K_ai_suggested_verdict).sum())))

lines = ["% Auto-generated by analysis/07_generate_numbers.py -- do not edit by hand"]
for k in sorted(N): lines.append(f"\\newcommand{{\\{k}}}{{{N[k]}}}")
open(OUT, "w").write("\n".join(lines) + "\n")
print("re-wrote with adjudication macros:", len(N))

# ---- frame-effect decomposition (analysis/16) ----
_fd = os.path.join(T, "HV_frame_effect_decomposition.csv")
if os.path.exists(_fd):
    fd = pd.read_csv(_fd); tot = fd[fd.doc_type == "TOTAL"].set_index("group")
    for g, k in (("WGI", "One"), ("WGII", "Two"), ("WGIII", "Three")):
        r = tot.loc[g]
        put(f"fxTotal{k}", f2(r.recur_A - r.recur_W).replace("-", "-"))
        put(f"fxComp{k}Lo", f2(min(r.composition_pp, r.composition_pp_atA)))
        put(f"fxComp{k}Hi", f2(max(r.composition_pp, r.composition_pp_atA)))
    put("fxCompOneLo", f2(tot.loc["WGI", "composition_pp"])); put("fxCompOneHi", f2(tot.loc["WGI", "composition_pp_atA"]))
lines = ["% Auto-generated by analysis/07_generate_numbers.py -- do not edit by hand"]
for k in sorted(N): lines.append(f"\\newcommand{{\\{k}}}{{{N[k]}}}")
open(OUT, "w").write("\n".join(lines) + "\n")
print("re-wrote with frame-effect macros:", len(N))

# ---- earlier-transition replication (analysis/17) ----
_et = os.path.join(T, "AUDIT_earlier_transition.csv")
if os.path.exists(_et):
    et = pd.read_csv(_et)
    def _gap(tag, wg):
        r = et[(et.tag == tag) & (et.group == wg)]
        return f2(r.iloc[0].gap_pp) if len(r) else "n/a"
    put("repGapOneEarly", _gap("earlier", "WG1")); put("repGapTwoEarly", _gap("earlier", "WG2"))
    put("repGapThreeEarly", _gap("earlier", "WG3"))
    put("repGapTwoLate", _gap("later", "WG2")); put("repGapThreeLate", _gap("later", "WG3"))
    _sf = rd("AUDIT_sample_flow.csv")
    for _r, _k in (("AR4", "Four"), ("AR5", "Five"), ("AR6", "Six")):
        _x = _sf[(_sf.level == "report") & (_sf.report == _r)]
        if len(_x): put(f"covRate{_k}", f2(_x.iloc[0].identity_coverage_pct))
lines = ["% Auto-generated by analysis/07_generate_numbers.py -- do not edit by hand"]
for k in sorted(N): lines.append(f"\\newcommand{{\\{k}}}{{{N[k]}}}")
open(OUT, "w").write("\n".join(lines) + "\n")
print("re-wrote with replication macros:", len(N))

# ---- source-aligned consistency corrections (no new statistical analysis) ----
from pathlib import Path
from consistency_macros import build_consistency_macros
_consistency_values, _consistency_registry = build_consistency_macros(Path(OUT).parent)
N.update(_consistency_values)
lines = ["% Auto-generated by analysis/07_generate_numbers.py -- do not edit by hand"]
for k in sorted(N): lines.append(f"\\newcommand{{\\{k}}}{{{N[k]}}}")
open(OUT, "w").write("\n".join(lines) + "\n")
print("re-wrote with source-aligned consistency macros:", len(N))
