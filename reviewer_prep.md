# Reviewer preparation

Updated 21 October 2026 for the second major-revision review. These outlines distinguish completed offline analyses from evidence that remains unavailable. AI-assisted judgments are not independent human validation. The manuscript and complete bibliography remain private. [Replay commands and result tables](README.md#second-round-sensitivity-analyses) are public.

## 1. Contribution, research questions and mechanisms

Likely comment: The study extends an existing IPCC comparison but needs a clearer contribution and questions that can be answered by the available records.

Response outline: State the report and working-group scope, traceable original entries and explicit eligibility denominators. Frame the questions around recurrence under different identity rules, age in the full extracted and matched populations, and dominance among observed outlet labels. These are descriptive questions. Topic expansion versus reassessment cannot be identified from bibliography overlap alone. The new selection results strengthen the measurement contribution; they do not supply that causal mechanism.

Location: Introduction research questions and closest-study comparison; Discussion interpretation. Keep the earlier post hoc expectations out of a confirmatory hypothesis claim.

## 2. External climate-literature baseline

Likely comment: Assessment reference shares need a source-supply and age benchmark from an independently defined climate-literature population.

Response outline: The completed offline package contains no external denominator. Its source-label bounds and raw-year comparisons do not substitute for that denominator. Any external result needs an archived query, field selection, date windows, coverage counts and retained API responses. Compare assessment shares with the same defined population, and distinguish citation descendants from direct shared identities. Report acquisition failures and incomplete outputs as such. A successful request is not, by itself, a completed benchmark.

Location: External-data acquisition record and any later independent archive. The current [offline methods](revision_preparation/review_round2/raw_age_v1/methods.txt) retain their narrower target.

## 3. Unequal matching coverage and working-group composition

Likely comment: Changing WoS coverage can produce an apparent age or outlet change. Group composition also changes across reports.

Completed response evidence: The [raw-year differences](revision_preparation/review_round2/raw_age_v1/raw_age_differences.csv) apply the same printed-year rule to all extracted and matched occurrences. Publication-origin mean age falls from 5.374923 to 5.252392 years for all extracted occurrences, but rises from 4.373688 to 4.957161 years for matched occurrences. The author approved revising the interpretation to match these results. Cutoff-origin and equal-ID comparisons retain these opposing directions. This comparison avoids using matching as an inclusion criterion for the full extracted population; it does not eliminate extraction or year-parsing error or identify a causal effect of matching.

The [Kitagawa decomposition](revision_preparation/review_round2/raw_age_v1/raw_age_WG_kitagawa.csv) reports group-internal and composition components. It uses single-working-group occurrences, with a separate [scope table](revision_preparation/review_round2/raw_age_v1/raw_age_single_WG_coverage.csv). Overlapping IDs receive total pooled weight one under equal-ID weighting, fractionally distributed by their included occurrences. The [missing-outlet outer envelope](revision_preparation/review_round2/raw_age_v1/missing_outlet_difference_envelopes.csv) crosses zero: −140.624170 to +211.358702 under conservative aliases. Narrow the dominance conclusion to observed named outlets. Do not describe independent report extrema as a sharp joint bound or an accuracy interval.

Location: Methods population definitions; parallel age Results; group-composition and missing-label appendix tables; Limitations.

## 4. Eligibility windows and earlier presence

Likely comment: Conditional reuse is sensitive to the year boundary, and a newly matched record may have been extracted in AR5 already.

Response outline: Keep all-extracted earlier presence separate from earlier matched presence. The [baseline eligibility table](reproducibility/reference/revision_identity_eligible_reuse.csv) records 34.16%, 28.40% and 20.47% at boundaries 2012, 2013 and 2014. These are different denominator definitions, not a temporal increase in reuse. Report the boundary range and the eligible numerator/denominator. The [earlier-presence partition](reproducibility/reference/revision_identity_earlier_incoming_partition_summary.csv) distinguishes matched absence from complete extracted absence. The new cutoff-year age clock is not a full-date eligibility analysis. Neither a publication date nor a print year alone establishes acceptance eligibility.

Location: Cohort and eligibility definitions; recurrence table; Abstract boundary sensitivity. Independent full-date results, if completed, need their own archived outputs.

## 5. AI review, sampling uncertainty and counterpart propagation

Likely comment: AI labels need independent validation, weighting and an honest account of their effect on the headline estimates.

Completed response evidence: The [AI sensitivity archive](revision_preparation/review_round2/ai_sensitivity_v1/manifest.json) projects the 319-ID probability sample with its frozen design weights. The 12 targeted supplements remain separate. The false-merge proposal fraction is 1.58%, with a conservative sampling-only 95% interval of 0.04–45.05%. It estimates AI-label prevalence under the design, not true linkage error. Other outcomes separate metadata-error proposals, unresolved metadata and counterpart proposals. Classification error and incomplete duplicate search are unquantified.

Zero false-sharing AI proposals among 94 sampled shared IDs do not imply perfect precision. The [one-sided calculation](revision_preparation/review_round2/ai_sensitivity_v1/AI_zero_event_one_sided_limits.csv) profiles the stratified zero-event probability instead of applying a binomial rule of three. Its upper count of 183 projected positive AI labels supports explicit [fixed-denominator loss scenarios](revision_preparation/review_round2/ai_sensitivity_v1/fixed_denominator_AI_loss_scenarios.csv), not corrected recurrence estimates or true-error bounds.

The [counterpart geometry](revision_preparation/review_round2/ai_sensitivity_v1/counterpart_edge_geometry.csv) shows why counterpart proposals cannot all be read as missed cross-report sharing. The separate [targeted endpoint scenario](revision_preparation/review_round2/counterpart_review_v1/counterpart_scenarios.csv) accepts 37 eligible AI edges in 33 components and yields 1,661 shared IDs, retention 12.3019% and reuse 4.7649%. Compound and unresolved nodes remain blocked. This nonprobability extension changes no stored identity or metadata. Historical eleven-case author-reported source checks, the 331-ID AI sample and the endpoint extension are separate activities. No independent human agreement or accuracy estimate is available.

Location: AI-review Methods and Results; sampling/scenario appendix; consolidated AI disclosure; Limitations.

## 6. Age clocks, outlet quantities and identity definitions

Likely comment: Publication timing affects age; richness differs from concentration; literal and repaired DOI keys describe different sets.

Response outline: Put publication-origin and cutoff-origin results side by side, with their inclusion counts. The [new empirical distributions](revision_preparation/review_round2/figures_v1/review_round2_age_comparison.pdf) use raw printed years and occurrence weights. Their 0–30-year main axes and 0–60-year insets share full-population normalization. The recent-reference fraction includes ages zero through five. New raw-age point summaries have no newly estimated intervals; existing chapter-resampling intervals retain their original cohort and interpretation. A decline in the recent share can coexist with a decline in mean age when distributions cross.

Report D0 as observed label richness and D2 as effective concentration separately. Name the known-outlet denominator. The [literal/repaired identity baselines](reproducibility/reference/revision_identity_independent_raw_baselines.csv) should remain side by side, with the rule and selected population stated. An identity-rule range is not automatically an identification interval for one common target.

Location: Definitions and figure captions; age and outlet tables; identity appendix. The historical Price reference gives context, while the exact recent-share boundary is the analysis's operational definition.

## 7. Working-group null model and pair rankings

Likely comment: Outlet-by-vintage strata do not control topics, and preserving each ID's number of group memberships fixes total sharing.

Response outline: Interpret the [archived conditional null](reproducibility/reference/revision_network_conditional_null.csv) only as pairwise allocation conditional on its preserved margins. It is not a test of total integration relative to climate literature. Retain its tail probabilities and within-family multiplicity results with their actual definitions; exploratory analysis still needs an explicit uncertainty target. Place detailed null-model evidence in an appendix or supplement and remove dependence on an unmeasured topic control. Jaccard and smaller-set overlap can rank group pairs differently, so neither supplies a metric-independent ordering of integration.

Location: Working-group appendix; main descriptive sharing table and Limitations. Topic stratification is additional analysis, not something the existing outlet strata already provide.

## 8. Reproducibility, provenance and concise reporting

Likely comment: Saved outputs may be mistaken for preregistration, and AI/source provenance needs one consistent account.

Response outline: Use “archived” or “fixed” for the outputs. Preserve the original 29 baseline, ten raw-DOI and six AI-descriptive tables and seed `20261011`; new work has separate scripts, methods, manifests and checks. Move build details from the main narrative. The [round-2 figure manifest](revision_preparation/review_round2/figures_v1/manifest.json) records data/code/font hashes and actual rendering versions; [QA](revision_preparation/review_round2/figures_v1/visual_QA.txt) covers final vector and raster exports. Report AI assistance for source inspection, metadata and counterpart judgments, code and prose without inventing human adjudication. Preserve source-specific metadata terms; no redistributive permission or completed DOI deposit follows from preparing an archive description.

Location: Main Methods and consolidated AI statement; data/code availability; appendix and repository records. The original submitted manuscript remains unchanged and private. Add no missing-ORCID placeholder for coauthors.

<details>
<summary>Historical phase-1 reviewer outline, superseded by the eight points above</summary>

The following 21 October outline records the earlier unweighted-only phase. Its statements about absent AI-label sampling intervals describe that phase, not the completed round-2 sensitivity archive. It is retained as revision history.


Updated 21 October 2026. The author chose to proceed with AI-led source review and declined further human annotation. The items below distinguish completed descriptive checks from evidence that remains unavailable. Locations use section names and LaTeX labels so they remain usable after pagination changes. These response outlines describe public methods and evidence; manuscript files remain private.

## 1. Record identity and validation

Location: Data and methods, identity construction and correction ledger; Results, identity audit; Table `tab:recurrence`; Appendix `sec:audit`.

Likely comment: Shared IDs determine retention and reuse. AI-assisted source checks identify individual problems, but the review has no independent human adjudication or population linkage-error estimate.

Available evidence: Source package `ai-initial-review-1.1.1` records 331 ID judgments and 801 entry judgments by Codex and delegated Codex agents. The separate [descriptive registry](revision_preparation/annotation/ai_review_summary_v1/manifest.json) reports 319 probability-sampled IDs and 12 targeted supplements. The probability sample has 11 mixed-work merge proposals and 40 IDs with counterpart proposals; the targeted group has four and one, respectively. Metadata-error proposals concern 120 probability-sample entries and eight targeted entries; unresolved metadata concerns 111 and five entries, respectively. All duplicate searches remain incomplete. No weighted accuracy, recall, confidence interval or estimated recurrence correction was calculated.

Response outline: Describe this as an AI-led diagnostic review. State the absence of independent human adjudication and keep probability and targeted counts separate. Explain that a compound extraction can contain a genuine cross-report shared work. The zero false-sharing proposals provide no population precision estimate. Retain the original snapshot results and registered flagged-set stress as separate quantities. Independent adjudication and an evidence-based corrected crosswalk would be additional work if requested by reviewers; they have not been performed.

## 2. Entry, eligibility and matching coverage

Location: Data and methods, cohort definitions and eligibility boundary; Results, earlier-presence partition and conditional reuse; Table `tab:recurrence`.

Likely comment: A record can enter a matched cohort because its metadata status changes. Unconditional reuse also contains later publications outside the earlier eligibility window.

Analysis needed: Check the partition against every extracted earlier-report ID, including unmatched records. Retain the distinction between matched earlier presence and any extracted earlier presence. Show the eligible denominator beside the annual boundary. Keep the boundary sensitivity and working-group publication or cutoff dates visible. The common 2000–2013 printed-year window gives 13.05% raw-DOI retention. Eligibility through 2013 gives 6.50% raw-DOI reuse, compared with 28.40% in the matched-record analysis. These rates describe different selected populations. Usable single raw DOIs occur in 11.22% of extracted AR5 entries and 72.36% of AR6 entries. Keep these availability denominators beside the comparison.

Proposed response after the partition check: “We define entry against the complete extracted earlier-report set. We report matching-status transitions separately. The raw-DOI common-window retention is 13.05%. Eligibility-conditioned reuse is 6.50% under raw DOI keys and printed years, compared with 28.40% under assigned identities and preferred years. Raw-DOI availability is 11.22% in AR5 and 72.36% in AR6. The comparison identifies sensitivity to identity rules and source coverage.”

## 3. Citation age and the recent-reference fraction

Location: Data and methods, age definition and weighting; Results, age distributions; Figure `fig:age`; Table `tab:age`.

Likely comment: Integer source-year age mixes publication timing with assessment timing. The recent-reference fraction also needs a precise boundary and an accurate historical citation.

Analysis needed: State whether ages zero through five are included. Preserve preferred-year and leading-year sensitivity results. Compare source-year and documented cutoff-year clocks. Inspect exceptional online/print dates through source records. Cite Price for immediacy and define the implemented fraction directly. Avoid assigning the exact operational formula to an unverified historical source.

Proposed response after those checks: “The Methods define the included ages and both weighting rules. The supplementary comparisons use leading-reference and cutoff-year clocks. We use ‘recent-reference share’ for the measured fraction and cite the historical immediacy literature for context.”

## 4. Resampling and the unit of uncertainty

Location: Data and methods, chapter resampling; Results, mean and recent-share differences; Figure `fig:age` and its caption.

Likely comment: Repeated references and unequal chapter sizes affect weighting. Resampling observed catalogue units describes composition within the snapshot. It does not quantify record-linkage error or the uncertainty of an unobserved literature population.

Analysis needed: Check the resampling unit, report and cohort strata, equal-ID reweighting, seed and replicate count. Keep empirical distributions beside coarse integer medians. Label each reported interval by the variation its design actually represents. Keep linkage validation separate. In the pooled raw-DOI occurrence analysis, the mean-age difference has a chapter-composition interval of −0.26 to 1.49 years. The interval includes zero. The recent-reference share falls by 9.75 percentage points, with an interval of −16.01 to −2.96 percentage points. Its interval remains negative. Preserve this distinction when comparing the raw-DOI and matched results.

Response outline: Identify the resampling unit and weighting within each draw. The intervals describe sensitivity to observed chapter composition. In the raw-DOI comparison, the mean-age interval includes zero and the recent-share interval remains negative. Keep the AI-coded source diagnostics separate from these intervals; independent linkage validation remains unavailable.

## 5. Outlet labels and concentration

Location: Data and methods, outlet normalization and Hill numbers; Results, outlet comparisons; Figure `fig:outlets`.

Likely comment: Outlet-title variants and sample size affect richness. Matching selects a different source mix. Lower concentration alone does not establish broader disciplinary coverage.

Analysis needed: Verify the alias map and known-outlet denominators. Retain full-extracted, matched and DOI-matched comparisons, equal-size sampling, common-window controls and common support. Use ISSN normalization only when a supported crosswalk exists. Interpret effective outlet counts as label-distribution measures. A claim about assessment selection relative to climate-research supply would require an independently defined literature pool.

Proposed response after those checks: “We report richness and effective outlet counts separately. Equal-size and label-normalization comparisons retain their defined populations. The interpretation concerns recorded outlet dominance. Disciplinary diversity requires direct subject metadata.”

## 6. Working-group sharing and its null model

Location: Data and methods, group-membership null; Results, coverage and pairwise sharing; Table `tab:groups`; Figure `fig:groups`; Appendix `sec:audit`.

Likely comment: Group coverage differs, and a null that preserves document membership counts also preserves total pairwise sharing. It tests how sharing is allocated among pairs.

Analysis needed: Check group coverage beside retention. Confirm the outlet-by-vintage strata, preserved margins, exact expectations and simulation ranges. Keep smaller-set overlap and Jaccard denominators distinct. Check small-state fixtures against independent enumeration. Interpret the null at the pair-allocation level.

Proposed response after those checks: “The Methods state the preserved margins. The Results compare observed sharing with pair-specific expectations and show group coverage. The interpretation concerns allocation of a fixed amount of sharing across working-group pairs.”

## 7. Contribution and historical measures

Location: Introduction, research question and closest IPCC studies; Data and methods, recent-reference and effective-number definitions; Discussion, contribution paragraph.

Likely comment: Age, recency and source concentration are established bibliometric measures. The contribution needs a clear relation to existing IPCC reference analyses and to the measurement populations used here.

Analysis needed: Compare the closest IPCC study's report scope, working groups, identity rules and weighting with this analysis. Add verified classic citations only where they explain a measure. Keep early-report inventories separate from the primary transition analysis. Describe the contribution through traceable assessment transitions, eligibility, weighting and source composition.

Proposed response after the literature and scope checks: “The Introduction now locates the analysis within citation-age and source-scattering research. It identifies the report and working-group scope of the closest IPCC comparison. The contribution is the linked assessment-transition analysis and its inspectable measurement populations.”

## 8. Reproduction, source provenance and AI assistance

Location: Data and methods, acquisition and reproduction paragraph and AI disclosure; Data and code availability; figure captions; release manifest and README.

Likely comment: A reviewer needs the exact input state and code version. Third-party fields need clear provenance. AI-assisted record decisions, code and writing also need specific attribution and human accountability.

Available checks and remaining work: A fresh baseline run matched all 29 registered tables by parsed CSV values. The database, seed `20261011` and ten raw-DOI tables remain unchanged. The AI summary checker verifies those hashes and replays six descriptive tables. Its clean-checkout replay is byte-identical. Public metadata provenance and imported-field sharing need to retain their documented scope. AI disclosure must identify source inspection, metadata and counterpart judgments, code, analysis checks and writing. Preserve the reported eleven historical author source checks separately from the AI-led sample review. Further human annotation is not planned, so no completed human audit or agreement statistic can be claimed.

Response outline: Identify the fixed snapshot, code and registered outputs. Distinguish the fresh parsed-value reproduction from the byte-identical descriptive replay. Report source-package version 1.1.1 and the unresolved preprint DOI versus journal-metadata combination for ID 49343. State that study-version grouping and version-specific metadata are separate decisions. Describe the sample review as AI-assisted and report its unweighted counts without claiming independent validation.

The 12 October 2026 preparation recorded a 150–250-word abstract requirement in the [Scientometrics submission guidance](https://link.springer.com/journal/11192/submission-guidelines) and checked [Springer Nature manuscript-preparation guidance](https://www.springernature.com/gp/policies/editorial-policies/ai-manuscript-preparation) and its [AI FAQ](https://group.springernature.com/kr/group/ai/ai-guidance-for-researchers-editors-reviewers). The literature ledger records the verified references and their source levels.

</details>
