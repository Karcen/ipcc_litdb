#!/usr/bin/env python3
"""Restore the verified gzip snapshot without overwriting a working database."""
from __future__ import annotations

import argparse
import errno
import gzip
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHUNK_SIZE = 1024 * 1024


def _entry(manifest: dict, name: str) -> dict:
    entry = manifest.get(name)
    if not isinstance(entry, dict):
        raise ValueError(f"Missing {name} snapshot metadata")
    filename = entry.get("file")
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        raise ValueError(f"Invalid {name} snapshot filename")
    if filename in (".", ".."):
        raise ValueError(f"Invalid {name} snapshot filename")
    if not isinstance(entry.get("bytes"), int) or entry["bytes"] <= 0:
        raise ValueError(f"Invalid {name} snapshot size")
    if not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
        raise ValueError(f"Invalid {name} snapshot SHA-256")
    return entry


def verify_compressed_snapshot(snapshot_dir: Path) -> dict:
    """Validate the manifest and compressed file; return the manifest."""
    snapshot_dir = Path(snapshot_dir)
    manifest = json.loads((snapshot_dir / "snapshot.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Snapshot metadata must be a JSON object")
    compressed = _entry(manifest, "compressed")
    _entry(manifest, "uncompressed")
    digest = hashlib.sha256()
    size = 0
    with (snapshot_dir / compressed["file"]).open("rb") as source:
        for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
            size += len(chunk)
            digest.update(chunk)
    if size != compressed["bytes"] or digest.hexdigest() != compressed["sha256"]:
        raise ValueError("Compressed snapshot size or SHA-256 does not match snapshot.json")
    return manifest


def restore_database(snapshot_dir: Path = REPO / "database", destination: Path | None = None,
                     force: bool = False) -> Path:
    """Restore atomically, checking both digests and SQLite integrity first.

    Existing destinations are returned unchanged unless force=True. A failed
    restore leaves the existing file intact and removes its temporary file.
    """
    snapshot_dir = Path(snapshot_dir).resolve()
    destination = Path(destination or snapshot_dir / "ipcc_litdb.sqlite").resolve()
    if destination.exists() and not destination.is_file():
        raise ValueError("Database destination must be a file")
    if destination.exists() and not force:
        return destination
    if force and any(Path(str(destination) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("Stop the database server and close SQLite connections before using --force")

    manifest = verify_compressed_snapshot(snapshot_dir)
    compressed = manifest["compressed"]
    uncompressed = manifest["uncompressed"]
    if destination == snapshot_dir / compressed["file"] or destination == snapshot_dir / "snapshot.json":
        raise ValueError("The destination cannot replace a packaged snapshot file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp",
                                                dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(descriptor, "wb") as output:
            with gzip.open(snapshot_dir / compressed["file"], "rb") as source:
                for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
                    size += len(chunk)
                    if size > uncompressed["bytes"]:
                        raise ValueError("Uncompressed snapshot exceeds the size in snapshot.json")
                    digest.update(chunk)
                    output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if size != uncompressed["bytes"] or digest.hexdigest() != uncompressed["sha256"]:
            raise ValueError("Uncompressed snapshot size or SHA-256 does not match snapshot.json")
        with closing(sqlite3.connect(temporary.as_uri() + "?mode=ro", uri=True)) as connection:
            result = connection.execute("PRAGMA quick_check").fetchall()
            if result != [("ok",)]:
                raise ValueError("Restored SQLite database failed its integrity check")
        # Serialize publication by restore processes on filesystems such as
        # exFAT that do not support hard links.
        lock = destination.parent / f".{destination.name}.restore.lock"
        try:
            lock_descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as error:
            raise ValueError("Another restore is publishing this database") from error
        os.close(lock_descriptor)
        try:
            if force:
                if any(Path(str(destination) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
                    raise ValueError("Stop the database server and close SQLite connections before using --force")
                os.replace(temporary, destination)
            else:
                # Hard links provide atomic no-overwrite publication where
                # available; the locked replacement also supports exFAT.
                try:
                    os.link(temporary, destination)
                except FileExistsError:
                    pass
                except OSError as error:
                    if error.errno not in (errno.ENOTSUP, errno.EOPNOTSUPP, errno.EPERM, errno.ENOSYS):
                        raise
                    if not destination.exists():
                        os.replace(temporary, destination)
        finally:
            lock.unlink(missing_ok=True)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", type=Path, default=REPO / "database")
    parser.add_argument("--destination", type=Path, help="Working SQLite file; defaults to database/ipcc_litdb.sqlite")
    parser.add_argument("--force", action="store_true",
                        help="Replace the working database with the snapshot. Stop the server first; local edits will be lost.")
    args = parser.parse_args()
    destination = args.destination or args.snapshot_dir / "ipcc_litdb.sqlite"
    retained = destination.exists() and not args.force
    try:
        path = restore_database(args.snapshot_dir, args.destination, args.force)
    except (OSError, ValueError, sqlite3.Error, EOFError) as error:
        print(f"Restore failed: {error}", file=sys.stderr)
        return 1
    print(f"{'Kept existing database' if retained else 'Restored database'}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
