#!/usr/bin/env python3
"""Describe unvalidated AI labels and proposal-only linkage scenarios.

Units: frozen paper_id for stratified labels, occurrence_id for field/status
diagnostics, directed proposed counterpart edge and undirected component for
geometry. Source joins are exact 1:1 or occurrence-to-paper many:1. No labels,
raw strings, report statuses, snapshot rows or earlier outputs are changed.
Only two tested hypergeometric mathematical functions are loaded from the
annotation tool. Its human-label estimator and completion guards are not run
or bypassed. No external API, random draw or manual validation is used.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import csv
import hashlib
import heapq
import io
import itertools
import json
import math
from pathlib import Path
import sqlite3
import subprocess
import sys
from fractions import Fraction

VERSION = "review-round2-ai-sensitivity-1.0.0"
SEED = 20261011
DEFAULT_OUTPUT = "revision_preparation/review_round2/ai_sensitivity_v1"
GROUPS = {"stratified_hash_sample": "probability_sample",
          "targeted_supplement_not_probability_sample": "targeted_supplement"}
MATCHED = {"matched_doi", "matched_title"}
FIELDS = ("authors", "doi", "journal", "other", "title", "year", "unspecified")


def need(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path):
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    need(all(None not in r and all(v is not None for v in r.values()) for r in rows),
         "Malformed CSV: " + path.name)
    return rows


def unique(rows, key):
    result = {str(r[key]): r for r in rows}
    need(len(result) == len(rows) and all(result), "Missing/duplicate key: " + key)
    return result


def values(value):
    if isinstance(value, list):
        return {str(x).strip() for x in value if str(x).strip()}
    return {x.strip() for x in str(value or "").split("|") if x.strip()}


def encode_json(obj):
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def encode_csv(rows, columns=None):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns or list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


def guard_ai(proposal, manifest):
    need(proposal["human_validated"] is False and manifest["human_validated"] is False,
         "AI source must not claim human validation")
    need(manifest["weighted_estimates_generated"] is False,
         "Source artifact must retain its descriptive-only provenance")
    need(proposal["review_type"] == "ai_assisted_initial_review", "Unexpected review origin")
    for case in proposal["cases"]:
        need(case["human_confirmed"] is False and case["review_complete"] == "no",
             "Source case claims human completion")
        need(case["missed_merge_search_status"] == "incomplete", "Source claims exhaustive search")


def load_math(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name in {"hypergeom_tail", "finite_binary_interval"}]
    need(len(selected) == 2, "Tested mathematical functions are absent")
    scope = {"math": math}
    exec(compile(ast.Module(body=selected, type_ignores=[]), path.name, "exec"), scope)
    return scope["hypergeom_tail"], scope["finite_binary_interval"]


def label(case, outcome):
    if outcome == "false_merge_proposal":
        return int(case["identity_label"] == "false_merge")
    if outcome == "any_metadata_error_proposal":
        return int(any(o["metadata_judgment"] == "metadata_error" for o in case["occurrences"]))
    if outcome == "any_unresolved_metadata_judgment":
        return int(any(o["metadata_judgment"] == "unresolved" for o in case["occurrences"]))
    if outcome == "counterpart_proposal":
        return int(bool(case["missed_merge_candidate_ids"]))
    if outcome == "false_sharing_proposal":
        return int(case["shared_link_label"] == "false_shared")
    raise ValueError("Unknown AI-label outcome")


def weighted_labels(strata, selected, cases, outcome, interval, prefix=None):
    ss = [s for s in strata if prefix is None or s["stratum"].startswith(prefix)]
    uncertain = sum(int(s["sampled_ids"]) < int(s["population_ids"]) for s in ss)
    details = []
    for s in ss:
        N, n = int(s["population_ids"]), int(s["sampled_ids"])
        pp = [p for p in selected if p["stratum"] == s["stratum"]]
        need(len(pp) == n, "Probability stratum is incomplete")
        k = sum(label(cases[p["paper_id"]], outcome) for p in pp)
        low, high = interval(N, n, k, .05 / max(1, uncertain))
        details.append({"outcome": outcome, "stratum": s["stratum"], "population_ids": N,
                        "sampled_ids": n, "positive_AI_sample_ids": k, "design_weight": N/n,
                        "HT_positive_AI_ids": N*k/n, "lower_positive_AI_ids": low,
                        "upper_positive_AI_ids": high})
    N = sum(r["population_ids"] for r in details)
    estimate = math.fsum(r["HT_positive_AI_ids"] for r in details)
    lower = sum(r["lower_positive_AI_ids"] for r in details)
    upper = sum(r["upper_positive_AI_ids"] for r in details)
    return {"outcome": outcome, "domain": "shared_matched_IDs" if prefix else "matched_union_IDs",
            "population_ID_denominator": N, "probability_sample_IDs": sum(r["sampled_ids"] for r in details),
            "positive_AI_sample_IDs": sum(r["positive_AI_sample_ids"] for r in details),
            "HT_positive_AI_ID_numerator": estimate, "weighted_AI_proposal_fraction": estimate/N,
            "lower_positive_AI_IDs": lower, "upper_positive_AI_IDs": upper,
            "sampling_only_lower_95": lower/N, "sampling_only_upper_95": upper/N,
            "strata": len(ss), "noncensus_strata": uncertain, "confidence_level_per_outcome": .95,
            "AI_classification_error_quantified": "no", "human_validated": "false"}, details


def graph_components(edges):
    adjacency = defaultdict(set)
    for a, b in edges:
        adjacency[a].add(b)
        adjacency[b].add(a)
    result, seen = [], set()
    for first in sorted(adjacency, key=int):
        if first in seen:
            continue
        pending, component = [first], set()
        while pending:
            node = pending.pop()
            if node in component:
                continue
            component.add(node)
            pending.extend(adjacency[node] - component)
        seen |= component
        result.append(sorted(component, key=int))
    return result


def single_study(pid, cases):
    return pid in cases and cases[pid]["identity_label"] == "correct_merge"


def component_counts(nodes, a, b):
    n5, n6 = len(set(nodes) & a), len(set(nodes) & b)
    shared = len(set(nodes) & a & b)
    return n5, n6, shared, int(n5 > 0), int(n6 > 0), int(n5 > 0 and n6 > 0)


def zero_probability(design, allocation):
    """Exact all-zero probability under independent within-stratum SRSWOR."""
    probability = Fraction(1)
    for (N, n), K in zip(design, allocation):
        need(0 <= K <= N and 0 < n <= N, "Invalid finite zero-event allocation")
        if N-K < n:
            return Fraction(0)
        probability *= Fraction(math.comb(N-K, n), math.comb(N, n))
    return probability


def greedy_zero_profiles(design, capacities):
    """Exact maximal P(all zero) for every total, by convex prefix allocation.

    An increment multiplies P0 by 1-n/(N-K). Its negative logarithm increases
    with K. Fraction(n,N-K) orders these marginal costs without rounding.
    Once zero probability is unavoidable, all further maxima are also zero.
    """
    need(len(design) == len(capacities), "Capacity/design dimensions differ")
    allocation = [0] * len(design)
    heap = []
    for i, ((N, n), cap) in enumerate(zip(design, capacities)):
        need(0 < n <= N and 0 <= cap <= N, "Invalid zero-event design/capacity")
        if cap:
            heapq.heappush(heap, (Fraction(n, N), i))
    probability, total = Fraction(1), 0
    yield total, tuple(allocation), probability
    while heap:
        marginal, i = heapq.heappop(heap)
        probability *= 1-marginal
        allocation[i] += 1
        total += 1
        yield total, tuple(allocation), probability
        if allocation[i] < capacities[i]:
            N, n = design[i]
            heapq.heappush(heap, (min(Fraction(1), Fraction(n, N-allocation[i])), i))


def zero_event_limit(design, capacities, observed, alpha=Fraction(1, 20)):
    """One-sided confidence procedure: return full capacity on any nonzero.

    With all zeros, U is the last K whose maximal P0 is >=alpha. For a fixed
    actual allocation with total K>U, rejection can occur only on all zeros,
    whose probability is bounded by the maximized P0(K)<alpha. Capping errors
    to a known eligible subset is valid: additional ineligible errors can
    only lower P0, so its worst-case allocation puts them at zero.
    """
    need(len(observed) == len(design) and 0 < alpha < 1, "Invalid observed counts/alpha")
    if any(observed):
        return {"all_zero": False, "upper": sum(capacities), "allocation": None,
                "probability": None, "next_K": None, "next_allocation": None,
                "next_probability": None}
    previous = None
    for K, allocation, probability in greedy_zero_profiles(design, capacities):
        if probability < alpha:
            need(previous is not None, "Zero-error probability unexpectedly below alpha")
            U, chosen, p = previous
            need(p == zero_probability(design, chosen), "Greedy/product probability mismatch")
            need(probability == zero_probability(design, allocation), "Next-total product mismatch")
            return {"all_zero": True, "upper": U, "allocation": chosen, "probability": p,
                    "next_K": K, "next_allocation": allocation, "next_probability": probability}
        previous = K, allocation, probability
    U, chosen, p = previous
    return {"all_zero": True, "upper": U, "allocation": chosen, "probability": p,
            "next_K": None, "next_allocation": None, "next_probability": None}


def fraction_log(value):
    return math.log(value.numerator)-math.log(value.denominator) if value else None


def zero_event_self_checks():
    fixtures = [([(3, 1), (4, 2)], [3, 4]), ([(3, 1), (4, 2)], [2, 2]),
                ([(4, 4), (5, 1)], [4, 5]), ([(2, 1), (3, 2), (4, 1)], [2, 3, 4]),
                ([(2, 1), (3, 2), (4, 1)], [1, 0, 3])]
    checked_allocations = 0
    for design, caps in fixtures:
        exact = defaultdict(lambda: Fraction(0))
        for allocation in itertools.product(*(range(cap+1) for cap in caps)):
            checked_allocations += 1
            K = sum(allocation)
            exact[K] = max(exact[K], zero_probability(design, allocation))
        greedy = list(greedy_zero_profiles(design, caps))
        need(len(greedy) == sum(caps)+1, "Greedy prefix inventory incomplete")
        for K, allocation, probability in greedy:
            need(probability == exact[K] == zero_probability(design, allocation),
                 "Greedy zero-event profile differs from exhaustive maximum")
        for alpha in (Fraction(1, 20), Fraction(1, 4), Fraction(1, 2)):
            answer = zero_event_limit(design, caps, [0]*len(design), alpha)
            U = max(K for K, probability in exact.items() if probability >= alpha)
            need(answer["upper"] == U, "One-sided zero-event inversion differs")
            for K, probability in exact.items():
                if K > U:
                    need(probability < alpha, "Worst-allocation zero-event test exceeds alpha")
        answer = zero_event_limit(design, caps, [1]+[0]*(len(design)-1))
        need(answer["upper"] == sum(caps) and not answer["all_zero"],
             "Nonzero outcome must return full capacity")
    return {"status": "passed", "fixtures": len(fixtures),
            "exhaustively_checked_allocations": checked_allocations,
            "tests": ["exact rational marginal ordering", "all-total optimal profile versus exhaustive allocations",
                      "three alpha thresholds", "eligible-capacity constraints", "census stratum",
                      "one-sided worst-allocation rejection probability", "nonzero full-capacity fallback"]}


def self_checks(tail, interval):
    need(interval(7, 7, 0, .05) == (0, 0), "Zero-event census interval failed")
    need(interval(7, 7, 3, .05) == (3, 3), "Positive census interval failed")
    need(interval(100, 12, 0, .05)[1] > 0, "Noncensus zero-event bound collapsed")
    # Independent finite-support coverage enumeration, no simulation.
    for N in range(2, 11):
        for n in range(1, N + 1):
            for K in range(N + 1):
                coverage = 0.0
                for k in range(max(0, n-(N-K)), min(n, K)+1):
                    lo, hi = interval(N, n, k, .05)
                    if lo <= K <= hi:
                        coverage += math.comb(K, k)*math.comb(N-K, n-k)/math.comb(N, n)
                need(coverage >= .95-1e-12, "Enumerated finite interval undercoverage")
    need(component_counts(["1", "2"], {"1"}, {"2"}) == (1, 1, 0, 1, 1, 1),
         "Opposite-report graph fixture failed")
    need(component_counts(["1", "2"], set(), {"1", "2"}) == (0, 2, 0, 0, 1, 0),
         "Within-report graph fixture failed")
    need(not single_study("1", {"1": {"identity_label": "false_merge"}}),
         "Compound-ID union gate failed")
    valid_manifest = {"human_validated": False, "weighted_estimates_generated": False}
    invalid = [
        ({"human_validated": True, "review_type": "ai_assisted_initial_review", "cases": []}, valid_manifest),
        ({"human_validated": False, "review_type": "ai_assisted_initial_review", "cases": []},
         {"human_validated": False, "weighted_estimates_generated": True}),
        ({"human_validated": False, "review_type": "ai_assisted_initial_review", "cases": [
            {"human_confirmed": False, "review_complete": "yes", "missed_merge_search_status": "incomplete"}]}, valid_manifest),
        ({"human_validated": False, "review_type": "ai_assisted_initial_review", "cases": [
            {"human_confirmed": False, "review_complete": "no", "missed_merge_search_status": "complete"}]}, valid_manifest),
    ]
    for fake, bad_manifest in invalid:
        rejected = False
        try:
            guard_ai(fake, bad_manifest)
        except ValueError:
            rejected = True
        need(rejected, "Synthetic unsupported source provenance was accepted")
    return {"status": "passed", "zero_event_profile_checks": zero_event_self_checks(),
            "checks": ["census intervals", "noncensus zero-event upper bound",
            "exhaustive finite-population coverage for N=2..10 and every n,K",
            "opposite-report and within-report component fixtures", "compound-ID union exclusion",
            "synthetic human-validation, weighted-source, completion and exhaustive-search claim rejection"]}


METHODS = """AI label prevalence and counterpart geometry, version 1.0.0

