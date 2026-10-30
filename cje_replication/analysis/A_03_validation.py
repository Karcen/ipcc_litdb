# Portable replay copy: algorithms and frozen labels preserved; paths adapted.
import type_paths as P
"""A_03_validation.py -- validation of the document-type classifier.

Validation design (fixed seed 20261027, reproducible):
  Component 1 -- stratum sample: 2 cycles x 3 working groups x 2 match-statuses
                 = 12 strata x 16 occurrences = 192 occurrences.
  Component 2 -- rule sample: 8 classifier rules x 6 occurrences = 48 occurrences
                 drawn from AR5/AR6, excluding component-1 rows.
  Total 240 occurrences.

The adjudication is AI-assisted: the labels are assigned by the same language
model that wrote the classifier, by reading the raw reference string against the
protocol in analysis/A_type_classifier_protocol.md, blind to the classifier's
own label for each sampled row. This is NOT human validation; no human coder
reviewed the sample. Precision estimates are therefore upper bounds on what an
independent human coder would obtain.

The script:
  --draw     draws the sample and writes analysis/data/validation_sample.csv
             and blind adjudication sheets analysis/data/validation_blind_*.txt
  (default)  reads analysis/data/validation_adjudication.csv, compares with the
             classifier, and writes tables/A_classifier_validation.csv plus
             analysis/data/validation_comparison.csv
"""
import os, sys, csv
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = str(P.OUTPUT)
DATA = str(P.DATA)
TAB = os.path.join(ROOT, "tables")
SEED = 20261027
TYPES = ["journal_article", "book_or_chapter", "institutional_report",
         "working_paper", "other", "uncertain"]

def wilson(k, n, z=1.96):
    if n == 0:
        return (np.nan, np.nan, np.nan)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (100 * p, 100 * max(0, centre - half), 100 * min(1, centre + half))

