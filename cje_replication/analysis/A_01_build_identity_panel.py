# Portable replay copy: algorithms and frozen labels preserved; paths adapted.
import type_paths as P
"""A_01_build_identity_panel.py -- build Task A analysis units.

Two units of analysis are used in Task A and they are kept separate:

  U1 occurrence : one bibliography entry = references_occurrence row.
                  All 160,029 are classified by A_type_classifier.py.
  U2 identity   : one assigned bibliographic identity = papers.paper_id.
                  An identity is the document being cited; it may occur many
                  times and in more than one report/working group. It is the unit
                  of the frame comparisons (identity coverage, recurrence).

This script builds U2 from the database (`identity_report_wg.csv`) and the
occurrence-type file, and derives a raw-string publication year for every
occurrence so that vintage is measured independently of the WoS/Crossref match.

VINTAGE DEFINITION (raw-string, deterministic, no matched metadata):
  ref_year = the first 4-digit year 1400-2029 that appears in the raw string
  followed by ')' or ':' and preceded by a boundary (start, space, comma, '(').
  This is the citation year in the IPCC reference format "Authors, YEAR: Title..."
  Identity year = modal ref_year across the identity's occurrences; ties broken
  towards the earliest year; fallback to papers.year_best when no ref_year.

Outputs:
  analysis/data/identity_panel.csv     one row per paper_id
  analysis/data/occurrence_vintage.csv one row per occurrence (id/year fields)
"""
import os, re, csv
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = str(P.DATA)

REP_YEAR = __import__("A_type_classifier")

YEAR_RE = re.compile(r"(?<![0-9])((?:1[4-9]\d{2}|20[0-2]\d))[a-z]?\s*[\):]")

def ref_year(raw: str):
    """Deterministic citation-year extraction from the raw reference string."""
    r = REP_YEAR.normalize(raw)
    for m in YEAR_RE.finditer(r):
        pos = m.start()
        if pos == 0:
            continue
        prev = r[pos - 1]
        if prev in " (,;[":
            y = int(m.group(1))
            if 1400 <= y <= 2029:
                return y
    return None

def main():
    occ = list(csv.DictReader(open(os.path.join(DATA, "occurrences_all.csv"), encoding="utf-8")))
    typ = {row["occurrence_id"]: row for row in
           csv.DictReader(open(os.path.join(DATA, "occurrence_types.csv"), encoding="utf-8"))}
    # ---- U1-derived vintage file (all occurrences)
    occ_rows = []
    for row in occ:
        ty = typ.get(row["occurrence_id"], {})
        occ_rows.append(dict(occurrence_id=row["occurrence_id"], report_id=row["report_id"],
                             wg_label=row["wg_label"], paper_id=row["paper_id"],
                             match_status=row["match_status"], category=ty.get("category", ""),
                             subcategory=ty.get("subcategory", ""), rule_id=ty.get("rule_id", ""),
                             ref_year=ref_year(row["raw_reference"]) or ""))
    with open(os.path.join(DATA, "occurrence_vintage.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(occ_rows[0].keys())); w.writeheader(); w.writerows(occ_rows)
    print(f"wrote {len(occ_rows):,} occurrence vintage rows")

    # ---- report x wg membership (unit: identity x report x wg)
    core = list(csv.DictReader(open(os.path.join(DATA, "identity_report_wg.csv"), encoding="utf-8")))
    meta = {}
    for row in occ:
        pid = row["paper_id"]
        if pid in meta:
            continue
        meta[pid] = dict(journal_best=row["journal_best"], metadata_source=row["metadata_source"],
                         verification_status=row["verification_status"],
                         identity_method=row["identity_method"])
    # year per identity: modal raw-string year over all occurrences of the identity
    years = defaultdict(list)
    cats = defaultdict(Counter)
    subcats = defaultdict(Counter)
    cats_rep = defaultdict(lambda: defaultdict(Counter))     # cycle -> pid -> Counter
    subcats_rep = defaultdict(lambda: defaultdict(Counter))
    n_occ_rep = defaultdict(Counter)                          # pid -> cycle -> n
    for row in occ_rows:
        pid = row["paper_id"]; rep = row["report_id"]
        if row["ref_year"] != "":
            years[pid].append(int(row["ref_year"]))
        cats[pid][row["category"]] += 1
        subcats[pid][row["subcategory"]] += 1
        if rep in ("AR5", "AR6"):
            cats_rep[rep][pid][row["category"]] += 1
            subcats_rep[rep][pid][row["subcategory"]] += 1
            n_occ_rep[pid][rep] += 1
    # "all occurrences" fallback: identities in papers without any occurrence is impossible (all have >=1)

    def modal_year(vals):
        if not vals:
            return None
        c = Counter(vals)
        top = max(c.values())
        return min(y for y, n in c.items() if n == top)

    # identity type: modal occurrence category. Tie-break by fixed precedence
    # (journal first: an identity that is a journal article in most occurrences
    # stays journal; lexical order is deterministic and documented).
    PREC = {"journal_article": 0, "book_or_chapter": 1, "institutional_report": 2,
            "working_paper": 3, "other": 4, "uncertain": 5}
    def modal_type(counter):
        if not counter:
            return "uncertain"
        top = max(counter.values())
        tied = [c for c, n in counter.items() if n == top]
        return sorted(tied, key=lambda c: PREC.get(c, 9))[0]

    # report/wg flags
    rep_members = defaultdict(set); rep_matched = defaultdict(set)
    rep_wg_any = defaultdict(lambda: defaultdict(set)); rep_wg_match = defaultdict(lambda: defaultdict(set))
    n_occ = Counter(); n_matched = Counter()
    for row in core:
        pid = str(row["paper_id"]); rep = row["report_id"]; wg = row["wg_label"]
        no = int(row["n_occ"]); nm = int(row["n_matched"])
        if no > 0:
            rep_members[pid].add(rep)
            rep_wg_any[pid][rep].add(wg)
        if nm > 0:
            rep_matched[pid].add(rep)
            rep_wg_match[pid][rep].add(wg)
        n_occ[pid] += no; n_matched[pid] += nm

    out = []
    for pid in sorted(meta, key=lambda x: int(x)):
        my = modal_year(years.get(pid, []))
        def cycle_type(rep):
            c = cats_rep[rep].get(pid)
            return modal_type(c if c else Counter())
        out.append(dict(
            paper_id=pid,
            year_ref=("" if my is None else my),
            identity_type=modal_type(cats.get(pid, Counter())),
            identity_subtype=modal_type(subcats.get(pid, Counter())),
            type_AR5=cycle_type("AR5"),
            type_AR6=cycle_type("AR6"),
            n_occ=n_occ.get(pid, 0), n_matched=n_matched.get(pid, 0),
            n_occ_AR5=n_occ_rep[pid].get("AR5", 0),
            n_occ_AR6=n_occ_rep[pid].get("AR6", 0),
            reports_any="|".join(sorted(rep_members.get(pid, set()))),
            reports_matched="|".join(sorted(rep_matched.get(pid, set()))),
            wg_any_AR5="|".join(sorted(rep_wg_any[pid].get("AR5", set()))),
            wg_any_AR6="|".join(sorted(rep_wg_any[pid].get("AR6", set()))),
            wg_matched_AR5="|".join(sorted(rep_wg_match[pid].get("AR5", set()))),
            wg_matched_AR6="|".join(sorted(rep_wg_match[pid].get("AR6", set()))),
            **meta[pid]))
    with open(os.path.join(DATA, "identity_panel.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
    print(f"wrote {len(out):,} identities -> identity_panel.csv")

if __name__ == "__main__":
    main()
