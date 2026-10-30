"""Mechanical linkage table extracted from the source selection script.

No regression is fitted; the calculation is identical to mechanical_linkage in
B_selection_model.py. It counts recurrence through matched later occurrences.
"""
import numpy as np
import pandas as pd
import BCD_lib as L
ROWS=[]
def add(**kw):
    ROWS.append(kw)

def mechanical_linkage(ident, core):
    """Mechanical detection channel: how a recurrence is observed depends on the
    earlier cohort's matching status. Not behaviour; a property of the linkage."""
    rows = []
    for g in L.GROUPS:
        # earlier identities with any AR5 occurrence in the group, and an AR6 occurrence
        a5 = set(core[(core.report_id == "AR5") & (core.wg_label == g)].paper_id)
        s6 = core[(core.report_id == "AR6") & (core.wg_label == g)]
        rec_any = set(s6.paper_id)
        rec_m = set(s6.loc[s6.n_matched > 0, "paper_id"])
        m5 = set(core[(core.report_id == "AR5") & (core.wg_label == g) & (core.n_matched > 0)].paper_id)
        for lab, cohort in (("matched_earlier", a5 & m5),
                            ("extracted_only_earlier", a5 - m5)):
            rec = cohort & rec_any
            k = len(rec); via_m = len(rec & rec_m)
            rows.append(dict(group=g, cohort=lab, earlier_identities=len(cohort),
                             recurring_identities=k, recur_via_WoS_matched=via_m,
                             pct_via_WoS_matched=100.0 * via_m / k if k else np.nan))
            add(task="B_mechanical_linkage",
                quantity="recurrence_via_WoS_matched_AR6_occurrence_pct",
                report="AR5_to_AR6", group=g, numerator=via_m, denominator=k,
                estimate=float(100.0 * via_m / k) if k else np.nan,
                unit="percent",
                note=f"Cohort {lab}: of earlier-cohort identities recurring in AR6 in the same WG, "
                     f"share whose recurrence is detected through a WoS-matched AR6 occurrence "
                     f"(all years). Mechanical detection channel: an extracted-only earlier identity "
                     f"cannot recur through a matched AR6 occurrence.")
    T = pd.DataFrame(rows)
    T.to_csv(L.OUT / "B_mechanical_linkage.csv", index=False)
    print("\nmechanical linkage (recurrence detection channel by earlier cohort):")
    print(T.round(2).to_string(index=False))
    return T

if __name__ == "__main__":
    mechanical_linkage(L.identity_frame(), L.core_frame())
