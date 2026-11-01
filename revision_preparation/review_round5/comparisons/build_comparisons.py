#!/usr/bin/env python3
"""Local-only comparison displays from unchanged frozen assessment outputs."""
from __future__ import annotations
import argparse
import csv
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

SEED = 20261011
VERSION = '1.6.0'
MATCHED = {'matched_doi', 'matched_title'}

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def read(path):
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))

def write(path, rows):
    if not rows:
        raise ValueError('No rows for ' + path.name)
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    return {'path': path.name, 'rows': len(rows), 'sha256': sha(path)}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent / 'output_v1')
    args = parser.parse_args()
    repo, out = args.repo.resolve(), args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    inputs = {
        'network': repo / 'reproducibility/reference/revision_network_retention_sensitivity.csv',
        'cutoff': repo / 'revision_preparation/review_round3/external/offline_v1/cutoff_reuse_sensitivity.csv',
        'counts': repo / 'revision_preparation/review_round3/external/journal_strict_v1/source_counts.csv',
        'diversity': repo / 'revision_preparation/review_round3/external/journal_strict_v1/outlet_diversity.csv',
        'assignments': repo / 'revision_preparation/review_round3/external/offline_v1/resolved_journal_source_assignments.csv',
        'occurrences': repo / 'revision_preparation/review_round2/raw_age_v1/raw_age_occurrence_audit.csv.gz',
        'indirect': repo / 'revision_preparation/review_round3/external/indirect_pool_v1/indirect_pool_baseline.csv',
    }
    original_hashes = {key: sha(path) for key, path in inputs.items()}
    network, cutoff = read(inputs['network']), read(inputs['cutoff'])
    counts, assigned = read(inputs['counts']), read(inputs['assignments'])
    occurrences, div = read(inputs['occurrences']), read(inputs['diversity'])
    wg = []
    for group in ['WG1', 'WG2', 'WG3']:
        base = [r for r in network if r['rule'] == 'matched' and r['screening'] == 'none'
                and r['group'] == group and r['report_a'] == 'AR5' and r['report_b'] == 'AR6']
        conditional = [r for r in cutoff if r['cohort'] == 'matched_assigned'
                       and r['working_group'] == group and r['date_exclusion'] == 'none']
        if len(base) != 1 or len(conditional) != 1:
            raise ValueError('Nonunique WG comparison')
        b, c = base[0], conditional[0]
        for metric, num, den, value, source, numcol, dencol, cohort in [
            ('retention', b['shared'], b['n_a'], b['retention'], 'network', 'shared', 'n_a', 'matched assigned identities'),
            ('unconditional_reuse', b['shared'], b['n_b'], b['reuse'], 'network', 'shared', 'n_b', 'matched assigned identities'),
            ('publication_by_cutoff_reuse', c['shared_eligible_keys'], c['AR6_eligible_keys'], c['conditional_reuse'], 'cutoff', 'shared_eligible_keys', 'AR6_eligible_keys', 'matched assigned identities with a unique OpenAlex publication date'),
        ]:
            if not math.isclose(int(num) / int(den), float(value), rel_tol=1e-14):
                raise ValueError('Stored fraction disagrees with numerator/denominator')
            wg.append(dict(working_group=group, metric=metric, cohort=cohort,
                           numerator=int(num), denominator=int(den), fraction=value,
                           cutoff_date=c['earlier_cutoff_date'] if source == 'cutoff' else '',
                           source_table=str(inputs[source].relative_to(repo)),
                           source_row_selector=json.dumps({'group': group, 'rule': 'matched', 'screening': 'none', 'report_a': 'AR5', 'report_b': 'AR6'} if source == 'network' else {'cohort': 'matched_assigned', 'working_group': group, 'date_exclusion': 'none'}, sort_keys=True),
                           numerator_column=numcol, denominator_column=dencol,
                           interval='Not estimated: fixed-frame descriptive proportion',
                           note='A work appearing in multiple groups counts once within each group. Group totals are not a report-level partition. Publication availability is distinct from acceptance eligibility.'))
    previous = [dict(r, source_table=str(inputs['network'].relative_to(repo))) for r in network
                if r['rule'] in {'matched', 'extracted'} and r['screening'] == 'none'
                and r['report_a'] in {'AR4', 'AR5'} and r['report_b'] in {'AR5', 'AR6'}
                and (r['report_a'], r['report_b']) in {('AR4', 'AR5'), ('AR5', 'AR6')}]
    source_map = defaultdict(list)
    name_map = defaultdict(Counter)
    for r in assigned:
        source_map[(r['report'], r['paper_id'])].append(r)
        if r['source_id'] and r['source_name']:
            name_map[r['source_id']][r['source_name']] += 1
    resolved = {}
    for key, rows in source_map.items():
        keys = {(r['openalex_id'], r['source_id'], r['work_type'], r['source_type']) for r in rows}
        if len(keys) != 1:
            raise ValueError('Conflicting frozen source resolution: ' + repr(key))
        resolved[key] = rows[0]
    membership = defaultdict(set)
    for r in occurrences:
        if not r['paper_id']:
            continue
        for cohort in ['all_extracted', 'matched']:
            if cohort == 'matched' and r['match_status'] not in MATCHED:
                continue
            membership[(r['report_id'], r['wg_label'], cohort)].add(r['paper_id'])
            membership[(r['report_id'], 'All', cohort)].add(r['paper_id'])
    missing = []
    for (rep, group, cohort), pids in sorted(membership.items()):
        if group not in {'All', 'WG1', 'WG2', 'WG3'}:
            continue
        category = Counter()
        for pid in pids:
            w = resolved.get((rep, pid))
            if w is None:
                category['unresolved_or_ambiguous'] += 1
            elif w['work_type'] in {'article', 'review'} and w['source_type'] == 'journal' and w['source_id']:
                category['known_journal_article_review'] += 1
            elif w['work_type'] not in {'article', 'review'} or w['source_type'] not in {'journal', ''}:
                category['known_other_work_or_source_type'] += 1
            else:
                category['resolved_type_or_source_missing'] += 1
        unknown = category['unresolved_or_ambiguous'] + category['resolved_type_or_source_missing']
        missing.append(dict(report=rep, working_group=group, cohort=cohort, assigned_IDs=len(pids),
                            **{key: category[key] for key in ['known_journal_article_review', 'known_other_work_or_source_type', 'unresolved_or_ambiguous', 'resolved_type_or_source_missing']},
                            unknown_type_or_source_IDs=unknown, unknown_fraction=unknown / len(pids),
                            note='Distinct assigned IDs within each report and group. All is the report union, including catalogue units outside the three working groups. Matched/unmatched source resolution follows the frozen preferred/raw DOI lookup; no unknown type is assigned to journals. Group overlap is retained.'))
    profiles, tops = [], []
    grouped = defaultdict(Counter)
    for r in counts:
        grouped[(r['cohort'], r['report'])][r['source_id']] += int(r['assigned_IDs'])
    for (cohort, rep), cc in sorted(grouped.items()):
        n = sum(cc.values())
        p = [x / n for x in cc.values()]
        values = {0: len(cc), 1: math.exp(-math.fsum(x * math.log(x) for x in p)),
                  2: n * n / sum(x * x for x in cc.values())}
        old = [r for r in div if r['cohort'] == cohort and r['report'] == rep]
        if len(old) != 1 or not math.isclose(values[2], float(old[0]['D2_full']), rel_tol=1e-14):
            raise ValueError('D2 differs from the frozen table')
        if n != int(old[0]['assigned_IDs']) or values[0] != int(old[0]['D0_full']):
            raise ValueError('Journal frame differs from frozen table')
        for q, value in values.items():
            profiles.append(dict(cohort=cohort, report=rep, q=q, assigned_IDs=n, known_journal_sources=len(cc), hill_number=value,
                                 scope='Resolved journal articles and reviews, one assigned ID per report',
                                 estimation='Observed full-cohort Hill profile; no coverage-based rarefaction or extrapolation',
                                 source_table=str(inputs['counts'].relative_to(repo))))
        if cohort == 'matched':
            for rank, (sid, count) in enumerate(sorted(cc.items(), key=lambda x: (-x[1], x[0]))[:10], 1):
                names = name_map[sid]
                tops.append(dict(report=rep, rank=rank, source_id=sid,
                                 source_name=sorted(names, key=lambda x: (-names[x], x))[0] if names else '',
                                 assigned_IDs=count, denominator=n, fraction=count / n,
                                 source_table=str(inputs['counts'].relative_to(repo)),
                                 scope='Matched assigned IDs uniquely resolved as article/review in a journal source; no publication-year restriction'))
    indirect = read(inputs['indirect'])
    if len(indirect) != 1:
        raise ValueError('Nonunique indirect comparison')
    r = indirect[0]
    nonempty = [dict(cohort=cohort, numerator=int(r[numerator]), denominator=int(r[denominator]),
                     fraction=r[fraction], empty_arrays=int(r[empty]), missing_fields=int(r[missingcol]),
                     source_table=str(inputs['indirect'].relative_to(repo)), numerator_column=numerator,
                     denominator_column=denominator,
                     interpretation='Recorded outgoing links conditional on a nonempty indexed array; unmatched reference-list lengths and subjects remain. No placebo benchmark is inferred.')
                for cohort, numerator, denominator, fraction, empty, missingcol in [
                    ('AR6_journal_cohort', 'IPCC_common_scope_at_least_one_AR5_target', 'IPCC_common_scope_nonempty_reference_arrays', 'IPCC_fraction_among_nonempty_arrays', 'IPCC_common_scope_empty_reference_arrays', 'IPCC_common_scope_missing_reference_fields'),
                    ('broad_query_probability_sample', 'at_least_one_AR5_target', 'nonempty_reference_arrays', 'fraction_among_nonempty_arrays', 'empty_reference_arrays', 'missing_reference_fields')]]
    tables = [write(out / name, rows) for name, rows in [
        ('wg_reference_recurrence.csv', wg), ('wg_previous_transition.csv', previous),
        ('source_availability_by_working_group.csv', missing), ('hill_profiles.csv', profiles),
        ('top10_journal_shares.csv', tops), ('indirect_nonempty_comparison.csv', nonempty)]]
    registry = []
    for table in tables:
        for i, row in enumerate(read(out / table['path']), 1):
            for field, value in row.items():
                try:
                    number = float(value)
                except (ValueError, TypeError):
                    continue
                if not math.isfinite(number):
                    raise ValueError('Nonfinite numeric output')
                registry.append(dict(table=table['path'], row=i, column=field, value=value,
                                     table_sha256=table['sha256'], analysis_version=VERSION, seed=SEED))
    tables.append(write(out / 'registry.csv', registry))
    for key, path in inputs.items():
        if sha(path) != original_hashes[key]:
            raise ValueError('Input changed: ' + key)
    manifest = dict(analysis_version=VERSION, seed=SEED, script_sha256=sha(Path(__file__)),
                    inputs=[dict(role=k, path=str(p.relative_to(repo)), sha256=original_hashes[k]) for k, p in inputs.items()],
                    outputs=tables, network_used=False, credentials_read=False,
                    notes=['No human validation, causal adjustment or error correction is produced.',
                           'Hill q=0,1,2 uses full observed resolved-source counts; sample coverage extrapolation was not performed.',
                           'The 2014–2021 citation count is post-AR5 and can be a mediator or collider; the year-only RR is the primary descriptive specification.',
                           'Frozen case-cohort observations lack pre-AR5 citation counts, author overlaps and topics. These adjustments require additional data.',
                           'The indirect aggregate does not supply matched pseudo-target sets. Its nonempty-array comparison is descriptive, not a placebo test.'])
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'output': str(out), 'tables': len(tables), 'numeric_cells': len(registry), 'all_input_hashes_unchanged': True}, indent=2))

if __name__ == '__main__':
    main()
