-- PostgreSQL schema for the public SQLite snapshot and dashboard.
-- Imported years, dates, flags and JSON documents retain their source text.
-- Apply to an empty database, then load export_for_postgres.py output with psql.

BEGIN;

CREATE TABLE IF NOT EXISTS assessment_reports (
    report_id TEXT PRIMARY KEY,
    report_label TEXT NOT NULL,
    report_kind TEXT,
    first_published TEXT
);

CREATE TABLE IF NOT EXISTS working_groups (
    wg_id SERIAL PRIMARY KEY,
    report_id TEXT NOT NULL REFERENCES assessment_reports(report_id),
    wg_label TEXT NOT NULL,
    UNIQUE (report_id, wg_label)
);

CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    report_id TEXT,
    file_type TEXT,
    host TEXT,
    page_url TEXT,
    file_url TEXT,
    retrieved_date TEXT,
    local_path TEXT,
    sha256 TEXT,
    size_bytes BIGINT,
    page_count INTEGER,
    has_text_layer TEXT,
    ocr_applied TEXT,
    ocr_path TEXT,
    edition TEXT,
    publication_year TEXT,
    publisher TEXT,
    notes TEXT,
    file_present INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS chapters (
    chapter_id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL REFERENCES assessment_reports(report_id),
    wg_id INTEGER NOT NULL REFERENCES working_groups(wg_id),
    source_id TEXT REFERENCES sources(source_id),
    volume TEXT,
    chapter_label TEXT,
    chapter_title TEXT,
    start_page INTEGER,
    end_page INTEGER,
    ref_start_page INTEGER,
    ref_end_page INTEGER,
    in_scope TEXT,
    extraction_status TEXT,
    extraction_method TEXT,
    n_refs INTEGER,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS papers (
    paper_id SERIAL PRIMARY KEY,
    doi_norm TEXT,
    title_norm TEXT,
    title_best TEXT,
    year_best TEXT,
    journal_best TEXT,
    authors_best TEXT,
    first_author_surname TEXT,
    identity_method TEXT,
    n_occurrences INTEGER,
    n_chapters INTEGER,
    n_reports INTEGER,
    wos_record_id TEXT,
    metadata_source TEXT,
    verification_status TEXT
);

CREATE TABLE IF NOT EXISTS authors (
    author_id SERIAL PRIMARY KEY,
    surname TEXT NOT NULL,
    given TEXT,
    full_name TEXT,
    UNIQUE (surname, given)
);

CREATE TABLE IF NOT EXISTS paper_authors (
    paper_id INTEGER NOT NULL REFERENCES papers(paper_id),
    author_id INTEGER NOT NULL REFERENCES authors(author_id),
    author_pos INTEGER NOT NULL,
    PRIMARY KEY (paper_id, author_pos)
);

CREATE TABLE IF NOT EXISTS metadata_sources (
    source_code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS references_occurrence (
    occurrence_id TEXT PRIMARY KEY,
    chapter_id TEXT NOT NULL REFERENCES chapters(chapter_id),
    paper_id INTEGER REFERENCES papers(paper_id),
    source_id TEXT,
    source_page INTEGER,
    raw_reference TEXT,
    parsed_authors TEXT,
    parsed_title TEXT,
    parsed_journal TEXT,
    parsed_year TEXT,
    doi_norm TEXT,
    doi_source TEXT,
    match_status TEXT NOT NULL,
    match_confidence TEXT,
    wos_uid TEXT,
    wos_record_id TEXT,
    as_of TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS chapter_references (
    chapter_id TEXT NOT NULL REFERENCES chapters(chapter_id),
    occurrence_id TEXT NOT NULL REFERENCES references_occurrence(occurrence_id),
    paper_id INTEGER REFERENCES papers(paper_id),
    PRIMARY KEY (chapter_id, occurrence_id)
);

CREATE TABLE IF NOT EXISTS wos_records (
    record_id TEXT PRIMARY KEY,
    "DOI" TEXT,
    "Article Title" TEXT,
    "Source Title" TEXT,
    "Publication Year" TEXT,
    "Document Type" TEXT,
    "UT (Unique WOS ID)" TEXT
);

CREATE TABLE IF NOT EXISTS data_quality_flags (
    flag_id SERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    flag_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    detail TEXT,
    detected_at TEXT
);

CREATE TABLE IF NOT EXISTS manual_review (
    review_id SERIAL PRIMARY KEY,
    occurrence_id TEXT REFERENCES references_occurrence(occurrence_id),
    status TEXT NOT NULL DEFAULT 'open',
    assigned_to TEXT,
    reason TEXT,
    note TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS collection_tasks (
    task_id SERIAL PRIMARY KEY,
    chapter_id TEXT REFERENCES chapters(chapter_id),
    researcher TEXT,
    status TEXT,
    total_refs INTEGER,
    collected INTEGER,
    unresolved INTEGER,
    completion DOUBLE PRECISION,
    last_updated TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS change_log (
    change_id BIGSERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    field TEXT,
    old_value TEXT,
    new_value TEXT,
    changed_by TEXT,
    changed_at TEXT,
    reason TEXT
);

CREATE TABLE IF NOT EXISTS build_info (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS analysis_runs (
    run_id SERIAL PRIMARY KEY,
    start_time TEXT,
    end_time TEXT,
    source TEXT,
    rows_processed INTEGER,
    rows_added INTEGER,
    rows_changed INTEGER,
    errors INTEGER,
    warnings INTEGER,
    analysis_version TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS analysis_results (
    analysis_id SERIAL PRIMARY KEY,
    run_id INTEGER REFERENCES analysis_runs(run_id),
    analysis_type TEXT NOT NULL,
    parameters TEXT,
    query TEXT,
    input_version TEXT,
    output TEXT,
    created_at TEXT,
    software_version TEXT
);

CREATE TABLE IF NOT EXISTS insights (
    insight_id SERIAL PRIMARY KEY,
    run_id INTEGER,
    category TEXT,
    severity TEXT,
    title_en TEXT,
    title_zh TEXT,
    body_en TEXT,
    body_zh TEXT,
    evidence TEXT,
    link TEXT,
    created_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_papers_doi
    ON papers(doi_norm) WHERE doi_norm IS NOT NULL AND doi_norm <> '';
CREATE INDEX IF NOT EXISTS ix_papers_title ON papers(title_norm);
CREATE INDEX IF NOT EXISTS ix_papers_year ON papers(year_best);
CREATE INDEX IF NOT EXISTS ix_papers_verif ON papers(verification_status);
CREATE INDEX IF NOT EXISTS ix_ref_chapter ON references_occurrence(chapter_id);
CREATE INDEX IF NOT EXISTS ix_ref_paper ON references_occurrence(paper_id);
CREATE INDEX IF NOT EXISTS ix_ref_status ON references_occurrence(match_status);
CREATE INDEX IF NOT EXISTS ix_ref_doi ON references_occurrence(doi_norm);
CREATE INDEX IF NOT EXISTS ix_flag_type ON data_quality_flags(flag_type);
CREATE INDEX IF NOT EXISTS ix_flag_entity ON data_quality_flags(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS ix_analysis_type ON analysis_results(analysis_type);

COMMIT;
