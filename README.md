# IPCC Literature Database

A searchable database of the works cited in IPCC reports, with a local dashboard for literature search, coverage analysis, citation networks, and reference review. The October 2026 snapshot contains 113,591 papers and 160,029 reference occurrences.

This is the second project alongside [IPCC References Toolkit](https://github.com/Karcen/ipcc_refs_toolkit). The toolkit collects and processes references; this repository provides the database and the interface for studying them across reports.

## Quick start

Use Python 3.10 or later:

```bash
git clone https://github.com/Karcen/ipcc_litdb.git
cd ipcc_litdb
python3 -m venv .venv
source .venv/bin/activate
bash scripts/install.sh
bash scripts/dev.sh
```

These commands work in macOS and Linux shells. Windows users can run the same commands through WSL. In Git Bash with native Windows Python, activate the environment with `source .venv/Scripts/activate`.

Open [the dashboard](http://127.0.0.1:8077/) or [the API documentation](http://127.0.0.1:8077/docs). Press Ctrl+C to stop the server.

Installation restores `database/ipcc_litdb.sqlite` from the bundled compressed snapshot and verifies its SHA-256 digest. The dashboard runs from this data without API keys or the original PDF collection.

The application uses English throughout. Paper titles, author names, and printed references retain their source spelling.

### Docker

```bash
docker compose up --build -d
```

Open [the dashboard](http://127.0.0.1:8077/). Docker uses SQLite in a persistent volume and restores the snapshot on first start. The service listens on the host's loopback address.

```bash
docker compose logs -f api
docker compose down
```

Stopping the containers preserves the database volume, including review history.

## Contents

- [Data and coverage](#data-and-coverage)
- [Dashboard](#dashboard)
- [Repository layout](#repository-layout)
- [Configuration](#configuration)
- [Database model](#database-model)
- [Analysis](#analysis)
- [Reproduce assessment analyses](#reproduce-assessment-analyses)
- [Exports and API](#exports-and-api)
- [Sources and provenance](#sources-and-provenance)
- [Data quality and review](#data-quality-and-review)
- [PostgreSQL](#postgresql)
- [Tests](#tests)
- [GitHub distribution](#github-distribution)
- [Troubleshooting](#troubleshooting)
- [Attribution and contact](#attribution-and-contact)

## Data and coverage

The bibliographic snapshot was built on 6 October 2026. Derived analyses and English insights were refreshed on 11 October 2026. Counts below describe that snapshot; the dashboard queries the working database for current values.

| Measure | Count |
|---|---:|
| Report entries | 36 |
| Working-group entries | 43 |
| Chapters and other citable units | 677 |
| Units with reference occurrences | 451 |
| Source PDFs registered | 438 |
| Papers | 113,591 |
| Reference occurrences | 160,029 |
| Occurrences matched to Web of Science | 91,832 |
| Unmatched occurrences | 68,197 |
| Web of Science metadata records | 61,264 |
| Papers cited in multiple chapters | 24,065 |
| Papers cited in multiple reports | 12,884 |
| Data-quality flags | 33,571 |

The occurrence-level Web of Science match rate is 57.38%. At paper level, 91,401 works carry either `wos_matched` or `doi_verified`, giving a combined workflow-label share of 80.47%. The builder assigns `doi_verified` to a syntactically valid DOI without a WoS record. It does not independently resolve that DOI or validate its correspondence to the printed reference. These measures use different denominators.

| Report | Matched occurrences | Total occurrences | Match rate |
|---|---:|---:|---:|
| AR6 | 47,053 | 63,027 | 74.7% |
| AR5 | 17,604 | 27,851 | 63.2% |
| AR4 | 5,855 | 16,193 | 36.2% |
| AR3 | 1,537 | 8,292 | 18.5% |
| AR2 | 43 | 8,250 | 0.5% |
| AR1 | 2 | 1,551 | 0.1% |
| SROCC | 4,957 | 6,309 | 78.6% |
| SRCCL | 5,098 | 6,712 | 76.0% |
| SR15 | 3,977 | 5,262 | 75.6% |
| SREX | 1,955 | 4,816 | 40.6% |
| SRREN | 1,484 | 3,995 | 37.1% |
| 2019REF | 1,205 | 2,626 | 45.9% |
| 2006GL | 310 | 1,338 | 23.2% |
| WETLANDS | 545 | 1,026 | 53.1% |
| SRCCS | 186 | 1,016 | 18.3% |
| SRES | 8 | 325 | 2.5% |

The collection covers assessment reports AR1 through AR6, synthesis reports, special reports, and greenhouse-gas inventory guidelines and supplements. Report entries with no extracted references remain in the catalogue.

## Dashboard

| Page | Use |
|---|---|
| Overview | Inspect totals, citation distributions, publication years, and widely cited works. |
| Assessment reports | Browse the report hierarchy; inspect printed references and source pages. |
| Literature | Search by title, DOI, report, working group, year, and verification status. |
| BI explorer | Compare a metric across a dimension and inspect the SQL behind the result. |
| Research comparison | Compare two report literature cohorts, publication years, journals, and shared works. |
| Analytics | Open stored analysis results, parameters, and queries. |
| Automated insights | Read findings with their supporting data. |
| Networks | Explore shared literature and co-authorship; inspect communities, centrality, and connected components. |
| Data quality | Inspect stored flags and run live completeness, identifier, relationship, count, and year-consistency checks. |
| Unresolved | Review unmatched references and record decisions. |
| Collection progress | Inspect extraction coverage by chapter. |
| AI analysis | Ask database questions through an optional model endpoint. |
| Settings | Change the theme and inspect recent analysis runs. |

FastAPI serves the static frontend and the API. ECharts and Cytoscape.js are bundled locally; the dashboard requires no frontend build or CDN.

Overview, BI, and research comparison charts provide CSV, PNG, SVG, and JSON downloads. Figures include their title, units, filters, source, and retrieval time. BI JSON files retain the executed SQL and returned rows. Networks provide PNG, JSON, GraphML, GEXF, and edge-list CSV downloads. The JSON retains rendered positions and view settings; GraphML and GEXF retain node metrics, edge weights, parameters, and provenance for further graph analysis.

![Overview with citation counts and report coverage](screenshots/01-overview.png)

![BI explorer with a chart, result table, and query](screenshots/02-bi-explorer.png)

![Paper details and the IPCC chapters citing the work](screenshots/03-paper-detail.png)

## Repository layout

```text
ipcc_litdb/
├── README.md
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── backend/                 # API, configuration, exports, optional model adapter
├── analytics/               # Metric definitions, analyses, network builders
├── frontend/                # English interface and local chart libraries
│   └── vendor/licenses/     # Upstream license texts and notices
├── database/
│   ├── ipcc_litdb.sqlite.gz # Compressed snapshot tracked by Git
│   ├── snapshot.json       # File sizes, hashes, and snapshot counts
│   ├── export_for_postgres.py
│   ├── schema/
│   └── migrations/
├── data/
│   ├── raw/                # Manifest of upstream inputs
│   └── processed/          # Audit statistics and deduplication summary
├── scripts/                # Install, restore, serve, analyse, test, export, migrate
├── reproducibility/        # Assessment analyses and registered result tables
├── screenshots/
├── tests/
└── .github/workflows/      # Automated checks
```

The working SQLite file, virtual environments, credentials, caches, and macOS sidecar files are excluded from Git. This README contains the toolkit documentation. Third-party license texts remain separate text files.

## Configuration

```bash
cp .env.example .env
```

Edit `.env` before starting the service. The launcher reads this file; exported environment variables take precedence.

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | Repository's SQLite database | API database connection. |
| `API_HOST` | `127.0.0.1` | Server bind address. |
| `API_PORT` | `8077` | Local server port. |
| `ALLOW_WRITES` | `true` | Enable reference edits and review actions. |
| `CHANGED_BY` | `dashboard-user` | Actor recorded with review changes. |
| `MAX_PAGE_SIZE` | `200` | Pagination limit. |
| `AI_ENABLED` | `true` (example: `false`) | Enable the optional model adapter. |
| `AI_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible endpoint. |
| `AI_API_KEY` | Empty | Endpoint credential. |
| `AI_MODEL` | `gpt-4o-mini` | Model name accepted by the endpoint. |
| `SQL_ROW_LIMIT` | `500` | Maximum rows returned by model-generated queries. |
| `SQL_TIMEOUT_SECONDS` | `15` | Model-query execution timeout. |

For a read-only dashboard or another port:

```bash
ALLOW_WRITES=false bash scripts/dev.sh
API_PORT=8090 bash scripts/dev.sh
```

Shared hosting requires an authentication gateway or reverse proxy. The application records the configured review actor; it does not authenticate individual users.

### Optional AI queries

The adapter sends the question and database schema to the configured endpoint, validates the proposed SQL, executes it, then requests an explanation of the returned rows. Questions and query results therefore leave the local machine when a remote endpoint is configured.

Queries use a SQLite read-only connection, an allowed-table list, a row limit, and a timeout. The API returns the executed SQL with the answer. This feature uses SQLite. The dashboard and deterministic analyses run without an AI credential.

## Database model

A paper is one cited work. A reference occurrence records that work's appearance in an IPCC chapter, with its source page and printed citation. One paper can have many occurrences.

```text
assessment_reports -> working_groups -> chapters -> references_occurrence
                                           ^                 |
                                           |                 v
                                         sources           papers
                                                             |
                                                       paper_authors -> authors
```

| Tables | Role |
|---|---|
| `assessment_reports`, `working_groups`, `chapters` | Report hierarchy. |
| `sources` | Source URLs, hashes, and extraction metadata. |
| `papers` | Deduplicated works and preferred bibliographic fields. |
| `references_occurrence` | Printed citations linked to chapters and papers. |
| `authors`, `paper_authors` | Authors and their ordered links to papers. |
| `wos_records`, `metadata_sources` | Retained metadata and verification sources. |
| `data_quality_flags` | Record-level quality checks. |
| `manual_review`, `change_log` | Review decisions and edit history. |
| `collection_tasks` | Assignment records, empty in this snapshot. |
| `analysis_runs`, `analysis_results`, `insights` | Stored analyses and findings. |
| `build_info` | Snapshot build metadata. |
| `chapter_references` | Chapter-to-paper associations used by analysis. |

Paper identity follows exact normalized DOI, then normalized title and year. DOI-less occurrences can attach to an existing DOI paper when the title matches and the publication years differ by at most one. Similar titles become candidates for review. The `identity_method` field records the rule used for each paper.

After restoring the snapshot, any SQLite client can query it directly:

```bash
sqlite3 database/ipcc_litdb.sqlite \
  "SELECT report_id, COUNT(*) AS chapters FROM chapters GROUP BY report_id;"

sqlite3 database/ipcc_litdb.sqlite \
  "SELECT title_best, year_best, n_reports, n_chapters
   FROM papers ORDER BY n_reports DESC LIMIT 20;"
```

## Analysis

`analytics/metrics.py` defines eight metrics and nine dimensions with their SQL. Filters cover report, working group, chapter, match status, and publication year.

BI queries return up to 200 aggregate groups by default. The API's `limit` parameter sets that number; the returned SQL records it. BI downloads contain those returned rows.

| Analysis | Calculation |
|---|---|
| `coverage_by_report` | Reference counts and match rates by report. |
| `metadata_completeness` | Completeness of title, author, journal, year, and DOI. |
| `chapter_unresolved_outliers` | Chapters whose unresolved rates differ from the corpus baseline. |
| `reference_age_by_report` | Source publication year minus cited-work year, weighted by reference occurrence; includes ages from 0 to 60 years and exclusion counts. |
| `cross_generation_persistence` | Works appearing in multiple reports. |
| `working_group_crossover` | Works cited across working-group labels. |
| `chapter_overlap_jaccard` | Intersection divided by union of distinct paper sets. |
| `journal_concentration` | Sum of squared journal shares. |
| `duplicate_detection` | DOI, title, token-overlap, and author/year candidate checks. |
| `year_outliers` | Implausible years and publication-year spikes. |
| `duplicate_units` | Chapter pairs with highly overlapping paper sets. |

```bash
bash scripts/analysis.sh
python -m analytics.cli list
python -m analytics.cli show reference_age_by_report
```

Refreshing analyses writes a new run to the local SQLite database. The bundled snapshot remains unchanged. Stored results include SQL, parameters, input version, and software version.

Reference ages use each source file's publication year. AR5 WG1 uses 2013; AR6 WG1 uses 2021 and WG2/WG3 use 2022. A documented report year supplies the fallback when a source date is absent or ambiguous. Results retain the number of citations excluded for missing, future, or older-than-60-year dates.

Network responses define their node population, edge weights, ranking, threshold, and display limit. Graph filters run before the edge limit. The response records eligible and omitted edges, isolated nodes, and connected nodes omitted from the display; the JSON download retains these definitions.

### Network structure

Choose an edge limit from 100 to 2,000, then color nodes by their source group or detected community. Available layouts are force-directed, breadth-first, circle, and concentric. Rankings show degree, weighted strength, and betweenness. The summary reports components, density, and weighted modularity.

NetworkX computes these metrics on the displayed graph after the threshold and edge limit. Degree counts displayed neighbors. Strength sums their edge weights. Betweenness uses unweighted shortest paths because the source weights measure citation strength or similarity. It is exact through 200 nodes and uses 64 sampled source nodes above that size, with seed 42. Louvain communities use positive edge weights, resolution 1, and seed 42. The response records the installed NetworkX version and algorithm parameters.

Changing the edge limit changes the graph and can change its rankings and communities. Source-population isolates and omitted connected nodes have separate counts.

### Report comparison

The Research comparison page selects two reports and counts each cited paper once per cohort. Shared papers divided by the union gives Jaccard similarity. Shared papers divided by the smaller cohort gives the overlap coefficient. The two cohort shares use their respective paper counts as denominators. Publication-year distributions follow numeric year order. Journal shares include a missing-journal category and use all cohort papers as their denominators.

The result retains cohort SQL, parameters, missing-year counts, and the limit on the shared-paper table. Journal downloads contain the full distribution even when the view shows only its leading rows.

The publication-year chart starts at 1850 by default. Use the year-range controls to inspect another period or show all recorded years. The caption counts papers outside the plotted range and papers with missing or invalid years. Chart downloads follow the applied range; the full comparison JSON retains all recorded years and the chosen view settings.

### Command-line research exports

These commands run without a dashboard server:

```bash
python -m analytics.cli compare AR5 AR6 --output comparison.json
python -m analytics.cli audit --sample-limit 10 --output quality-audit.json
python -m analytics.cli network report-report --limit 300 --format graphml --output reports.graphml
python -m analytics.cli bundle AR5 AR6 --limit 300 --output research-export
```

Use `--database /path/to/database.sqlite` to select another SQLite file. The bundle contains the comparison, live audit, and all five networks in JSON, GraphML, GEXF, and CSV. All analyses read one transaction-consistent SQLite backup. A manifest records the input digest, parameters, output sizes, and SHA-256 digests. The temporary input copy is removed after export. Choose a new output directory for each bundle.

## Reproduce assessment analyses

The reproducibility package reads the distributed SQLite snapshot. It computes report transitions, publication-age distributions, outlet concentration, and working-group overlap. It also generates a stratified reference-review worksheet. All outputs go to a new directory. The input database stays unchanged.

Use Python 3.10 or 3.12 with the pinned analysis dependency:

```bash
python -m pip install -r requirements-analysis.txt
python scripts/reproduce_assessment_analysis.py \
  --output-dir analysis-reproduction \
  --check-reference
```

Run these commands from the repository root after Quick start installation. Choose a new output directory for each run. The full run uses 2,500 chapter resamples and conditional sharing draws, and 2,000 equal-size outlet subsamples. The seed is `20261011`.

The input SHA-256 is:

```text
faa7fc711ae4607acfa9833739ec8f202f8aefd6d929b474a12fceb589c5b85d
```

The runner checks this digest before analysis and again after analysis. `--check-reference` compares 29 complete registered CSV tables with `reproducibility/reference/`. Table headers, row order, and nonnumeric values must agree. Numeric comparisons use absolute and relative tolerances of `1e-10`. `reproduction.json` records the dependency version, input and code hashes, parameters, output hashes, and comparison results. Each step has its own log. The `qc/` directory contains checks of identifiers, joins, resampling margins, and analytical sampling expectations.

The bundled snapshot supplies every numerical input. API keys, journal subscriptions, original PDFs, and LaTeX are unnecessary for this run. Data acquisition from PDFs and the historical WoS collection remain separate upstream stages, described under [Sources and provenance](#sources-and-provenance).

Some printed references contain embedded NUL characters. Generated CSV files represent these characters with the literal escape `\u0000`, which Python 3.10 can read. SQLite and JSON preserve the original text. The snapshot contains no pre-existing literal `\u0000` tokens in the extracted reference or preferred-title fields, so replacing the marker with a NUL reconstructs those fields.

### Export assessment figures

The figure generator reads ten registered result tables and exports four plots as PDF, editable SVG, and 600-dpi PNG. It includes workflow coverage, citation-age ECDFs, equal-size effective outlet comparisons, and working-group retention and conditional sharing. It writes source hashes, figure contracts, and layout audits to a separate output directory.

Install the plotting dependencies and the pinned [Nature Figure QA tools](https://github.com/Yuan1z0825/nature-skills/tree/e605b35611ab2849af46d0cf7831aefdcf85be07/skills/nature-figure) outside this repository:

```bash
python -m pip install -r requirements-figures.txt
git clone https://github.com/Yuan1z0825/nature-skills ../nature-skills
git -C ../nature-skills checkout e605b35611ab2849af46d0cf7831aefdcf85be07
python reproducibility/make_assessment_figures.py \
  --results-dir reproducibility/reference \
  --output-dir assessment-figures \
  --qc-dir assessment-figure-checks \
  --skill-directory ../nature-skills/skills/nature-figure
```

Use fresh output directories. Substitute `analysis-reproduction/results` for the results directory to plot a complete rerun. The generator checks input hashes before and after rendering. At 133 mm width, each plot must pass panel alignment within 1.5 pt, exported PDF text at least 7 pt, and rendered collision checks. The audits cover file geometry; the figure contract also records units, weights, denominators and interval definitions. The QA tools are distributed under Apache 2.0. Figure exports are generated locally and are absent from this repository.

### Units and definitions

| Quantity | Unit and denominator |
|---|---|
| Extraction coverage | Reference occurrences with the selected workflow status divided by all occurrences in the report or working group. |
| Retention | Distinct IDs shared by the later and earlier reports divided by earlier-report IDs. |
| Reuse | Shared distinct IDs divided by later-report IDs. |
| Conditional reuse | Shared eligible matched IDs divided by all later-report matched IDs within the selected publication-year cutoff. All-extracted and DOI-status sensitivities use their respective cohorts. Cutoffs 2012, 2013, and 2014 are reported separately. |
| First observed in AR6 | AR6 matched IDs absent from every AR5 extracted ID, including unmatched entries. This describes the saved collection. |
| Reference age | Source publication year minus publication year of the cited work; missing, future, and over-60-year dates have separate counts. |
| Equal-ID age | Each eligible ID contributes total weight one within a report, shared equally across its eligible occurrences. |
| Price fraction | Share of eligible citation weights aged 0–5 years inclusive; a separate sensitivity uses ages under 5 years. |
| Outlet concentration | Sum of squared named-outlet shares, with one weight per distinct ID. Missing labels have separate counts. |
| Hill diversity | `q0` counts labels, `q1` is the exponential Shannon entropy, and `q2` is inverse concentration. |
| Working-group overlap | Shared distinct IDs divided by their union (Jaccard), or by the smaller group (overlap coefficient). |

`paper_id` denotes the database's linked identity. A `matched_doi` occurrence has an exact DOI match at the WoS acceptance stage. Earlier enrichment can supply or replace its title, DOI, and year. This workflow status therefore does not establish the accuracy of the link to the original printed reference. The separate `doi_verified` paper label has the builder meaning given under [Data and coverage](#data-and-coverage).

### Results registered for this snapshot

| Check | Result |
|---|---|
| AR5–AR6 matched-ID overlap | 1,664 shared IDs; AR5 retention 12.32%, AR6 reuse 4.77%. |
| Symmetric earlier-report presence | 1,665 AR6 matched IDs appear anywhere in AR5. Of 4,160 earlier-publication IDs absent from the AR5 matched cohort, one is present in its unmatched cohort. |
| Publication-year-eligible reuse | 34.16%, 28.40%, and 20.47% at cutoffs 2012, 2013, and 2014. |
| Occurrence-weighted age | Median 4 years in both reports; mean increases from 4.434 to 5.139 years. The 0–5-year fraction decreases from 71.21% to 62.79%. |
| Equal-ID age | Median increases from 4 to 5 years; mean increases from 4.513 to 5.306 years. |

Age uncertainty tables describe stability under chapter resampling within this fixed collection. Outlet subsampling compares reports at the same number of IDs. These intervals retain the saved record links and metadata. The working-group null preserves journal/vintage margins, group counts, and each ID's number of working-group memberships. It tests how fixed sharing is allocated across pairs. It does not test whether total sharing exceeds a climate-literature population benchmark.

Outlet sensitivities compare the preferred labels, conservative punctuation aliases, and registered WoS source titles. The local snapshot and upstream exports lack usable ISSNs. The alias crosswalk records exactly which strings were combined. Named-outlet concentration and richness have separate results; outlet labels do not assign disciplines.

The review worksheet contains 337 IDs and 818 printed entries. It includes a probability sample and targeted anomaly cases, identified separately. Mechanical title, DOI, year, and author flags accompany the raw entries. Human adjudication fields remain unreviewed. Informal spot checks were performed during the original work; retained adjudication records are unavailable. The package estimates no human-validated linkage error rate.

## Exports and API

The literature page exports CSV, XLSX, JSON, BibTeX, and RIS using the applied filters and sorting. Each format records the total matching records, export limit, and truncation state. Paper exports contain one row per work. The export script requires a running server:

```bash
bash scripts/export.sh papers csv AR6
bash scripts/export.sh unresolved xlsx AR2
```

| Endpoint | Result |
|---|---|
| `GET /api/health` | Service status. |
| `GET /api/statistics` | Database totals and coverage. |
| `GET /api/reports` | Report catalogue. |
| `GET /api/chapters?report_id=AR6` | Chapters in a report. |
| `GET /api/papers?q=emissions` | Paginated literature search. |
| `GET /api/papers/{paper_id}/citations` | Citation history for a work. |
| `GET /api/bi/query?metric=reference_count&dimension=report` | Metric values and SQL. |
| `GET /api/insights` | Stored findings. |
| `GET /api/research/compare?report_a=AR5&report_b=AR6` | Distinct-paper cohort comparison and supporting queries. |
| `GET /api/data-quality/audit?sample_limit=10` | Live audit with denominators, samples, and SQL. |
| `GET /api/networks/report-report?limit=300` | Graph, centrality, communities, and scope. |
| `GET /api/networks/report-report/export?fmt=graphml&limit=300` | GraphML export; also accepts `gexf` and `csv`. |
| `GET /api/unresolved` | Unmatched references and review status. |
| `GET /api/export/papers.csv?report_id=AR6` | Filtered export. |

The [interactive API documentation](http://127.0.0.1:8077/docs) describes parameters and review endpoints.

## Sources and provenance

The collection uses PDFs from [IPCC report pages](https://www.ipcc.ch/reports/) and the [IPCC inventory programme](https://www.ipcc-nggip.iges.or.jp/). Each occurrence retains its report, working group, chapter, page, and printed reference. The source registry records URLs, SHA-256 hashes, page counts, and extraction status.

`data/raw/manifest.csv` records the size and SHA-256 digest of upstream inputs:

| Input | Use |
|---|---|
| `refs_occurrence.csv` | Extracted citation occurrences. |
| `sources.csv` | Source PDF registry. |
| `units.csv` | Chapters and other citable units. |
| `occurrence_record.csv` | Links between occurrences and matched records. |
| `Record_by_occurrence.csv` | Matched metadata joined to occurrences. |
| `Unrecord_ALL.csv` | Unmatched-reference table. |
| `unit_label_map.csv` | Chapter-label mapping. |
| `ipcc_refs.sqlite` | Upstream collection database. |
| `screening_summary_ALL.xlsx` | Screening summary, listed with an English filename alias. |

The upstream inputs and extraction environment remain in the original project workspace. This repository distributes the finished database, its provenance manifest, and the application. `scripts/import_data.sh` restores a missing snapshot. Run `scripts/analysis.sh` separately to refresh analyses. Rebuilding the collection from PDFs uses the upstream pipeline.

The retained `wos_records` fields are record ID, DOI, article title, source title, publication year, document type, and Web of Science UID. Subscription payloads such as abstracts and cited-reference lists were removed from the delivery database.

Snapshot provenance paths are relative to the source workspace. Citation text and bibliographic identifiers retain their original values. `database/snapshot.json` records the packaged file hashes.

### Manual Web of Science export convention

Upstream exports use names such as `AR6WG1CH1T1.txt`, `AR6WG1CH1T1.xlsx`, and `AR6WG1CH1T1Z.txt`. `AR6`, `WG1`, and `CH1` identify the report, working group, and chapter; `T1` identifies the first export batch.

The collection workflow searches by title through institutional Web of Science access, adds matching works to a marked list, and exports batches in plain text, Excel, and tab-delimited formats. Plain text and tab-delimited exports use the full record with cited references; Excel uses the full record. Keep export batches with the upstream collection files.

## Data quality and review

Match rates describe the collection run and its bibliographic coverage. Older assessments have fewer matched references. The collection's Starter API probes also returned fewer records for publication years before 1998; those probes identify a coverage issue to investigate with additional metadata sources. Unmatched records include books, government reports, and other works requiring manual verification.

| Flag | Count |
|---|---:|
| Missing DOI | 26,236 |
| Missing journal | 5,457 |
| Possible duplicate title | 974 |
| Missing title | 379 |
| Missing author | 284 |
| Missing year | 114 |
| Invalid DOI | 103 |
| Suspicious year | 24 |

These are stored flags from the collection run. Live completeness counts both NULL and blank fields: 4,169 reference occurrences lack a publication year, giving 97.39% year completeness.

Run the live audit to recalculate missing fields, DOI syntax, normalized DOI duplicates, parent-child relationships, stored citation counters, and year consistency. Each of its 21 checks records the affected grain, count, denominator, percentage, query, and bounded sample. Report-level completeness includes zero-reference reports with null percentages. A malformed DOI is a syntax finding; identifier existence requires a registry lookup.

Two year checks identify citations for review: a preferred paper year later than the source's unambiguous publication year, and a first printed year token that differs from the preferred metadata. Review the printed citation, source page, and linked record to resolve these candidates. Printed references can contain several dates, editions, and numbers that resemble years. The audit reads the current database and provides a JSON download with its evidence and query provenance.

The flag table and the broader duplicate analysis use different candidate rules. The stored duplicate analysis identifies 976 title groups for inspection.

Two extractions of AR6 WG2 Annex I share 35 papers. The stored duplicate-unit analysis also identifies overlapping AR4 synthesis-report extractions. Both sets remain available for source comparison. Forty-eight units carry `qc_warn` extraction status.

The deduplication audit records 756 papers with publication-year conflicts and 1,054 with title variants. Compare the printed citations and source pages when reviewing these works.

The unresolved queue records review status, assignee, and notes. Reference edits require a reason and create a change-log entry with the actor, timestamp, previous value, and new value. Back up the working database before replacing it: review history and later analysis runs are stored there.

The quality score is mean title, author, year, journal, and DOI completeness minus half the unresolved rate. Check component rates and flagged records when interpreting it.

Further collection work includes source-level duplicate review, publication-year checks, metadata verification for early reports, and recording researcher assignments. Shared deployment also needs a hosting location and an access-control arrangement.

## PostgreSQL

SQLite is the default for the dashboard and analysis engine. The PostgreSQL schema and export script support transferring the snapshot to a server:

```bash
python database/export_for_postgres.py --strip-nul > /tmp/ipcc_litdb.sql
psql "$POSTGRES_URL" -v ON_ERROR_STOP=1 -f database/schema/schema_postgres.sql
psql "$POSTGRES_URL" -v ON_ERROR_STOP=1 -f /tmp/ipcc_litdb.sql
```

`POSTGRES_URL` is a libpq URL such as `postgresql://user:password@host:5432/ipcc_litdb`. Import into an empty database. The exporter reads SQLite without changing it, writes PostgreSQL `COPY` sections, and restores sequence values. `--strip-nul` removes 12 NUL characters from this snapshot's PostgreSQL output, because PostgreSQL text fields cannot store them. The default export stops on those characters; the SQLite snapshot retains them.

The API connection uses the SQLAlchemy form:

```bash
DATABASE_URL='postgresql+psycopg://user:password@host:5432/ipcc_litdb' \
  bash scripts/dev.sh
```

Networks, research comparisons, live audits, the analysis CLI, and the AI query executor use SQLite. PostgreSQL deployment requires a separate server and integration check.

## Tests

```bash
bash scripts/install.sh --dev
bash scripts/test.sh
bash scripts/lint.sh
```

Tests cover database integrity, paper/occurrence relationships, metrics, exports, API responses, SQL guards, and snapshot restoration. The bundled data is required for database and API tests.

Ten small, fully enumerated membership fixtures test the conditional working-group model against independent labelled assignments. GitHub Actions also runs the full assessment reproduction and compares all 29 registered tables on Python 3.10 and 3.12. Each run saves its `reproduction.json` record as a workflow artifact.

For live checks, keep the dashboard running in another terminal:

```bash
API_BASE=http://127.0.0.1:8077 python scripts/verify_project.py --require-api
python -m pip install playwright
python -m playwright install chromium
API_BASE=http://127.0.0.1:8077 python -m pytest tests/test_frontend_smoke.py -q
```

The 38 browser tests cover all thirteen dashboard pages, themes, applied export filters, report comparisons, live audits, network controls, and downloaded data and images. GitHub Actions runs the database, API, and script checks.

The 11 October 2026 package passed 290 tests in 107.22 seconds with Python 3.12, plus 50 project checks. A fresh Docker volume passed 68 API and browser tests in 18.19 seconds, plus 24 checks covering snapshot restoration, live analyses, graph exports, shipped frontend assets, and persistent state.

## GitHub distribution

The source repository is [Karcen/ipcc_litdb](https://github.com/Karcen/ipcc_litdb). Clone it or extract a source ZIP, then follow the Quick start commands.

The compressed snapshot is about 70 MiB; the restored database is about 235 MiB and is ignored by Git. Use Git to publish a fork with the snapshot because [GitHub's browser uploader accepts files up to 25 MiB, while regular Git blocks files larger than 100 MiB](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github).

## Troubleshooting

| Symptom | Action |
|---|---|
| Python is older than 3.10 | Install a newer Python and recreate `.venv`. |
| The port is occupied | Use `API_PORT=8090 bash scripts/dev.sh`. |
| Snapshot verification fails | Restore `database/ipcc_litdb.sqlite.gz` and `database/snapshot.json` together from the repository, then rerun installation. |
| The page uses cached assets | Refresh with Ctrl+Shift+R or Cmd+Shift+R. |
| AI analysis is unconfigured | Set `AI_ENABLED=true`, `AI_API_KEY`, `AI_BASE_URL`, and `AI_MODEL` in `.env`, then restart. |
| Review writes are disabled | Set `ALLOW_WRITES=true` and restart. |
| Browser tests skip | Start the dashboard and install Playwright's Chromium browser. |

To restore a missing database without installing dependencies:

```bash
python3 scripts/restore_database.py
```

An existing database is preserved. To replace it with the packaged snapshot, first copy it to a backup, then run `python3 scripts/restore_database.py --force`.

## Attribution and contact

Jiacheng Zheng, Ma Yinchu School of Economics, Tianjin University.

Contact: [karcenzheng@tju.edu.cn](mailto:karcenzheng@tju.edu.cn).

For permission to reuse the original project code, contact the author. Bibliographic records and source documents retain their source terms.

### Third-party licenses

| Bundled component | Version | License | Files |
|---|---|---|---|
| [Apache ECharts](https://github.com/apache/echarts) | 6.1.0 | Apache-2.0 | `frontend/vendor/echarts.min.js`, [license](frontend/vendor/licenses/echarts-LICENSE.txt), [notice](frontend/vendor/licenses/echarts-NOTICE.txt) |
| [Cytoscape.js](https://github.com/cytoscape/cytoscape.js) | 3.34.3 | MIT | `frontend/vendor/cytoscape.min.js`, [license](frontend/vendor/licenses/cytoscape-LICENSE.txt) |

The bundled JavaScript files retain their upstream headers. Check versions and notices when replacing a library.

Network-method review used the [NetworkX guidance in Scientific Agent Skills](https://github.com/K-Dense-AI/scientific-agent-skills/tree/main/skills/networkx). Reference: Kassis, T., Agarwal, V., He, Y., Patel, D., and Brueckner, A. M. (2026). [Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents](https://doi.org/10.48550/arXiv.2609.00065).
