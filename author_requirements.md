# Review status and evidence limits

Updated 21 October 2026. The author chose to proceed with the AI-led source review and declined further human annotation. The earlier nine-request annotation checklist is superseded. The current descriptive workflow requires no completed human-label export.

## Evidence available

Codex and delegated Codex agents reviewed 331 frozen IDs and 801 original AR5/AR6 entries. The [source package](revision_preparation/annotation/ai_review/manifest.json), version `ai-initial-review-1.1.1`, retains individual judgments, source locators, candidate comparisons and unresolved fields. The [descriptive registry](revision_preparation/annotation/ai_review_summary_v1/manifest.json), version `ai-review-descriptive-1.0.0`, separates the 319 probability-sampled IDs from the 12 targeted supplements. Its six tables contain unweighted case and occurrence counts.

The probability sample contains 771 entries. AI proposals identify mixed-work merges in 11 IDs, metadata errors in 120 entries and unresolved metadata in 111 entries. The targeted supplement contains 30 entries, with four mixed-work merge proposals, eight metadata-error proposals and five unresolved entries. Counterpart proposals concern 40 probability-sampled IDs and one targeted ID. Categories can overlap.

The author authorized grouping verified publication versions and using the year of the cited version for citation age. The individual version judgments remain AI-assisted. The preprint DOI versus journal-metadata combination in one ID 49343 entry remains unresolved. The source review retains this uncertainty separately from the verified study relationship.

## Limits of this review

The sample has no independent human adjudication, second-reader agreement statistic or completed exhaustive duplicate search. Every proposal retains `review_complete=no`, and human-label files remain empty. The AI-coded counts provide no population precision, missed-merge rate, recall or confidence interval. They also supply no estimated correction to retention or reuse.

The authors previously reported checking primary sources for eleven historical correction cases. That earlier activity is separate from adjudicating the 331 sampled IDs. It must retain its original scope in the disclosure.

The absence of a false-sharing proposal does not establish perfect linkage precision. Mixed-work extraction and cross-report sharing are separate judgments. The original registered estimates continue to describe the frozen linked database.

## Reproducibility and optional later annotation

The seed remains `20261011`. A fresh baseline reproduction matched all 29 registered tables by parsed CSV values. The database and ten raw-DOI tables remain unchanged. The AI summary checker verifies their hashes and reproduces the descriptive outputs:

```bash
python3 scripts/summarize_ai_annotation_proposals.py --check
```

The annotation interface, blank templates, integrity checker and weighted estimator remain available for a later independent exercise. That optional exercise would need actual reviewer identities, dates, source checks, duplicate-search records and complete labels. Its estimator rejects incomplete or AI-attributed completed reviews. Until such evidence exists, the current workflow reports AI-coded diagnostics only.

The supplied contributions, funding and competing-interest declarations remain unchanged. Manuscript files and complete bibliographies remain private.
