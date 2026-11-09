# IPCC Literature Database — Change Log

## What's new

### 2026-10-30: Portable CJE offline replication

- Added [`cje_replication`](cje_replication/INSTRUCTIONS.txt), with frozen inputs, portable analysis code, 32 current reference tables and 199 numerical definitions. The checked replay reproduces those tables and definitions, and regenerates four figures.
- The workflow uses saved database and bibliographic inputs. It makes no API calls, draws no new sample and creates no review labels. The [replay scope](cje_replication/replay_scope.json) records the excluded obsolete classifier audit and missing historical registry.
- Locked pandas 3.0.1 and NumPy 2.3.5 after a cold-start check identified different missing-year handling in pandas 2.x. The runner rejects incompatible dependencies before computing results; identity rules and frozen results remain unchanged.
- Manuscript sources, article PDFs, author information and full bibliographies remain private. The public module reproduces the numerical results and figures; compiling the article requires the separate local submission package.

### 2026-10-25: Completed model review and frozen offline replay

- Completed the primary DeepSeek review of 5,956 cases and 9,701 reference records, plus 3,944 candidate pairs containing 7,888 records. A separate blinded Codex comparison covers the existing 331-ID, 801-entry sample. The [result registries](revision_preparation/deepseek_validation_v1/reports) retain availability, unresolved labels, raw classifications and separate logical-QC views.
- Three configurations use two DeepSeek model identifiers from one provider family. Codex comparisons measure model agreement; no human gold labels were collected. These outputs do not establish population linkage accuracy or a calibrated error correction.
- The [frozen archive](revision_preparation/deepseek_validation_v1/frozen_review_v1/manifest.json) preserves 25,074 input, call, code and result files in 67 archive files, each at most 640 KiB. [Offline replay](#deepseek-validation-and-offline-replay) verifies source hashes without network access, credentials or new model calls. The [original Mac check](revision_preparation/deepseek_validation_v1/raw_replay_status.json) records 132 direct CSV byte matches and one explicitly guarded historical empty-table serialization adapter.
- Cross-Python CI uses an explicit [portable verification policy](revision_preparation/deepseek_validation_v1/portable_replay_policy_v1.json). Only frozen-SHA, allowlisted floating-point fields receive narrow bounds: weighted fields at most four ULP, and CP95 bounds or Fleiss' kappa at most `1e-12` absolute difference. Labels, integer counts, table structure and other fields remain exact; formatted manuscript claims must match. This verification policy changes no frozen output or scientific calculation.

### 2026-10-25: Initial DeepSeek workflow setup

- Added the separate [`1.5.0` validation workflow](revision_preparation/deepseek_validation_v1/protocol.txt), with three configurations of two DeepSeek model identifiers: Flash with strategies A and B, and Pro with strategy C. The database snapshot, baseline workflow, earlier numerical registries and sampling seed remain unchanged.
- The initial fixed pilot compared blinded references with a separate metadata-visible arm. Full collection was still in progress at this setup stage. Identity, cross-report recurrence, cited-version year and concatenated references have separate outputs.
- Added hash-verified source restoration and offline tests for schema validation, provenance, budgeting, aggregation and sampling calculations. [Preparation and tests](#deepseek-validation-and-offline-replay) use repository files without an API key or model calls. Provisional controls remain separate from human gold labels and population accuracy claims.

### 2026-10-22: Offline release inputs

- Fixed the project check to include the requested model-review protocol and removed machine-specific skill paths from the acquisition and source-allocation scripts. Skill locations use explicit configuration.
- Analysis package `1.4.0` includes nine shared binary chunks for the two identical source-count tables. The restoration command recreates both original files and checks their full SHA-256 hashes. The 22 earlier input archives and all numerical tables retain their original bytes.
- Code, data and replay instructions are public release materials. Manuscript sources, paper PDFs and editorial records remain local.

### 2026-10-21: Completed external benchmarks and chapter uncertainty

- Added analysis package `1.4.0` under [`review_round3`](revision_preparation/review_round3). Original snapshot `v1.2.0`, baseline workflow `v1.2.1`, earlier tables and seed `20261011` are preserved.
- Original-string citation ages now have chapter-composition intervals, unmatched distributions and matching-status decompositions. The full-extraction mean change is −0.123 years, with a 95% chapter-resampling interval of −0.648 to +0.359. The matched change is +0.583 [0.371, 0.798]. These intervals describe recorded chapter composition.
- Completed broad and narrow publication pools for both report windows, with all source pages and metadata for 30,640 source IDs. In the broad journal article/review pool, full D2 increases from 482.11 to 694.03; the equal-size difference is 209.47 [199.18, 220.16]. ISSN-L gives the same full D2. Working-group date screens, first-day exclusions, equal-length age windows and DOI-recovered source labels are also archived.
- Added a probability-sampled recurrence benchmark. The annual-stratified risk ratio is 8.68 (95% design-bootstrap interval 5.71–15.19); adding 2014–2021 citation-count strata gives 3.53 [2.25, 6.38]. These are journal-pool associations on observed common support. Source type is fixed to journal, and the second comparison conditions on later citation attention.
- Added a seeded indirect-link comparator using the same 2,664 AR5 target works on both sides: 196/1,000 pool works (19.60%) and 16,932/32,545 comparable AR6 works (52.03%) have an indexed link to a target. Reference-list coverage is recorded separately.
- Conditional sharing now uses enumerated distributions and direct convolution for tail probabilities. Numeric claim specifications trace displayed values to exact table cells and retain source hashes.
- Restricting both work type and source type to journal articles/reviews gives a matched equal-size D2 increase of 60.084 [56.621, 63.571]. Adding uniquely DOI-resolved unmatched records gives 69.886 [65.941, 73.838]. These comparisons exclude unrecovered records.
- The author stopped further model validation after the pilot; the external data analyses are complete. No full identity census, parser panel, source-type panel or calibrated error correction is reported. Pilot outputs and unsuccessful attempts remain archived in [`panel`](revision_preparation/review_round3/panel); they do not establish accuracy.
- Frozen inputs use 22 deterministic archives and nine binary chunks. The restoration helper recreates both identical source-count tables from the same chunks and verifies their original SHA-256. Local raw files and numerical outputs are preserved.
- Manuscript sources and paper PDFs remain outside this repository. Offline replay commands for this package appear below.

### 2026-10-21: Fixed analysis version and delivery checks

- Analysis package `1.3.0` is frozen at [commit c858167](https://github.com/Karcen/ipcc_litdb/tree/c858167e68c30b1f2d6ab8857f1445e6504f13bb). This is a package version, not a Git tag. It extends baseline workflow `v1.2.1` without changing snapshot `v1.2.0` or the earlier 45 CSVs.
- The independent offline audit checks 89 archived CSVs and 5,506,283 cells. All seventeen OpenAlex tables replay from the response bundles without network access or credentials. The [skills application record](revision_preparation/review_round2/skills_application.json) links this revision to the preserved search, installation and license record.
- Manuscript sources, full bibliographies and paper PDFs remain outside this repository. The age comparison distinguishes the full extraction from the matched subset; the AI proposals remain separate from independent human validation.

### 2026-10-21: Frozen OpenAlex benchmarks and offline replay

- Completed the independent external acquisition and [17-table archive](revision_preparation/review_round2/openalex_v1/activated_attempt_03/results_v1/manifest.json). Preferred-DOI OpenAlex work identities give 12.4649% retention and 4.7908% reuse. Missing and ambiguous matches remain excluded with separate counts.
- Added broad/narrow climate title-and-abstract publication pools, source-ID/ISSN-L comparisons, leading named sources, annual pool-relative ages and group-specific publication-date screens. Source ratios compare compositions; API dates do not establish acceptance dates or deposited date precision.
- Archived indirect continuity and a frozen 300-key citation-survival sample separately from direct recurrence. The raw-DOI indirect result is 17,143/33,357 unique AR6 works. The survival sample contains 282 positive, two zero and sixteen unresolved outcomes; it is not a linkage-accuracy sample.
- Saved complete selected-field API responses and request provenance in 48 deterministic compressed bundles. Cold replay reproduced all 17 tables byte for byte with network and credential functions disabled. The fixed database, old 45 CSV tables and seed remain unchanged. [Restore and replay](#external-openalex-benchmarks) needs no API key. Manuscript files remain private.

### 2026-10-21: Second-round coverage and AI sensitivity analyses

- Added an independent [raw-reference age archive](revision_preparation/review_round2/raw_age_v1/manifest.json) for all extracted AR5/AR6 occurrences, including unmatched references. With assessment publication year as the origin, mean age falls from 5.374923 to 5.252392 years in the full extracted population and rises from 4.373688 to 4.957161 years in the matched subset. The author approved revising the interpretation to reflect this direction change. The fixed snapshot and earlier results are preserved.
- Added [design-weighted AI-label projections](revision_preparation/review_round2/ai_sensitivity_v1/manifest.json), sampling-only intervals and fixed-denominator loss scenarios. These quantify the archived AI proposals under the sample design; they do not measure independent linkage accuracy.
- Added a separate [targeted counterpart-endpoint review](revision_preparation/review_round2/counterpart_review_v1/manifest.json). An explicit AI-proposal acceptance scenario admits 37 edges in 33 components and yields 1,661 shared IDs. It changes no stored identity and is not a corrected estimate or a true recurrence bound.
- Added [missing-outlet envelopes](revision_preparation/review_round2/raw_age_v1/missing_outlet_difference_envelopes.csv) and a [two-panel raw-age ECDF figure](revision_preparation/review_round2/figures_v1/review_round2_age_comparison.pdf), with editable SVG, 600-dpi PNG and saved QA. [Offline replay commands](#second-round-sensitivity-analyses) are below. The original 29 baseline, ten raw-DOI and six AI-descriptive CSVs remain unchanged; the seed is `20261011`.

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
