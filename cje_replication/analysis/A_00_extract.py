# Portable replay copy: algorithms and frozen labels preserved; paths adapted.
import type_paths as P
"""A_00_extract.py -- extract analysis inputs for Task A (document-type visibility).

Reads READ-ONLY source databases and writes a flat intermediate under
CJE_revision/analysis/data/. No source data are modified.

Unit of analysis: one bibliography occurrence (references_occurrence.occurrence_id).
A second output is identity x report x working-group membership (unit: assigned
identity = papers.paper_id), used for the recurrence analyses.

Outputs:
  analysis/data/occurrences_all.csv   one row per occurrence, all reports
  analysis/data/identity_report_wg.csv one row per (paper_id, report_id, wg_label)
Deterministic, no sampling: python3 analysis/A_00_extract.py
"""
import os, sqlite3, csv

ROOT = str(P.OUTPUT)
DB = str(P.DB)
OUT = str(P.DATA)
os.makedirs(OUT, exist_ok=True)

OCC_SQL = """
SELECT r.occurrence_id, r.chapter_id, c.report_id, w.wg_label, r.paper_id,
       r.raw_reference, r.parsed_authors, r.parsed_title, r.parsed_journal,
       r.parsed_year, r.doi_norm, r.match_status, r.match_confidence,
       r.wos_record_id, p.journal_best, p.metadata_source, p.verification_status,
       p.identity_method
FROM references_occurrence r
JOIN chapters c ON c.chapter_id = r.chapter_id
LEFT JOIN working_groups w ON w.wg_id = c.wg_id
LEFT JOIN papers p ON p.paper_id = r.paper_id
ORDER BY r.occurrence_id
"""

CORE_SQL = """
SELECT r.paper_id, c.report_id, w.wg_label,
       COUNT(*) AS n_occ,
       SUM(CASE WHEN r.match_status IN ('matched_doi','matched_title') THEN 1 ELSE 0 END) AS n_matched,
       COUNT(DISTINCT r.chapter_id) AS n_chap
FROM references_occurrence r
JOIN chapters c ON c.chapter_id = r.chapter_id
LEFT JOIN working_groups w ON w.wg_id = c.wg_id
WHERE r.paper_id IS NOT NULL
GROUP BY r.paper_id, c.report_id, w.wg_label
ORDER BY r.paper_id, c.report_id, w.wg_label
"""

def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.cursor()
    for sql, name in ((OCC_SQL, "occurrences_all.csv"), (CORE_SQL, "identity_report_wg.csv")):
        cur.execute(sql)
        hdr = [d[0] for d in cur.description]
        rows = cur.fetchall()
        path = os.path.join(OUT, name)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(hdr)
            for row in rows:
                w.writerow(["" if v is None else v for v in row])
        print(f"wrote {len(rows):,} rows x {len(hdr)} cols -> {path}")
    con.close()

if __name__ == "__main__":
    main()
