# Reviewer preparation

Working author-preparation draft, 12 October 2026. The items below anticipate questions about an assessment-reference analysis. Structured probability-sample labels are pending. Response wording applies after the stated action is completed. Locations use section names and LaTeX labels so they remain usable after pagination changes.

## 1. Record identity and validation

Location: Data and methods, identity construction and correction ledger; Results, identity audit; Table `tab:recurrence`; Appendix `sec:audit`.

Likely comment: Shared IDs determine retention and reuse. Targeted corrections establish individual cases. Population linkage precision and missed splits still require the pending probability-sample annotation.

Analysis needed: Complete the probability-sample annotation. Record source evidence, disagreement and unresolved cases. Apply the recorded sampling weights. Define false merges and missed splits separately. Report weighted rates and uncertainty under the actual sample design. Preserve the targeted cases as a separate audit population. Report fixed-denominator scenarios for the unresolved flagged set. A new corrected recurrence table requires an adjudicated crosswalk; no new crosswalk is available yet.

Proposed response after that analysis: “The validation supplement reports the sampling frame, case definitions, weighted rates and uncertainty. It separates targeted corrections from probability-sample judgments. The fixed-denominator scenarios show how unresolved flagged identities affect the reported rates.” Use this response only after annotation and weighted estimation are complete. While annotation is pending, report the snapshot estimates and flagged-set stress as separate quantities.

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

Proposed response after the reproducibility check: “The Methods identify the resampling unit and weighting within each draw. The intervals describe sensitivity to observed chapter composition. In the raw-DOI comparison, the mean-age interval includes zero and the recent-share interval remains negative. The source-adjudication workflow addresses record-linkage validation separately.”

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

Action needed: Check the release tag, snapshot hash, registered outputs and restoration commands in a fresh directory. Document public field provenance and the basis for sharing imported metadata. Confirm the journal's abstract length. Match the AI declaration to actual tools, model, tasks and recorded author checks. Check figure captions against the current visual-disclosure policy. Keep pending human annotation visible as pending.

Proposed response after those checks: “The data-availability statement identifies the release and input hash. The reproduction record lists the environment and registered comparisons. Source metadata and AI assistance are documented at the stages where they enter the analysis. Author checks and pending annotation are reported separately.”

The [Scientometrics submission guidance](https://link.springer.com/journal/11192/submission-guidelines) specifies a 150–250-word abstract. Current [Springer Nature manuscript-preparation guidance](https://www.springernature.com/gp/policies/editorial-policies/ai-manuscript-preparation) and its [AI FAQ](https://group.springernature.com/kr/group/ai/ai-guidance-for-researchers-editors-reviewers) were checked on 12 October 2026. The literature ledger records the verified references and their source levels.
