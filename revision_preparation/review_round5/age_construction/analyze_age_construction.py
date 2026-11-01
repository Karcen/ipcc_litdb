#!/usr/bin/env python3
"""Local-only construction counts and age/cluster-composition sensitivities.

Reads the frozen corrected snapshot and existing parser implementations. Writes
only a new output registry. It neither changes identities nor estimates human
accuracy from model agreement. Run with the archived NumPy 2.2.6 environment.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sqlite3
import sys

import numpy as np

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
sys.path.insert(0, str(REPO / 'reproducibility'))
import review_round2_raw_age as prior
import revision_identity as identity

VERSION = '1.6.0-age-construction-2'
SEED = 20261011
CAPS = (50, 60, 100, 150, 200, None)
ORIGINS = ('source_publication_year', 'documented_cutoff_year')
COHORTS = ('all_extracted', 'matched_occurrences', 'unmatched_occurrences',
           'raw_DOI_literal_occurrences', 'raw_DOI_repaired_occurrences')
BOOT_COHORTS = COHORTS[:3]
METRICS = ('mean_age', 'median_inverse_ecdf', 'q25_inverse_ecdf',
           'q75_inverse_ecdf', 'recent_le5')
MATCHED = {'matched_doi', 'matched_title'}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1048576), b''):
            h.update(b)
    return h.hexdigest()


def csv_data(rows):
    if not rows:
        raise ValueError('Empty registry table is not allowed')
    out = io.StringIO(newline='')
    w = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator='\n')
    w.writeheader()
    w.writerows(rows)
    return out.getvalue().encode()


def cohort_rows(rows, cohort):
    if cohort == 'all_extracted':
        return rows
    if cohort == 'matched_occurrences':
        return [r for r in rows if r['match_status'] in MATCHED]
    if cohort == 'unmatched_occurrences':
        return [r for r in rows if r['match_status'] not in MATCHED]
    key = 'literal_tokens' if cohort == 'raw_DOI_literal_occurrences' else 'repaired_tokens'
    return [r for r in rows if r[key]]


def status(row, origin, cap):
    base, cited = row[origin], row['raw_leading_year']
    if base is None:
        return 'missing_or_ambiguous_origin', None
    if cited is None:
        return 'missing_cited_year', None
    if cited > base:
        return 'cited_year_after_origin', None
    age = base - cited
    if cap is not None and age > cap:
        return 'age_above_cap', None
    return 'included', age


def stats(ages, weights=None):
    if not len(ages):
        return {k: None for k in ('weight_denominator',) + METRICS}
    a = np.asarray(ages, dtype=int)
    h = np.bincount(a, weights=weights)
    total = float(h.sum())
    cumulative = np.cumsum(h)
    return {'weight_denominator': total,
            'mean_age': float(np.dot(h, np.arange(len(h))) / total),
            'median_inverse_ecdf': int(np.searchsorted(cumulative, .5 * total, side='left')),
            'q25_inverse_ecdf': int(np.searchsorted(cumulative, .25 * total, side='left')),
            'q75_inverse_ecdf': int(np.searchsorted(cumulative, .75 * total, side='left')),
            'recent_le5': float(h[:6].sum() / total)}


def histogram_stats(h):
    h = np.atleast_2d(h)
    if not np.all(np.isfinite(h)):
        raise ValueError('Nonfinite histogram')
    totals = h.sum(axis=1)
    if np.any(totals <= 0):
        raise ValueError('Empty cluster draw; no replicate is discarded')
    cum = h.cumsum(axis=1)
    return np.column_stack(((h * np.arange(h.shape[1])).sum(axis=1) / totals,
                            np.argmax(cum >= .5 * totals[:, None], axis=1),
                            np.argmax(cum >= .25 * totals[:, None], axis=1),
                            np.argmax(cum >= .75 * totals[:, None], axis=1),
                            h[:, :6].sum(axis=1) / totals))


def bootstrap(selected, origin, scheme, replicates, rng):
    chapters = sorted({r['chapter_id'] for r in selected})
    pos = {c: i for i, c in enumerate(chapters)}
    group_by_chapter = defaultdict(set)
    for r in selected:
        group_by_chapter[r['chapter_id']].add(r['wg_label'])
    if any(len(v) != 1 for v in group_by_chapter.values()):
        raise ValueError('A chapter maps to multiple WG strata')
    strata = defaultdict(list)
    for chapter in chapters:
        group = next(iter(group_by_chapter[chapter])) if scheme == 'WG_stratified' else 'all_chapters'
        strata[group].append(pos[chapter])
    hist = np.zeros((len(chapters), 61), dtype=float)
    for r in selected:
        reason, age = status(r, origin, 60)
        if reason == 'included':
            hist[pos[r['chapter_id']], age] += 1
    draws = np.zeros((replicates, len(chapters)), dtype=float)
    for indices in strata.values():
        n = len(indices)
        draws[:, indices] = rng.multinomial(n, np.full(n, 1 / n), size=replicates)
        if not np.all(draws[:, indices].sum(axis=1) == n):
            raise AssertionError('Cluster count changed inside a stratum')
    point = histogram_stats(hist.sum(axis=0))[0]
    estimates = histogram_stats(np.einsum('bc,ca->ba', draws, hist, optimize=False))
    deletions = []
    for group, indices in sorted(strata.items()):
        for index in indices:
            if len(indices) == 1:
                deletions.append({'stratum': group, 'chapter_id': chapters[index],
                                  'stratum_chapters': 1, 'estimable': False,
                                  **{k: None for k in METRICS}})
                continue
            weights = np.ones(len(chapters))
            weights[indices] *= len(indices) / (len(indices) - 1)
            weights[index] = 0
            deletion = histogram_stats(np.einsum('c,ca->a', weights, hist, optimize=False))[0]
            deletions.append({'stratum': group, 'chapter_id': chapters[index],
                              'stratum_chapters': len(indices), 'estimable': True,
                              **dict(zip(METRICS, deletion.tolist()))})
    return point, estimates, deletions, len(chapters), len(strata)


def build(rows, corrections, replicates):
    construction, cross, summaries, exclusions, differences = [], [], [], [], []
    for report in prior.REPORTS:
        all_report = [r for r in rows if r['report_id'] == report]
        groups = ['All'] + sorted({r['wg_label'] for r in all_report})
        for group in groups:
            rr = [r for r in all_report if group == 'All' or r['wg_label'] == group]
            n = len(rr)
            counts = Counter(r['match_status'] for r in rr)
            construction.append({'report': report, 'working_group': group,
                'extracted_occurrences': n, 'distinct_assigned_IDs': len({r['paper_id'] for r in rr}),
                'chapters': len({r['chapter_id'] for r in rr}),
                'raw_DOI_regex_candidate_occurrences': sum(r['doi_regex_candidates'] > 0 for r in rr),
                'usable_literal_DOI_occurrences': sum(bool(r['literal_tokens']) for r in rr),
                'single_literal_DOI_occurrences': sum(len(r['literal_tokens']) == 1 for r in rr),
                'usable_repaired_DOI_occurrences': sum(bool(r['repaired_tokens']) for r in rr),
                'single_repaired_DOI_occurrences': sum(len(r['repaired_tokens']) == 1 for r in rr),
                'matched_DOI_occurrences': counts['matched_doi'],
                'matched_title_occurrences': counts['matched_title'],
                'matched_occurrences': sum(counts[s] for s in MATCHED),
                'unmatched_occurrences': sum(v for k, v in counts.items() if k not in MATCHED),
                'literal_DOI_presence_fraction': sum(bool(r['literal_tokens']) for r in rr) / n,
                'repaired_DOI_presence_fraction': sum(bool(r['repaired_tokens']) for r in rr) / n,
                'matched_occurrence_fraction': sum(counts[s] for s in MATCHED) / n})
            for parser, field in [('literal', 'literal_tokens'), ('punctuation_repaired', 'repaired_tokens')]:
                cc = Counter((min(len(r[field]), 2), r['match_status']) for r in rr)
                for (count, match_status), value in sorted(cc.items()):
                    cross.append({'report': report, 'working_group': group, 'DOI_parser': parser,
                                  'raw_DOI_count_class': str(count) if count < 2 else '2_or_more',
                                  'match_status': match_status, 'occurrences': value,
                                  'report_WG_denominator': n})
            for cohort in COHORTS:
                selected = cohort_rows(rr, cohort)
                for origin in ORIGINS:
                    for cap in CAPS:
                        included, reasons = [], Counter()
                        for r in selected:
                            reason, age = status(r, origin, cap)
                            reasons[reason] += 1
                            if reason == 'included':
                                included.append((r, age))
                        base = {'report': report, 'working_group': group, 'cohort': cohort,
                                'age_origin': origin, 'age_max': 'unbounded_nonnegative' if cap is None else cap}
                        if sum(reasons.values()) != len(selected):
                            raise AssertionError('Exclusions fail to account for all selected occurrences')
                        for reason in ['included', 'missing_or_ambiguous_origin', 'missing_cited_year',
                                       'cited_year_after_origin', 'age_above_cap']:
                            exclusions.append(base | {'reason': reason, 'occurrences': reasons[reason],
                                'selected_occurrences': len(selected),
                                'selected_fraction': reasons[reason] / len(selected) if selected else None})
                        for weighting in ['occurrence', 'equal_assigned_ID']:
                            pp = Counter(r['paper_id'] for r, age in included)
                            weights = None if weighting == 'occurrence' else np.array([1 / pp[r['paper_id']] for r, age in included])
                            summaries.append(base | {'weighting': weighting,
                                'selected_occurrences': len(selected), 'included_occurrences': len(included),
                                'included_assigned_IDs': len(pp), **stats([age for r, age in included], weights)})
    index = {tuple(r[k] for k in ['report', 'working_group', 'cohort', 'age_origin', 'age_max', 'weighting']): r for r in summaries}
    for key, later in index.items():
        if key[0] != 'AR6' or ('AR5',) + key[1:] not in index:
            continue
        earlier = index[('AR5',) + key[1:]]
        row = {k: later[k] for k in ['working_group', 'cohort', 'age_origin', 'age_max', 'weighting']}
        for metric in METRICS:
            a, b = earlier[metric], later[metric]
            row.update({'AR5_' + metric: a, 'AR6_' + metric: b,
                        metric + '_difference': b - a if a is not None and b is not None else None})
        differences.append(row)
    intervals, bootstrap_draws, delete_rows = [], [], []
    combinations = [(cohort, origin, scheme) for cohort in BOOT_COHORTS for origin in ORIGINS
                    for scheme in ['unstratified', 'WG_stratified']]
    streams = np.random.SeedSequence(SEED).spawn(2 * len(combinations))
    for combination_index, (cohort, origin, scheme) in enumerate(combinations):
        points, samples = {}, {}
        for report_index, report in enumerate(prior.REPORTS):
            selected = cohort_rows([r for r in rows if r['report_id'] == report], cohort)
            point, estimates, deletions, chapters, strata = bootstrap(selected, origin, scheme, replicates,
                np.random.default_rng(streams[2 * combination_index + report_index]))
            points[report], samples[report] = point, estimates
            for row in deletions:
                delete_rows.append({'report': report, 'cohort': cohort, 'age_origin': origin,
                                    'bootstrap_scheme': scheme, **row})
            for i, sample in enumerate(estimates):
                bootstrap_draws.append({'report': report, 'cohort': cohort, 'age_origin': origin,
                    'bootstrap_scheme': scheme, 'replicate': i, **dict(zip(METRICS, sample.tolist()))})
        change = points['AR6'] - points['AR5']
        draws = samples['AR6'] - samples['AR5']
        lo, hi = np.quantile(draws, [.025, .975], axis=0, method='linear')
        for i, metric in enumerate(METRICS):
            intervals.append({'cohort': cohort, 'age_origin': origin, 'weighting': 'occurrence',
                'age_max': 60, 'bootstrap_scheme': scheme, 'metric': metric,
                'AR5': float(points['AR5'][i]), 'AR6': float(points['AR6'][i]),
                'difference': float(change[i]), 'difference_lower_95': float(lo[i]),
                'difference_upper_95': float(hi[i]), 'interval_includes_zero': bool(lo[i] <= 0 <= hi[i]),
                'replicates': replicates, 'interval_method': 'percentile_linear',
                'interval_scope': 'conditional_chapter_composition', 'coverage_calibrated': False})
    actor_counts = Counter((r['changed_by'], r['entity_type']) for r in corrections)
    correction_rows = [{'actor': actor or '', 'entity_type': entity, 'field_changes': n}
                       for (actor, entity), n in sorted(actor_counts.items())]
    return {'construction_by_report_WG.csv': construction, 'DOI_matching_cross_tab.csv': cross,
            'age_cap_summary.csv': summaries, 'age_cap_exclusions.csv': exclusions,
            'age_cap_differences.csv': differences, 'chapter_bootstrap_differences.csv': intervals,
            'chapter_bootstrap_draws.csv.gz': bootstrap_draws,
            'chapter_delete_one_sensitivity.csv': delete_rows,
            'correction_ledger_actor_counts.csv': correction_rows}


METHODS = '''Unit and immutable inputs
One original occurrence joins many-to-one to chapter, assigned paper, working
group and source. The corrected v1.2.0 SQLite hash must equal the v1.2.1 baseline
registry hash; its old29 CSV files and every imported helper are hash protected.
The new registry neither edits those tables nor changes raw strings or IDs.

Construction counts
All is the full report, including catalogue labels other than the three WGs.
Individual rows retain every observed working-group label. Counts are entry
occurrences, not unique works, unless the column explicitly states assigned IDs.
The literal DOI regex, normalization and usability gate are imported unchanged
from revision_identity.py. Repaired counts use its separate punctuation-repair
function. Presence means at least one usable token; single-token counts are
separate. A candidate regex hit is not automatically a usable DOI.

Year and age
The exact existing raw-year rule is re.search(r"\\b(1[6-9]\\d{2}|20\\d{2})[a-z]?\\b",
raw_reference[:300]). It selects the first lexical year in that prefix; it does
not parse an author--year syntactic field or independently verify dates.
Origins reuse unambiguous source-publication year and documented WG cutoff year.
Mutually exclusive exclusions, in priority order, are missing/ambiguous origin,
missing cited year, cited year after origin, and age above the chosen cap.
Caps50,60,100,150,200 and all nonnegative parsed ages are reported independently.
Every exclusion denominator is the selected report/WG/cohort entry population.
All cap summaries include occurrence and equal-assigned-ID weights; the latter
give total one to each included ID inside each report/WG/cohort population.
Quartiles and median use the left inverse empirical CDF. Median differences
compare those medians; no continuous-time precision is implied.
DOI-presence cohorts ignore stored matching status and include any entry with
at least one usable literal or repaired DOI; these are separate selected sets.

Chapter resampling
Only occurrence-weighted ages0--60 enter this additional interval analysis.
For each report/cohort, every chapter with selected entries enters the frame,
including chapters with zero included ages. Reports are sampled independently.
Unstratified draws resample the observed number of chapters across all WGs;
WG-stratified draws preserve chapter counts separately in every observed WG
label, including synthesis or combined units if present. These schemes share
the same observed point estimate but use separate fixed child random streams.
Each report's multiplicities multiply its chapter-by-age histogram. No empty
draw is discarded or replaced. Percentiles use linear interpolation at2.5/97.5.
Difference intervals subtract independent-report draws by replicate index.
They measure conditional chapter-composition sensitivity, not extraction,
parser or matching uncertainty, and no coverage calibration is asserted.
Delete-one sensitivity removes each cluster and rescales the remaining clusters
in its stratum to preserve the stratum's cluster-count mass. Singleton strata
have no estimable leave-one contrast and remain explicitly missing.
BCa is not computed: a generic one-sample acceleration would not establish
calibrated coverage for the unequal multi-report, stratified ratio design.
No simulation coverage study or independently validated parser rate is claimed.

Reproduction
Use Python with NumPy2.2.6. --database is mandatory and explicit. --output must
be a new directory, or --check can replay an existing registry byte for byte.
The frozen upstream inspection record permits offline replay without the original
private builder. --upstream-root additionally repeats that source inspection.
Seed20261011; default2500 draws; bootstrap output uses deterministic gzip mtime0.
No API request, model call or human judgment is made.
'''


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--database', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--replicates', type=int, default=2500)
    p.add_argument('--upstream-root', type=Path,
                   help='Optional upstream source folder for repeating the recorded code inspection; default replays the frozen inspection record')
    p.add_argument('--check', action='store_true')
    args = p.parse_args()
    if args.replicates < 100:
        raise ValueError('At least100 draws are required for this descriptive sensitivity')
    manifest_path = REPO / 'reproducibility/reference/manifest.json'
    baseline = json.loads(manifest_path.read_text())
    database = args.database.expanduser().resolve()
    if digest(database) != baseline['database_sha256']:
        raise ValueError('Input does not match the frozen corrected snapshot')
    if np.__version__ != baseline['numpy_version']:
        raise ValueError('Use frozen NumPy version ' + baseline['numpy_version'])
    protected = {database: digest(database), manifest_path: digest(manifest_path),
                 Path(__file__).resolve(): digest(Path(__file__).resolve())}
    for module in [prior, identity]:
        protected[Path(module.__file__)] = digest(module.__file__)
    for rel in ['revision_preparation/robustness/coverage_robustness.py',
                'reproducibility/revision_age_networks.py', 'reproducibility/revision_outlets.py',
                'revision_preparation/review_round3/age/output_v1/age_changes.csv',
                'reproducibility/corrections/2026-10-11.json']:
        protected[REPO / rel] = digest(REPO / rel)
    for item in baseline['tables']:
        file = manifest_path.parent / item['filename']
        if digest(file) != item['sha256']:
            raise ValueError('Original29 table changed: ' + str(file))
        protected[file] = item['sha256']
    rows = prior.load_occurrences(database)
    for row in rows:
        raw = row['raw_reference'] or ''
        row['doi_regex_candidates'] = len(identity.DOI_RE.findall(raw))
        row['literal_tokens'] = sorted({d for value in identity.DOI_RE.findall(raw)
            if identity.usable_raw_doi(d := identity.normalized_doi(value))})
        row['repaired_tokens'] = identity.punctuation_repaired_raw_dois(raw)
    with sqlite3.connect(database.as_uri() + '?mode=ro&immutable=1', uri=True) as con:
        con.row_factory = sqlite3.Row
        corrections = [dict(r) for r in con.execute('SELECT changed_by,entity_type,entity_id,field FROM change_log')]
        resolved = con.execute("SELECT COUNT(*) FROM manual_review WHERE status='resolved' AND reason='targeted_source_inspection'").fetchone()[0]
    tables = build(rows, corrections, args.replicates)
    # Compare every frozen main point estimate, independently of the new draws.
    old_change = REPO / 'revision_preparation/review_round3/age/output_v1/age_changes.csv'
    with old_change.open(newline='') as f:
        archived_changes = list(csv.DictReader(f))
    for earlier in archived_changes:
        if earlier['weighting'] != 'occurrence':
            continue
        current = next(r for r in tables['age_cap_differences.csv'] if r['cohort'] == earlier['cohort']
            and r['working_group'] == 'All' and r['age_max'] == 60
            and r['weighting'] == 'occurrence' and r['age_origin'] == earlier['age_origin'])
        for metric in METRICS:
            for key in ['AR5_' + metric, 'AR6_' + metric, metric + '_difference']:
                if not math.isclose(float(earlier[key]), current[key], rel_tol=1e-12, abs_tol=1e-12):
                    raise ValueError('Frozen point estimate mismatch: ' + key)
    inspection_file = HERE / 'upstream_inspection_record.json'
    protected[inspection_file] = digest(inspection_file)
    inspection = json.loads(inspection_file.read_text())
    if args.upstream_root is None:
        rules = inspection['rules']
    else:
        upstream = args.upstream_root.expanduser().resolve()
        upstream_files = ['run_all.py', 'ipccdb/refextract.py', 'ipccdb/wos.py', 'ipccdb/cite.py']
        for rel in upstream_files:
            protected[upstream / rel] = digest(upstream / rel)
        configuration = (upstream / 'run_all.py').read_text()
        audit_status = 'inspected_current_or_archived_source_not_proven_historical_execution'
        rules = []
        for key in ['TITLE_SIMILARITY', 'CROSSREF_DOI_TITLE_SIMILARITY', 'YEAR_TOLERANCE']:
            found = re.search(r'^' + key + r'\s*:\s*\w+\s*=\s*([0-9.]+)', configuration, re.M)
            if not found:
                raise ValueError('Could not inspect configured rule: ' + key)
            rules.append({'rule': key, 'value': found.group(1), 'source_file': 'run_all.py',
                          'source_sha256': digest(upstream / 'run_all.py'), 'execution_status': audit_status})
        for rule, value, source in [
            ('year_parser_prefix_characters', '300', 'revision_preparation/robustness/coverage_robustness.py'),
            ('raw_year_regex', r'\b(1[6-9]\d{2}|20\d{2})[a-z]?\b', 'revision_preparation/robustness/coverage_robustness.py'),
            ('extraction_splitters', 'PDF blocks; hanging indent; year/author regex', 'ipccdb/refextract.py'),
            ('hanging_indent_override', 'confidence >= 0.5 and median_gap >= 3.0', 'ipccdb/refextract.py'),
            ('title_similarity_definition', 'max(token-set Jaccard, RapidFuzz token_set_ratio/100); Jaccard fallback', 'ipccdb/cite.py'),
            ('DOI_acceptance', 'normalized exact DOI; Crossref-backfilled DOI additionally needs configured title similarity', 'ipccdb/wos.py'),
            ('title_year_author_acceptance', 'configured similarity plus year tolerance when both years usable; first-surname equality when both available', 'ipccdb/wos.py'),
            ('IPCC_self_reference_policy', 'No additional exclusion: retain under same matching, identity and age rules as other entries', 'scripts/review_round2_raw_age.py')]:
            source_path = REPO / source if (REPO / source).is_file() else upstream / source
            rules.append({'rule': rule, 'value': value, 'source_file': source,
                          'source_sha256': digest(source_path), 'execution_status': audit_status})
        if rules != inspection['rules']:
            raise ValueError('Fresh source inspection differs from the frozen inspection record')
    tables['matching_rule_audit.csv'] = rules
    ledger = json.loads((REPO / 'reproducibility/corrections/2026-10-11.json').read_text())
    nominated = re.search(r'inspection of (\d+) nominated', ledger['audit_method'])
    if not nominated:
        raise ValueError('Ledger does not state nominated case count')
    correction_counts = [
        ('field_changes', len(corrections)),
        ('affected_occurrence_IDs', len({r['entity_id'] for r in corrections if r['entity_type'] == 'occurrence'})),
        ('affected_paper_IDs', len({r['entity_id'] for r in corrections if r['entity_type'] == 'paper'})),
        ('resolved_targeted_source_inspection_decisions', resolved),
        ('reviewed_occurrence_IDs_in_ledger', len(ledger['reviewed_occurrence_ids'])),
        ('nominated_chronology_cases_in_ledger_description', int(nominated.group(1))),
        ('case_records_in_ledger_including_related_cases', len(ledger['cases'])),
        ('changes_to_raw_reference_field', sum(r['field'] == 'raw_reference' for r in corrections))]
    tables['correction_ledger_summary.csv'] = [{'metric': k, 'value': v,
        'interpretation': 'AI-assisted execution ledger; no independent human accuracy inferred'} for k, v in correction_counts]
    payloads = {name: gzip.compress(csv_data(values), mtime=0) if name.endswith('.gz') else csv_data(values)
                for name, values in tables.items()}
    payloads['methods.txt'] = METHODS.encode()
    for file, expected in protected.items():
        if digest(file) != expected:
            raise ValueError('Protected input changed: ' + str(file))
    registry = {'version': VERSION, 'seed': SEED, 'replicates': args.replicates,
        'numpy_version': np.__version__, 'database_sha256': digest(database),
        'inspected_upstream_source_hashes': inspection['source_files'],
        'inputs': {str(f.relative_to(REPO)) if f.is_relative_to(REPO) else f.name: h
                   for f, h in sorted(protected.items(), key=lambda x: str(x[0]))},
        'registered_outputs': [{'filename': n, 'sha256': hashlib.sha256(b).hexdigest(),
                                 'rows': len(tables[n]) if n in tables else None}
                               for n, b in sorted(payloads.items())],
        'checks': {'original29_tables_hash_unchanged': True, 'database_unchanged': True,
                   'raw_strings_unchanged': True, 'all_exclusions_accounted': True,
                   'stratified_cluster_counts_preserved': True, 'human_accuracy_estimated': False,
                   'frozen_age_point_estimates_reproduced': True,
                   'BCa_computed': False, 'coverage_simulation_performed': False}}
    payloads['manifest.json'] = (json.dumps(registry, indent=2, sort_keys=True, allow_nan=False) + '\n').encode()
    if args.check:
        for name, raw in payloads.items():
            if (args.output / name).read_bytes() != raw:
                raise ValueError('Replay mismatch: ' + name)
    else:
        args.output.mkdir(parents=True, exist_ok=False)
        for name, raw in payloads.items():
            (args.output / name).write_bytes(raw)
    print(json.dumps({'status': 'passed', 'version': VERSION, 'outputs': len(payloads),
                      'output': str(args.output), 'seed': SEED}))


if __name__ == '__main__':
    main()
