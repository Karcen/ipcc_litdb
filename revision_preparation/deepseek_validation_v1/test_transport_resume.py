"""Mock-only bounded transport recovery with preserved raw evidence and charges."""
import contextlib
import io
import json
import threading
import urllib.error
from decimal import Decimal
from unittest.mock import patch

import runner
from test_runner import RunnerTests, FakeResponse, FAKE_KEY

POLICY = {'provider': 'DeepSeek', 'transport_retries_authorized': True,
          'max_connection_retries': 2, 'budget_limit_cny': '100',
          'authorized_date': '2026-10-25', 'scope': 'Same prepared requests; uncertain charges retained.'}

class TransportRetryTests(RunnerTests):
    def resumed(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return runner.run_job_with_transport_retries(self.job, self.work, self.run,
                self.ledger, FAKE_KEY, self.halt, 1, retry_policy=POLICY)

    def test_success_is_never_replayed_with_retry_authorization(self):
        with patch.object(runner.urllib.request, 'urlopen', return_value=FakeResponse(self.envelope())) as opened:
            self.resumed()
            self.resumed()
        self.assertEqual(opened.call_count, 1)

    def test_historical_connection_failure_recovers_without_erasing_unknown_charge(self):
        with patch.object(runner.urllib.request, 'urlopen', side_effect=urllib.error.URLError('mock connection')):
            self.call()
        original = (self.call_dir() / 'result.json').read_bytes()
        unknown = next(iter(self.ledger.data['entries'].values()))['charged_upper_cny']
        self.halt.clear()
        with patch.object(runner.urllib.request, 'urlopen', return_value=FakeResponse(self.envelope())) as opened:
            result = self.resumed()
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(result['status'], 'valid')
        self.assertEqual(len(self.ledger.data['entries']), 2)
        self.assertGreater(Decimal(self.ledger.totals()['charged_upper_cny']), Decimal(unknown))
        archive = self.run / result['interrupted_attempt_archives'][0]['path']
        self.assertEqual((archive / 'result.json').read_bytes(), original)
        self.assertEqual(result['transport_retry_counts'], {'1': 1})
        self.assertTrue(result['attempts'][0]['ledger_attempt_id'].endswith('/transport_retry_1'))

    def test_two_transport_retries_are_global_across_resume_processes(self):
        with patch.object(runner.urllib.request, 'urlopen', side_effect=urllib.error.URLError('mock connection')) as opened:
            with self.assertRaisesRegex(runner.RunStop, 'retry limit'):
                self.resumed()
        self.assertEqual(opened.call_count, 3)
        self.assertEqual(len(self.ledger.data['entries']), 3)
        with patch.object(runner.urllib.request, 'urlopen') as opened:
            with self.assertRaisesRegex(runner.RunStop, 'retry limit'):
                self.resumed()
            opened.assert_not_called()
        self.assertEqual({v['state'] for v in self.ledger.data['entries'].values()}, {'unknown_charge'})

    def test_http_failure_not_retried_even_with_policy(self):
        failure = urllib.error.HTTPError(runner.ENDPOINT, 401, 'mock authorization', {}, io.BytesIO(b'{}'))
        with patch.object(runner.urllib.request, 'urlopen', side_effect=failure) as opened:
            with self.assertRaisesRegex(runner.RunStop, 'cannot be retried'):
                self.resumed()
        self.assertEqual(opened.call_count, 1)
        self.assertTrue(self.halt.is_set())

    def test_halted_after_first_schema_failure_resumes_only_second_slot(self):
        class HaltAfterResponse(FakeResponse):
            def read(inner):
                self.halt.set()
                return super().read()
        with patch.object(runner.urllib.request, 'urlopen', return_value=HaltAfterResponse(self.envelope({'cases': []}))):
            result = self.call()
        self.assertEqual(result['status'], 'halted_without_new_call')
        old = (self.call_dir() / 'attempt_1.response.json').read_bytes()
        self.halt.clear()
        with patch.object(runner.urllib.request, 'urlopen', return_value=FakeResponse(self.envelope())) as opened:
            result = self.resumed()
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(result['status'], 'valid')
        self.assertEqual([x['attempt'] for x in result['attempts']], [1, 2])
        self.assertEqual((self.call_dir() / 'attempt_1.response.json').read_bytes(), old)
        self.assertEqual(len(self.ledger.data['entries']), 2)

    def test_second_schema_request_transport_retry_does_not_reset_schema_budget(self):
        side = [FakeResponse(self.envelope({'cases': []})), urllib.error.URLError('mock connection'),
                FakeResponse(self.envelope({'cases': []}))]
        with patch.object(runner.urllib.request, 'urlopen', side_effect=side) as opened:
            result = self.resumed()
        self.assertEqual(opened.call_count, 3)
        self.assertEqual(result['status'], 'unresolvable_after_two_invalid_outputs')
        self.assertEqual([x['attempt'] for x in result['attempts']], [1, 2])
        self.assertEqual(result['transport_retry_counts'], {'2': 1})

    def test_archive_tampering_blocks_before_resend(self):
        with patch.object(runner.urllib.request, 'urlopen', side_effect=[urllib.error.URLError('mock connection'), FakeResponse(self.envelope())]):
            result = self.resumed()
        archive = self.run / result['interrupted_attempt_archives'][0]['path']
        (archive / 'result.json').write_text('{}')
        with patch.object(runner.urllib.request, 'urlopen') as opened:
            with self.assertRaisesRegex(runner.RunStop, 'evidence changed'):
                self.resumed()
            opened.assert_not_called()
        state = json.loads((self.call_dir() / 'resume_state.json').read_text())
        with self.assertRaisesRegex(runner.RunStop, 'evidence changed'):
            runner.verify_interruption_history(self.call_dir(), state)