The source is the fixed AI-assisted initial review, not independent human
adjudication. One label observation is a sampled paper_id. The 319 probability
IDs are a stratified hash-ranked sample of the matched AR5/AR6 union. The 12
targeted supplements remain separate and receive no probability weight.
Occurrence diagnostics use all 801 original entries attached to these IDs,
including unmatched occurrences. IDs unmatched in both reports are outside the
probability frame. Source strings, statuses, labels and weights are immutable.

Each AI outcome is a binary proposal/uncertainty indicator. The estimator is
sum_h(N_h*k_h/n_h)/sum_h(N_h). It projects AI-coded labels, conditional on the
unvalidated coding protocol. It is not precision, recall or a true error rate.
Treat the seeded hash ordering as simple random sampling without replacement
within the fixed strata. Invert exact hypergeometric tails for the unknown
number of positive AI labels in each stratum. For each outcome separately,
allocate alpha=.05 equally across its noncensus strata; aggregate the resulting
simultaneous stratum count bounds. Census strata have point intervals. The
shared false-sharing outcome uses only its eleven shared-membership strata;
its unequal sampling fractions preclude a binomial rule-of-three interval.
The 95% intervals describe sampling only and exclude AI classification error,
incomplete duplicate search, extraction error and independent human agreement.
The five outcome intervals have separate coverage, not joint across outcomes.

