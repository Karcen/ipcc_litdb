"""Synthetic completed report publication and exact gzip restoration tests."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from publish_reports import publish_report, restore_report


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / 'completed_scope'; self.source.mkdir()
        self.registry = self.root / 'registry'
        (self.source / 'summary.json').write_text('{"status":"complete","incomplete_call_ids":[]}\n')
        (self.source / 'recurrence_scenarios.csv').write_text('scenario,value\nsynthetic,1\n')
        (self.source / 'record_consensus.csv').write_text('case_id,value\nsynthetic,1\n')
        self.write_manifest()

    def tearDown(self):
        self.temporary.cleanup()

    def write_manifest(self):
        names = [path for path in self.source.rglob('*') if path.is_file() and path.name != 'manifest.json']
        data = {'status': 'complete', 'all_scheduled_calls_complete': True,
                'output_sha256': {path.relative_to(self.source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in names}}
        (self.source / 'manifest.json').write_text(json.dumps(data) + '\n')

    def test_lossless_deterministic_gzip_and_small_plain_summaries(self):
        manifest = publish_report(self.source, self.registry)
        self.assertTrue((self.registry / 'record_consensus.csv.gz').is_file())
        self.assertTrue((self.registry / 'recurrence_scenarios.csv').is_file())
        self.assertTrue((self.registry / 'manifest.json').is_file())
        self.assertEqual(publish_report(self.source, self.registry), manifest)
        second = self.root / 'second'
        publish_report(self.source, second)
        self.assertEqual((self.registry / 'record_consensus.csv.gz').read_bytes(), (second / 'record_consensus.csv.gz').read_bytes())
        destination = self.root / 'restored'
        restore_report(self.registry, destination)
        for path in self.source.iterdir():
            self.assertEqual(path.read_bytes(), (destination / path.name).read_bytes())

    def test_incomplete_source_and_changed_output_hash_are_rejected(self):
        (self.source / 'manifest.json').write_text('{"status":"partial"}')
        with self.assertRaises(ValueError):publish_report(self.source, self.registry)
        self.write_manifest()
        (self.source / 'record_consensus.csv').write_text('changed')
        with self.assertRaises(ValueError):publish_report(self.source, self.registry)

    def test_validated_addition_creates_new_manifest_without_overwrite(self):
        first = publish_report(self.source, self.registry)
        old_manifest = first.read_bytes()
        (self.source / 'new_summary.csv').write_text('metric,value\nsynthetic,2\n')
        self.write_manifest()
        with self.assertRaises(ValueError):publish_report(self.source, self.registry)
        second = publish_report(self.source, self.registry, allow_additions=True)
        self.assertEqual(second.name, 'publication_manifest_v002.json')
        self.assertEqual(first.read_bytes(), old_manifest)
        old_restored, new_restored = self.root / 'old', self.root / 'new'
        restore_report(self.registry, old_restored, publication_manifest=first)
        restore_report(self.registry, new_restored)
        self.assertFalse((old_restored / 'new_summary.csv').exists())
        self.assertTrue((new_restored / 'new_summary.csv').exists())

    def test_changed_published_table_never_gets_overwritten(self):
        publish_report(self.source, self.registry)
        before = (self.registry / 'record_consensus.csv.gz').read_bytes()
        (self.source / 'record_consensus.csv').write_text('changed')
        (self.source / 'new_summary.csv').write_text('new')
        self.write_manifest()
        with self.assertRaises(ValueError):publish_report(self.source, self.registry, allow_additions=True)
        self.assertEqual((self.registry / 'record_consensus.csv.gz').read_bytes(), before)

    def test_corrupt_stored_bytes_block_restore(self):
        publish_report(self.source, self.registry)
        (self.registry / 'record_consensus.csv.gz').write_bytes(b'corrupt')
        with self.assertRaises(ValueError):restore_report(self.registry, self.root / 'restored')

    def test_source_manifest_alias_corruption_blocks_idempotent_publish(self):
        publish_report(self.source, self.registry)
        (self.registry / 'manifest.json').write_text('changed alias')
        with self.assertRaises(ValueError):publish_report(self.source, self.registry)

    def test_passed_spending_audit_requires_exact_table_hash(self):
        source = self.root / 'spending'; source.mkdir()
        data = b'attempt,cost\nsynthetic,1\n'
        (source / 'paid_attempts.csv').write_bytes(data)
        (source / 'spending_audit.json').write_text(json.dumps({'status': 'passed', 'invoice_claimed': False,
            'paid_attempts_sha256': hashlib.sha256(data).hexdigest()}))
        registry = self.root / 'spending_registry'
        publish_report(source, registry)
        self.assertTrue((registry / 'spending_audit.json').exists())
        (source / 'paid_attempts.csv').write_bytes(b'changed')
        with self.assertRaises(ValueError):publish_report(source, registry)


if __name__ == '__main__':
    unittest.main()
