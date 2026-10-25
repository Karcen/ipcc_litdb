"""Offline tests for explicit schedule selection and dependency validation."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from schedule_selection import digest, select_schedule_path


class ScheduleSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.work = Path(self.temp.name).resolve()
        self.write('input_manifest.json', {})
        self.write('inputs/candidates/blind_candidates.json', {'cases': []})
        self.write('packets/new/batch.json', {'cases': []})
        packets = {'packets/new/batch.json': digest(self.work / 'packets/new/batch.json')}
        self.provenance = {'packet_sha256': packets, 'output_sha256': packets,
            'source_input_sha256': {'inputs/candidates/blind_candidates.json': digest(self.work / 'inputs/candidates/blind_candidates.json')},
            'canonical_candidate_input_sha256': digest(self.work / 'inputs/candidates/blind_candidates.json')}
        self.write('derived/manifest.json', self.provenance)
        self.schedule = {'stage': 'candidates', 'input_manifest_sha256': digest(self.work / 'input_manifest.json'),
            'derived_provenance_manifest_path': 'derived/manifest.json',
            'derived_provenance_manifest_sha256': digest(self.work / 'derived/manifest.json'),
            'jobs': [{'job_id': 'one', 'packet_path': 'packets/new/batch.json', 'packet_sha256': packets['packets/new/batch.json']}]}
        self.write('schedules/new.json', self.schedule)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        path = self.work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def select(self):
        return select_schedule_path(self.work, 'candidates', 'schedules/new.json')

    def test_default_path_is_returned_without_creating_it(self):
        path = select_schedule_path(self.work, 'full')
        self.assertEqual(path, self.work / 'schedules/full.json')
        self.assertFalse(path.exists())

    def test_valid_explicit_schedule_accepts_relative_and_absolute_paths(self):
        self.assertEqual(self.select(), self.work / 'schedules/new.json')
        self.assertEqual(select_schedule_path(self.work, 'candidates', self.work / 'schedules/new.json'), self.select())

    def test_wrong_stage_and_input_manifest_fail(self):
        for field, value in [('stage', 'full'), ('input_manifest_sha256', 'wrong')]:
            changed = copy.deepcopy(self.schedule)
            changed[field] = value
            self.write('schedules/new.json', changed)
            with self.assertRaises(ValueError):
                self.select()

    def test_path_escape_and_schedule_outside_folder_fail(self):
        self.write('outside.json', self.schedule)
        for path in ('../elsewhere.json', 'outside.json'):
            with self.assertRaises(ValueError):
                select_schedule_path(self.work, 'candidates', path)

    def test_changed_packet_fails(self):
        self.write('packets/new/batch.json', {'changed': True})
        with self.assertRaisesRegex(ValueError, 'packet hash'):
            self.select()

    def test_missing_or_changed_provenance_fails(self):
        (self.work / 'derived/manifest.json').unlink()
        with self.assertRaises(ValueError):
            self.select()
        self.write('derived/manifest.json', {})
        with self.assertRaisesRegex(ValueError, 'manifest hash'):
            self.select()

    def test_manifest_must_cover_exactly_the_scheduled_packets(self):
        self.provenance['packet_sha256']['packets/extra.json'] = 'extra'
        self.write('derived/manifest.json', self.provenance)
        self.schedule['derived_provenance_manifest_sha256'] = digest(self.work / 'derived/manifest.json')
        self.write('schedules/new.json', self.schedule)
        with self.assertRaisesRegex(ValueError, 'scheduled packets'):
            self.select()

    def test_changed_source_file_and_duplicate_jobs_fail(self):
        self.write('inputs/candidates/blind_candidates.json', {'changed': True})
        with self.assertRaisesRegex(ValueError, 'source input'):
            self.select()
        self.schedule['jobs'] *= 2
        self.write('schedules/new.json', self.schedule)
        with self.assertRaisesRegex(ValueError, 'duplicate job'):
            self.select()