A separate one-sided 95% zero-event procedure is also reported for shared-ID
false-sharing AI labels. For total K, maximize P0=product_h C(N_h-K_h,n_h)/
C(N_h,n_h) over allocations summing to K. Incrementing K_h multiplies P0 by
1-n_h/(N_h-K_h). The negative-log marginal cost increases with K_h, so selecting
the smallest available marginal cost gives the global minimum cumulative cost
for each total K while respecting prefix allocations. The implementation uses
exact Fraction(n_h,N_h-K_h) priorities and exact rational products. U is the
largest K with maximized P0>=.05. It records the maximizing allocation, product,
log product and the next-total product. The probability calculations and alpha
comparison use exact rational arithmetic, without a Bonferroni adjustment.
For any actual allocation with total K>U, the probability of observing all
zeros is at most the maximized P0(K)<.05. On any nonzero observation the defined
procedure returns the entire domain capacity. Thus its unconditional coverage
is at least 95% under stratified SRS and stable hypothetical AI labels. The
older two-sided conservative simultaneous-stratum intervals remain visible.

For the through-2013 variant, reconstruct each shared stratum's eligible-ID
capacity E_h from unchanged preferred years, validate all N_h against the
frozen frame, and require K_h<=E_h. This profiles the number of eligible positive
AI labels, not the total number. Ineligible positive labels only reduce P0, so
zero ineligible labels maximize zero-event probability at a fixed eligible
total. It receives a separate per-outcome one-sided interval, not joint coverage
with the all-shared result. The same 94-ID all-zero event is used. Fixed-
denominator loss scenarios subtract the all-shared U only from shared numerators
for retention/reuse, and the eligibility-constrained U only from the through-
2013 numerator. They preserve all denominators and original points. They assume
hypothetical projected AI flags remove shared identities; they are neither
true-error bounds nor corrected recurrence estimates. No AI classification
error is quantified. Exhaustive small-stratum allocation checks verify every
total's maximizing product, census behavior and constrained capacities.

