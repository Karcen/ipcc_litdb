"""Offline restoration checks for hashes, regular files, and overwrite refusal."""
import copy
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

import restore_source_inputs as restore


class SourceRestorationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repository"
        self.destination = self.root / "restored"
        self.archive_path = "revision_preparation/review_round3/frozen_inputs/fixture.tar.gz"
        self.member_name = "panel/frames/tiny.json"
        self.direct_path = "revision_preparation/review_round3/panel/prompt_A.txt"
        self.manifest_path = self.root / "source_manifest.json"
        self.content = b'{"cases": []}\n'
        self.make_fixture()

    def tearDown(self):
        self.temporary.cleanup()

    def save(self, relative, data):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"repository_path": relative, "bytes": len(data), "sha256": restore.sha256(data)}

    def make_fixture(self, member_type="file", duplicate=False, missing=False):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            member = tarfile.TarInfo("unselected.txt" if missing else self.member_name)
            if member_type == "symlink":
                member.type = tarfile.SYMTYPE
                member.linkname = "../../outside.txt"
                archive.addfile(member)
            else:
                member.size = len(self.content)
                archive.addfile(member, io.BytesIO(self.content))
                if duplicate:
                    archive.addfile(member, io.BytesIO(self.content))
        archive = self.save(self.archive_path, stream.getvalue())
        direct = self.save(self.direct_path, b"Read supplied references only.\n")
        member = {"path": self.member_name, "bytes": len(self.content), "sha256": restore.sha256(self.content),
                  "archive": "frozen_inputs/fixture.tar.gz"}
        original_index = {"archives": [{"path": "frozen_inputs/fixture.tar.gz", "bytes": archive["bytes"], "sha256": archive["sha256"]}], "members": [member]}
        frozen = self.save("revision_preparation/review_round3/frozen_inputs/manifest.json", (json.dumps(original_index) + "\n").encode())
        self.manifest = {"format_version": 1, "analysis_package": "1.5.0", "source_commit": "fixture-pinned-revision",
                         "artifact_count": 2, "frozen_manifest": frozen, "archives": [archive],
                         "members": [{"repository_archive": self.archive_path, "archive_member": self.member_name,
                                      "destination": "frames/tiny.json", "bytes": member["bytes"], "sha256": member["sha256"]}],
                         "direct_files": [{**direct, "destination": "prompt_A.txt"}]}
        self.save_manifest()

    def save_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest) + "\n")

    def run_restore(self):
        return restore.restore_sources(self.repo, self.destination, self.manifest_path)

    def test_exact_bytes_idempotence_and_no_network(self):
        first = self.run_restore()
        self.assertEqual((self.destination / "frames/tiny.json").read_bytes(), self.content)
        self.assertEqual((self.destination / "prompt_A.txt").read_bytes(), b"Read supplied references only.\n")
        self.assertEqual(first["restored_artifacts"], 2)
        self.assertEqual(first["verified_archives"], 1)
        self.assertEqual(first["network_requests"], 0)
        self.assertEqual(first["model_calls_made"], 0)
        self.assertEqual(self.run_restore(), first)

    def test_archive_hash_corruption_fails(self):
        (self.repo / self.archive_path).write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "SHA-256 differs"):
            self.run_restore()

    def test_direct_hash_corruption_fails_before_writing(self):
        (self.repo / self.direct_path).write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "SHA-256 differs"):
            self.run_restore()
        self.assertFalse(self.destination.exists())

    def test_frozen_manifest_corruption_fails(self):
        (self.repo / self.manifest["frozen_manifest"]["repository_path"]).write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "SHA-256 differs"):
            self.run_restore()

    def test_member_descriptor_must_match_frozen_index(self):
        self.manifest["members"][0]["sha256"] = "0" * 64
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "original frozen index"):
            self.run_restore()

    def test_selected_symlink_member_is_rejected(self):
        self.make_fixture(member_type="symlink")
        with self.assertRaisesRegex(ValueError, "not a regular file"):
            self.run_restore()

    def test_duplicate_and_missing_members_are_rejected(self):
        self.make_fixture(duplicate=True)
        with self.assertRaisesRegex(ValueError, "duplicated"):
            self.run_restore()
        self.make_fixture(missing=True)
        with self.assertRaisesRegex(ValueError, "missing"):
            self.run_restore()

    def test_unsafe_destination_and_archive_member_paths_are_rejected(self):
        self.manifest["members"][0]["destination"] = "../outside.txt"
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.run_restore()
        self.manifest["members"][0]["destination"] = "frames/tiny.json"
        self.manifest["members"][0]["archive_member"] = "panel/../../outside.txt"
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.run_restore()

    def test_destination_symlink_and_conflicting_bytes_are_preserved(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.destination.mkdir()
        (self.destination / "frames").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Symbolic links"):
            self.run_restore()
        self.assertFalse((outside / "tiny.json").exists())
        (self.destination / "frames").unlink()
        (self.destination / "frames").mkdir()
        conflict = self.destination / "frames/tiny.json"
        conflict.write_bytes(b"existing user content")
        with self.assertRaisesRegex(ValueError, "refusing overwrite"):
            self.run_restore()
        self.assertEqual(conflict.read_bytes(), b"existing user content")

    def test_repository_symlink_is_rejected(self):
        direct = self.repo / self.direct_path
        direct.unlink()
        outside = self.root / "outside.txt"
        outside.write_bytes(b"Read supplied references only.\n")
        direct.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "Symbolic links"):
            self.run_restore()

    def test_published_manifest_has_all_39_pinned_descriptors(self):
        published = json.loads(restore.DEFAULT_MANIFEST.read_text())
        self.assertEqual(published["source_commit"], "f2383b80bf95e777249b3d0705392f2b58035cb1")
        self.assertEqual(published["artifact_count"], 39)
        self.assertEqual(len(published["archives"]), 5)
        self.assertEqual(len(published["members"]), 25)
        self.assertEqual(len(published["direct_files"]), 14)
        self.assertEqual(published["frozen_manifest"]["sha256"], "d733d00ddfc7d7b0e0dc41ae835e7b920cde179612140303ffd7329cb09ad218")


if __name__ == "__main__":
    unittest.main()