def draw():
    rng = np.random.default_rng(SEED)
    d = pd.read_csv(os.path.join(DATA, "occurrences_all.csv"), low_memory=False)
    t = pd.read_csv(os.path.join(DATA, "occurrence_types.csv"), low_memory=False)
    m = d.merge(t[["occurrence_id", "category", "subcategory", "rule_id", "confidence"]],
                on="occurrence_id", how="inner", validate="1:1")
    m = m[m.report_id.isin(["AR5", "AR6"]) & m.wg_label.isin(["WG1", "WG2", "WG3"])].copy()
    m["matched"] = m.match_status.isin(["matched_doi", "matched_title"])
    picked = []
    for rep in ["AR5", "AR6"]:
        for wg in ["WG1", "WG2", "WG3"]:
            for matched in [True, False]:
                sub = m[(m.report_id == rep) & (m.wg_label == wg) & (m.matched == matched)]
                n = min(16, len(sub))
                idx = rng.choice(sub.index.values, size=n, replace=False)
                for i in idx:
                    picked.append((i, f"stratum:{rep}:{wg}:{'matched' if matched else 'unmatched'}"))
    chosen = set(i for i, _ in picked)
    rule_sample = ["R10_report_phrase", "R11_ipcc_structural", "R12_org_author",
                   "R13_book_chapter", "R14_book_publisher", "R16_institution",
                   "R17_journal_volume_issue", "R22_journal_doi_weak"]
    for rule in rule_sample:
        sub = m[(~m.index.isin(chosen)) & (m.rule_id == rule)]
        if len(sub) == 0:
            continue
        n = min(6, len(sub))
        idx = rng.choice(sub.index.values, size=n, replace=False)
        for i in idx:
            picked.append((i, f"rule:{rule}"))
            chosen.add(i)
    rows = []
    for i, comp in picked:
        r = m.loc[i]
        rows.append(dict(sample_index=len(rows) + 1, occurrence_id=r.occurrence_id,
                         component=comp, report_id=r.report_id, wg_label=r.wg_label,
                         match_status=r.match_status, rule_id=r.rule_id,
                         classifier_category=r.category, raw_reference=r.raw_reference))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(DATA, "validation_sample.csv"), index=False)
    # blind sheets (occurrence_id + raw string; no classifier label)
    for b in range(4):
        part = df.iloc[b * 60:(b + 1) * 60]
        lines = []
        for _, r in part.iterrows():
            raw = str(r.raw_reference).replace("\n", " ")
            lines.append(f"[{r.sample_index}] id={r.occurrence_id} rep={r.report_id} wg={r.wg_label} {r.match_status}\n{raw}\n")
        with open(os.path.join(DATA, f"validation_blind_{b+1}.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    print(f"sample rows: {len(df)}")
    print(df.component.str.split(":").str[0].value_counts().to_dict())
    print("blind sheets: analysis/data/validation_blind_1..4.txt")

def score():
    samp = pd.read_csv(P.VALIDATION / "validation_sample.csv", low_memory=False)
    # always score the CURRENT classifier output (the sample keeps occurrence ids,
    # the classifier is re-run independently of the sample draw)
    cur = pd.read_csv(os.path.join(DATA, "occurrence_types.csv"), low_memory=False)
    cur = cur[["occurrence_id", "category", "subcategory", "rule_id", "confidence"]]
    samp = samp.drop(columns=[c for c in ["classifier_category", "rule_id"] if c in samp.columns])
    samp = samp.merge(cur.rename(columns={"category": "classifier_category"}),
                      on="occurrence_id", how="left", validate="1:1")
    adj = pd.read_csv(P.VALIDATION / "validation_adjudication.csv", low_memory=False)
    m = samp.merge(adj[["sample_index", "adjudicated_category", "adjudication_note"]],
                   on="sample_index", how="left", validate="1:1")
    missing = m.adjudicated_category.isna().sum()
    if missing:
        print(f"WARNING: {missing} rows unadjudicated; scoring adjudicated rows only")
    m = m[m.adjudicated_category.notna()].copy()
    m["agree"] = (m.adjudicated_category == m.classifier_category).astype(int)
    m.to_csv(os.path.join(DATA, "validation_comparison.csv"), index=False)
    rows = []
    def add(level, name, sub):
        n = len(sub); k = int(sub.agree.sum())
        p, lo, hi = wilson(k, n)
        rows.append(dict(level=level, group=name, n_adjudicated=n, n_agree=k,
                         agreement_pct=None if np.isnan(p) else round(p, 2),
                         ci_lo_pct=None if np.isnan(lo) else round(lo, 2),
                         ci_hi_pct=None if np.isnan(hi) else round(hi, 2)))
    add("overall", "ALL", m)
    for c in TYPES:
        sub = m[m.classifier_category == c]
        if len(sub):
            add("classifier_category", c, sub)
    for r in sorted(m.rule_id.dropna().unique()):
        sub = m[m.rule_id == r]
        if len(sub) >= 3:
            add("rule", r, sub)
    for comp in m.component.str.split(":").str[0].unique():
        sub = m[m.component.str.startswith(comp)]
        add("component", comp, sub)
    # matched vs unmatched (the confound of interest)
    for ms in m.match_status.unique():
        sub = m[m.match_status == ms]
        if len(sub): add("match_status", ms, sub)
    # confusion matrix long form
    cm = (m.groupby(["adjudicated_category", "classifier_category"]).size()
          .reset_index(name="n"))
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(TAB, "A_classifier_validation.csv"), index=False)
    cm.to_csv(os.path.join(DATA, "validation_confusion.csv"), index=False)
    print(out.to_string(index=False))
    print("\nconfusion (adjudicated rows x classifier cols):")
    print(pd.crosstab(m.adjudicated_category, m.classifier_category).to_string())
    # per-rule precision with denominator is the 'rule' rows; category rows are precision-by-category



# ---------------------------------------------------------------------------
# Corroboration against external OpenAlex work types (matched identities only)
# ---------------------------------------------------------------------------
RESOLVED = str(P.validation_source_assignments())
OA_MAP = {
    "article": "journal_article", "review": "journal_article", "letter": "journal_article",
    "editorial": "journal_article", "erratum": "journal_article", "retraction": "journal_article",
    "book-review": "journal_article",
    "book": "book_or_chapter", "book-chapter": "book_or_chapter",
    "report": "institutional_report",
    "preprint": "working_paper",
    "conference-paper": "other", "conference-abstract": "other", "dissertation": "other",
    "dataset": "other", "software-paper": "other", "reference-entry": "other",
    "paratext": "other", "data-paper": "other", "other": "other",
}

def corroborate():
    iv = pd.read_csv(os.path.join(DATA, "identity_panel.csv"), low_memory=False)
    ra = pd.read_csv(RESOLVED, low_memory=False)
    ra = ra[ra.occurrence_status_group == "matched"].drop_duplicates(["report", "paper_id"])
    ra["oa_cat"] = ra.work_type.map(OA_MAP)
    rows = []
    for rep in ("AR5", "AR6"):
        s = iv[iv[f"n_occ_{rep}"] > 0].copy()
        s["clf_cat"] = s[f"type_{rep}"]
        r = ra[ra.report == rep][["paper_id", "work_type", "oa_cat", "source_type"]]
        r["paper_id"] = r.paper_id.astype(int)
        s["paper_id"] = s.paper_id.astype(int)
        mm = s.merge(r, on="paper_id", how="left", validate="1:1")
        cov = mm.oa_cat.notna()
        rows.append(dict(level="openalex_coverage", group=f"{rep}:all_identities",
                         n=len(mm), n_covered=int(cov.sum()), share_pct=round(100 * cov.mean(), 2),
                         n_agree="", agreement_pct=""))
        for cat in ["journal_article", "book_or_chapter", "institutional_report",
                    "working_paper", "other", "uncertain"]:
            sub = mm[mm.clf_cat == cat]
            if len(sub) == 0:
                continue
            have = sub.oa_cat.notna()
            n = int(have.sum())
            k = int((sub.loc[have, "oa_cat"] == cat).sum())
            rows.append(dict(level="category_openalex_corroboration",
                             group=f"{rep}:{cat}", n=n, n_covered=n,
                             share_pct=round(100 * n / len(sub), 2) if len(sub) else "",
                             n_agree=k,
                             agreement_pct=round(100 * k / n, 2) if n else ""))
    df = pd.DataFrame(rows)
    p = os.path.join(TAB, "A_classifier_validation_openalex.csv")
    df.to_csv(p, index=False)
    print(f"\nwrote {p}")
    print(df.to_string(index=False))
    # confusion long form for matched identities
    out = []
    for rep in ("AR5", "AR6"):
        s = iv[iv[f"n_occ_{rep}"] > 0][["paper_id"]].copy()
        s["clf_cat"] = iv.loc[iv[f"n_occ_{rep}"] > 0, f"type_{rep}"].values
        r = ra[ra.report == rep][["paper_id", "oa_cat"]].copy()
        r["paper_id"] = r.paper_id.astype(int); s["paper_id"] = s.paper_id.astype(int)
        mm = s.merge(r, on="paper_id", how="left", validate="1:1")
        mm = mm[mm.oa_cat.notna()]
        cm = mm.groupby(["clf_cat", "oa_cat"]).size().reset_index(name="n")
        cm["report"] = rep
        out.append(cm)
    pd.concat(out).to_csv(os.path.join(DATA, "openalex_confusion.csv"), index=False)

def stratum_agreement():
    """Agreement by design stratum and corpus-weighted agreement."""
    samp = pd.read_csv(P.VALIDATION / "validation_sample.csv", low_memory=False)
    cur = pd.read_csv(os.path.join(DATA, "occurrence_types.csv"), low_memory=False)
    samp = samp.drop(columns=[c for c in ["classifier_category", "rule_id"] if c in samp.columns])
    samp = samp.merge(cur[["occurrence_id", "category", "rule_id"]].rename(
        columns={"category": "classifier_category"}), on="occurrence_id", how="left")
    adj = pd.read_csv(P.VALIDATION / "validation_adjudication.csv", low_memory=False)
    m = samp.merge(adj[["sample_index", "adjudicated_category"]], on="sample_index", how="left")
    m = m[m.adjudicated_category.notna()].copy()
    m["agree"] = (m.adjudicated_category == m.classifier_category).astype(int)
    st = m[m.component.str.startswith("stratum")].copy()
    st["stratum"] = st.component.str.replace("stratum:", "", regex=False)
    rows = []
    for key, sub in st.groupby("stratum"):
        k = int(sub.agree.sum()); n = len(sub)
        rows.append(dict(stratum=key, n=n, n_agree=k, agreement_pct=round(100 * k / n, 2)))
    # corpus weights: AR5/AR6 x WG x match status cell sizes
    occ = pd.read_csv(os.path.join(DATA, "occurrence_vintage.csv"), low_memory=False)
    occ = occ[occ.report_id.isin(["AR5", "AR6"]) & occ.wg_label.isin(["WG1", "WG2", "WG3"])]
    occ["matched"] = occ.match_status.isin(["matched_doi", "matched_title"])
    occ["key"] = occ.report_id + ":" + occ.wg_label + ":" + occ.matched.map({True: "matched", False: "unmatched"})
    w = occ.key.value_counts()
    st["key"] = st.stratum.str.replace(":matched", ":matched", regex=False)
    st["key"] = st.apply(lambda r: ":".join(r.stratum.split(":")), axis=1)
    # stratum keys are rep:wg:status -- same as occ key
    ww = w.reindex(st.key.unique()).fillna(0)
    weighted = float((st.groupby("key").agree.mean() * ww).sum() / ww.sum() * 100)
    rows.append(dict(stratum="CORPUS_WEIGHTED", n=int(ww.sum()), n_agree="", agreement_pct=round(weighted, 2)))
    df = pd.DataFrame(rows)
    p = os.path.join(TAB, "A_classifier_validation_strata.csv")
    df.to_csv(p, index=False)
    print(f"\nwrote {p}\n{df.to_string(index=False)}")

if __name__ == "__main__":
    if "--draw" in sys.argv:
        raise SystemExit("Offline replay prohibits drawing a new sample; frozen labels are inputs.")
    elif "--corroborate" in sys.argv:
        corroborate()
    elif "--strata" in sys.argv:
        stratum_agreement()
    else:
        score()
        corroborate()
        stratum_agreement()
