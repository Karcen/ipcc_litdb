"""Offline rate-evidence checks; peak reservations and old charges stay fixed."""
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

import budget

WEEKEND = {'service_date': 'Sun, 25 Oct 2026 10:54:36 GMT',
           'requested_utc': '2026-10-25T10:54:00+00:00',
           'received_utc': '2026-10-25T10:54:36.260000+00:00'}


class RateEvidenceTests(unittest.TestCase):
    def test_verified_weekend_discount_and_clock_evidence(self):
        factor, evidence = budget.settlement_rate(**WEEKEND)
        self.assertEqual(factor, Decimal('0.5'))
        self.assertEqual(evidence['classification'], 'verified_offpeak')
        self.assertAlmostEqual(evidence['provider_to_received_seconds'], .26)
        self.assertEqual(evidence['buffered_start_utc'], '2026-10-25T10:49:00+00:00')
        self.assertFalse(evidence['holiday_discounts_applied'])

    def test_weekday_both_peak_windows_keep_peak(self):
        for hour in ('02', '08'):
            factor, evidence = budget.settlement_rate(
                service_date=f'Mon, 26 Oct 2026 {hour}:10:00 GMT',
                requested_utc=f'2026-10-26T{hour}:09:00+00:00',
                received_utc=f'2026-10-26T{hour}:10:00+00:00')
            self.assertEqual(factor, 1)
            self.assertEqual(evidence['reason'], 'buffered_window_intersects_weekday_peak')

    def test_weekday_offpeak_can_qualify(self):
        factor, _ = budget.settlement_rate(service_date='Mon, 26 Oct 2026 04:20:00 GMT',
            requested_utc='2026-10-26T04:19:00+00:00', received_utc='2026-10-26T04:20:00+00:00')
        self.assertEqual(factor, Decimal('.5'))

    def test_transition_buffers_and_exact_contact_keep_peak(self):
        for requested, received, service in (
            ('00:54:00', '00:55:00', '00:55:00'),
            ('04:03:00', '04:04:00', '04:04:00'),
            ('05:54:00', '05:55:00', '05:55:00'),
            ('10:04:00', '10:05:00', '10:05:00'),
        ):
            factor, evidence = budget.settlement_rate(service_date=f'Mon, 26 Oct 2026 {service} GMT',
                requested_utc=f'2026-10-26T{requested}+00:00', received_utc=f'2026-10-26T{received}+00:00')
            self.assertEqual(factor, 1)
            self.assertEqual(evidence['reason'], 'buffered_window_intersects_weekday_peak')

    def test_missing_invalid_naive_and_skewed_dates_keep_peak(self):
        self.assertEqual(budget.settlement_rate()[0], 1)
        for change in ({'service_date': None}, {'service_date': 'invalid'},
                       {'requested_utc': '2026-10-25T10:54:00'},
                       {'service_date': 'Sun, 25 Oct 2026 10:52:35 GMT'},
                       {'service_date': 'Sun, 25 Oct 2026 10:56:37 GMT'}):
            self.assertEqual(budget.settlement_rate(**{**WEEKEND, **change})[0], 1)

    def test_negative_and_overlong_intervals_keep_peak(self):
        for requested in ('2026-10-25T10:55:00+00:00', '2026-10-25T10:24:00+00:00'):
            factor, evidence = budget.settlement_rate(**{**WEEKEND, 'requested_utc': requested})
            self.assertEqual(factor, 1)
            self.assertEqual(evidence['reason'], 'invalid_or_excessive_request_interval')

    def test_buffer_checks_adjacent_day_and_no_holiday_exception(self):
        # Monday before 01:00 qualifies only while its entire buffer avoids 01:00.
        self.assertEqual(budget.settlement_rate(service_date='Mon, 26 Oct 2026 00:54:00 GMT',
            requested_utc='2026-10-26T00:53:00Z', received_utc='2026-10-26T00:54:00Z')[0], Decimal('.5'))
        # The published weekday rule remains in force on Christmas Day.
        self.assertEqual(budget.settlement_rate(service_date='Fri, 25 Dec 2026 02:10:00 GMT',
            requested_utc='2026-12-25T02:09:00Z', received_utc='2026-12-25T02:10:00Z')[0], 1)


class SettlementTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / 'budget.json'
        self.ledger = budget.Ledger(self.path, 100)
        self.usage = {'prompt_tokens': 100, 'completion_tokens': 10,
                      'prompt_cache_hit_tokens': 40, 'prompt_cache_miss_tokens': 60}

    def tearDown(self):
        self.temporary.cleanup()

    def test_reserve_peak_then_settle_discounted_hit_miss_and_output(self):
        for identifier, model, peak in (('flash', 'deepseek-flash', Decimal('.0002016')),
                                        ('pro', 'deepseek-v4-pro', Decimal('.000822'))):
            reservation = self.ledger.reserve(identifier, model, 100, 10)
            rates = budget.PEAK_CNY_PER_MILLION[model]
            self.assertEqual(Decimal(reservation), (100 * rates['miss'] + 10 * rates['output']) / budget.MILLION)
            charged = self.ledger.settle(identifier, self.usage, **WEEKEND)
            entry = self.ledger.data['entries'][identifier]
            self.assertEqual(Decimal(charged), peak / 2)
            self.assertEqual(Decimal(entry['peak_usage_upper_cny']), peak)
            self.assertEqual(entry['settlement_factor'], '0.5')
            self.assertEqual(entry['rate_evidence']['classification'], 'verified_offpeak')

    def test_default_settlement_remains_peak_and_legacy_entry_is_immutable(self):
        self.ledger.reserve('old', 'deepseek-flash', 100, 10)
        self.assertEqual(Decimal(self.ledger.settle('old', self.usage)), Decimal('.0002016'))
        # Simulate an older entry created before rate-evidence fields existed.
        for name in ('peak_usage_upper_cny', 'settlement_factor', 'rate_evidence'):
            self.ledger.data['entries']['old'].pop(name)
        self.ledger.save()
        original = json.loads(json.dumps(self.ledger.data['entries']['old']))
        self.ledger.reserve('new', 'deepseek-flash', 100, 10)
        self.ledger.settle('new', self.usage, **WEEKEND)
        restored = budget.Ledger(self.path, 100)
        self.assertEqual(restored.data['entries']['old'], original)
        with self.assertRaises(ValueError):
            restored.settle('old', self.usage, **WEEKEND)
        self.assertEqual(restored.data['entries']['old'], original)

    def test_totals_charge_discount_and_pending_peak_reservations(self):
        self.ledger.reserve('settled', 'deepseek-flash', 100, 10)
        amount = Decimal(self.ledger.settle('settled', self.usage, **WEEKEND))
        pending = Decimal(self.ledger.reserve('pending', 'deepseek-v4-pro', 1000, 1000))
        totals = self.ledger.totals()
        self.assertEqual(Decimal(totals['charged_upper_cny']), amount)
        self.assertEqual(Decimal(totals['reserved_upper_cny']), pending)
        self.assertEqual(Decimal(totals['remaining_upper_cny']), Decimal(100) - amount - pending)

    def test_unknown_charge_stays_full_peak_reservation(self):
        reserved = self.ledger.reserve('unknown', 'deepseek-flash', 100, 10)
        self.ledger.uncertain('unknown', 'TimeoutError')
        self.assertEqual(self.ledger.data['entries']['unknown']['charged_upper_cny'], reserved)

    def test_peak_reservation_breach_stops_even_when_discount_would_fit(self):
        self.ledger.reserve('breach', 'deepseek-flash', 60, 0)
        with self.assertRaises(budget.BudgetStop):
            self.ledger.settle('breach', self.usage, **WEEKEND)
        self.assertTrue(self.ledger.data['entries']['breach']['reservation_exceeded'])
        with self.assertRaises(budget.BudgetStop):
            self.ledger.reserve('later', 'deepseek-flash', 1, 1)


if __name__ == '__main__':
    unittest.main()