Geometry joins every positive counterpart edge to unchanged snapshot report
memberships and occurrence matching statuses. Only edges with both endpoints
classified correct_merge in the canonical AI sample are eligible for automatic
whole-ID union. Compound IDs, unresolved IDs and unsampled counterpart IDs are
blocked. This conservative restriction avoids guessing that an unreviewed ID
contains one study. All edges, including blocked ones, remain in the table.
Connected components of eligible edges define an explicit accept-eligible-
proposals scenario. A component contributes at most one identity per report;
it contributes one shared identity if it has a matched occurrence in both.
No occurrence status is changed. The baseline and proposal scenario are not
bounds on true recurrence or calibrated corrections. Untested counterpart
edges and compound work groups remain outside this scenario. Conditional
reuse and metadata-corrected ages are not inferred from aggregate flags.

Field-specific counts separate proposed errors, typed unresolved fields and
missing stored fields. An unresolved judgment without typed fields is recorded
as unspecified. Explicit uncertainty may coexist with a definite error in
another field. Rows can overlap. Missing metadata alone is not an error label.

The seed is 20261011, inherited from the frozen design. There are no new random
draws or API requests. The database is opened with mode=ro&immutable=1. Every
prior baseline29, raw-DOI10 and AI-descriptive6 CSV hash is checked before the
run. Source hashes are checked again before writing. Human-label files and the
human estimator are untouched. All outputs belong to this separate registry.
"""


def run(repo, output, check_only):
    ann = repo / "revision_preparation/annotation"
    ai = ann / "ai_review"
    manifest, proposal = read_json(ai / "manifest.json"), read_json(ai / "proposals.json")
    guard_ai(proposal, manifest)
    check = subprocess.run([sys.executable, str(repo / "scripts/summarize_ai_annotation_proposals.py"),
                            "--repo", str(repo), "--check"], capture_output=True, text=True)
    need(check.returncode == 0, "Existing guarded AI summary failed: " + check.stdout + check.stderr)
    frozen = read_json(ann / "input_manifest.json")
    need(frozen["sampling_seed"] == SEED == manifest["sampling_seed_unchanged"], "Seed changed")
    papers = read_csv(ann / "inputs/revision_identity_audit_sample_papers.csv")
    entries = read_csv(ann / "inputs/revision_identity_audit_sample_entries.csv")
    strata = read_csv(ann / "inputs/revision_identity_audit_strata.csv")
    pmap, cmap = unique(papers, "paper_id"), unique(proposal["cases"], "paper_id")
    emap = unique(entries, "occurrence_id")
    need(set(pmap) == set(cmap) and len(emap) == 801, "Frozen source coverage differs")
    protected = {}

    def protect(path, expected=None):
        digest = sha(path)
        need(expected is None or digest == expected, "Protected input differs: " + path.name)
        protected[path.relative_to(repo).as_posix()] = {"sha256": digest, "bytes": path.stat().st_size}

    for path in [ann / "input_manifest.json", ann / "annotation_schema.json", ai / "manifest.json",
                 ann / "annotation_tools.py", repo / "scripts/summarize_ai_annotation_proposals.py",
                 repo / "scripts/check_ai_annotation_proposals.py"]:
        protect(path)
    for item in frozen["sources"].values():
        protect(ann / "inputs" / item["filename"], item["sha256"])
    for name, item in manifest["files"].items():
        protect(ai / name, item["sha256"])
    for name in ["id_annotations.csv", "occurrence_annotations.csv"]:
        protect(ann / "templates" / name)
    previous = []
    for relative, key, expected in [
        ("reproducibility/reference/manifest.json", "tables", 29),
        ("revision_preparation/robustness/output_v1/manifest.json", "registered_tables", 10),
        ("revision_preparation/annotation/ai_review_summary_v1/manifest.json", "registered_outputs", 6),
    ]:
        path = repo / relative
        reg = read_json(path)
        protect(path)
        tables = [x for x in reg[key] if x["filename"].endswith(".csv")]
        need(reg["seed"] == SEED and len(tables) == expected, "Prior inventory changed")
        for item in reg[key]:
            protect(path.parent / item["filename"], item["sha256"])
        previous.append({"registry": relative, "CSV_tables_verified": len(tables)})
    db = repo / "database/ipcc_litdb.sqlite"
    protect(db, frozen["database_sha256"])
    tail, interval = load_math(ann / "annotation_tools.py")
    tests = self_checks(tail, interval)
    probability = [p for p in papers if GROUPS[p["selection"]] == "probability_sample"]
    outcomes = ("false_merge_proposal", "any_metadata_error_proposal",
                "any_unresolved_metadata_judgment", "counterpart_proposal")
    rates, stratum_rows, targeted = [], [], []
    for outcome in (*outcomes, "false_sharing_proposal"):
        row, details = weighted_labels(strata, probability, cmap, outcome, interval,
                                      "shared_matched|" if outcome == "false_sharing_proposal" else None)
        rates.append(row)
        stratum_rows.extend(details)
        pp = [p for p in papers if GROUPS[p["selection"]] == "targeted_supplement"
              and (outcome != "false_sharing_proposal" or p["AR5_status_set"] != "absent"
                   and p["AR6_status_set"] != "absent")]
        if outcome == "false_sharing_proposal":
            pp = [p for p in pp if p["AR5_status_set"] in {"doi_only", "title_only", "doi_and_title"}
                  and p["AR6_status_set"] in {"doi_only", "title_only", "doi_and_title"}]
        targeted.append({"outcome": outcome, "targeted_sample_ID_denominator": len(pp),
                         "positive_AI_sample_IDs": sum(label(cmap[p["paper_id"]], outcome) for p in pp),
                         "probability_weight_available": "no"})

    status_rows, unmatched, field_rows = [], [], []
    all_occ = {o["occurrence_id"]: o for c in cmap.values() for o in c["occurrences"]}
    missing_key = {"doi": "paper_doi", "authors": "authors_best", "title": "title_best",
                   "year": "year_best", "journal": "journal_best"}
    for group in GROUPS.values():
        rr = [r for r in entries if GROUPS[r["selection"]] == group]
        for status in ("matched_doi", "matched_title", "unmatched"):
            selected = [r for r in rr if r["match_status"] == status]
            status_rows.append({"sample_group": group, "match_status": status,
                                "occurrences": len(selected), "distinct_sampled_IDs": len({r['paper_id'] for r in selected}),
                                "all_sample_occurrences": len(rr)})
        for r in rr:
            if r["match_status"] == "unmatched":
                unmatched.append({"sample_group": group, **{k: r[k] for k in
                    ("paper_id", "occurrence_id", "report_id", "wg_label", "source_id", "source_page")},
                    "raw_reference_sha256_utf8": hashlib.sha256(r["raw_reference"].encode()).hexdigest()})
        for field in FIELDS:
            error, uncertain, missing = [], [], []
            for r in rr:
                o = all_occ[r["occurrence_id"]]
                if o["metadata_judgment"] == "metadata_error" and field in values(o["metadata_error_fields"]):
                    error.append(r)
                uf = values(o.get("metadata_unresolved_fields"))
                if field in uf or (field == "unspecified" and not uf and o["metadata_judgment"] == "unresolved"):
                    uncertain.append(r)
                if field in missing_key and not r[missing_key[field]].strip():
                    missing.append(r)
            field_rows.append({"sample_group": group, "field": field,
                "error_occurrences": len(error), "error_IDs": len({r['paper_id'] for r in error}),
                "explicitly_unresolved_occurrences": len(uncertain),
                "explicitly_unresolved_IDs": len({r['paper_id'] for r in uncertain}),
                "missing_preferred_occurrences": len(missing), "missing_preferred_IDs": len({r['paper_id'] for r in missing}),
                "occurrence_denominator": len(rr)})

    edges = [(pid, q) for pid, c in cmap.items() for q in c["missed_merge_candidate_ids"]]
    nodes = sorted({p for e in edges for p in e}, key=int)
    memberships = defaultdict(lambda: defaultdict(set))
    shared_status, shared_groups, shared_year = defaultdict(set), defaultdict(set), {}
    with sqlite3.connect(db.as_uri() + "?mode=ro&immutable=1", uri=True) as connection:
        a, b = set(), set()
        for pid, report in connection.execute("SELECT DISTINCT r.paper_id,c.report_id FROM references_occurrence r JOIN chapters c ON c.chapter_id=r.chapter_id WHERE c.report_id IN ('AR5','AR6') AND r.match_status IN ('matched_doi','matched_title')"):
            (a if report == "AR5" else b).add(str(pid))
        shared_set = a & b
        for pid, report, status, group, preferred_year in connection.execute(
            "SELECT r.paper_id,c.report_id,r.match_status,w.wg_label,p.year_best "
            "FROM references_occurrence r JOIN chapters c ON c.chapter_id=r.chapter_id "
            "LEFT JOIN working_groups w ON w.wg_id=c.wg_id JOIN papers p ON p.paper_id=r.paper_id "
            "WHERE c.report_id IN ('AR5','AR6')"):
            pid = str(pid)
            if pid not in shared_set:
                continue
            shared_status[pid].add(status)
            if report == "AR6" and status in MATCHED and group in {"WG1", "WG2", "WG3"}:
                shared_groups[pid].add(group)
            need(pid not in shared_year or shared_year[pid] == preferred_year,
                 "Shared-ID preferred metadata differ across occurrences")
            shared_year[pid] = preferred_year
        placeholders = ",".join("?" for _ in nodes)
        known = {str(r[0]) for r in connection.execute("SELECT paper_id FROM papers WHERE paper_id IN ("+placeholders+")", nodes)}
        need(known == set(nodes), "Counterpart endpoint absent from snapshot")
        for pid, report, status in connection.execute("SELECT DISTINCT r.paper_id,c.report_id,r.match_status FROM references_occurrence r JOIN chapters c ON c.chapter_id=r.chapter_id WHERE r.paper_id IN ("+placeholders+")", nodes):
            memberships[str(pid)][report].add(status)
    base = next(r for r in read_csv(repo / "reproducibility/reference/revision_identity_transitions.csv")
                if r["earlier"] == "AR5" and r["later"] == "AR6" and r["definition"] == "matched")
    need((len(a), len(b), len(a & b)) == tuple(int(base[k]) for k in ("papers_a", "papers_b", "shared")),
         "Snapshot matched memberships disagree with frozen baseline")
    shared_strata = [s for s in strata if s["stratum"].startswith("shared_matched|")]
    frame_assignment, frame_counts, eligible_counts = {}, Counter(), Counter()
    eligible_shared = set()
    for pid in sorted(a & b, key=int):
        statuses, groups = shared_status[pid], shared_groups[pid]
        d, t = "matched_doi" in statuses, "matched_title" in statuses
        status = "doi_and_title" if d and t else "doi_only" if d else "title_only" if t else "unmatched_only"
        wg = next(iter(groups)) if len(groups) == 1 else "multiple_groups" if groups else "other_units"
        stratum = "shared_matched|" + status + "|" + wg
        frame_assignment[pid] = stratum
        frame_counts[stratum] += 1
        yr = str(shared_year[pid] or "").strip()
        if len(yr) == 4 and yr.isdecimal() and yr[0] in "12" and int(yr) <= 2013:
            eligible_counts[stratum] += 1
            eligible_shared.add(pid)
    need(dict(frame_counts) == {s["stratum"]: int(s["population_ids"]) for s in shared_strata},
         "Reconstructed shared stratum populations differ from frozen frame")
    for p in probability:
        if p["stratum"].startswith("shared_matched|"):
            need(frame_assignment[p["paper_id"]] == p["stratum"], "Shared sample stratum differs")
    eligibility = next(r for r in read_csv(repo / "reproducibility/reference/revision_identity_eligible_reuse.csv")
        if r["earlier"] == "AR5" and r["later"] == "AR6" and r["working_group"] == "All"
        and r["cutoff_year"] == "2013" and r["earlier_membership"] == "earlier_matched")
    need(len(eligible_shared) == int(eligibility["shared_eligible_ids"]), "Eligible shared census differs")
    design = [(int(s["population_ids"]), int(s["sampled_ids"])) for s in shared_strata]
    observed = [sum(label(cmap[p["paper_id"]], "false_sharing_proposal") for p in probability
                    if p["stratum"] == s["stratum"]) for s in shared_strata]
    need(not any(observed), "Source is no longer the all-zero AI false-sharing case")
    zero_limits, zero_allocations, limits = [], [], {}
    for domain, caps in (("all_shared_AI_labels", [N for N, n in design]),
                         ("preferred_year_through_2013_shared_AI_labels",
                          [eligible_counts[s["stratum"]] for s in shared_strata])):
        answer = zero_event_limit(design, caps, observed)
        limits[domain] = answer["upper"]
        p0, p1 = answer["probability"], answer["next_probability"]
        zero_limits.append({"domain": domain, "population_shared_IDs": len(a & b),
            "domain_capacity_IDs": sum(caps), "sample_IDs_in_zero_event": sum(n for N, n in design),
            "observed_positive_AI_labels": sum(observed), "strata": len(design),
            "confidence_side": "one_sided", "confidence_level": .95, "alpha": .05,
            "upper_positive_AI_IDs": answer["upper"], "upper_fraction_of_domain_capacity": answer["upper"]/sum(caps),
            "max_P_zero_at_upper": float(p0), "log_max_P_zero_at_upper": fraction_log(p0),
            "exact_P_zero_numerator": str(p0.numerator), "exact_P_zero_denominator": str(p0.denominator),
            "next_total_K": answer["next_K"], "max_P_zero_at_next": float(p1) if p1 is not None else None,
            "log_max_P_zero_at_next": fraction_log(p1) if p1 is not None else None,
            "AI_classification_error_quantified": "no", "human_validated": "false"})
        for i, s in enumerate(shared_strata):
            N, n = design[i]
            allocation = answer["allocation"][i]
            pi = zero_probability([(N, n)], [allocation])
            zero_allocations.append({"domain": domain, "stratum": s["stratum"], "population_IDs": N,
                "sample_IDs": n, "observed_positive_AI_labels": observed[i],
                "positive_AI_ID_capacity": caps[i], "upper_total_maximizing_allocation": allocation,
                "P_zero_in_stratum_at_allocation": float(pi), "log_P_zero_in_stratum": fraction_log(pi),
                "next_total_maximizing_allocation": answer["next_allocation"][i]
                    if answer["next_allocation"] is not None else None})
    loss_scenarios = []
    for metric, numerator, denominator, domain in (
        ("retention", len(a & b), len(a), "all_shared_AI_labels"),
        ("reuse", len(a & b), len(b), "all_shared_AI_labels"),
        ("conditional_reuse_through_2013", len(eligible_shared),
         int(eligibility["temporally_eligible_later_ids"]),
         "preferred_year_through_2013_shared_AI_labels"),
    ):
        for scenario, removed in (("unchanged_snapshot", 0),
            ("one_sided_upper_AI_label_projection_numerator_loss", limits[domain])):
            loss_scenarios.append({"scenario": scenario, "metric": metric, "source_upper_domain": domain,
                "original_shared_numerator": numerator, "fixed_denominator": denominator,
                "hypothetical_shared_ID_loss": removed, "scenario_shared_numerator": numerator-removed,
                "original_fraction": numerator/denominator, "scenario_fraction": (numerator-removed)/denominator,
                "true_error_bound": "no", "estimated_correction": "no"})
    geometry, eligible = [], set()
    for source, candidate in sorted(edges, key=lambda e: (int(e[0]), int(e[1]))):
        safe = single_study(source, cmap) and single_study(candidate, cmap)
        row = {"source_paper_id": source, "candidate_paper_id": candidate,
               "source_sample_group": GROUPS[pmap[source]["selection"]],
               "source_identity_AI_proposal": cmap[source]["identity_label"],
               "candidate_identity_AI_proposal": cmap[candidate]["identity_label"] if candidate in cmap else "outside_reviewed_sample",
               "automatic_union_eligible": "yes" if safe else "no"}
        for prefix, pid in (("source", source), ("candidate", candidate)):
            for report in ("AR5", "AR6"):
                statuses = memberships[pid].get(report, set())
                row[prefix + "_" + report + "_extracted"] = int(bool(statuses))
                row[prefix + "_" + report + "_matched"] = int(bool(statuses & MATCHED))
                row[prefix + "_" + report + "_statuses"] = "|".join(sorted(statuses))
            row[prefix + "_all_reports"] = "|".join(sorted(memberships[pid]))
        x, y = (source in a, source in b), (candidate in a, candidate in b)
        row["matched_pair_geometry"] = (
            "endpoint_without_pair_matched_membership" if not any(x) or not any(y) else
            "opposite_report_only" if x in ((True, False), (False, True)) and y == x[::-1] else
            "both_already_shared" if all(x) and all(y) else
            "same_report_only" if x == y else "shared_plus_single_report")
        row["blocked_reason"] = "" if safe else "Both endpoints must have canonical correct_merge proposals; compound or unreviewed identities are not whole-ID unions"
        geometry.append(row)
        if safe:
            eligible.add(tuple(sorted((source, candidate), key=int)))
    components = []
    for index, component in enumerate(graph_components(eligible), 1):
        n5, n6, ns, new5, new6, news = component_counts(component, a, b)
        components.append({"component": index, "paper_ids": "|".join(component),
                           "nodes": len(component), "original_AR5_IDs": n5, "original_AR6_IDs": n6,
                           "original_shared_IDs": ns, "scenario_AR5_IDs": new5, "scenario_AR6_IDs": new6,
                           "scenario_shared_IDs": news, "delta_AR5_IDs": new5-n5,
                           "delta_AR6_IDs": new6-n6, "delta_shared_IDs": news-ns})
    geometry_counts = []
    for group in GROUPS.values():
        gg = [r for r in geometry if r["source_sample_group"] == group]
        for category in ("opposite_report_only", "endpoint_without_pair_matched_membership",
                         "shared_plus_single_report", "same_report_only", "both_already_shared"):
            rr = [r for r in gg if r["matched_pair_geometry"] == category]
            geometry_counts.append({"source_sample_group": group, "matched_pair_geometry": category,
                "positive_directed_edges": len(rr), "distinct_source_IDs": len({r['source_paper_id'] for r in rr}),
                "distinct_candidate_IDs": len({r['candidate_paper_id'] for r in rr}),
                "automatic_union_eligible_edges": sum(r["automatic_union_eligible"] == "yes" for r in rr),
                "selection_edge_denominator": len(gg)})
    scenarios = []
    for scenario, accept in (("unchanged_snapshot", False), ("accept_eligible_single_study_AI_edges", True)):
        n5, n6, ns = len(a), len(b), len(a & b)
        if accept:
            n5 += sum(c["delta_AR5_IDs"] for c in components)
            n6 += sum(c["delta_AR6_IDs"] for c in components)
            ns += sum(c["delta_shared_IDs"] for c in components)
        scenarios.append({"scenario": scenario, "AR5_IDs": n5, "AR6_IDs": n6, "shared_IDs": ns,
                          "retention": ns/n5, "reuse": ns/n6,
                          "accepted_distinct_edges": len(eligible) if accept else 0,
                          "accepted_components": len(components) if accept else 0,
                          "estimated_correction": "no", "true_recurrence_bound": "no"})

    outputs = {
        "AI_label_prevalence.csv": encode_csv(rates),
        "AI_label_stratum_counts.csv": encode_csv(stratum_rows),
        "AI_zero_event_one_sided_limits.csv": encode_csv(zero_limits),
        "AI_zero_event_upper_allocations.csv": encode_csv(zero_allocations),
        "fixed_denominator_AI_loss_scenarios.csv": encode_csv(loss_scenarios),
        "targeted_AI_label_counts.csv": encode_csv(targeted),
        "sample_occurrence_status_counts.csv": encode_csv(status_rows),
        "sample_unmatched_entries.csv": encode_csv(unmatched),
        "field_specific_diagnostics.csv": encode_csv(field_rows),
        "counterpart_edge_geometry.csv": encode_csv(geometry),
        "counterpart_geometry_counts.csv": encode_csv(geometry_counts),
        "eligible_counterpart_components.csv": encode_csv(components, ["component", "paper_ids", "nodes",
            "original_AR5_IDs", "original_AR6_IDs", "original_shared_IDs", "scenario_AR5_IDs",
            "scenario_AR6_IDs", "scenario_shared_IDs", "delta_AR5_IDs", "delta_AR6_IDs", "delta_shared_IDs"]),
        "counterpart_scenarios.csv": encode_csv(scenarios), "methods.txt": METHODS.encode(),
        "self_checks.json": encode_json(tests),
    }
    registry = {"schema_version": 1, "analysis_version": VERSION, "seed": SEED,
        "random_draws_used": False, "source_artifact_version": manifest["artifact_version"],
        "source_updated_utc": manifest["updated_utc"], "human_validated": False,
        "true_accuracy_estimated": False, "AI_classification_error_quantified": False,
        "design_weighted_AI_label_prevalence": True, "interval_scope": "Sampling only, conditional on unvalidated AI coding",
        "zero_event_interval": "Exact one-sided95% worst-allocation profile, separate from conservative two-sided stratum intervals",
        "zero_event_eligibility": "Through2013 capacities reconstructed and checked per frozen shared stratum",
        "targeted_supplements_weighted": False, "counterpart_scenarios_are_true_bounds": False,
        "database_modified": False, "human_labels_modified": False, "prior_registries_verified": previous,
        "input_files": protected,
        "script": {"path": Path(__file__).resolve().relative_to(repo).as_posix(), "sha256": sha(Path(__file__))},
        "counts": {"probability_IDs": len(probability), "targeted_IDs": len(papers)-len(probability),
            "sample_entries": len(entries), "positive_directed_edges": len(edges),
            "IDs_with_positive_edges": len({a for a, b in edges}),
            "distinct_counterpart_IDs": len({b for a, b in edges}),
            "directed_edges_with_unreviewed_candidate_ID": sum(r["candidate_identity_AI_proposal"] == "outside_reviewed_sample" for r in geometry),
            "eligible_distinct_edges": len(eligible), "eligible_components": len(components)},
        "registered_outputs": []}
    for name, data in outputs.items():
        record = {"filename": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        if name.endswith(".csv"):
            record["rows"] = len(list(csv.DictReader(io.StringIO(data.decode()))))
        registry["registered_outputs"].append(record)
    outputs["manifest.json"] = encode_json(registry)
    for name, record in protected.items():
        need(sha(repo / name) == record["sha256"], "Input changed during analysis: " + name)
    protected_paths = {(repo / name).resolve() for name in protected}
    need(all((output/name).resolve() not in protected_paths for name in outputs), "Protected input overwrite")
    if check_only:
        for name, data in outputs.items():
            need((output / name).read_bytes() == data, "Registered output differs: " + name)
    else:
        need(not output.exists(), "Choose a fresh independent output directory")
        output.mkdir(parents=True)
        for name, data in outputs.items():
            (output/name).write_bytes(data)
    print(json.dumps({"status": "passed", "check_only": check_only, "analysis_version": VERSION,
                      "AI_label_prevalence": rates, "counterpart_scenarios": scenarios,
                      "zero_event_limits": zero_limits, "fixed_denominator_AI_loss_scenarios": loss_scenarios,
                      "counts": registry["counts"], "human_validated": False}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        repo = args.repo.resolve()
        run(repo, (args.output or repo / DEFAULT_OUTPUT).resolve(), args.check)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        print(json.dumps({"status": "failed", "error": str(error), "human_validated": False}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
