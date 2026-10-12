"""Offline tests for archive integrity, relocation and fetch-output guards."""

from pathlib import Path
from datetime import datetime, timezone
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
import bibtex_helpers
import check_archived_verification as checker
import verify_reference_metadata as fetcher

ROOT = Path(__file__).resolve().parent


def run_script(script, *arguments, cwd=None):
    return subprocess.run([sys.executable, '-B', str(script), *map(str, arguments)],
                          cwd=cwd, capture_output=True, text=True, check=False)


def archive_copy(destination):
    destination.mkdir()
    shutil.copytree(ROOT/'raw', destination/'raw')
    for name in ['api_response_inventory.json', 'reference_verification.json',
                 'reference_verification.csv', 'references_additions.bib',
                 'bibtex_helpers.py', 'check_archived_verification.py',
                 'generate_verification_records.py']:
        shutil.copy2(ROOT/name, destination/name)
    return destination


class VerificationToolsTest(unittest.TestCase):
    def test_pages_preserve_article_numbers_and_ranges(self):
        for source, expected in [('510-515', '510--515'), ('510--515', '510--515'),
                                 ('1123-30', '1123--1130'), ('pp. 18–22', '18--22'),
                                 ('e0123456', 'e0123456'), (None, '')]:
            with self.subTest(source=source):
                self.assertEqual(bibtex_helpers.format_pages(source), expected)

    def test_parser_retains_nested_braces_and_rejects_duplicate_keys(self):
        fields = {'title':'A {nested} title', 'doi':'10.1000/example'}
        entry = bibtex_helpers.render_entry('article', 'sample', fields)
        self.assertEqual(checker.parse_bibtex(entry)['sample']['fields'], fields)
        with self.assertRaisesRegex(ValueError, 'Duplicate BibTeX key'):
            checker.parse_bibtex(entry + '\n' + entry)

    def test_fixed_archive_checks_six_entries_and_eleven_payloads(self):
        result = checker.verify(ROOT)
        self.assertTrue(result['passed'])
        self.assertEqual(result['accepted_bibtex_entries'], 6)
        self.assertEqual(result['work_payload_hashes_checked'], 8)
        self.assertEqual(result['search_payload_hashes_checked'], 3)

    def test_relocated_archive_is_read_only_and_generator_bytes_match(self):
        with tempfile.TemporaryDirectory(prefix='ipcc-reference-test-') as temporary:
            copied = archive_copy(Path(temporary)/'relocated')
            before = {str(p.relative_to(copied)):hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in copied.rglob('*') if p.is_file()}
            checked = run_script(copied/'check_archived_verification.py', cwd=temporary)
            generated = run_script(copied/'generate_verification_records.py', cwd=temporary)
            self.assertEqual(checked.returncode, 0, checked.stderr + checked.stdout)
            self.assertEqual(generated.returncode, 0, generated.stderr + generated.stdout)
            self.assertTrue(json.loads(generated.stdout)['passed'])
            after = {str(p.relative_to(copied)):hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in copied.rglob('*') if p.is_file()}
            self.assertEqual(before, after)

    def test_payload_corruption_fails(self):
        with tempfile.TemporaryDirectory(prefix='ipcc-reference-test-') as temporary:
            copied = archive_copy(Path(temporary)/'archive')
            payload = copied/'raw'/'price1965networks_crossref.json'
            payload.write_bytes(payload.read_bytes() + b' ')
            with self.assertRaisesRegex(ValueError, 'Payload hash differs'):
                checker.verify(copied)

    def test_bibtex_corruption_fails(self):
        with tempfile.TemporaryDirectory(prefix='ipcc-reference-test-') as temporary:
            copied = archive_copy(Path(temporary)/'archive')
            bibliography = copied/'references_additions.bib'
            bibliography.write_text(bibliography.read_text().replace('Networks of Scientific Papers', 'Altered title'), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'bibliography hash differs'):
                checker.verify(copied)

    def test_explicit_regeneration_uses_fresh_directory(self):
        with tempfile.TemporaryDirectory(prefix='ipcc-reference-test-') as temporary:
            destination = Path(temporary)/'regenerated'
            result = run_script(ROOT/'generate_verification_records.py', '--output-dir', destination)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            for name in ['references_additions.bib', 'reference_verification.json', 'reference_verification.csv']:
                self.assertEqual((ROOT/name).read_bytes(), (destination/name).read_bytes())
            repeated = run_script(ROOT/'generate_verification_records.py', '--output-dir', destination)
            self.assertNotEqual(repeated.returncode, 0)

    def test_fetcher_rejects_fixed_and_existing_directories_before_network(self):
        with tempfile.TemporaryDirectory(prefix='ipcc-reference-test-') as temporary:
            for destination in [ROOT, ROOT/'raw', Path(temporary)]:
                with self.subTest(destination=destination), mock.patch.object(sys, 'argv', ['fetch', '--output-dir', str(destination)]), mock.patch.object(sys, 'stderr', io.StringIO()), mock.patch('urllib.request.urlopen') as request:
                    with self.assertRaises(SystemExit) as stopped:
                        fetcher.main()
                    self.assertEqual(stopped.exception.code, 2)
                    request.assert_not_called()

    def test_fetcher_query_date_comes_from_actual_utc_timestamp(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"message":{"items":[],"total-results":0}}'
        response.status = 200
        response.headers.get.return_value = 'application/json'
        when = datetime(2031, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory(prefix='ipcc-reference-test-') as temporary:
            with mock.patch.object(fetcher, 'datetime') as clock, mock.patch('urllib.request.urlopen', return_value=response):
                clock.now.return_value = when
                result = fetcher.fetch('sample', 'crossref_search', 'https://example.test/query', Path(temporary))
            self.assertEqual(result['query_date'], '2031-02-03')
            self.assertEqual(result['queried_at_utc'], when.isoformat())
            self.assertEqual(result['raw_response_path'], 'raw/sample_crossref_search.json')
            self.assertEqual(result['bibliographic_fields'], {'items':[], 'total_results':0})


if __name__ == '__main__':
    unittest.main()
