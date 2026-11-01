"""Publish completed CSV/JSON registries losslessly; raw replay remains separate."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile

from restore_source_inputs import _check_bytes, _safe_path, _write_unchanged

HERE = Path(__file__).resolve().parent
DETAIL_NAMES = {'case_consensus', 'record_consensus', 'call_usage', 'call_status', 'parser_disagreements',
                'metadata_arm_comparison', 'sample_id_outcomes', 'paired_id_outcomes', 'paired_divergences',
                'candidate_pair_geometry'}
DETAIL_SUFFIXES = ('_detail', '_derived', '_exclusions', '_changes', '_flags')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def compressed(data):
    buffer = io.BytesIO()
    with gzip.GzipFile(filename='', mode='wb', fileobj=buffer, mtime=0, compresslevel=9) as stream:
        stream.write(data)
    return buffer.getvalue()


def should_compress(relative, data):
    path = Path(relative)
    if path.stem in {'candidate_label_totals', 'candidate_resolution_totals'} and len(data) <= 700 * 1024:
        return False
    if path.suffix == '.json':
        return len(data) > 512 * 1024
    return (len(data) > 64 * 1024 or path.stem in DETAIL_NAMES
            or path.stem.endswith(DETAIL_SUFFIXES))


def latest_publication(directory):
    directory = Path(directory)
    paths = [directory / 'publication_manifest.json'] if (directory / 'publication_manifest.json').is_file() else []
    paths += sorted(directory.glob('publication_manifest_v[0-9][0-9][0-9].json'))
    return paths[-1] if paths else None


def _read_stored(directory, item):
    path = _safe_path(directory, item['stored_path'])
    data = path.read_bytes()
    _check_bytes(data, {'bytes': item['stored_bytes'], 'sha256': item['stored_sha256']}, item['stored_path'])
    original = gzip.decompress(data) if item['encoding'] == 'gzip' else data
    _check_bytes(original, {'bytes': item['original_bytes'], 'sha256': item['original_sha256']}, item['source_path'])
    return original


def publish_report(source, destination, *, allow_additions=False):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    top_path = source / 'manifest.json'
    spending_audit = not top_path.exists() and (source / 'spending_audit.json').is_file()
    if spending_audit:
        top_path = source / 'spending_audit.json'
    top = json.loads(top_path.read_text())
    if top.get('status') not in ('complete', 'complete_paired_model_comparison') and not (
        spending_audit and top.get('status') == 'passed' and top.get('invoice_claimed') is False
    ):
        raise ValueError('Only completed result scopes may be published')
    if spending_audit and sha((source / 'paid_attempts.csv').read_bytes()) != top.get('paid_attempts_sha256'):
        raise ValueError('Completed spending audit table hash differs')
    if top.get('status') == 'complete' and top.get('all_scheduled_calls_complete') is not True:
        raise ValueError('Aggregate calls are not complete')
    if (source / 'summary.json').is_file():
        summary = json.loads((source / 'summary.json').read_text())
        if summary.get('status') != 'complete' or summary.get('incomplete_call_ids'):
            raise ValueError('Summary declares an incomplete scope')
    originals = {}
    for path in sorted(source.rglob('*')):
        if path.name.startswith('._') or path.suffix not in ('.csv', '.json'):
            continue
        relative = path.relative_to(source).as_posix()
        _safe_path(source, relative)
        if path.is_symlink() or not path.is_file():
            raise ValueError('Report inputs must be regular files')
        if relative.startswith('publication_manifest'):
            raise ValueError('Publication manifests cannot be source result files')
        data = path.read_bytes()
        if path.suffix == '.json' and re.search(rb'/(?:Users|Volumes)/[^/\s]+/', data):
            raise ValueError('Source JSON contains a machine-specific private path: ' + relative)
        originals[relative] = data
    for relative, data in originals.items():
        if Path(relative).suffix != '.json':
            continue
        record = json.loads(data)
        if isinstance(record, dict) and isinstance(record.get('output_sha256'), dict):
            for name, expected in record['output_sha256'].items():
                path = _safe_path(source / Path(relative).parent, name)
                if not path.is_file() or sha(path.read_bytes()) != expected:
                    raise ValueError('Completed source output hash differs: ' + name)
    previous_path = latest_publication(destination)
    previous = json.loads(previous_path.read_text()) if previous_path else None
    if previous:
        old = {item['source_path']: item for item in previous['files']}
        for relative, item in old.items():
            original = _read_stored(destination, item)
            if Path(relative).name == 'manifest.json':
                alias = relative + ('.gz' if item['encoding'] == 'gzip' else '')
                alias_path = _safe_path(destination, alias)
                if not alias_path.is_file() or sha(alias_path.read_bytes()) != item['stored_sha256']:
                    raise ValueError('Published source-manifest alias differs: ' + alias)
            if relative not in originals:
                raise ValueError('An existing published source file was removed')
            if originals[relative] == original:
                continue
            if not allow_additions or Path(relative).name != 'manifest.json':
                raise ValueError('Existing published result bytes differ: ' + relative)
            before, after = json.loads(original), json.loads(originals[relative])
            prior_outputs = before.get('output_sha256')
            current_outputs = after.get('output_sha256')
            if not isinstance(prior_outputs, dict) or not isinstance(current_outputs, dict) or any(
                current_outputs.get(name) != digest for name, digest in prior_outputs.items()
            ):
                raise ValueError('A manifest revision changes earlier output hashes')
        unchanged = set(old) == set(originals) and all(sha(originals[name]) == item['original_sha256'] for name, item in old.items())
        if unchanged:
            return previous_path
        if not allow_additions or not set(old) < set(originals):
            raise ValueError('Refreshing publication requires a validated strict addition of source files')
    files = []
    aliases = []
    for relative, data in originals.items():
        use_gzip = should_compress(relative, data)
        suffix = '.gz' if use_gzip else ''
        encoded = compressed(data) if use_gzip else data
        if Path(relative).name == 'manifest.json':
            stored = 'provenance/source_manifests/' + sha(data) + '/' + Path(relative).name + suffix
            aliases.append((relative + suffix, data, encoded))
        else:
            stored = relative + suffix
        _write_unchanged(destination, stored, encoded)
        files.append({'source_path': relative, 'original_bytes': len(data), 'original_sha256': sha(data),
                      'stored_path': stored, 'stored_bytes': len(encoded), 'stored_sha256': sha(encoded),
                      'encoding': 'gzip' if use_gzip else 'identity'})
    # Source-manifest aliases are convenience views. Immutable version entries
    # always point to content-addressed source manifests, preserving old bytes.
    for alias, original, encoded in aliases:
        target = _safe_path(destination, alias)
        if target.exists() and target.read_bytes() != encoded:
            if not previous or not allow_additions:
                raise ValueError('Conflicting source-manifest alias')
            descriptor, temporary = tempfile.mkstemp(prefix='.source_manifest.', dir=target.parent)
            try:
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(encoded)
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        else:
            _write_unchanged(destination, alias, encoded)
    revision = previous['publication_revision'] + 1 if previous else 1
    manifest = {'analysis_package': '1.5.0', 'scope': source.name, 'publication_revision': revision,
                'status': 'complete_output_registry_raw_replay_pending',
                'source_status': top['status'], 'files': files,
                'source_artifact_count': len(files), 'source_manifests_preserved_exactly': True,
                'previous_publication_manifest_sha256': sha(previous_path.read_bytes()) if previous_path else None,
                'raw_input_replay_status': 'pending_separate_frozen_archive',
                'interpretation': ('Published-rate accounting audit, not a provider invoice.' if spending_audit else
                                   'Saved model outputs and model-conditioned diagnostics; no human gold labels or calibrated population accuracy.'),
                'network_requests': 0, 'model_calls_made': 0}
    name = 'publication_manifest.json' if revision == 1 else f'publication_manifest_v{revision:03d}.json'
    _write_unchanged(destination, name, (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode())
    return destination / name


def restore_report(registry, destination, *, publication_manifest=None):
    registry, destination = Path(registry).resolve(), Path(destination).resolve()
    path = Path(publication_manifest) if publication_manifest else latest_publication(registry)
    if path is None:
        raise ValueError('No publication manifest exists')
    if not path.is_absolute():
        path = _safe_path(registry, path.as_posix())
    manifest = json.loads(path.read_text())
    for item in manifest['files']:
        _write_unchanged(destination, item['source_path'], _read_stored(registry, item))
    return {'restored_files': len(manifest['files']), 'network_requests': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    publish = commands.add_parser('publish')
    publish.add_argument('--source', type=Path, required=True)
    publish.add_argument('--destination', type=Path, required=True)
    publish.add_argument('--allow-additions', action='store_true')
    replay = commands.add_parser('restore')
    replay.add_argument('--registry', type=Path, required=True)
    replay.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'publish':
        path = publish_report(args.source, args.destination, allow_additions=args.allow_additions)
        record = json.loads(path.read_text())
        print(json.dumps({'scope': record['scope'], 'artifacts': record['source_artifact_count'],
                          'publication_manifest': path.name, 'raw_input_replay_status': record['raw_input_replay_status']}))
    else:
        print(json.dumps(restore_report(args.registry, args.destination)))


if __name__ == '__main__':
    main()
