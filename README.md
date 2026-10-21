# IPCC Literature Database

A searchable database of the works cited in IPCC reports, with a local dashboard for literature search, coverage analysis, citation networks, and reference review. The corrected v1.2.0 snapshot contains 113,594 papers and 160,029 reference occurrences.

This is the second project alongside [IPCC References Toolkit](https://github.com/Karcen/ipcc_refs_toolkit). The toolkit collects and processes references; this repository provides the database and the interface for studying them across reports.

## What's new

### 2026-10-21: AI-led diagnostics

- Refined the source proposals to `ai-initial-review-1.1.1`. The [version ledger](revision_preparation/annotation/ai_review/version_decisions.json) retains verified study grouping and records the unresolved preprint DOI versus journal-metadata combination in one entry for ID 49343.
- Registered six descriptive CSV tables as [`ai-review-descriptive-1.0.0`](revision_preparation/annotation/ai_review_summary_v1/manifest.json). They separate 319 probability-sampled IDs from 12 targeted supplements and describe the AI review of 331 IDs and 801 original entries.
- The author chose to proceed with the AI-led review. The sample has no independent human adjudication, and the release contains no accuracy or recall estimate. The optional annotation tools remain available.
- A fresh v1.2.1 reproduction matched all 29 registered tables by parsed CSV values. The ten raw-DOI tables, database and seed `20261011` remain unchanged. [Reproduction commands](#reproduce-assessment-analyses) and the [summary checker](#ai-led-diagnostic-summary) are documented below.

### 2026-10-21: Study-version rules

- Updated the [AI review proposals](revision_preparation/annotation/ai_review/proposals.json) to `ai-initial-review-1.1.0`. The [version ledger](revision_preparation/annotation/ai_review/version_decisions.json) records source checks for three working-paper, preprint and journal-version relationships.
- Applied the author-authorized rules: group verified versions of the same study and use the year of the version actually cited for citation age. The frozen database and registered analysis results remain unchanged.
- Updated the annotation interface, proposal exports and checks. All 42 annotation tests passed. The release kept individual judgments separate from author confirmation.

### 2026-10-20

- Added initial AI proposals for all 331 sampled IDs and 801 original entries, with source links, a review priority queue and offline evidence-replay tools.
- Added a separate AI review panel to the [annotation workspace](#manual-annotation). Draft import fills blank fields, preserves existing annotations and leaves human review completion pending.

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

- [What's new](#whats-new)
- [Data and coverage](#data-and-coverage)
- [Dashboard](#dashboard)
- [Repository layout](#repository-layout)
- [Configuration](#configuration)
- [Database model](#database-model)
- [Analysis](#analysis)
- [Reproduce assessment analyses](#reproduce-assessment-analyses)
- [Manual annotation](#manual-annotation)
- [AI-led diagnostic summary](#ai-led-diagnostic-summary)
- [Additional raw-DOI checks](#additional-raw-doi-checks)
- [Verified references and revision preparation](#verified-references-and-revision-preparation)
- [Exports and API](#exports-and-api)
- [Sources and provenance](#sources-and-provenance)
- [Data quality and review](#data-quality-and-review)
- [Snapshot corrections](#snapshot-corrections)
- [PostgreSQL](#postgresql)
- [Tests](#tests)
- [GitHub distribution](#github-distribution)
- [Troubleshooting](#troubleshooting)
- [Attribution and contact](#attribution-and-contact)

## Data and coverage

The original bibliographic snapshot was built on 6 October 2026. Version v1.2.0 applies targeted record corrections dated 11 October 2026 and refreshes the derived analyses. Counts below describe the corrected snapshot; the dashboard queries the working database for current values.

| Measure | Count |
|---|---:|
| Report entries | 36 |
| Working-group entries | 43 |
| Chapters and other citable units | 677 |
| Units with reference occurrences | 451 |
| Source PDFs registered | 438 |
| Papers | 113,594 |
| Reference occurrences | 160,029 |
| Occurrences matched to Web of Science | 91,814 |
| Unmatched occurrences | 68,215 |
| Web of Science metadata records | 61,264 |
| Papers cited in multiple chapters | 24,061 |
| Papers cited in multiple reports | 12,880 |
| Data-quality flags | 35,300 |
| Open review cases | 1,720 |

The occurrence-level Web of Science match rate is 57.37%. At paper level, 91,398 works carry either `wos_matched` or `doi_verified`, giving a combined workflow-label share of 80.46%. The builder assigns `doi_verified` to a syntactically valid DOI without a WoS record. It does not independently resolve that DOI or validate its correspondence to the printed reference. These measures use different denominators.

| Report | Matched occurrences | Total occurrences | Match rate |
|---|---:|---:|---:|
| AR6 | 47,049 | 63,027 | 74.6% |
| AR5 | 17,593 | 27,851 | 63.2% |
| AR4 | 5,855 | 16,193 | 36.2% |
| AR3 | 1,537 | 8,292 | 18.5% |
| AR2 | 43 | 8,250 | 0.5% |
| AR1 | 2 | 1,551 | 0.1% |
| SROCC | 4,957 | 6,309 | 78.6% |
| SRCCL | 5,097 | 6,712 | 75.9% |
| SR15 | 3,976 | 5,262 | 75.6% |
| SREX | 1,955 | 4,816 | 40.6% |
| SRREN | 1,483 | 3,995 | 37.1% |
| 2019REF | 1,205 | 2,626 | 45.9% |
| 2006GL | 310 | 1,338 | 23.2% |
| WETLANDS | 545 | 1,026 | 53.1% |
| SRCCS | 186 | 1,016 | 18.3% |
| SRES | 8 | 325 | 2.5% |

The collection covers assessment reports AR1 through AR6, synthesis reports, special reports, and greenhouse-gas inventory guidelines and supplements. Report entries with no extracted references remain in the catalogue.

## Dashboard

| Page | Use |
|---|---|
| Overview | Inspect totals, open review cases, citation distributions, publication years, and widely cited works. |
| Assessment reports | Browse the report hierarchy; inspect printed references and source pages. |
| Literature | Search by title, DOI, report, working group, year, and verification status. |
| BI explorer | Compare a metric across a dimension and inspect the SQL behind the result. |
| Research comparison | Compare two report literature cohorts, publication years, journals, and shared works. |
| Analytics | Open stored analysis results, parameters, and queries. |
| Automated insights | Read findings with their supporting data. |
| Networks | Explore shared literature and co-authorship; inspect communities, centrality, and connected components. |
| Data quality | Inspect stored flags and run live completeness, identifier, relationship, count, and year-consistency checks. |
| Unresolved | Review unmatched references or select the open review queue across all match statuses; record decisions. |
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
├── revision_preparation/   # Annotation app, additional analyses, citation records
├── changes.md              # Revision changes and verification status
├── reviewer_prep.md        # Anticipated reviewer questions and conditional replies
├── author_requirements.md  # Current review status and remaining evidence limits
├── screenshots/
├── tests/
└── .github/workflows/      # Automated checks
```

The working SQLite file, virtual environments, credentials, caches, and macOS sidecar files are excluded from Git. This README contains the toolkit documentation. The three revision documents record changes, anticipated reviewer questions and the current evidence limits. Third-party license texts remain separate text files.

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
| `data_quality_flags` | Record-level quality checks and targeted review flags. |
| `manual_review`, `change_log` | Review decisions and edit history. |
| `collection_tasks` | Assignment records, empty in this snapshot. |
| `analysis_runs`, `analysis_results`, `insights` | Stored analyses and findings. |
| `build_info` | Snapshot build metadata. |
| `chapter_references` | Chapter-to-paper associations used by analysis. |

The original builder linked paper identities by exact normalized DOI, then normalized title and year. DOI-less occurrences could attach to an existing DOI paper when the title matched and the publication years differed by at most one. Version v1.2.0 applies the explicit identity and metadata changes in the [correction ledger](reproducibility/corrections/2026-10-11.json). The `identity_method` field records the rule used for each paper, and `change_log` records the corrected fields.

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
a887a535947ae3b9bab7656070cc038eb3a7a5161494afcb2c3df2245f5ff0ec
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
| Conditional reuse | Shared eligible matched IDs divided by all later-report matched IDs within the selected publication-year cutoff. All-extracted and DOI-status sensitivities use their respective cohorts. Cutoffs 2012, 2013, and 2014 are reported separately. An advance-online article can precede its preferred print year. |
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
| AR5–AR6 matched-ID overlap | 1,657 shared IDs among 13,503 AR5 and 34,869 AR6 IDs; AR5 retention 12.27%, AR6 reuse 4.75%. |
| Symmetric earlier-report presence | 1,658 AR6 matched IDs appear anywhere in AR5. Of 4,160 IDs published by 2013 and absent from the AR5 matched cohort, one is present in its unmatched cohort. |
| Publication-year-eligible reuse | 34.16%, 28.40%, and 20.47% at cutoffs 2012, 2013, and 2014. |
| Unresolved chronology flags among shared IDs | 38 AR5–AR6 shared matched IDs have 85 open flagged occurrences across all reports. All 38 have preferred years through 2013. |
| Fixed-denominator chronology stress | Treating all 38 flagged IDs as false shared identities gives retention 11.99%, reuse 4.64%, and conditional reuse through 2013 of 27.75%. Original cohort denominators remain fixed. |
| Occurrence-weighted age | Median 4 years in both reports; mean increases from 4.434 to 5.139 years. The 0–5-year fraction decreases from 71.21% to 62.79%. |
| Equal-ID age | Median increases from 4 to 5 years; mean increases from 4.513 to 5.306 years. |

Age uncertainty tables describe stability under chapter resampling within this fixed collection. Outlet subsampling compares reports at the same number of IDs. These intervals retain the saved record links and metadata. The working-group null preserves journal/vintage margins, group counts, and each ID's number of working-group memberships. It tests how fixed sharing is allocated across pairs. It does not test whether total sharing exceeds a climate-literature population benchmark.

Outlet sensitivities compare the preferred labels, conservative punctuation aliases, and registered WoS source titles. The local snapshot and upstream exports lack usable ISSNs. The alias crosswalk records exactly which strings were combined. Named-outlet concentration and richness have separate results; outlet labels do not assign disciplines.

The corrected review worksheet contains 331 IDs and 801 printed entries: 319 probability-sampled IDs and 12 targeted anomaly IDs. Codex completed an initial source-based review and retained its case judgments separately from the empty human-label files. The [descriptive registry](revision_preparation/annotation/ai_review_summary_v1/manifest.json) reports unweighted AI counts for the two selections. The author chose to proceed without further human annotation. Population linkage accuracy and recall remain unestimated. The separate correction ledger records earlier AI-assisted comparisons by Codex (ChatGPT 6.1 Sol). The authors report checking primary sources for all eleven nominated historical correction cases. Those checks and the original informal spot checks do not constitute independent adjudication of the sampled IDs.

## Manual annotation

Manual annotation is optional for the current AI-led diagnostic report. The workspace retains 319 probability-sampled IDs and 12 targeted supplements, with 801 original entries from AR5 and AR6. The probability sample has 33 strata and comes from the 46,715-ID union of the matched sets. The frozen sample, source strings and sampling weights remain unchanged. Human-label files are empty. The instructions below support a future independent annotation exercise.

Restore the database through Quick start, then launch the workspace from the repository root:

```bash
revision_preparation/annotation/launch_annotation.command
```

The launcher selects Python 3.10 or later from the project environment or an available installation. On macOS, the same `.command` file can be opened in Finder. On Linux or WSL, an explicit Python command also works:

```bash
python3 revision_preparation/annotation/local_annotation_app.py
```

Open [the annotation workspace](http://127.0.0.1:8765/). The server uses the local machine's loopback address. It opens the saved database read-only. Original entries and preferred metadata appear side by side, with source-page and DOI links. Database searches cover preserved raw references as well as preferred DOI, title and author fields. To select another snapshot or port, pass `--db /path/to/database.sqlite` or `--port 8766`. Stop the server with Ctrl+C.

Read the [annotation rules](revision_preparation/annotation/annotation_rules.txt). Assign a document group to each original entry. Record the within-ID identity decision, cross-report sharing decision and occurrence metadata judgment separately. Then search for the same work under other IDs and record the queries, candidates and evidence. A false merge can still contain a genuinely shared work. Finding no duplicate during the specified search describes that search result, rather than global linkage recall.

Edits save automatically in `revision_preparation/annotation/work/`. Wait for **Saved locally** before closing the page. **Save and next ID** saves the current record before navigation. **Check annotations** reports missing or inconsistent decisions without changing them. **Export files** downloads the ID CSV, occurrence CSV and complete work JSON. Back up the work directory or exported files while annotating. Work files and generated estimate directories are excluded from Git.

### AI-assisted initial review

[Initial proposals](revision_preparation/annotation/ai_review/proposals.json), version `ai-initial-review-1.1.1`, cover all 331 sampled IDs and 801 original entries. Codex read the frozen references, compared stored metadata and inspected source-page evidence and selected local cited-paper PDFs. All 76 sampled IPCC PDFs match the snapshot hashes. In the probability sample, the review proposes mixed-work merges for 11 IDs and counterpart records for 40 IDs; 63 IDs have unresolved metadata judgments. Its entries include 120 metadata-error proposals and 111 unresolved judgments. The targeted supplement has four mixed-work proposals, one ID with a counterpart proposal and four IDs with unresolved metadata; eight entries have metadata-error proposals and five remain unresolved. The [descriptive registry](revision_preparation/annotation/ai_review_summary_v1/manifest.json) records these unweighted counts separately. Every review retains `review_complete=no`, and every duplicate search remains incomplete. No independent human adjudication was performed for this sample.

The **AI-assisted initial review** panel shows each proposal, its reasons, occurrence judgments and candidate citations. Use the issue and uncertainty filters to find cases that need attention. **Use as draft for this ID** copies suggestions into blank fields. Existing annotations stay in place. Check the original entries and sources, resolve open questions, and enter your actual reviewer code before marking a review complete. The browser and estimator reject completed reviews attributed to Codex or an AI reviewer. The saved notes retain the AI assistance disclosure.

The [priority queue](revision_preparation/annotation/ai_review/priority_queue.csv) lists cases for source review. The [source index](revision_preparation/annotation/ai_review/source_index.csv) records original-string hashes, PDF hashes and both page conventions. Stored page indices start at zero; browser PDF pages start at one. A compound extraction can continue beyond its recorded starting page. Working-paper, preprint and journal-version decisions remain unresolved where the available evidence does not establish equivalence.

On 21 October 2026, the author authorized two rules: group publication versions after checking their study identity, and use the year of the version actually cited for citation age. Codex checked IDs 33960, 49343 and 102897 against sources in [the version ledger](revision_preparation/annotation/ai_review/version_decisions.json). The author authorized the convention; individual case judgments remain AI-assisted. ID 33960's working-paper occurrence has a proposed cited-version year of 2004, compared with the preferred 2008 journal year. For ID 49343, the verified preprint-to-article relationship supports grouping. One entry combines a preprint DOI with journal-version fields, so its DOI metadata judgment remains unresolved in version 1.1.1. The snapshot and registered results remain unchanged.

The same suggestions are available as [ID proposals](revision_preparation/annotation/ai_review/id_proposals.csv) and [occurrence proposals](revision_preparation/annotation/ai_review/occurrence_proposals.csv) for spreadsheet review. They retain all original fields and weights, identify Codex as the reviewer and leave review completion at `no`. Author annotations continue to save separately in `work/`.

Check the proposal package and the unchanged 29 registered tables:

```bash
python3 scripts/check_ai_annotation_proposals.py
```

After a sourced update to the saved proposals, `python3 scripts/refresh_ai_review_exports.py --write` refreshes their derived CSVs and manifest hashes. It exports the recorded judgments and leaves human annotations untouched. Run the proposal checker afterwards.

Rebuild offline retrieval evidence from the restored snapshot in a fresh output directory:

```bash
python3 scripts/build_offline_annotation_candidates.py \
  --repo . --output revision_preparation/annotation/work/ai_evidence/retrieval_replay
```

Source-page extraction also requires `pdftotext` and a local workspace containing the original `input/raw/` PDFs. Existing `output/papers/` PDFs and enrichment caches are optional:

```bash
python3 scripts/extract_annotation_source_pages.py \
  --repo . --workspace "/path/to/IPCC Database" \
  --output revision_preparation/annotation/work/ai_evidence/source_pages_replay.json
```

These commands reconstruct retrieval and page evidence. They preserve the frozen source strings and do not adjudicate identities or create accuracy estimates. The archived [retrieval log](revision_preparation/annotation/ai_review/retrieval_log.json.gz) records query parameters and uncapped results. [Review scope](revision_preparation/annotation/ai_review/review_scope.txt) describes the inspected evidence and unresolved questions.

### AI-led diagnostic summary

The [descriptive registry](revision_preparation/annotation/ai_review_summary_v1/manifest.json) contains six CSV tables: ID categories, occurrence categories, their cross-tabulation, per-ID diagnostics, flagged IDs and metadata-field counts. [The summary](revision_preparation/annotation/ai_review_summary_v1/summary.json) defines each category. The two sample selections remain separate.

| AI-coded quantity | Probability sample | Targeted supplements |
|---|---:|---:|
| IDs / original entries | 319 / 771 | 12 / 30 |
| Mixed-work merge proposals | 11 | 4 |
| Same-study sharing proposals | 94 | 6 |
| False-sharing proposals | 0 | 0 |
| IDs with a proposed metadata error | 71 | 6 |
| IDs with an unresolved metadata judgment | 63 | 4 |
| IDs with counterpart proposals | 40 | 1 |
| Entries with correct metadata proposals | 540 | 17 |
| Entries with metadata-error proposals | 120 | 8 |
| Entries with unresolved metadata | 111 | 5 |

Sources: [ID counts](revision_preparation/annotation/ai_review_summary_v1/ai_review_id_category_counts.csv), [entry counts](revision_preparation/annotation/ai_review_summary_v1/ai_review_occurrence_category_counts.csv) and [cross-tabs](revision_preparation/annotation/ai_review_summary_v1/ai_review_id_cross_tabs.csv). Categories can overlap within an ID. A compound extraction can include several works and still preserve a genuine AR5–AR6 shared work. The absence of a false-sharing proposal supplies no population precision estimate. Unequal sampling probabilities and incomplete duplicate searches preclude interpreting these unweighted counts as population error rates or recall.

Replay the summary without API access:

```bash
python3 scripts/summarize_ai_annotation_proposals.py --check
```

The generator validates the source package, frozen sample, empty human-label templates, database hash and all 29 baseline plus ten raw-DOI table hashes. It uses seed `20261011` as the inherited sample identifier and performs no random draws. A clean-checkout replay reproduces the summary byte for byte. The summary changes no identity, raw entry, human label or headline estimate.

### Optional human-label estimates

If independent annotation is undertaken later, run the integrity check against its saved labels:

```bash
python3 revision_preparation/annotation/annotation_tools.py validate \
  --id-labels revision_preparation/annotation/work/id_annotations.csv \
  --occurrence-labels revision_preparation/annotation/work/occurrence_annotations.csv \
  > revision_preparation/annotation/work/integrity.json
```

The report checks expected keys and counts, unchanged source fields and weights, allowed labels, missing judgments and duplicate consistency. Incomplete or invalid input returns a nonzero exit code. It does not repair labels. Review the listed issues before continuing.

That optional estimator requires all 331 ID reviews, 801 occurrence judgments and duplicate searches to be complete before generating estimates in a fresh directory:

```bash
python3 revision_preparation/annotation/annotation_tools.py estimate \
  --id-labels revision_preparation/annotation/work/id_annotations.csv \
  --occurrence-labels revision_preparation/annotation/work/occurrence_annotations.csv \
  --output revision_preparation/annotation/outputs/review_01
```

Missing, unresolved or inconsistent labels produce an integrity report and error exit, with no rate or sensitivity tables. Successful estimation registers `annotation_rates.csv`, `annotation_strata.csv` and `annotation_recurrence_stress.csv` with JSON provenance and file hashes. Probability-sample estimates use the recorded inclusion weights. Targeted supplements have separate descriptive counts. The estimator measures false merges, identity precision, detected missed merges and ID-level metadata errors. Its 95% intervals invert finite-population hypergeometric tails within strata and combine conservative stratum bounds. Zero observed errors in a noncensus stratum retain a positive upper error bound.

The optional within-ID and metadata audit covers the exported AR5/AR6 entries. Other-report occurrences are outside its scope. Detected missed-merge rates would describe the recorded search protocol. Shared-identity sensitivity would hold the original cohort denominators fixed. [The pending registry](revision_preparation/annotation/pending_registry.json) records these unused planned outputs. [The initial integrity report](revision_preparation/annotation/initial_integrity_report.json) records consistent source keys and empty human decisions. The current AI-led workflow uses the descriptive registry above and produces no human-label accuracy estimates.

## Additional raw-DOI checks

These analyses construct identities from usable DOI tokens in original reference text across all matching statuses. They use printed years for cited-work age and annual eligibility. They compare common publication windows, working groups, occurrence weights, equal-DOI weights and equal-group weights. The method therefore selects entries without using the Web of Science match decision. Raw DOI availability still differs between reports.

Complete the 29-table baseline reproduction first, using the command under [Reproduce assessment analyses](#reproduce-assessment-analyses). Then run:

```bash
python3 revision_preparation/robustness/coverage_robustness.py \
  --repo . \
  --baseline-reproduction analysis-reproduction \
  --output-dir coverage-reproduction

python3 revision_preparation/robustness/verify_registered_outputs.py \
  --output-dir coverage-reproduction \
  --reference-dir revision_preparation/robustness/output_v1 \
  --report coverage-reproduction-check.json
```

Choose fresh output and report paths. The extension verifies the input database, baseline code and all 29 baseline tables before generating ten additional tables. It uses NumPy 2.2.6, 2,500 chapter resamples and seed `20261011`. The [separate manifest](revision_preparation/robustness/output_v1/manifest.json) records units, filters, code hashes, exclusions and the ten table hashes. The verification command compares each generated table with this registry. The original baseline registry remains unchanged.

| Raw-DOI check | Registered result |
|---|---|
| Usable single DOI in original entries | AR5: 3,124 entries, 11.22% of extracted entries; AR6: 45,605 entries, 72.36%. |
| Common 2000–2013 publication window | 355 shared DOI keys among 2,721 AR5 and 5,230 AR6 keys; retention 13.05%, reuse 6.79%. |
| Reuse eligible through 2013 | 364 shared eligible keys among 5,597 eligible AR6 keys, or 6.50%. |
| Occurrence-weighted mean age | 4.52 years in AR5 and 5.14 years in AR6; the difference interval is −0.26 to 1.49 years. |
| References aged zero through five | 74.67% in AR5 and 64.92% in AR6; the change is −9.75 percentage points, with an interval of −16.01 to −2.96. |

The common-window recurrence still shows substantial replacement. The recent-reference share decreases. The pooled mean-age interval includes zero. Raw-DOI conditional reuse of 6.50% and matched-ID conditional reuse of 28.40% describe different selected populations and year rules. Their difference is a measurement sensitivity, rather than a calibrated correction. These checks do not supply an external climate-literature pool or independent outlet metadata. [The method record](revision_preparation/robustness/methods.txt) describes the selection and interval scope.

## Verified references and revision preparation

The [six BibTeX additions](revision_preparation/literature/references_additions.bib) cover Price's citation networks, Burton and Kebler's literature half-life, Bradford's source scattering, Hill numbers, Simpson diversity and Jost's entropy interpretation. Each DOI and title was checked against a successful Crossref work record. The [JSON ledger](revision_preparation/literature/reference_verification.json), [CSV ledger](revision_preparation/literature/reference_verification.csv) and retained API responses record the decisions and source hashes. OpenAlex rate-limit failures are recorded separately. The [verification report](revision_preparation/literature/verification_report.txt) explains rejected candidates and Bradford's 1985 reprint metadata.

Check the archived reference records without network access or file changes:

```bash
python revision_preparation/literature/check_archived_verification.py
```

The inclusive zero-through-five-year share is this analysis's operational definition. Price's historical immediacy discussion supplies context; it does not establish this exact boundary.

[Changes](changes.md) maps the revision work to the requested tasks. [Reviewer preparation](reviewer_prep.md) contains eight anticipated comments and evidence-based response outlines. [Author requirements](author_requirements.md) records the decision to proceed with AI-led diagnostics and the remaining evidence limits. The submitted and revision manuscripts, complete bibliographies and compiled PDFs remain outside this public repository. Public files contain tools, empty human-label templates, AI proposals, descriptive summaries, analysis code, registered results and verification records.

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
| `GET /api/unresolved` | Unmatched references and review status; `review_queue=true` selects open review cases across match statuses. |
| `GET /api/export/unresolved.csv?review_queue=true` | Export the selected open review queue; also accepts JSON, XLSX, BibTeX, and RIS. |
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

Snapshot provenance paths are relative to the source workspace. Every occurrence ID, chapter, source, page and printed reference remains unchanged. Corrected paper identities and bibliographic fields are recorded in the versioned ledger and database change log. `database/snapshot.json` records the packaged file hashes and the original snapshot digest.

### Manual Web of Science export convention

Upstream exports use names such as `AR6WG1CH1T1.txt`, `AR6WG1CH1T1.xlsx`, and `AR6WG1CH1T1Z.txt`. `AR6`, `WG1`, and `CH1` identify the report, working group, and chapter; `T1` identifies the first export batch.

The collection workflow searches by title through institutional Web of Science access, adds matching works to a marked list, and exports batches in plain text, Excel, and tab-delimited formats. Plain text and tab-delimited exports use the full record with cited references; Excel uses the full record. Keep export batches with the upstream collection files.

## Data quality and review

Match rates describe the collection run and its bibliographic coverage. Older assessments have fewer matched references. The collection's Starter API probes also returned fewer records for publication years before 1998; those probes identify a coverage issue to investigate with additional metadata sources. Unmatched records include books, government reports, and other works requiring manual verification.

| Flag | Count |
|---|---:|
| Missing DOI | 26,236 |
| Missing journal | 5,457 |
| Matched-DOI chronology review | 1,728 |
| Possible duplicate title | 974 |
| Missing title | 379 |
| Missing author | 284 |
| Missing year | 114 |
| Invalid DOI | 103 |
| Suspicious year | 24 |
| Compound extraction block | 1 |

The completeness and duplicate flags come from the original collection run. Version v1.2.0 adds chronology and compound-extraction flags. Live completeness counts both NULL and blank fields: 4,169 reference occurrences lack a publication year, giving 97.39% year completeness.

Run the live audit to recalculate missing fields, DOI syntax, normalized DOI duplicates, parent-child relationships, stored citation counters, and year consistency. Each of its 21 checks records the affected grain, count, denominator, percentage, query, and bounded sample. Report-level completeness includes zero-reference reports with null percentages. A malformed DOI is a syntax finding; identifier existence requires a registry lookup.

Two year checks identify citations for review: a preferred paper year later than the source's unambiguous publication year, and a first printed year token that differs from the preferred metadata. Review the printed citation, source page, and linked record to resolve these candidates. Printed references can contain several dates, editions, and numbers that resemble years. The audit reads the current database and provides a JSON download with its evidence and query provenance.

Stored duplicate flags and live duplicate analyses use different candidate rules. The original collection audit in `data/processed/` identified 974 possible duplicate-title groups for inspection. These retained files describe the original builder output; corrected live analyses read the v1.2.0 database.

Two extractions of AR6 WG2 Annex I share 35 papers. The stored duplicate-unit analysis also identifies overlapping AR4 synthesis-report extractions. Both sets remain available for source comparison. Forty-eight units carry `qc_warn` extraction status.

The original deduplication audit recorded 756 papers with publication-year conflicts and 1,054 with title variants. Compare the printed citations and source pages when reviewing these candidates.

The Unresolved page opens unmatched references by default. Select **Open review queue** to show distinct occurrences with an `open` or `in_progress` review, including matched DOI candidates. The corrected snapshot has 1,720 open cases: 1,719 chronology candidates and one compound extraction block. Applied report, chapter, working-group, match-status, text-search and DOI-presence filters carry through to queue exports. The overview and Data quality page display **Open review cases** separately from unmatched-reference counts.

CSV, JSON and XLSX queue exports include the review status and reason. Their provenance records `review_queue=true`. BibTeX and RIS retain their bibliographic formats.

The queue records review status, assignee, reason and notes. Reference edits require a reason and create a change-log entry with the actor, timestamp, previous value, and new value. Back up the working database before replacing it: review history and later analysis runs are stored there.

The quality score is mean title, author, year, journal, and DOI completeness minus half the unresolved rate. Check component rates and flagged records when interpreting it.

Further collection work includes source-level duplicate review, publication-year checks, metadata verification for early reports, and recording researcher assignments. Shared deployment also needs a hosting location and an access-control arrangement.

## Snapshot corrections

The v1.2.0 snapshot follows AI-assisted source inspection and identity correction by Codex (ChatGPT 6.1 Sol). It compared the original references with publisher and institutional records. The [field ledger](reproducibility/corrections/2026-10-11.json) lists each old and new value, affected occurrence and evidence URL. The [original occurrence export](reproducibility/corrections/original_occurrences.csv) retains the inspected entries. The database records 46 resolved source-inspection decisions. Its 372 field changes retain the actor `Codex: targeted primary-source inspection`. The authors report that they checked the primary sources for all eleven nominated cases. The historical ledger retains the original Codex execution records.

The 11 nominated shared IDs cover five false mergers (`26271`, `26560`, `28296`, `31985`, `35229`), two valid shared identities with incorrect metadata (`28347`, `29105`), one wrong enrichment and duplicate identity (`35431`), and three valid advance-online articles (`32928`, `33835`, `35438`). The three advance-online articles were available in 2013 and printed in 2015. Their identities remain linked. The original AMPERE article assigned to `35431` joins existing ID `70675`; its verified online date is 11 March 2014 and its preferred print year is 2015.

The repair also separates an additional Klein chapter occurrence from ID `17259` and restores that ID's remaining original reference. It merges `7174` into the corrected Crutzen and Stoermer identity `28347`. A compound reference block under ID `104503` remains unchanged and has an open extraction-review case. One AR6 Fedrigo reference prints another article's DOI. Its derived DOI is corrected using the cited authors, pages and Wiley record; the printed DOI stays in the raw reference.

All 160,029 occurrence IDs and their original reference text, chapter, source and page are preserved. The corrected corpus has 113,594 paper identities. Matched AR5–AR6 overlap changes from 1,664 to 1,657 IDs; retention changes from 12.32% to 12.27% and reuse from 4.77% to 4.75%. The registered tables use the corrected snapshot.

The original snapshot remains available in [commit `6676e49`](https://github.com/Karcen/ipcc_litdb/tree/6676e496f30975ec95fdde514816639458de1cfe). Its restored SQLite SHA-256 is:

```text
faa7fc711ae4607acfa9833739ec8f202f8aefd6d929b474a12fceb589c5b85d
```

Replay the correction after installing the project dependencies. Choose a fresh directory:

```bash
mkdir snapshot-replay
git show 6676e49:database/ipcc_litdb.sqlite.gz > snapshot-replay/original.sqlite.gz
gzip -dc snapshot-replay/original.sqlite.gz > snapshot-replay/original.sqlite
python scripts/correct_record_links.py \
  --source snapshot-replay/original.sqlite \
  --destination snapshot-replay/corrected.sqlite \
  --corrections reproducibility/corrections/2026-10-11.json \
  --report snapshot-replay/correction-report.json
```

The script checks the original digest and each expected old value. It validates the corrected foreign keys, chapter crosswalk, paper counters, unique DOIs and raw-occurrence digest. It refreshes the cached analyses and writes the corrected digest and checks to the report. Existing inputs and output files are preserved.

The chronology rule queues `matched_doi` occurrences whose preferred year exceeds the source's unambiguous publication year or differs from the first printed year by more than one. The parser accepts year suffixes such as `2013a`. The snapshot contains 1,728 chronology flags. Nine have documented targeted decisions; 1,719 remain open. Verified advance-online dates resolve several flagged cases. Historical targeted inspections and the later AI-led sample review have separate records. Human sample annotations remain empty, and the release contains no annotation-derived accuracy estimates.

Analysis version [v1.2.1](https://github.com/Karcen/ipcc_litdb/tree/v1.2.1) adds an explicit census of unresolved chronology flags among AR5–AR6 shared matched IDs. The database remains the v1.2.0 snapshot with the same SHA-256 digest. The selection uses `open` or `in_progress` reviews with reason `matched_doi_chronology_review`, joins by occurrence ID, and counts each occurrence once across all reports. It selects 38 shared IDs and 85 flagged occurrences. Of these occurrences, 49 are in AR5 or AR6 and 36 are in other reports. The AR5/AR6-only subset spans 29 IDs. The exported detail table records each flagged occurrence's report, preferred year and review status.

All 38 selected IDs have preferred years through 2013. A fixed-denominator stress calculation removes them from the shared numerator: 1,619/13,503 gives 11.99% retention and 1,619/34,869 gives 4.64% reuse. The corresponding conditional rate is 1,612/5,810, or 27.75%. This is the maximum loss of shared identity within the selected set under fixed cohort denominators. It leaves the database unchanged. Source adjudication determines which flagged identities need correction; the stress calculation measures their possible numerical effect. The registered [stress table](reproducibility/reference/revision_identity_flagged_sensitivity.csv) reproduces this calculation. The workflow also generates `results/revision_identity_open_shared_chronology_details.csv`, with one row per flagged occurrence, and `results/revision_identity_summary.json`, with the selection queries and report-level counts.

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

Correction tests cover expected-value preconditions, immutable raw occurrences, replay determinism, identity splits and merges, author relationships, bibliographic year decisions, review-queue selection, and snapshot counters.

## GitHub distribution

The source repository is [Karcen/ipcc_litdb](https://github.com/Karcen/ipcc_litdb). Clone it or extract a source ZIP, then follow the Quick start commands.

Before each GitHub update, add a dated entry to [What's new](#whats-new) describing the changes and the data or analysis version involved. Keep the newest entries first and retain the earlier entries. Link the relevant checks and provenance records.

The compressed snapshot is about 70 MiB; the restored database is about 238 MiB and is ignored by Git. Use Git to publish a fork with the snapshot because [GitHub's browser uploader accepts files up to 25 MiB, while regular Git blocks files larger than 100 MiB](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github).

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
