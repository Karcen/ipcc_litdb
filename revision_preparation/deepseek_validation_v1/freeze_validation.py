"""Freeze or restore completed validation files in deterministic small chunks."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

from restore_source_inputs import _check_bytes, _relative, _safe_path, _write_unchanged

HERE = Path(__file__).resolve().parent
CHUNK_BYTES = 640 * 1024
PUBLIC_FILE_BYTES = 700 * 1024
LOGICAL_BYTES = 8 * 1024 * 1024
EXCLUDED_DIRECTORIES = {'.git', '.venv', 'analysis_venv', '__pycache__', '.pytest_cache',
                        'source_panel', 'recovery', 'integration_backups', 'paper', 'manuscript'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def excluded(relative):
    path = _relative(relative)
    if any(part in EXCLUDED_DIRECTORIES or part.startswith('._') or 'credential' in part.casefold()
           or 'secret' in part.casefold() for part in path.parts):
        return True
    name = path.name.casefold()
    return (name in {'.active_runner.lock', '.env', 'auth.json', 'account_balance.json', 'balance_response.json'}
            or name.startswith(('.env.', 'manuscript_source_guard'))
            or name.endswith(('.lock', '.tmp', '.temp', '.pem', '.key'))
            or 'credential' in name or 'secret' in name)


def _json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')


def _store_manifest(manifest, destination, *, manifest_format='auto'):
    """Keep small legacy manifests, or write a bounded nonrecursive v2 index."""
    if manifest_format in ('1', '2'):
        manifest_format = int(manifest_format)
    if type(manifest_format) is bool or manifest_format not in ('auto', 1, 2):
        raise ValueError('Manifest format must be auto, 1, or 2')
    destination = Path(destination).resolve()
    payload = dict(manifest)
    legacy = _json_bytes(payload)
    if manifest_format != 2 and len(legacy) <= PUBLIC_FILE_BYTES:
        _write_unchanged(destination, 'manifest.json', legacy)
        return payload
    if manifest_format == 1:
        raise ValueError('Format 1 manifest exceeds the 700 KiB publication limit')
    payload['format_version'] = 2
    raw_payload = _json_bytes(payload)
    buffer = io.BytesIO()
    with gzip.GzipFile(filename='', mode='wb', fileobj=buffer, compresslevel=9, mtime=0) as stream:
        stream.write(raw_payload)
    encoded = buffer.getvalue()
    chunks = []
    for number, start in enumerate(range(0, len(encoded), CHUNK_BYTES), 1):
        part = encoded[start:start + CHUNK_BYTES]
        chunks.append({'path': f'manifest_payload.part_{number:04d}.bin',
                       'bytes': len(part), 'sha256': sha(part)})
    index = {'format_version': 2, 'analysis_package': payload['analysis_package'],
             'format': 'Small index for a deterministic gzip JSON manifest payload',
             'manifest_payload': {'encoding': 'gzip', 'bytes': len(encoded), 'sha256': sha(encoded),
                                  'uncompressed_bytes': len(raw_payload), 'uncompressed_sha256': sha(raw_payload),
                                  'chunk_bytes_limit': CHUNK_BYTES, 'chunks': chunks},
             'source_file_count': payload['source_file_count'], 'source_bytes': payload['source_bytes'],
             'logical_archive_count': len(payload['archives']),
             'chunk_bytes_limit': payload['chunk_bytes_limit'],
             'original_member_bytes_unchanged': payload['original_member_bytes_unchanged'],
             'network_requests': 0}
    index_bytes = _json_bytes(index)
    if len(index_bytes) > PUBLIC_FILE_BYTES:
        raise ValueError('Manifest payload index exceeds the 700 KiB publication limit; use a smaller archive scope')
    for item, start in zip(chunks, range(0, len(encoded), CHUNK_BYTES)):
        _write_unchanged(destination, item['path'], encoded[start:start + CHUNK_BYTES])
    _write_unchanged(destination, 'manifest.json', index_bytes)
    return payload


def load_manifest(archive_dir):
    """Read format 1 or verify and reassemble a format 2 payload before use."""
    archive_dir = Path(archive_dir).resolve()
    path = _safe_path(archive_dir, 'manifest.json')
    index_bytes = path.read_bytes()
    index = json.loads(index_bytes)
    if not isinstance(index, dict):
        raise ValueError('Manifest index must be a JSON object')
    if index.get('format_version') == 1:
        return index  # Older format 1 archives remain readable, including large ones.
    if index.get('format_version') != 2 or len(index_bytes) > PUBLIC_FILE_BYTES:
        raise ValueError('Unknown manifest format or oversized format 2 index')
    descriptor = index.get('manifest_payload')
    if not isinstance(descriptor, dict) or descriptor.get('encoding') != 'gzip':
        raise ValueError('Format 2 requires one gzip manifest payload')
    if descriptor.get('chunk_bytes_limit') != CHUNK_BYTES:
        raise ValueError('Manifest payload chunk limit differs')
    parts = descriptor.get('chunks')
    if not isinstance(parts, list) or not parts:
        raise ValueError('Manifest payload has no chunks')
    chunks = []
    for number, part in enumerate(parts, 1):
        expected_name = f'manifest_payload.part_{number:04d}.bin'
        if not isinstance(part, dict) or part.get('path') != expected_name:
            raise ValueError('Manifest payload chunk sequence differs; index cycles and external paths are forbidden')
        chunk_path = _safe_path(archive_dir, expected_name)
        if not chunk_path.is_file():
            raise ValueError('Manifest payload chunk is not a regular file')
        data = chunk_path.read_bytes()
        _check_bytes(data, part, expected_name)
        if not data or len(data) > CHUNK_BYTES:
            raise ValueError('Manifest payload chunk exceeds its publication limit')
        chunks.append(data)
    encoded = b''.join(chunks)
    _check_bytes(encoded, descriptor, 'compressed manifest payload')
    length = descriptor.get('uncompressed_bytes')
    if type(length) is not int or length < 0:
        raise ValueError('Invalid uncompressed manifest byte count')
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(encoded), mode='rb') as stream:
            raw_payload = stream.read(length + 1)
    except (OSError, EOFError) as error:
        raise ValueError('Manifest payload is not valid gzip') from error
    _check_bytes(raw_payload, {'bytes': length, 'sha256': descriptor.get('uncompressed_sha256')},
                 'uncompressed manifest payload')
    payload = json.loads(raw_payload)
    if (not isinstance(payload, dict) or payload.get('format_version') != 2 or 'manifest_payload' in payload
            or not isinstance(payload.get('archives'), list) or not isinstance(payload.get('members'), list)):
        raise ValueError('Manifest payload cannot be another index or an unsupported format')
    for name in ('analysis_package', 'source_file_count', 'source_bytes', 'chunk_bytes_limit',
                 'original_member_bytes_unchanged', 'network_requests'):
        if payload.get(name) != index.get(name):
            raise ValueError('Manifest index and payload metadata differ: ' + name)
    if len(payload['archives']) != index.get('logical_archive_count'):
        raise ValueError('Manifest logical archive count differs')
    return payload


def freeze(root, destination, *, include=None, chunk_bytes=CHUNK_BYTES, logical_bytes=LOGICAL_BYTES,
           manifest_format='auto'):
    """Freeze an inactive tree. No live requests, credentials, or network reads."""
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if (root / 'work/.active_runner.lock').exists():
        raise ValueError('Refuse to freeze while a validation runner is active')
    if type(chunk_bytes) is not int or not 1 <= chunk_bytes <= PUBLIC_FILE_BYTES:
        raise ValueError('Chunk size must be a positive integer no greater than 700 KiB')
    if type(logical_bytes) is not int or logical_bytes < 1:
        raise ValueError('Logical archive size must be a positive integer')
    if include is None:
        include = [name for name in ('work', 'results') if (root / name).exists()]
        include += [path.name for path in root.iterdir() if path.is_file() and path.suffix in ('.py', '.json', '.txt')]
    selected, omitted = {}, set()
    for relative in include:
        base = root if relative == '.' else _safe_path(root, relative)
        if not base.exists():
            raise ValueError('Explicit source path is missing: ' + relative)
        candidates = [base] if base.is_file() else sorted(base.rglob('*'))
        for path in candidates:
            relative_path = path.relative_to(root).as_posix()
            if excluded(relative_path):
                omitted.add(relative_path)
                continue
            if path.is_symlink():
                raise ValueError('Symbolic links are not accepted in a validation archive')
            if not path.is_file():
                continue
            _safe_path(root, relative_path)
            if path.resolve().is_relative_to(destination):
                raise ValueError('Archive output overlaps its selected input tree')
            selected[relative_path] = path.stat().st_size
    if not selected:
        raise ValueError('No source files selected')
    groups, pending, size = [], [], 0
    for relative, length in sorted(selected.items()):
        if pending and size + length > logical_bytes:
            groups.append(pending); pending, size = [], 0
        pending.append(relative); size += length
    if pending:
        groups.append(pending)
    archives, members = [], []
    for index, names in enumerate(groups, 1):
        archive_id = f'validation_{index:04d}.tar.gz'
        raw_tar = io.BytesIO()
        with tarfile.open(fileobj=raw_tar, mode='w', format=tarfile.PAX_FORMAT) as archive:
            for relative in names:
                path = _safe_path(root, relative)
                before = path.stat()
                data = path.read_bytes()
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or len(data) != selected[relative]:
                    raise ValueError('Source changed while freezing: ' + relative)
                header = tarfile.TarInfo(relative)
                header.size = len(data); header.mtime = 0; header.mode = 0o644
                header.uid = header.gid = 0; header.uname = header.gname = ''
                archive.addfile(header, io.BytesIO(data))
                members.append({'path': relative, 'archive': archive_id, 'bytes': len(data), 'sha256': sha(data)})
        compressed = io.BytesIO()
        with gzip.GzipFile(filename='', mode='wb', fileobj=compressed, compresslevel=9, mtime=0) as stream:
            stream.write(raw_tar.getvalue())
        data = compressed.getvalue()
        chunks = []
        for number, start in enumerate(range(0, len(data), chunk_bytes), 1):
            part = data[start:start + chunk_bytes]
            name = f'validation_{index:04d}.part_{number:04d}.bin'
            _write_unchanged(destination, name, part)
            chunks.append({'path': name, 'bytes': len(part), 'sha256': sha(part)})
        archives.append({'id': archive_id, 'bytes': len(data), 'sha256': sha(data), 'chunks': chunks})
    manifest = {'format_version': 1, 'analysis_package': '1.5.0',
                'format': 'Deterministic gzip tar logical archives split into exact binary chunks',
                'chunk_bytes_limit': chunk_bytes, 'logical_bytes_target': logical_bytes,
                'archives': archives, 'members': members, 'excluded_paths': sorted(omitted),
                'source_file_count': len(members), 'source_bytes': sum(item['bytes'] for item in members),
                'original_member_bytes_unchanged': True, 'network_requests': 0}
    return _store_manifest(manifest, destination, manifest_format=manifest_format)


def restore(archive_dir, destination):
    """Restore exactly hashed regular members; never overwrite different files."""
    archive_dir, destination = Path(archive_dir).resolve(), Path(destination).resolve()
    manifest = load_manifest(archive_dir)
    expected_all = {}
    for member in manifest['members']:
        _relative(member['path'])
        if member['path'] in expected_all or excluded(member['path']):
            raise ValueError('Duplicate or excluded archived path')
        expected_all[member['path']] = member
    found_all, seen_archives = set(), set()
    for descriptor in manifest['archives']:
        archive_id = descriptor['id']
        if archive_id in seen_archives:
            raise ValueError('Duplicate logical archive ID')
        seen_archives.add(archive_id)
        chunks = []
        for part in descriptor['chunks']:
            path = _safe_path(archive_dir, part['path'])
            if not path.is_file():
                raise ValueError('Archive chunk is not a regular file')
            data = path.read_bytes()
            _check_bytes(data, part, part['path'])
            if len(data) > PUBLIC_FILE_BYTES:
                raise ValueError('Archive chunk exceeds the publication limit')
            chunks.append(data)
        compressed = b''.join(chunks)
        _check_bytes(compressed, descriptor, archive_id)
        expected = {name: item for name, item in expected_all.items() if item['archive'] == archive_id}
        found = set()
        with tarfile.open(fileobj=io.BytesIO(compressed), mode='r:gz') as archive:
            for member in archive:
                if member.name not in expected or member.name in found or not member.isfile():
                    raise ValueError('Unexpected, duplicate, or non-regular archive member')
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError('Cannot read archive member')
                data = stream.read()
                _check_bytes(data, expected[member.name], member.name)
                _write_unchanged(destination, member.name, data)
                found.add(member.name)
        if found != set(expected):
            raise ValueError('Logical archive members are incomplete')
        found_all.update(found)
    if found_all != set(expected_all) or len(found_all) != manifest['source_file_count']:
        raise ValueError('Restored member accounting differs')
    return {'restored_files': len(found_all), 'restored_bytes': sum(item['bytes'] for item in expected_all.values()),
            'network_requests': 0, 'original_member_bytes_unchanged': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    make = commands.add_parser('freeze')
    make.add_argument('--root', type=Path, default=HERE)
    make.add_argument('--destination', type=Path, required=True)
    make.add_argument('--include', action='append')
    make.add_argument('--manifest-format', choices=('auto', '1', '2'), default='auto')
    replay = commands.add_parser('restore')
    replay.add_argument('--archive-dir', type=Path, required=True)
    replay.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'freeze':
        result = freeze(args.root, args.destination, include=args.include, manifest_format=args.manifest_format)
        print(json.dumps({key: result[key] for key in ('format_version', 'source_file_count', 'source_bytes', 'chunk_bytes_limit', 'network_requests')}))
    else:
        print(json.dumps(restore(args.archive_dir, args.destination)))


if __name__ == '__main__':
    main()
