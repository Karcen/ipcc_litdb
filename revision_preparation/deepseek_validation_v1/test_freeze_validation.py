"""Synthetic-only checks for deterministic lossless validation archives."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import freeze_validation as archive_tools
from freeze_validation import freeze, load_manifest, restore


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source'
        (self.source / 'work/calls').mkdir(parents=True)
        (self.source / 'work/calls/response.json').write_bytes(b'{"test": "synthetic only"}\n')
        (self.source / 'code.py').write_bytes(b'print("synthetic fixture")\n')
        self.archive = self.root / 'archive'

    def tearDown(self):
        self.temporary.cleanup()

    def test_exact_roundtrip_and_deterministic_chunks(self):
        first = freeze(self.source, self.archive, include=['work', 'code.py'], chunk_bytes=50, logical_bytes=40)
        second_dir = self.root / 'second'
        second = freeze(self.source, second_dir, include=['code.py', 'work'], chunk_bytes=50, logical_bytes=40)
        self.assertEqual(first, second)
        for path in self.archive.iterdir():
            self.assertEqual(path.read_bytes(), (second_dir / path.name).read_bytes())
        output = self.root / 'restored'
        report = restore(self.archive, output)
        self.assertEqual(report['restored_files'], 2)
        self.assertEqual((output / 'work/calls/response.json').read_bytes(), (self.source / 'work/calls/response.json').read_bytes())
        self.assertEqual(restore(self.archive, output), report)

    def test_large_incompressible_member_splits_below_limit(self):
        (self.source / 'random.bin').write_bytes(os.urandom(800000))
        manifest = freeze(self.source, self.archive, include=['random.bin'])
        chunks = manifest['archives'][0]['chunks']
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(part['bytes'] <= 640 * 1024 for part in chunks))
        restore(self.archive, self.root / 'restored')
        self.assertEqual((self.root / 'restored/random.bin').read_bytes(), (self.source / 'random.bin').read_bytes())

    def test_corrupt_chunk_and_destination_conflict_fail(self):
        manifest = freeze(self.source, self.archive, include=['work'])
        output = self.root / 'restored'
        restore(self.archive, output)
        target = output / 'work/calls/response.json'
        target.write_bytes(b'preserve this content')
        with self.assertRaises(ValueError):
            restore(self.archive, output)
        self.assertEqual(target.read_bytes(), b'preserve this content')
        chunk = self.archive / manifest['archives'][0]['chunks'][0]['path']
        chunk.write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            restore(self.archive, self.root / 'other')

    def test_active_lock_refuses_freeze(self):
        (self.source / 'work/.active_runner.lock').write_bytes(b'fixture lock')
        with self.assertRaisesRegex(ValueError, 'runner is active'):
            freeze(self.source, self.archive)

    def test_private_names_temporaries_and_sidecars_are_excluded_without_reading(self):
        for name in ('.env', 'credentials.json', 'temporary.tmp', '._code.py'):
            (self.source / name).write_bytes(b'fixture private placeholder')
        manifest = freeze(self.source, self.archive, include=['.'])
        names = {item['path'] for item in manifest['members']}
        self.assertEqual(names, {'code.py', 'work/calls/response.json'})
        self.assertIn('credentials.json', manifest['excluded_paths'])

    def test_unsafe_manifest_and_symlink_source_are_rejected(self):
        (self.source / 'linked.py').symlink_to(self.source / 'code.py')
        with self.assertRaises(ValueError):
            freeze(self.source, self.archive, include=['linked.py'])
        freeze(self.source, self.archive, include=['code.py'])
        path = self.archive / 'manifest.json'
        manifest = json.loads(path.read_text())
        manifest['members'][0]['path'] = '../escape'
        path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            restore(self.archive, self.root / 'restored')

    def test_format2_roundtrip_is_deterministic_and_bounded(self):
        first = freeze(self.source, self.archive, include=['work', 'code.py'], manifest_format=2)
        second_dir = self.root / 'second'
        second = freeze(self.source, second_dir, include=['code.py', 'work'], manifest_format='2')
        self.assertEqual(first, second)
        index = json.loads((self.archive / 'manifest.json').read_text())
        self.assertEqual(index['format_version'], 2)
        self.assertNotIn('members', index)
        self.assertEqual(load_manifest(self.archive), first)
        for path in self.archive.iterdir():
            self.assertLessEqual(path.stat().st_size, 700 * 1024)
            self.assertEqual(path.read_bytes(), (second_dir / path.name).read_bytes())
        output = self.root / 'restored'
        self.assertEqual(restore(self.archive, output)['restored_files'], 2)
        self.assertEqual((output / 'code.py').read_bytes(), (self.source / 'code.py').read_bytes())

    def large_manifest(self):
        members = [{'path': f'work/calls/synthetic_{number:06d}/response.json',
                    'archive': 'validation_0001.tar.gz', 'bytes': 1,
                    'sha256': archive_tools.sha(str(number).encode())} for number in range(25000)]
        return {'format_version': 1, 'analysis_package': '1.5.0',
                'archives': [{'id': 'validation_0001.tar.gz', 'bytes': 1, 'sha256': '0' * 64, 'chunks': []}],
                'members': members, 'source_file_count': len(members), 'source_bytes': len(members),
                'chunk_bytes_limit': 640 * 1024, 'original_member_bytes_unchanged': True, 'network_requests': 0}

    def test_large_manifest_automatically_splits_and_preserves_provenance(self):
        original = self.large_manifest()
        result = archive_tools._store_manifest(original, self.archive)
        self.assertEqual(result['format_version'], 2)
        index = json.loads((self.archive / 'manifest.json').read_text())
        self.assertGreater(len(index['manifest_payload']['chunks']), 1)
        self.assertLessEqual((self.archive / 'manifest.json').stat().st_size, 700 * 1024)
        self.assertTrue(all(path.stat().st_size <= 700 * 1024 for path in self.archive.iterdir()))
        verified = load_manifest(self.archive)
        self.assertEqual(verified['members'], original['members'])
        self.assertEqual(verified['archives'], original['archives'])

    def test_manifest_payload_corruption_fails_before_extracting(self):
        freeze(self.source, self.archive, include=['work'], manifest_format=2)
        index = json.loads((self.archive / 'manifest.json').read_text())
        part = self.archive / index['manifest_payload']['chunks'][0]['path']
        part.write_bytes(b'corrupt payload chunk')
        output = self.root / 'restored'
        with self.assertRaises(ValueError):
            restore(self.archive, output)
        self.assertFalse(output.exists())

    def test_manifest_compressed_uncompressed_and_summary_provenance(self):
        freeze(self.source, self.archive, include=['work'], manifest_format=2)
        path = self.archive / 'manifest.json'
        original = path.read_bytes()
        for field in ('sha256', 'uncompressed_sha256'):
            index = json.loads(original)
            index['manifest_payload'][field] = '0' * 64
            path.write_text(json.dumps(index))
            with self.assertRaises(ValueError):
                restore(self.archive, self.root / 'restored')
        index = json.loads(original)
        index['source_file_count'] += 1
        path.write_text(json.dumps(index))
        with self.assertRaisesRegex(ValueError, 'metadata differ'):
            load_manifest(self.archive)

    def test_manifest_index_cannot_reference_itself_or_change_chunk_sequence(self):
        freeze(self.source, self.archive, include=['work'], manifest_format=2)
        path = self.archive / 'manifest.json'
        original = path.read_bytes()
        for bad in ('manifest.json', '../outside.bin', 'manifest_payload.part_0002.bin'):
            index = json.loads(original)
            index['manifest_payload']['chunks'][0]['path'] = bad
            path.write_text(json.dumps(index))
            with self.assertRaisesRegex(ValueError, 'cycles'):
                load_manifest(self.archive)

    def test_format1_remains_readable_and_new_oversized_format1_is_refused(self):
        legacy = freeze(self.source, self.archive, include=['work'], manifest_format=1)
        self.assertEqual(load_manifest(self.archive), legacy)
        self.assertEqual(restore(self.archive, self.root / 'restored')['restored_files'], 1)
        large = self.large_manifest()
        directory = self.root / 'oversized_new'
        with self.assertRaisesRegex(ValueError, 'Format 1 manifest exceeds'):
            archive_tools._store_manifest(large, directory, manifest_format=1)
        self.assertFalse((directory / 'manifest.json').exists())
        # Legacy files are readable even if an older publisher made them large.
        (self.archive / 'manifest.json').write_bytes(archive_tools._json_bytes(large))
        self.assertEqual(load_manifest(self.archive), large)

    def test_oversized_index_fails_without_publishing_it(self):
        with patch.object(archive_tools, 'CHUNK_BYTES', 64):
            with self.assertRaisesRegex(ValueError, 'payload index exceeds'):
                archive_tools._store_manifest(self.large_manifest(), self.archive)
        self.assertFalse((self.archive / 'manifest.json').exists())


if __name__ == '__main__':
    unittest.main()
