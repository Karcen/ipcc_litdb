# Review status and evidence limits

Updated 21 October 2026. The author chose to proceed with the AI-led source review and declined further human annotation. The earlier nine-request annotation checklist is superseded. The current AI-diagnostic and sensitivity workflows require no completed human-label export. They do not establish independent linkage accuracy.

## Evidence available

Codex and delegated Codex agents reviewed 331 frozen IDs and 801 original AR5/AR6 entries. The [source package](revision_preparation/annotation/ai_review/manifest.json), version `ai-initial-review-1.1.1`, retains individual judgments, source locators, candidate comparisons and unresolved fields. The [descriptive registry](revision_preparation/annotation/ai_review_summary_v1/manifest.json), version `ai-review-descriptive-1.0.0`, separates the 319 probability-sampled IDs from the 12 targeted supplements. Its six original tables contain unweighted case and occurrence counts. They remain unchanged alongside the later analyses below.

The probability sample contains 771 entries. AI proposals identify mixed-work merges in 11 IDs, metadata errors in 120 entries and unresolved metadata in 111 entries. The targeted supplement contains 30 entries, with four mixed-work merge proposals, eight metadata-error proposals and five unresolved entries. Counterpart proposals concern 40 probability-sampled IDs and one targeted ID. Categories can overlap.

The author authorized grouping verified publication versions and using the year of the cited version for citation age. The individual version judgments remain AI-assisted. The preprint DOI versus journal-metadata combination in one ID 49343 entry remains unresolved. The source review retains this uncertainty separately from the verified study relationship.

## Completed second-round analyses

The [raw-year archive](revision_preparation/review_round2/raw_age_v1/manifest.json) compares all extracted and matched occurrences using the same leading-reference-year rule. It reports both age origins, occurrence and equal-ID weights, group-specific distributions, fractional working-group composition, exact descriptive decompositions and exclusions. The all-extracted mean age falls; the matched mean rises. The author approved changing the interpretation to reflect this result. The unchanged preferred-year baseline remains a selected-cohort comparison. Full-date eligibility and a causal explanation for the difference are not supplied by this analysis.

The [AI-label sensitivity archive](revision_preparation/review_round2/ai_sensitivity_v1/manifest.json) adds weighted projections and sampling-only intervals for the probability sample. Targeted supplements remain unweighted. It also contains an exact one-sided stratified zero-event procedure and fixed-denominator numerator-loss scenarios. These outputs quantify hypothetical AI-coded labels under the sample design. They do not quantify AI classification error, independent precision or recall. Missing metadata, unresolved metadata and error proposals remain separate categories.

The [targeted endpoint archive](revision_preparation/review_round2/counterpart_review_v1/manifest.json) reviews saved occurrences of the counterpart IDs. It provides an explicit unvalidated union scenario for eligible single-study nodes. The scenario is neither a probability-sample estimate nor a true recurrence bound. It does not change the snapshot or substitute proposed metadata into citation ages. Recorded PDF hashes are provenance; the separate [local-page evidence](revision_preparation/review_round2/counterpart_review_v1/local_page_evidence.json) identifies the contexts actually inspected.

The [missing-outlet calculation](revision_preparation/review_round2/raw_age_v1/missing_outlet_difference_envelopes.csv) does not identify the D2 direction for the entire extracted population. Its outer envelope crosses zero. The observed lower dominance concerns named outlet labels. This calculation does not measure disciplines or reference selection relative to climate-literature supply.

## Limits of this review

The sample has no independent human adjudication, second-reader agreement statistic or completed exhaustive duplicate search. Every proposal retains `review_complete=no`, and human-label files remain empty. The original unweighted counts provide no accuracy interval. The later weighted AI-label intervals describe sampling only, not independent accuracy. The recurrence scenarios are not estimated corrections or bounds on true retention and reuse.

The authors previously reported checking primary sources for eleven historical correction cases. That earlier activity is separate from adjudicating the 331 sampled IDs. It must retain its original scope in the disclosure.

The absence of a false-sharing proposal does not establish perfect linkage precision. Mixed-work extraction and cross-report sharing are separate judgments. The original registered estimates continue to describe the frozen linked database.

## Reproducibility and optional later annotation

The seed remains `20261011`. A fresh baseline reproduction matched all 29 baseline tables by parsed CSV values. Those tables, the database, ten raw-DOI tables and six AI-descriptive tables remain unchanged. “Archived” refers to saved hashed outputs, not preregistration. The current offline checks are:

```bash
python3 scripts/summarize_ai_annotation_proposals.py --check
python3 scripts/review_round2_raw_age.py --check
python3 scripts/review_round2_ai_sensitivity.py --check
python3 scripts/review_round2_counterpart_review.py check
```

The annotation interface, blank templates, integrity checker and human-label weighted estimator remain available for a later independent exercise. That optional exercise would need actual reviewer identities, dates, source checks, duplicate-search records and complete labels. Its estimator rejects incomplete or AI-attributed completed reviews. The current workflow continues to disclose AI provenance instead of treating those labels as human decisions.

An external climate-literature denominator, complete publication dates, source-ID normalization and citation-descendant analyses have a separate acquisition status. These offline analyses do not establish that those tasks are complete. [Reviewer preparation](reviewer_prep.md) distinguishes their requested evidence from the completed checks. Depositing a DOI-bearing archive would require a later upload; preparing metadata alone does not create a DOI. Imported metadata retain their source terms.

The supplied contributions, funding and competing-interest declarations remain unchanged. Manuscript files and complete bibliographies remain private.
