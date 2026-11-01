"""Offline checks for provenance-only candidate derivation."""
import copy
import json
import sqlite3
import unittest

from prepare_candidate_provenance import derive_packet, text_digest


class CandidateProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.packet = {'cases': [{'case_id': 'M1', 'task': 'identity', 'records': [
            {'record_id': 'R001', 'raw_reference': 'Smith 2001. Study A.'},
            {'record_id': 'R002', 'raw_reference': 'Smith 2001. Study A revised.'}], 'frozen_evidence': []}]}
        self.rows = [{'case_id': 'M1', 'AR5_paper_id': '10', 'AR6_paper_id': '20',
            'AR5_occurrence_id': 'AR5-WG1-Chapter 1-00001', 'AR6_occurrence_id': 'AR6-WG1-Chapter 1-00001'}]
        self.database = sqlite3.connect(':memory:')
        self.database.executescript('CREATE TABLE chapters(chapter_id TEXT,report_id TEXT);'
            'CREATE TABLE references_occurrence(occurrence_id TEXT,paper_id INTEGER,raw_reference TEXT,chapter_id TEXT);'
            "INSERT INTO chapters VALUES('C5','AR5'),('C6','AR6');")
        for record, report, chapter in zip(self.packet['cases'][0]['records'], ('AR5', 'AR6'), ('C5', 'C6')):
            self.database.execute('INSERT INTO references_occurrence VALUES(?,?,?,?)',
                (self.rows[0][report + '_occurrence_id'], self.rows[0][report + '_paper_id'], record['raw_reference'], chapter))

    def tearDown(self):
        self.database.close()

    def derive(self, packet=None, rows=None):
        return derive_packet(self.packet if packet is None else packet,
            self.rows if rows is None else rows, connection=self.database, expected_cases=1)

    def test_adds_only_expected_groups_and_preserves_input_objects(self):
        original = copy.deepcopy(self.packet)
        derived, provenance = self.derive()
        self.assertEqual(self.packet, original)
        self.assertEqual([record['report_group'] for record in derived['cases'][0]['records']], ['earlier', 'later'])
        self.assertEqual([row['source_report'] for row in provenance], ['AR5', 'AR6'])
        self.assertEqual(provenance[0]['raw_reference_sha256'], text_digest(original['cases'][0]['records'][0]['raw_reference']))
        self.assertTrue(all(row['database_checked'] == 'true' for row in provenance))

    def test_reordered_records_fail(self):
        packet = copy.deepcopy(self.packet)
        packet['cases'][0]['records'].reverse()
        with self.assertRaisesRegex(ValueError, 'R001 then R002'):
            self.derive(packet)

    def test_changed_raw_text_fails_source_hash(self):
        packet = copy.deepcopy(self.packet)
        packet['cases'][0]['records'][0]['raw_reference'] += ' changed'
        with self.assertRaisesRegex(ValueError, 'raw-reference hash'):
            self.derive(packet)

    def test_wrong_report_or_assigned_id_fails(self):
        for field, value in [('AR5_occurrence_id', 'AR6-WG1-Chapter 1-00001'), ('AR5_paper_id', '999')]:
            rows = copy.deepcopy(self.rows)
            rows[0][field] = value
            with self.assertRaises(ValueError):
                self.derive(rows=rows)

    def test_duplicate_or_missing_case_ids_fail(self):
        for packet, rows in [({'cases': self.packet['cases'] * 2}, self.rows),
                             (self.packet, [{**self.rows[0], 'case_id': 'wrong'}])]:
            with self.assertRaises(ValueError):
                self.derive(packet, rows)

    def test_existing_conflicting_provenance_fails(self):
        packet = copy.deepcopy(self.packet)
        packet['cases'][0]['records'][0]['report_group'] = 'later'
        with self.assertRaisesRegex(ValueError, 'contradicts'):
            self.derive(packet)

    def test_database_check_is_explicit_when_unavailable(self):
        derived, provenance = derive_packet(self.packet, self.rows, expected_cases=1)
        self.assertEqual(provenance[0]['database_checked'], 'false')
        self.assertEqual(derived['cases'][0]['records'][0]['raw_reference'], self.packet['cases'][0]['records'][0]['raw_reference'])
