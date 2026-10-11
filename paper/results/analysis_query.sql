SELECT r.occurrence_id, c.report_id, wg.wg_label, r.chapter_id, r.paper_id,
       r.match_status, r.parsed_year, r.raw_reference, p.doi_norm, p.verification_status, p.year_best,
       p.journal_best, p.title_best, p.n_reports, s.publication_year AS source_year,
       r.source_id, c.source_id AS chapter_source_id
FROM references_occurrence r
JOIN chapters c ON c.chapter_id = r.chapter_id
LEFT JOIN working_groups wg ON wg.wg_id = c.wg_id
LEFT JOIN papers p ON p.paper_id = r.paper_id
LEFT JOIN sources s ON s.source_id = c.source_id
WHERE c.report_id IN ('AR1','AR2','AR3','AR4','AR5','AR6')
ORDER BY c.report_id, r.occurrence_id;
