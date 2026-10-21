#!/usr/bin/env python3
"""Restore compressed large round-two audit tables without changing their bytes.

Run before round-two replay in a clean checkout. --check verifies archives only.
Existing files are accepted only when their size and SHA-256 match the manifest.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REGISTRY = REPO / 'revision_preparation/review_round2/large_table_archives.json'

def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    manifest = json.loads(REGISTRY.read_text())
    checked = []
    for entry in manifest['files']:
        relative = Path(entry['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe table path in archive manifest')
        destination = REPO / relative
        archive = REPO / entry['archive']
        compressed = archive.read_bytes()
        if sha(compressed) != entry['archive_sha256']:
            raise ValueError(f'Compressed hash mismatch: {entry["archive"]}')
        raw = gzip.decompress(compressed)
        if len(raw) != entry['bytes'] or sha(raw) != entry['sha256']:
            raise ValueError(f'Table hash mismatch: {entry["path"]}')
        if destination.exists():
            existing = destination.read_bytes()
            if existing != raw:
                raise ValueError(f'Refusing to overwrite a different table: {relative}')
        elif not args.check:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        checked.append(str(relative))
    print(json.dumps({'archives_verified': len(checked), 'check_only': args.check,
                      'tables': checked}, indent=2))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
