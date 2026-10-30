# Portable replay copy: algorithms and frozen labels preserved; paths adapted.
import type_paths as P
"""A_05_visibility.py -- composition of what the matched frame can and cannot see.

For each cycle x working group x match status, report the document-type
composition of the occurrence pool. The unmatched rows are the direct measure of
what the observation frame discards; their type composition is the visibility
gradient. Unit: occurrence. Counts and denominators only.

Output: tables/A_invisible_pool_composition.csv
"""
import os
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = str(P.OUTPUT)
DATA = str(P.DATA)
TAB = os.path.join(ROOT, "tables")
TYPES = ["journal_article", "book_or_chapter", "institutional_report",
         "working_paper", "other", "uncertain"]

def main():
    ov = pd.read_csv(os.path.join(DATA, "occurrence_vintage.csv"), low_memory=False)
    ov = ov[ov.report_id.isin(["AR5", "AR6"])]
    ov["match_status_group"] = ov.match_status.map(
        lambda x: "matched" if x in ("matched_doi", "matched_title") else "unmatched")
    rows = []
    for rep in ["AR5", "AR6"]:
        for wg in ["ALL", "WG1", "WG2", "WG3"]:
            sub = ov[ov.report_id == rep]
            if wg != "ALL":
                sub = sub[sub.wg_label == wg]
            for msg in ["matched", "unmatched", "ALL"]:
                s = sub if msg == "ALL" else sub[sub.match_status_group == msg]
                N = len(s)
                vc = s.category.value_counts()
                for t in TYPES:
                    rows.append(dict(cycle=rep, wg=wg, match_status_group=msg, category=t,
                                     n=int(vc.get(t, 0)), denominator=N,
                                     share_pct=round(100 * vc.get(t, 0) / N, 3) if N else None,
                                     share_uncertain_pct=round(100 * vc.get("uncertain", 0) / N, 3) if N else None))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(TAB, "A_invisible_pool_composition.csv"), index=False)
    print(f"wrote A_invisible_pool_composition.csv rows={len(df)}")
    sub = df[(df.cycle == "AR5") & (df.match_status_group == "ALL") & (df.category != "uncertain")]
    print(sub.pivot_table(index="category", columns="wg", values="share_pct").round(1).to_string())
    print("\nAR5 unmatched pool, pooled:", end=" ")
    u = ov[(ov.report_id == "AR5") & (ov.match_status_group == "unmatched")]
    print({k: round(v * 100, 1) for k, v in (u.category.value_counts(normalize=True)).items()})

if __name__ == "__main__":
    main()
