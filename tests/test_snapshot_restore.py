"""Database snapshot restoration, verification, and replacement failures."""
import errno
import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from scripts.restore_database import restore_database
import scripts.restore_database as restore_module


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_manifest(snapshot_dir: Path, database: bytes, compressed: bytes) -> dict:
    manifest = {
        "compressed": {
            "file": "ipcc_litdb.sqlite.gz",
            "bytes": len(compressed),
            "sha256": digest(compressed),
        },
        "uncompressed": {
            "file": "ipcc_litdb.sqlite",
            "bytes": len(database),
            "sha256": digest(database),
        },
    }
    (snapshot_dir / "snapshot.json").write_text(json.dumps(manifest))
    (snapshot_dir / "ipcc_litdb.sqlite.gz").write_bytes(compressed)
    return manifest


@pytest.fixture
def snapshot(tmp_path):
    source = tmp_path / "source.sqlite"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE papers (paper_id INTEGER PRIMARY KEY, title TEXT)")
        connection.execute("INSERT INTO papers VALUES (1, 'Climate impacts')")
    database = source.read_bytes()
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()
    compressed = gzip.compress(database, mtime=0)
    manifest = write_manifest(snapshot_dir, database, compressed)
    return snapshot_dir, database, compressed, manifest


def test_restores_verified_sqlite_contents(snapshot, tmp_path):
    snapshot_dir, database, _, _ = snapshot
    destination = tmp_path / "restored.sqlite"
    result = restore_database(snapshot_dir=snapshot_dir, destination=destination)
    assert result == destination.resolve()
    assert destination.read_bytes() == database
    with sqlite3.connect(destination) as connection:
        assert connection.execute("SELECT * FROM papers").fetchall() == [(1, "Climate impacts")]
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_default_destination_uses_manifest_filename(snapshot):
    snapshot_dir, database, _, _ = snapshot
    destination = restore_database(snapshot_dir=snapshot_dir)
    assert destination == (snapshot_dir / "ipcc_litdb.sqlite").resolve()
    assert destination.read_bytes() == database


def test_preserves_existing_database_without_force(snapshot, tmp_path):
    snapshot_dir, _, _, _ = snapshot
    destination = tmp_path / "existing.sqlite"
    destination.write_bytes(b"existing local database")
    (snapshot_dir / "ipcc_litdb.sqlite.gz").write_bytes(b"broken snapshot")
    assert restore_database(snapshot_dir=snapshot_dir, destination=destination) == destination.resolve()
    assert destination.read_bytes() == b"existing local database"


@pytest.mark.parametrize("section", ["compressed", "uncompressed"])
def test_rejects_digest_mismatch(snapshot, tmp_path, section):
    snapshot_dir, _, _, manifest = snapshot
    manifest[section]["sha256"] = "0" * 64
    (snapshot_dir / "snapshot.json").write_text(json.dumps(manifest))
    destination = tmp_path / "rejected.sqlite"
    with pytest.raises(ValueError):
        restore_database(snapshot_dir=snapshot_dir, destination=destination)
    assert not destination.exists()
    assert not list(tmp_path.glob(".rejected.sqlite.*.tmp"))


@pytest.mark.parametrize("section", ["compressed", "uncompressed"])
def test_rejects_size_mismatch(snapshot, tmp_path, section):
    snapshot_dir, _, _, manifest = snapshot
    manifest[section]["bytes"] += 1
    (snapshot_dir / "snapshot.json").write_text(json.dumps(manifest))
    destination = tmp_path / "rejected.sqlite"
    with pytest.raises(ValueError):
        restore_database(snapshot_dir=snapshot_dir, destination=destination)
    assert not destination.exists()
    assert not list(tmp_path.glob(".rejected.sqlite.*.tmp"))


def test_rejects_truncated_gzip_after_compressed_verification(snapshot, tmp_path):
    snapshot_dir, database, compressed, _ = snapshot
    write_manifest(snapshot_dir, database, compressed[:-8])
    destination = tmp_path / "rejected.sqlite"
    with pytest.raises((EOFError, OSError, ValueError)):
        restore_database(snapshot_dir=snapshot_dir, destination=destination)
    assert not destination.exists()
    assert not list(tmp_path.glob(".rejected.sqlite.*.tmp"))


