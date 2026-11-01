"""Offline planning arithmetic and synthetic archive provenance tests."""
import copy
import json
from decimal import Decimal
from pathlib import Path
import unittest

import aggregate
from budget import usage_upper
import forecast
import test_aggregation as fixtures

dump = fixtures.dump
synthetic_validator = fixtures.synthetic_validator


class ForecastTests(unittest.TestCase):
    def setUp(self):
        self.archive = fixtures.Archive()
        self.archive.setUp()
        self.root, self.run = self.archive.root, self.archive.run
        self.ledger_path = self.root / 'work' / 'budget_20261025.json'
        self.ledger = {'authorized_limit_cny': '100', 'entries': {}}
        for job in self.archive.jobs:
            job.update(record_count=2, case_count=1)
            directory = self.run / 'calls' / job['call_id']
            request_path = directory / 'attempt_1.request.json'
            dump(request_path, {'synthetic': True})
            result = json.loads((directory / 'result.json').read_text())
            result.update(utc_finished='2026-10-25T00:00:02+00:00', elapsed_seconds=2)
            attempt = result['attempts'][0]
            cost = str(usage_upper(job['model_id'], attempt['usage']))
            attempt.update(cost_upper_cny=cost, returned_model=job['model_id'], http_status=200,
                           requested_utc='2026-10-25T00:00:00+00:00', raw_request_sha256=aggregate.sha256(request_path))
            dump(directory / 'result.json', result)
            self.ledger['entries']['synthetic/' + job['call_id'] + '/1'] = {
                'model': job['model_id'], 'state': 'settled', 'usage': attempt['usage'],
                'charged_upper_cny': cost, 'reservation_upper_cny': '1', 'reservation_exceeded': False}
        dump(self.ledger_path, self.ledger)
        dump(self.run / 'schedule.json', {'stage': 'pilot', 'jobs': self.archive.jobs})
        dump(self.run / 'software.json', {'schedule_sha256': aggregate.sha256(self.run / 'schedule.json')})
        for stage, count in (('full', 12), ('candidates', 8)):
            jobs = [{'job_id': config, 'configuration_id': config, 'arm': 'blind', 'record_count': count}
                    for config in aggregate.CONFIGURATIONS]
            dump(self.root / 'work' / 'schedules' / (stage + '.json'),
                 {'stage': stage, 'scope': stage, 'arms': ['blind'], 'jobs': jobs,
                  'initial_call_count': 3, 'case_count': count // 2, 'record_count': count})

    def tearDown(self):
        self.archive.tearDown()

    def create(self, **kwargs):
        return forecast.create_forecast(self.root, 'synthetic', validator=synthetic_validator, **kwargs)

    def test_verified_pilot_uses_blind_model_rates_and_actual_schedules(self):
        result = self.create()
        self.assertTrue(result['pilot_complete'])
        self.assertTrue(result['pilot_qc_passed'])
        self.assertEqual(result['valid_job_fraction'], '1')
        self.assertEqual(result['transport_or_provenance_errors'], [])
        self.assertEqual(result['actual_usage_blind_arm_by_model']['deepseek-flash']['record_reviews'], 4)
        self.assertEqual(result['actual_usage_blind_arm_by_model']['deepseek-v4-pro']['record_reviews'], 2)
        primary = result['primary_full_forecast']
        self.assertEqual(primary['initial_call_count'], 3)
        self.assertEqual(primary['distinct_records'], 12)
        blind_cost = sum(Decimal(row['cost_upper_cny']) for row in result['actual_usage_blind_arm_by_model'].values())
        self.assertEqual(Decimal(primary['base_cost_upper_cny_estimate']), blind_cost * 6)
        self.assertEqual(Decimal(primary['planning_cost_upper_cny_estimate']), blind_cost * 9)
        self.assertFalse(result['automatic_calls_authorized_by_this_record'])
        self.assertEqual(result['observed_pilot_wall_seconds'], 2)

    def test_retry_costs_use_single_initial_record_denominator(self):
        job = self.archive.jobs[0]
        directory = self.run / 'calls' / job['call_id']
        result = json.loads((directory / 'result.json').read_text())
        attempt = copy.deepcopy(result['attempts'][0])
        attempt['attempt'] = 2
        request_path = directory / 'attempt_2.request.json'
        response_path = directory / 'attempt_2.response.json'
        dump(request_path, {'synthetic_retry': True})
        response_path.write_bytes((directory / 'attempt_1.response.json').read_bytes())
        attempt['raw_request_sha256'] = aggregate.sha256(request_path)
        attempt['raw_response_sha256'] = aggregate.sha256(response_path)
        result['attempts'][0]['status'] = 'parse_or_schema_failure'
        result['attempts'].append(attempt)
        dump(directory / 'result.json', result)
        self.ledger['entries']['synthetic/' + job['call_id'] + '/2'] = copy.deepcopy(self.ledger['entries']['synthetic/' + job['call_id'] + '/1'])
        dump(self.ledger_path, self.ledger)
        record = self.create()
        row = record['actual_usage_blind_arm_by_model']['deepseek-flash']
        self.assertEqual(row['initial_calls'], 2)
        self.assertEqual(row['record_reviews'], 4)
        self.assertEqual(row['attempts'], 3)
        self.assertEqual(row['prompt_tokens'], 30)
        self.assertEqual(Decimal(record['blind_arm_rates']['deepseek-flash']['attempts_per_initial_call']), Decimal('1.5'))

    def test_accounting_mismatch_fails_qc_and_withholds_forecasts(self):
        first = next(iter(self.ledger['entries'].values()))
        first['charged_upper_cny'] = '9'
        dump(self.ledger_path, self.ledger)
        result = self.create()
        self.assertTrue(result['pilot_complete'])
        self.assertFalse(result['pilot_qc_passed'])
        self.assertNotIn('primary_full_forecast', result)
        self.assertTrue(any('ledger_provenance_mismatch' in e for e in result['transport_or_provenance_errors']))

    def test_missing_call_cannot_authorize_full(self):
        (self.run / 'calls' / self.archive.jobs[0]['call_id'] / 'result.json').unlink()
        result = self.create()
        self.assertFalse(result['pilot_complete'])
        self.assertFalse(result['pilot_qc_passed'])
        self.assertFalse(result['primary_full_planning_budget_gate_passed'])

    def test_transport_error_and_changed_raw_request_fail_qc(self):
        directory = self.run / 'calls' / self.archive.jobs[0]['call_id']
        result = json.loads((directory / 'result.json').read_text())
        result['attempts'][0]['http_status'] = 429
        dump(directory / 'result.json', result)
        (directory / 'attempt_1.request.json').write_text('{}')
        record = self.create()
        self.assertFalse(record['pilot_qc_passed'])
        self.assertTrue(any('transport_or_execution_error' in e for e in record['transport_or_provenance_errors']))
        self.assertTrue(any('raw_request_hash_mismatch' in e for e in record['transport_or_provenance_errors']))

    def test_unknown_charge_and_reservations_reduce_remaining_budget(self):
        self.ledger['entries']['older/unknown'] = {'state': 'unknown_charge', 'charged_upper_cny': '8', 'reservation_upper_cny': '8'}
        self.ledger['entries']['other/reserved'] = {'state': 'reserved', 'charged_upper_cny': None, 'reservation_upper_cny': '90'}
        totals = forecast.ledger_totals(self.ledger)
        actual = sum(Decimal(x['charged_upper_cny']) for x in self.ledger['entries'].values() if x['charged_upper_cny'] is not None)
        self.assertEqual(Decimal(totals['remaining_upper_cny']), 100 - actual - 90)
        self.assertEqual(totals['entry_state_counts']['unknown_charge'], 1)

    def test_budget_fit_uses_planning_margin(self):
        initial = self.create()
        base = Decimal(initial['primary_full_forecast']['base_cost_upper_cny_estimate'])
        charged = sum(Decimal(x['charged_upper_cny']) for x in self.ledger['entries'].values())
        self.ledger['authorized_limit_cny'] = str(charged + base * Decimal('1.1'))
        dump(self.ledger_path, self.ledger)
        result = self.create()
        self.assertTrue(result['pilot_qc_passed'])
        self.assertFalse(result['primary_full_planning_budget_gate_passed'])
        self.assertFalse(result['primary_full_forecast']['fits_remaining_upper_budget_at_planning_multiplier'])

    def test_model_and_schedule_denominators_are_fixed(self):
        schedule = json.loads((self.root / 'work' / 'schedules' / 'full.json').read_text())
        exposed = forecast.schedule_exposures(schedule)
        self.assertEqual(exposed['deepseek-flash'], {'initial_calls': 2, 'record_reviews': 24})
        self.assertEqual(exposed['deepseek-v4-pro'], {'initial_calls': 1, 'record_reviews': 12})
        schedule['jobs'].append(schedule['jobs'][0])
        with self.assertRaises(ValueError):
            forecast.schedule_exposures(schedule)


if __name__ == '__main__':
    unittest.main()
