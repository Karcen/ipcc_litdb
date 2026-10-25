#!/usr/bin/env python3
"""Derive report-labelled candidate packets without changing frozen evidence.

This is an offline transformation. It adds only report_group to each record.
R001 maps to the candidate CSV's AR5 occurrence; R002 maps to its AR6 occurrence.
An optional read-only database check verifies every occurrence and raw-text hash.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import sqlite3

from prepare import (ANALYSIS_PACKAGE, CONFIGURATIONS, SEED, build_batches,
                     digest, encoded, verify_prepared_inputs, write_frozen)

HERE = Path(__file__).resolve().parent
SCOPE = 'candidates_with_provenance_v2'


def text_digest(value):
    if not isinstance(value, str):
        raise ValueError('Raw reference must be a string')
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def derive_packet(packet, rows, *, connection=None, expected_cases=3944):
    """Return a deep copy plus record provenance; never modify supplied objects."""
    if set(packet) != {'cases'} or len(packet['cases']) != expected_cases or len(rows) != expected_cases:
        raise ValueError('Candidate frame/CSV case counts differ from the fixed design')
    case_ids = [case['case_id'] for case in packet['cases']]
    row_ids = [row['case_id'] for row in rows]
    if len(set(case_ids)) != expected_cases or len(set(row_ids)) != expected_cases or set(case_ids) != set(row_ids):
        raise ValueError('Candidate case IDs are duplicated or disagree with the CSV')
    by_case = {row['case_id']: row for row in rows}
    derived = copy.deepcopy(packet)
    provenance = []
    seen = set()
    for case in derived['cases']:
        if set(case) != {'case_id', 'task', 'records', 'frozen_evidence'} or case['task'] != 'identity':
            raise ValueError('Unexpected candidate case fields or task')
        if [record['record_id'] for record in case['records']] != ['R001', 'R002']:
            raise ValueError('Candidate records must be R001 then R002, without duplicates')
        row = by_case[case['case_id']]
        for record, report, group in zip(case['records'], ('AR5', 'AR6'), ('earlier', 'later')):
            if set(record) not in ({'record_id', 'raw_reference'}, {'record_id', 'raw_reference', 'report_group'}):
                raise ValueError('Unexpected candidate record fields')
            if record.get('report_group', group) != group:
                raise ValueError('Existing report provenance contradicts the occurrence order')
            occurrence_id = row[report + '_occurrence_id']
            if not occurrence_id.startswith(report + '-'):
                raise ValueError('Candidate occurrence identifier contradicts its report')
            key = (case['case_id'], record['record_id'])
            if key in seen:
                raise ValueError('Duplicate composite record key')
            seen.add(key)
            raw_sha = text_digest(record['raw_reference'])
            if connection is not None:
                found = connection.execute('SELECT r.paper_id,r.raw_reference,c.report_id '
                    'FROM references_occurrence r JOIN chapters c USING(chapter_id) '
                    'WHERE r.occurrence_id=?', (occurrence_id,)).fetchall()
                if len(found) != 1:
                    raise ValueError('Source occurrence is missing or duplicated: ' + occurrence_id)
                paper_id, raw, actual_report = found[0]
                if actual_report != report or str(paper_id) != str(row[report + '_paper_id']):
                    raise ValueError('Source report or assigned ID differs: ' + occurrence_id)
                if text_digest(raw) != raw_sha:
                    raise ValueError('Candidate raw-reference hash differs from source: ' + occurrence_id)
            record['report_group'] = group
            provenance.append({'case_id': case['case_id'], 'record_id': record['record_id'],
                'report_group': group, 'source_report': report, 'occurrence_id': occurrence_id,
                'raw_reference_sha256': raw_sha, 'database_checked': str(connection is not None).lower()})
    if len(seen) != 2 * expected_cases:
        raise ValueError('Derived record count differs')
    # Removing only the newly supplied provenance must exactly recover frozen inputs.
    stripped = copy.deepcopy(derived)
    for source_case, derived_case in zip(packet['cases'], stripped['cases']):
        for source_record, record in zip(source_case['records'], derived_case['records']):
            if 'report_group' not in source_record:
                del record['report_group']
    if stripped != packet:
        raise ValueError('Derivation changed content beyond report provenance')
    return derived, provenance


def prepare_candidate_provenance(work_dir, *, database=None, expected_cases=3944,
                                 max_cases=10, max_records=20, max_bytes=40000):
    work = Path(work_dir).resolve()
    verify_prepared_inputs(work)
    inputs = work / 'inputs'
    packet_path = inputs / 'candidates/blind_candidates.json'
    csv_path = inputs / 'candidates/candidate_pairs.csv'
    source_manifest = inputs / 'candidates/manifest.json'
    original = json.loads(packet_path.read_text())
    with csv_path.open(newline='', encoding='utf-8') as stream:
        rows = list(csv.DictReader(stream))
    connection = None
    database_sha = None
    if database is not None:
        database = Path(database).resolve()
        expected = json.loads(source_manifest.read_text())['input_sha256']['database/ipcc_litdb.sqlite']
        database_sha = digest(database)
        if database_sha != expected:
            raise ValueError('Read-only database differs from the frozen candidate-generation input')
        connection = sqlite3.connect(database.as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        derived, provenance = derive_packet(original, rows, connection=connection, expected_cases=expected_cases)
    finally:
        if connection is not None:
            connection.close()
    if database is not None and digest(database) != database_sha:
        raise ValueError('Database changed during source verification')
    base = work / 'derived' / SCOPE
    outputs = {}

    def freeze(path, data):
        write_frozen(path, data)
        outputs[path.relative_to(work).as_posix()] = digest(path)

    freeze(base / 'blind_candidates.json', encoded(derived))
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=list(provenance[0]))
    writer.writeheader()
    writer.writerows(provenance)
    freeze(base / 'record_provenance.csv', stream.getvalue().encode('utf-8'))
    input_provenance = {'analysis_package': ANALYSIS_PACKAGE, 'seed': SEED, 'scope': SCOPE,
        'transformation': 'Only report_group added: R001=earlier/AR5; R002=later/AR6.',
        'source_input_sha256': {'inputs/candidates/blind_candidates.json': digest(packet_path),
            'inputs/candidates/candidate_pairs.csv': digest(csv_path),
            'inputs/candidates/manifest.json': digest(source_manifest),
            'input_manifest.json': digest(work / 'input_manifest.json')},
        'database_sha256': database_sha, 'database_check_completed': database is not None,
        'raw_string_changes': 0, 'record_key_unit': '(case_id, record_id)',
        'case_count': len(derived['cases']), 'record_count': len(provenance),
        'preparation_code_sha256': digest(__file__), 'model_calls_made': 0}
    freeze(base / 'input_provenance.json', encoded(input_provenance))
    batches = build_batches(derived, max_cases=max_cases, max_records=max_records, max_bytes=max_bytes)
    jobs, summaries = [], []
    for batch in batches:
        summaries.append({key: value for key, value in batch.items() if key not in ('packet', 'paired_packet')})
        relative = Path('packets') / SCOPE / 'blind' / (batch['batch_id'] + '.json')
        freeze(work / relative, encoded(batch['packet']))
        for configuration in CONFIGURATIONS:
            jobs.append({**configuration,
                'job_id': SCOPE + '_' + batch['batch_id'] + '_blind_' + configuration['configuration_id'],
                'arm': 'blind', 'batch_id': batch['batch_id'], 'packet_path': relative.as_posix(),
                'packet_sha256': digest(work / relative),
                'prompt_path': f"inputs/prompt_{configuration['prompt_variant']}.txt",
                'prompt_sha256': digest(inputs / f"prompt_{configuration['prompt_variant']}.txt"),
                'schema_path': 'inputs/response_schema.json', 'schema_sha256': digest(inputs / 'response_schema.json'),
                'case_count': batch['case_count'], 'record_count': batch['record_count'],
                'packet_bytes': (work / relative).stat().st_size,
                'oversized_single_case': batch['oversized_single_case']})
    provenance_manifest = {**input_provenance,
        'canonical_candidate_input_sha256': digest(packet_path),
        'candidate_pairs_sha256': digest(csv_path),
        'packet_sha256': {path: checksum for path, checksum in outputs.items() if path.startswith('packets/')},
        'output_sha256': {path: checksum for path, checksum in outputs.items() if path.startswith('packets/')}}
    freeze(base / 'derived_provenance_manifest.json', encoded(provenance_manifest))
    schedule = {'analysis_package': ANALYSIS_PACKAGE, 'seed': SEED, 'stage': 'candidates', 'scope': SCOPE,
        'configurations': list(CONFIGURATIONS), 'provider_families': 1, 'model_tiers': 2,
        'configuration_count': 3, 'independent_reviewers_claimed': False,
        'case_count': len(derived['cases']), 'record_count': len(provenance), 'arms': ['blind'],
        'batch_limits': {'max_cases': max_cases, 'max_records': max_records, 'max_utf8_bytes': max_bytes},
        'batch_count_per_arm': len(batches), 'initial_call_count': len(jobs), 'maximum_schema_attempts_per_job': 2,
        'batch_summaries': summaries, 'jobs': jobs, 'model_calls_made': 0, 'automatic_execution_enabled': False,
        'execution_requires_runner_budget_gate': True, 'full_execution_requires_completed_pilot': True,
        'input_manifest_sha256': digest(work / 'input_manifest.json'),
        'derivation_provenance_path': (base / 'input_provenance.json').relative_to(work).as_posix(),
        'derivation_provenance_sha256': digest(base / 'input_provenance.json'),
        'derived_frame_path': (base / 'blind_candidates.json').relative_to(work).as_posix(),
        'derived_provenance_manifest_path': (base / 'derived_provenance_manifest.json').relative_to(work).as_posix(),
        'derived_provenance_manifest_sha256': digest(base / 'derived_provenance_manifest.json')}
    freeze(work / 'schedules' / (SCOPE + '.json'), encoded(schedule))
    manifest = {**input_provenance, 'output_sha256': outputs,
        'schedule_path': 'schedules/' + SCOPE + '.json',
        'batch_count_per_arm': len(batches), 'initial_call_count': len(jobs)}
    write_frozen(base / 'manifest.json', encoded(manifest))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir', type=Path, default=HERE / 'work')
    parser.add_argument('--database', type=Path)
    args = parser.parse_args()
    result = prepare_candidate_provenance(args.work_dir, database=args.database)
    print(json.dumps({key: result[key] for key in ('scope', 'case_count', 'record_count',
        'database_check_completed', 'batch_count_per_arm', 'initial_call_count', 'raw_string_changes')}))


if __name__ == '__main__':
    main()
