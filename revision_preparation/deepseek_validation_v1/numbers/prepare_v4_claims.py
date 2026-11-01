#!/usr/bin/env python3
"""Append explicit validation source-cell selectors to the frozen round-3 spec.

Only the separate v4 specification is written. Existing claims and constants are
copied without modification. Manuscript/support paths are supplied by the caller.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
MODULE = 'revision_preparation/deepseek_validation_v1/'
REPORT = MODULE + 'reports/'
FULL = REPORT + 'deepseek_full_20261025_v1/'
SCI = FULL + 'scientific_diagnostics/'
PAIR = REPORT + 'codex_comparison_sample_v1/'
IDENTITY = PAIR + 'identity_record_count_supplement/'
PILOT = REPORT + 'deepseek_pilot_20261025_v3/'
CAND = REPORT + 'deepseek_candidates_20261025_v2/'
CAND_SCI = CAND + 'candidate_scientific_diagnostics/'
CAND_TOTALS = CAND_SCI + 'candidate_totals/'


def make_spec(base_path, repo=REPO):
    spec = json.loads(base_path.read_text())
    old_claims = json.loads(json.dumps(spec['claims']))
    old_constants = json.loads(json.dumps(spec.get('design_constants', [])))
    used = {x['id'] for x in spec['claims'] + spec.get('design_constants', [])}

    def stored(path):
        for candidate in (path, path + '.gz'):
            if (repo / candidate).is_file():
                return candidate
        raise FileNotFoundError('Published numerical source is missing: ' + path)

    def add(identifier, path, column, filters=None, fmt='integer', explanation=''):
        if identifier in used:
            raise ValueError('Duplicate numeric ID: ' + identifier)
        used.add(identifier)
        source = {'path': stored(path)}
        if path.endswith('.json'):
            source['json_pointer'] = column
        else:
            source.update(filters=filters or {}, column=column)
        spec['claims'].append({'id': identifier, 'source': source, 'format': fmt,
                               'explanation': explanation or 'Completed model-review diagnostic; conditional on the saved model outputs.'})

    for identifier, pointer in [
        ('dv_full_cases','/distinct_cases'), ('dv_full_records','/record_count_by_arm/blind'),
        ('dv_full_jobs','/scheduled_calls'), ('dv_full_valid','/model_decision_calls'),
        ('dv_full_abstentions','/code_abstention_calls')]:
        add(identifier,FULL+'summary.json',pointer)
    for identifier,pointer in [('dv_codex_sample_cases','/case_count'),('dv_codex_sample_records','/record_count')]:
        add(identifier,PAIR+'manifest.json',pointer)
    for identifier,pointer in [('dv_pilot_cases','/distinct_cases'),('dv_pilot_records','/record_count_by_arm/blind'),
                               ('dv_pilot_valid','/model_decision_calls'),('dv_pilot_jobs','/scheduled_calls')]:
        add(identifier,PILOT+'summary.json',pointer)
    add('dv_qc_excluded',SCI+'manifest.json','/qc_excluded_configuration_case_pairs')
    for identifier,column in [('dv_parser_n','parser_occurrences'),('dv_source_n','source_occurrences'),
                               ('dv_occurrence_overlap','overlapping_occurrences')]:
        add(identifier,SCI+'occurrence_frame_overlap.csv',column,{'sampling_frames':'parser|source'})
    filters={'arm':'blind','reviewer':'consensus'}
    for suffix,column,fmt in [('census_recurrent','recurrent_n','integer'),('census_nonrecurrent','non_recurrent_n','integer'),
            ('census_unresolved','unresolved_n','integer'),('retention_lower','retention_lower','percent2'),
            ('retention_upper','retention_upper','percent2'),('reuse_lower','reuse_lower','percent2'),('reuse_upper','reuse_upper','percent2')]:
        add('dv_'+suffix,SCI+'recurrence_scenarios.csv',column,filters,fmt)
    for field,prefix,labels in [('identity','identity',['same_study','different_studies','unresolvable','no_majority']),
                              ('cross_report_recurrence','recurrence',['recurrent','non_recurrent','unresolvable','no_majority'])]:
        for label in labels:
            add('dv_'+prefix+'_'+label,SCI+'shared_census_labels.csv','paper_ids',
                {**filters,'field':field,'model_label':label})
    for report in ['AR5','AR6']:
        for suffix,column,fmt in [('N','population_N','integer'),('n','actual_sample_n','integer'),
                ('resolved','observed_resolved_n','integer'),('unresolved','observed_unresolved_n','integer'),
                ('coverage','resolved_coverage','percent2'),('lower','disagreement_lower_share','percent2'),
                ('upper','disagreement_upper_share','percent2')]:
            add('dv_parser_'+report+'_'+suffix,SCI+'parser_weighted_diagnostics.csv',column,
                {**filters,'report_scope':report},fmt)
        for source_type in ['journal','book','report','conference','other','unresolvable','unavailable','no_majority']:
            add('dv_source_'+report+'_'+source_type,SCI+'source_weighted_counts.csv','projected_share',
                {**filters,'report_scope':report,'source_type':source_type},'percent2')
        for suffix,column in [('N','population_N'),('n','actual_sample_n')]:
            add('dv_source_'+report+'_'+suffix,SCI+'source_weighted_counts.csv',column,
                {**filters,'report_scope':report,'source_type':'journal'})
    paired_filters={**filters,'population':'probability_319_targeted_12'}
    fields=[('year','cited_year'),('recurrence','cross_report_recurrence'),('source','source_type'),
            ('outlet','outlet_canonical')]
    metrics=[('scheduled','scheduled_items_n'),('shared','shared_available_n'),
             ('agree_shared','exact_agreement_shared_n'),('resolved','both_resolved_n'),
             ('agree_resolved','exact_agreement_resolved_n')]
    for prefix,field in fields:
        for suffix,column in metrics:
            add('dv_pair_'+prefix+'_'+suffix,PAIR+'paired_agreement.csv',column,{**paired_filters,'field':field})
    for prefix,stratum in [('multi','multiple_records'),('singleton','singleton')]:
        for suffix,column in metrics:
            add('dv_pair_'+prefix+'_'+suffix,IDENTITY+'identity_record_count_agreement.csv',column,
                {**paired_filters,'field':'identity','stratum':stratum})
    for identifier,pointer in [('dv_cand_cases','/distinct_cases'),('dv_cand_jobs','/scheduled_calls'),
            ('dv_cand_valid','/model_decision_calls'),('dv_cand_abstentions','/code_abstention_calls')]:
        add(identifier,CAND+'summary.json',pointer)
    add('dv_cand_qc_excluded',CAND_SCI+'manifest.json','/qc_excluded_configuration_case_pairs')
    add('dv_cand_qc_changed',CAND_SCI+'manifest.json','/row_counts/qc_consensus_changes')
    for short,geometry in [('all','all'),('opposite','opposite_report_only_matched_ids')]:
        selection={**filters,'geometry':geometry,'classification_basis':'qc_derived'}
        add('dv_cand_'+short+'_N',CAND_TOTALS+'candidate_label_totals.csv','candidate_N',
            {**selection,'field':'identity','model_label':'same_study'})
        for field,prefix,positive,negative in [('identity','identity','same_study','different_studies'),
                    ('cross_report_recurrence','recurrence','recurrent','non_recurrent')]:
            for suffix,label in [('positive',positive),('negative',negative)]:
                add('dv_cand_'+short+'_'+prefix+'_'+suffix,CAND_TOTALS+'candidate_label_totals.csv',
                    'candidate_pair_rows',{**selection,'field':field,'model_label':label})
            add('dv_cand_'+short+'_'+prefix+'_unresolved',CAND_TOTALS+'candidate_resolution_totals.csv',
                'candidate_pair_rows',{**selection,'field':field,'model_label':'unresolved'})
    spec.setdefault('structural_exclusions', []).append({'text': r'\texttt{deepseek-v4-pro}', 'explanation': 'Requested provider model identifier; its digit is model nomenclature, not an empirical quantity.'})
    assert spec['claims'][:len(old_claims)] == old_claims
    assert spec.get('design_constants',[]) == old_constants
    return spec


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-spec',type=Path,default=REPO/'revision_preparation/review_round3/numbers/claim_spec.json')
    parser.add_argument('--output',type=Path,default=HERE/'claim_spec_v4.json')
    args=parser.parse_args()
    spec=make_spec(args.base_spec.resolve())
    args.output.write_text(json.dumps(spec,indent=2)+'\n')
    print(json.dumps({'empirical_claims':len(spec['claims']),'design_constants':len(spec.get('design_constants',[])),
                      'output':str(args.output)},indent=2))

if __name__=='__main__':
    main()