def test_rejects_non_sqlite_payload_with_valid_digests(snapshot, tmp_path):
    snapshot_dir, _, _, _ = snapshot
    database = b"This file is not a SQLite database."
    write_manifest(snapshot_dir, database, gzip.compress(database, mtime=0))
    destination = tmp_path / "rejected.sqlite"
    with pytest.raises((sqlite3.Error, ValueError)):
        restore_database(snapshot_dir=snapshot_dir, destination=destination)
    assert not destination.exists()
    assert not list(tmp_path.glob(".rejected.sqlite.*.tmp"))


def test_forced_restore_failure_preserves_prior_database(snapshot, tmp_path):
    snapshot_dir, _, _, manifest = snapshot
    manifest["uncompressed"]["sha256"] = "0" * 64
    (snapshot_dir / "snapshot.json").write_text(json.dumps(manifest))
    destination = tmp_path / "existing.sqlite"
    destination.write_bytes(b"existing local database")
    with pytest.raises(ValueError):
        restore_database(snapshot_dir=snapshot_dir, destination=destination, force=True)
    assert destination.read_bytes() == b"existing local database"
    assert not list(tmp_path.glob(".existing.sqlite.*.tmp"))


def test_forced_restore_replaces_prior_database(snapshot, tmp_path):
    snapshot_dir, database, _, _ = snapshot
    destination = tmp_path / "existing.sqlite"
    destination.write_bytes(b"existing local database")
    restore_database(snapshot_dir=snapshot_dir, destination=destination, force=True)
    assert destination.read_bytes() == database


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_forced_restore_rejects_database_sidecars(snapshot, tmp_path, suffix):
    snapshot_dir, _, _, _ = snapshot
    destination = tmp_path / "existing.sqlite"
    destination.write_bytes(b"existing local database")
    Path(str(destination) + suffix).touch()
    with pytest.raises((ValueError, OSError)):
        restore_database(snapshot_dir=snapshot_dir, destination=destination, force=True)
    assert destination.read_bytes() == b"existing local database"


def test_restores_when_filesystem_does_not_support_hard_links(snapshot, tmp_path, monkeypatch):
    snapshot_dir, database, _, _ = snapshot
    destination = tmp_path / "restored.sqlite"

    def unsupported_link(source, target):
        raise OSError(errno.ENOTSUP, "Hard links are not supported")

    monkeypatch.setattr(restore_module.os, "link", unsupported_link)
    assert restore_database(snapshot_dir=snapshot_dir, destination=destination) == destination.resolve()
    assert destination.read_bytes() == database
    assert not (tmp_path / ".restored.sqlite.restore.lock").exists()
    assert not list(tmp_path.glob(".restored.sqlite.*.tmp"))


def test_fallback_preserves_database_created_during_publication(snapshot, tmp_path, monkeypatch):
    snapshot_dir, _, _, _ = snapshot
    destination = tmp_path / "restored.sqlite"

    def concurrent_creation(source, target):
        Path(target).write_bytes(b"database created by another process")
        raise OSError(errno.ENOTSUP, "Hard links are not supported")

    monkeypatch.setattr(restore_module.os, "link", concurrent_creation)
    restore_database(snapshot_dir=snapshot_dir, destination=destination)
    assert destination.read_bytes() == b"database created by another process"
    assert not (tmp_path / ".restored.sqlite.restore.lock").exists()
    assert not list(tmp_path.glob(".restored.sqlite.*.tmp"))


def test_existing_publication_lock_is_preserved(snapshot, tmp_path):
    snapshot_dir, _, _, _ = snapshot
    destination = tmp_path / "restored.sqlite"
    lock = tmp_path / ".restored.sqlite.restore.lock"
    lock.write_bytes(b"another process owns this lock")
    with pytest.raises(ValueError, match="Another restore"):
        restore_database(snapshot_dir=snapshot_dir, destination=destination)
    assert lock.read_bytes() == b"another process owns this lock"
    assert not destination.exists()
    assert not list(tmp_path.glob(".restored.sqlite.*.tmp"))
