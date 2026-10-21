# Revision changes

Initial preparation: 12 October 2026. Latest update: 21 October 2026. Baseline analysis: [v1.2.1](https://github.com/Karcen/ipcc_litdb/tree/v1.2.1). The database remains the corrected v1.2.0 snapshot. Manuscript revisions remain private; the submitted manuscript is preserved.

The author chose AI-led source review and declined further human annotation. Human-label files remain empty. The current workflow combines the original unweighted AI diagnostics with independent design-weighted AI-label projections and explicit scenarios. It supplies no independent population linkage-accuracy or recall estimate. Optional independent annotation tools remain available. Phase-1 statements about unweighted-only reporting describe that earlier stage.

## Phase 2: second major-revision review, 21 October 2026

The [second-round README section](README.md#second-round-sensitivity-analyses) records completed offline work. Each extension has its own code and archive; the outputs were not preregistered. External acquisition and literature-pool work have separate status and are not claimed complete by this entry.

Review issues 3 and 6, selection and age origins: add `scripts/review_round2_raw_age.py` and [raw-age archive](revision_preparation/review_round2/raw_age_v1/manifest.json). Apply one leading-reference-year rule to all extracted and matched occurrences, with publication-year and cutoff-year clocks, occurrence and equal-ID weights, group-specific summaries, full ECDFs and explicit exclusions. Retain all original strings. Add fractional working-group composition and an exact symmetric Kitagawa decomposition for mean age and the at-most-five-year share. The single-group decomposition population is recorded separately from pooled All.

### Author-approved change in the age conclusion

The all-extracted raw-year mean falls from 5.374923 to 5.252392 years using publication-year origin. The matched raw-year mean rises from 4.373688 to 4.957161 years. The respective changes are −0.122531 and +0.583473 years. The author approved revising the main interpretation to reflect this difference. The original matched preferred-year result remains unchanged as a selected-cohort comparison. The code was not adjusted to preserve the earlier general age narrative. [The difference table](revision_preparation/review_round2/raw_age_v1/raw_age_differences.csv) contains the two clocks, weights and working-group results. The recent-reference share falls in both populations.

Review issues 3 and 6, missing outlet labels: add fixed-population label bounds and [difference envelopes](revision_preparation/review_round2/raw_age_v1/missing_outlet_difference_envelopes.csv). Under conservative aliases, the D2 outer envelope is −140.624170 to +211.358702. Its crossing of zero limits the dominance conclusion to known labels. It is not a confidence interval, sharp joint region, external source-supply comparison or evidence of assessment-wide diversification.

Review issue 5, sampling and AI labels: add `scripts/review_round2_ai_sensitivity.py` and the [separate archive](revision_preparation/review_round2/ai_sensitivity_v1/manifest.json). Project five AI-label outcomes with the fixed sampling weights. Invert finite-population tails with conservative simultaneous stratum bounds; keep targeted counts separate. Add exact one-sided stratified zero-event limits and fixed-denominator numerator-loss scenarios. The false-merge proposal fraction is 1.58%, with a sampling-only 95% interval of 0.04–45.05%. This is not linkage accuracy; AI classification error and incomplete search remain unquantified. The unweighted phase-1 archive is preserved.

Review issue 5, counterpart scope: link all directed candidate edges to actual report memberships and statuses. Add `scripts/review_round2_counterpart_review.py` with a [targeted endpoint archive](revision_preparation/review_round2/counterpart_review_v1/manifest.json). The canonical-only scenario accepts no whole-ID edge. The separate endpoint AI scenario accepts 37 edges in 33 components, yielding 1,661 shared matched identities, retention 12.3019% and reuse 4.7649%. These are unvalidated proposal-acceptance scenarios, not corrections or true recurrence bounds. Compound nodes remain blocked; raw strings, occurrence statuses, canonical proposals and human work remain unchanged.

Review issues 6 and 8, figures and reporting: add a portable raw-age ECDF renderer, three final [figure formats](revision_preparation/review_round2/figures_v1/manifest.json), separate optional dependency pins, data/code/font hashes and layout/text/collision audits. Main axes retain full-distribution normalization when zoomed to 0–30 years; insets show 0–60. Both figure panels have explicit age-origin definitions and occurrence-level populations. PDF/SVG/PNG repeat byte for byte in the recorded environment. Audit JSON paths are repository-relative. Earlier figures are unchanged.

Verification: the raw-age self-checks pass exclusion accounting, identifier uniqueness, fractional overlapping-ID fixtures and decomposition reconstruction. All 45 earlier CSV hashes pass: 29 baseline, ten raw-DOI and six AI-descriptive tables. The raw-age archive also reproduces 16 preferred-year summary rows. The database and seed `20261011` remain unchanged. These checks replay saved inputs; they do not validate AI labels independently.

Public documentation: update the existing README, changes, reviewer preparation and evidence-limit documents. Keep completed offline analyses separate from external data acquisition. All manuscript text, complete bibliographies, compiled papers, author work files and private cleanup preparation remain outside the public checkout.

## Phase 1 revision: AI-led diagnostics, 21 October 2026

Review issue 1, linkage accuracy: replace the prospective manual-validation account with the actual AI-led review. Source package `ai-initial-review-1.1.1` covers 331 IDs and 801 original entries. No independent human adjudication or second-reader agreement is claimed. Every duplicate search remains incomplete. The earlier eleven author-reported historical source checks retain their separate scope.

Review issue 2, identity sensitivity: keep study grouping separate from occurrence metadata. For ID 49343, the verified preprint-to-article relationship supports grouping, while one entry's preprint DOI and journal-version fields leave a metadata question unresolved. This refinement changes one AI metadata judgment from correct to unresolved. It changes no registered scientific estimate.

Review issue 3, registered diagnostics: add `scripts/summarize_ai_annotation_proposals.py` and the independent `ai-review-descriptive-1.0.0` [registry](revision_preparation/annotation/ai_review_summary_v1/manifest.json). Six CSV tables report categories, cross-tabs, ID-level diagnostics, flagged IDs and metadata-field counts. The 319 probability-sampled IDs and 771 entries remain separate from the 12 targeted IDs and 30 entries. Probability-sample proposals contain 11 mixed-work IDs, 120 metadata-error entries, 111 unresolved entries and 40 IDs with counterpart proposals. Targeted proposals contain four mixed-work IDs, eight metadata-error entries, five unresolved entries and one ID with a counterpart proposal. Unresolved metadata judgments affect 63 probability-sampled IDs and four targeted IDs. The tables register zero false-sharing proposals in both selections. These counts supply no population precision estimate or correction to the headline results.

Review issues 1–3, reporting: update README with this status, version links, descriptive counts and offline replay command. Replace the obsolete nine-author-request checklist and conditional validation response with the current evidence limits. Keep the manual interface and strict estimator as optional tools. Preserve all manuscript content outside the public checkout.

Verification: the source-package checker and strict descriptive replay pass. A clean-checkout replay without private work files reproduces the summary byte for byte. Synthetic nonblank human labels and a forged human-validation claim each cause failure before output writes. All 29 baseline and ten raw-DOI table hashes match their registries. A fresh baseline reproduction also matches all 29 tables by parsed CSV values; regenerated CSV line endings differ from the archived files. The database and seed `20261011` remain unchanged. No weighted rate, confidence interval or corrected recurrence result is generated.

## Phase 1 preparation: AI-assisted annotation, 20 October 2026

Review issue 1, linkage accuracy: add explicit Codex initial judgments for all 331 sampled IDs and 801 original entries in `revision_preparation/annotation/ai_review/`. Record per-ID reasoning, document groups, occurrence metadata judgments, candidate comparisons, unresolved fields and author questions. Preserve the 319 probability IDs, 12 targeted supplements, sampling weights and original reference strings. Keep human confirmation pending. The proposal counts are separate from validation estimates.

Review issue 2, identity sensitivity: preserve counterpart-ID evidence and distinguish compound extraction, incorrect linking, metadata problems and version uncertainty. A false-merged ID can retain a genuinely shared document. Working-paper and preprint relationships remain unresolved where the evidence or author convention is missing. Scientific recurrence estimates were not recalculated in this preparation step.

Review issue 3, evidence and reproducibility: add read-only retrieval and source-page extraction scripts, an uncapped query log, PDF and original-string hashes, a source index, proposal CSVs, a priority queue and a dedicated manifest. The source replay reproduced all 682 start-page texts, 76 IPCC PDF hashes, 50 local cited-PDF texts and 801 entry records. Local full-text evidence stays in ignored `work/ai_evidence/`; public proposals retain bibliography facts and source locators.

The interface now presents initial AI proposals separately from author decisions. Draft import fills blank fields only and preserves existing annotations. Both the server and CSV validator reject completed reviews attributed to Codex or an AI reviewer. Correct PDF links convert the saved zero-based index to the one-based browser page. Joined entries retain their recorded starting page, with separately inspected locations where available.

Verification: 42 annotation tests passed, including browser workflows, blank-only import, source-page conversion, AI completion guards and author confirmation with disclosed AI assistance. Structural checks cover all proposal keys, original-string hashes, ownership, labels, sharing logic and artifact hashes. All 29 original registered tables and the snapshot hash remain unchanged. The manuscript and bibliography were not edited. This step prepares author annotation; it does not complete the human-validation stage.

CI checks the AI proposal package and preserves its structural-check report alongside the analysis reproduction records. The separate proposal manifest records the review scope, skill provenance and replay-helper hashes.

Git preserves the AI CSV exports byte for byte, including quoted original-reference whitespace and CSV record endings. Their hashes and immutable fields are checked separately from source-code whitespace.

## Phase 1 preparation: verified publication versions, 21 October 2026

Review issue 2, identity sensitivity: record the author's instruction to group working-paper, preprint and journal versions after verifying that they represent the same study. Update the protocol and preserve version-specific source facts. The convention itself is author-confirmed; the case judgments remain AI proposals.

Review issue 3, evidence: add a version ledger for IDs 33960, 49343 and 102897. Primary RFF covers establish the discussion-paper revision chain; publisher-indexed journal content supports the journal relationship. The archived Research Square notice explicitly identifies the final DOI. Harvard's official article and indexed SSRN metadata identify Craig's journal work. Log failed direct publisher/repository page access and the scope of the available evidence.

In source package `ai-initial-review-1.1.0`, the two uncertain within-ID identities received same-study proposals. Counterpart proposals increased from 38 to 41 sampled IDs. Six occurrence metadata questions were resolved by the source follow-up and the author's cited-version age convention; the count of IDs with metadata questions decreased from 68 to 66. Five received correct-metadata proposals. The remaining working-paper occurrence received a year-error proposal: it cites 2004 while preferred metadata supply the 2008 journal year. Proposed cited-version years of 2004 and 2008 were recorded for the respective occurrences without changing the snapshot. Version 1.1.1 restores the ID 49343 metadata uncertainty described above. The version ledger records the base and updated proposal hashes and per-case hashes. A portable export script refreshes CSVs, counts and artifact hashes from the saved judgments.

These changes affect annotation proposals. Human labels, raw entries, the snapshot, sampling seed, original 29 registered tables, ten robustness tables and manuscript files remain unchanged. No accuracy rate or recurrence estimate was calculated.

Verification: all 42 annotation tests passed, including the updated browser terminology, with no skipped tests. The project checker passed all 30 available checks. The AI package checker passed all 331 IDs, 801 entries, version-ledger case hashes and original-table hashes. The browser displays the new study-version rule and ID 33960's cited-version year decision.

## Earlier preparation by task

The table records the 12 October preparation. Its prospective human-annotation paragraphs and nine-request checklist are superseded by the 21 October AI-led workflow above.

README update, 21 October 2026: add a dated What's new section for the annotation releases and record the requirement to maintain it with every GitHub update. Preserve older entries and link source checks. No code, labels, data or analytical results change in this documentation update.

| Task | Location or deliverable | Change and reason |
|---|---|---|
| A | `revision_preparation/annotation/inputs/`, `input_manifest.json` | Freeze the existing 319 probability IDs, 12 targeted IDs and 801 AR5/AR6 original entries. Retain source keys, strata and inclusion weights so later judgments can be checked against the planned sample. |
| A | `annotation_rules.txt`, `annotation_schema.json` | Define correct and false merges, genuine and false sharing, occurrence metadata errors and detected missed merges separately. State the reviewed AR5/AR6 scope and the required duplicate-search evidence. A false merge can contain a genuinely shared document. |
| A | `local_annotation_app.py`, `app.html`, `launch_annotation.command` | Add an English local annotation interface with original entries, preferred metadata, source links, raw-reference and preferred-field searches, progress, automatic saves and exports. Open the database read-only and store labels separately. The launcher selects Python 3.10 or later. |
| A | `annotation_tools.py`, `initial_integrity_report.json` | Check expected IDs and entries, unchanged source fields and weights, allowed labels, blanks and consistent duplicate rows. Report defects without repairing decisions. The empty templates have the expected keys and counts. |
| A | `annotation_tools.py`, `pending_registry.json` | Implement weighted identity precision, false-merge, detected missed-merge and ID-level metadata-error estimates. Use finite-population hypergeometric intervals with simultaneous stratum bounds for each outcome. Keep targeted counts separate. Require all planned judgments and searches before writing rates. |
| A | `annotation_tools.py`, recurrence sensitivity | Transform the shared-ID error-count interval into fixed-denominator retention and reuse scenarios. Distinguish false merges from false sharing. Do not subtract metadata errors or detected duplicate IDs as if they were false shared identities. A corrected crosswalk requires subsequent adjudication. |
| A | `annotation/tests/`, `annotation/qc/` | Check incomplete-label exits, immutable source fields, inconsistent decisions, weights and interval behavior. Independent finite-population checks cover census strata, zero-error upper bounds and small samples. Quote complete CSV fields so embedded carriage returns round-trip without changing the frozen source text. |
| A | Local manuscript, Data and methods | Describe the prospective probability audit, sample design, required source checking and separate targeted supplements. Reserve actual annotator details, secondary review and agreement for the authors. |
| A | Local manuscript, Results and Discussion | Reserve weighted rates, targeted descriptive results, recurrence sensitivity and their interpretation for completed annotation. Distinguish the prior targeted source checks from the pending structured sample. |
| A | Local manuscript, Appendix C | State the implemented interval method, reviewed scope, duplicate-search estimand and fixed-denominator sensitivity. Retain author requests for the actual search procedure, completed export and successful integrity report. |
| A | `revision_preparation/robustness/coverage_robustness.py` and ten additional registered tables | Add original-reference DOI comparisons across all matching statuses. Use common publication windows, annual eligibility, working-group strata, alternate age clocks and three weighting rules. Require the complete 29-table baseline to pass first. Fix the seed at `20261011`. |
| A | Local manuscript, Results, Discussion and Appendix D | Integrate the raw-DOI results. Report unequal DOI availability and differing measurement populations. State that the pooled mean-age interval includes zero while the recent-reference-share interval remains negative. Keep the original conclusions and their scope. |
| B | Local manuscript, Introduction closing paragraph | Specify one research question and three testable diagnostic expectations. Describe them as post hoc checks. Explain the report and working-group scope, record-level traceability and eligibility-window differences from Huang et al. (2023). |
| B | Local manuscript, Discussion opening | Start with the three main findings, then explain measurement choices and their implications. Preserve the existing limits on population and disciplinary interpretation. |
| C | Local manuscript, Abstract | Use 180 words and seven numerical facts under the recorded word-count rule. Present the question, data, methods, three findings, a limit and the implication in ordinary terms. |
| D | Local manuscript, Data and methods and Results | Replace workflow variables with their recorded meanings. Keep units, denominators, identity definitions, eligibility, age rules and inference in the main text. |
| D | Local manuscript, Appendix B | Move acquisition thresholds, dates, environment details, hashes, engineering checks, workflow-name mappings and individual correction IDs from the main text. Retain the information needed to inspect the workflow. |
| D | Local manuscript, Appendix A and public README | Replace legacy descriptions of missing accuracy fields with the actual pending annotation process. Keep immutable source worksheets and historical correction records unchanged. |
| E | `revision_preparation/literature/` and local manuscript | Add six DOI/title-verified classic references on citation age, immediacy, source scattering and effective diversity. Keep Crossref responses, acceptance decisions and rejected candidates in the verification ledger. Record OpenAlex failures as failures. |
| E | Local manuscript, recent-reference definition | Cite Price's immediacy discussion and state the inclusive zero-through-five-year boundary as this analysis's operational definition. Cite Bradford's verified 1985 reprint with its correct publication metadata. |
| F | Local manuscript, AI disclosure | Compress the disclosure to two sentences. Identify Codex (ChatGPT 6.1 Sol) and its actual tasks. Delete the sentence about simulated pre-submission comments. Keep known checks of the eleven targeted cases separate from the pending probability audit and ask the authors to identify their other independent checks. |
| F | Local manuscript, captions `fig:coverage`, `fig:age`, `fig:outlets`, `fig:groups` | Disclose AI assistance in plotting code under the checked Springer Nature guidance. Keep the registered data and artwork unchanged. |
| F′ | Local manuscript, Declarations | Keep the supplied declarations unchanged. Add no acknowledgment or data-source placeholder. |
| G | `reviewer_prep.md` | Prepare eight likely reviewer comments, conditional responses and the corresponding analysis or revision locations. No response asserts that pending annotation has been completed. |
| A–G | `author_requirements.md` | Collect the nine actual author placeholders and their manuscript locations. The list identifies information the authors must supply before the accuracy paragraphs can be finalized. |
| A/E | Portable literature scripts and CI | Remove host-specific skill dependencies, check the fixed API archive offline, and require fresh output directories for new retrievals. Add the annotation and citation tests plus all ten additional table comparisons to both CI Python versions. Preserve archived CSV bytes in Git. |
| A–G | Project verification and syntax checks | Allow the three requested revision documents alongside README and include new source files in syntax checks. Continue checking English authored text and portable paths. |
| A–G | Public README and release scope | Document the local annotation commands, strict estimate checks, ten-table extension and verified citations. Publish the non-paper tools and records. Keep manuscript files, complete bibliographies, PDFs, local labels and private preparation notes outside the public checkout. |

## Verification and preserved inputs

The original `paper.tex`, `paper.pdf`, `references.bib`, journal class and bibliography style remain unchanged. The revision uses a separate manuscript file and combined bibliography. The SHA-256 of the submitted LaTeX source is:

```text
b886eef50bc2c9f4852e11e81d5ff9b1174508f12c375eac66c0de56d7a6dbae
```

The database SHA-256 remains:

```text
a887a535947ae3b9bab7656070cc038eb3a7a5161494afcb2c3df2245f5ff0ec
```

A full v1.2.1 rerun passes all 29 registered tables. The original baseline analysis modules and registry remain unchanged. The new robustness registry has ten tables. Its independent rerun reproduces all ten byte for byte. The input database is unchanged before and after both workflows. The [additional manifest](revision_preparation/robustness/output_v1/manifest.json) records the baseline comparison and extra table hashes.

In the 12 October preparation copy, the original four tabular blocks remained identical. All original unique numerical tokens were retained, including material moved to the appendices. Under that preparation's word-count rule, the Introduction through the end of Discussion decreased from 7,432 to 6,845 words. That copy compiled through pdflatex and BibTeX with the existing journal template. Its compilation had no errors, undefined references or overfull boxes; rendered pages were inspected. These historical checks do not describe later manuscript edits.

The annotation integrity report confirms 331 unique sampled IDs and 801 unique entries with no source-key mismatch or duplicate rows. All manual decisions are empty. The missing-label check exits without generating rates. The current AI descriptive outputs have their own hashes and registry. Optional later human-label estimates would also require an independent registry.

The [citation ledger](revision_preparation/literature/reference_verification.json) records successful DOI and title verification for all six additions. It explains the Bradford reprint's attribution and metadata and excludes unverified candidates. [The protocol](revision_preparation/annotation/annotation_rules.txt) and [robustness methods](revision_preparation/robustness/methods.txt) state the scope of each new quantity.

## Current evidence limits

No independent human sample adjudication or exhaustive duplicate search is available. The workflow describes AI case counts, sampling-weighted label projections and explicit recurrence scenarios. Population linkage accuracy and recall remain unestimated. The AI-label intervals exclude classification error; the loss and counterpart scenarios are not corrected results or true-error bounds. [Review status](author_requirements.md) records the author's decision and the scope of the available evidence. The optional human-annotation estimator remains subject to its original completeness requirements.
