#!/usr/bin/env python3
"""Extract frozen annotation-source pages offline, without labels or judgments.

The repository supplies its read-only database and frozen 801-entry sample.
Pass a separate source workspace containing input/raw and optional output/papers
and output/state/enrich_doi.json. No source files are downloaded or modified.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess

EXPECTED_DB = 'a887a535947ae3b9bab7656070cc038eb3a7a5161494afcb2c3df2245f5ff0ec'


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def extract_text(args):
    key, path, first, last = args
    command = [PDFTOTEXT, '-f', str(first + 1), '-l', str(last + 1),
               '-enc', 'UTF-8', str(path), '-']
    result = subprocess.run(command, capture_output=True, timeout=90)
    return key, {
        'text': result.stdout.decode('utf-8', errors='replace'),
        'command': command,
        'exit_code': result.returncode,
        'stderr': result.stderr.decode('utf-8', errors='replace'),
        'page_index_start_zero_based': first,
        'page_index_end_zero_based': last,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path,
                        default=Path(__file__).resolve().parents[1],
                        help='Repository containing database/ and frozen annotation inputs')
    parser.add_argument('--workspace', type=Path,
                        required=True,
                        help='Local source workspace containing input/raw; optional output/papers and output/state')
    parser.add_argument('--output', type=Path, required=True,
                        help='Output JSON file for source-page evidence, never annotation labels')
    parser.add_argument('--workers', type=int, default=6)
    opts = parser.parse_args()
    global PDFTOTEXT
    PDFTOTEXT = shutil.which('pdftotext')
    if not PDFTOTEXT:
        raise SystemExit('pdftotext is required.')
    if opts.workers < 1:
        parser.error('--workers must be a positive integer')
    root = opts.workspace.resolve()
    repo = opts.repo.resolve()
    db = repo / 'database/ipcc_litdb.sqlite'
    entries_path = repo / ('revision_preparation/annotation/'
                          'inputs/revision_identity_audit_sample_entries.csv')
    if not db.is_file():
        raise SystemExit(f'Frozen snapshot is missing: {db}')
    if not entries_path.is_file():
        raise SystemExit(f'Frozen annotation sample is missing: {entries_path}')
    db_sha_before = sha256(db)
    if db_sha_before != EXPECTED_DB:
        raise SystemExit('Snapshot hash is not the immutable corrected v1.2.0 snapshot.')
    entries_sha_before = sha256(entries_path)
    rows = list(csv.DictReader(entries_path.open(encoding='utf-8', newline='')))
    if len(rows) != 801 or len({r['paper_id'] for r in rows}) != 331:
        raise SystemExit('Frozen sample size differs from expected 801 entries and 331 IDs.')
    with closing(sqlite3.connect(db.as_uri()+'?mode=ro&immutable=1', uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        source_db = {r['source_id']: dict(r)
                     for r in connection.execute('SELECT * FROM sources')}
    sources = {}
    page_keys = {}
    tasks = []
    for source_id in sorted({r['source_id'] for r in rows}):
        source = source_db[source_id]
        relative = source['local_path'].removeprefix('upstream/')
        path = (root / relative).resolve()
        if not path.is_file():
            raise SystemExit(f'Required local IPCC source PDF is missing: {source_id}: {path}')
        observed_sha = sha256(path)
        if observed_sha != source['sha256']:
            raise SystemExit(f'Source hash mismatch: {source_id}')
        sources[source_id] = {
            'recorded_local_path': source['local_path'],
            'resolved_local_path': str(path),
            'sha256': observed_sha,
            'recorded_page_count': source['page_count'],
            'file_url': source['file_url'],
            'retrieved_date': source['retrieved_date'],
            'publication_year': source['publication_year'],
            'source_metadata': 'read-only SQLite sources table',
        }
        for page in sorted({int(r['source_page']) for r in rows
                            if r['source_id'] == source_id}):
            if not 0 <= page < source['page_count']:
                raise SystemExit(f'Page out of bounds: {source_id}:{page}')
            key = f'{source_id}::page_index_{page}'
            page_keys[(source_id, page)] = key
            tasks.append((key, path, page, page))
    with ThreadPoolExecutor(max_workers=opts.workers) as pool:
        pages = dict(pool.map(extract_text, tasks))
    for (source_id, page), key in page_keys.items():
        pages[key].update({
            'source_id': source_id,
            'viewer_page_one_based': page + 1,
            'neighbor_page_indices_available': [p for p in [page - 1, page + 1]
                                                if 0 <= p < sources[source_id]['recorded_page_count']],
            'neighbor_text_included': False,
        })

    pdf_paths = [p for p in (root / 'output/papers').rglob('*.pdf')
                 if not p.name.startswith('._')]
    by_filename = {}
    for path in pdf_paths:
        by_filename.setdefault(path.name.lower(), []).append(path)
    sampled_dois = sorted({r['paper_doi'].lower() for r in rows if r['paper_doi']})
    cited_documents = {}
    cited_tasks = []
    for doi in sampled_dois:
        filename = doi.replace('/', '_') + '.pdf'
        for path in sorted(by_filename.get(filename, [])):
            key = str(path.relative_to(root))
            cited_documents[key] = {
                'filename_doi_candidate': doi,
                'resolved_local_path': str(path),
                'sha256': sha256(path),
                'mapping_rule': 'preferred DOI lowercased; slash replaced with underscore; .pdf suffix',
                'identity_verified': False,
                'provenance': 'Existing local cited-work PDF; filename mapping is a lookup aid, not validation.',
            }
            cited_tasks.append((key, path, 0, 1))
    with ThreadPoolExecutor(max_workers=opts.workers) as pool:
        cited_texts = dict(pool.map(extract_text, cited_tasks))
    for key in cited_documents:
        cited_documents[key]['first_two_pages'] = cited_texts[key]
    cited_by_doi = {}
    for key, doc in cited_documents.items():
        cited_by_doi.setdefault(doc['filename_doi_candidate'], []).append(key)

    cache_path = root / 'output/state/enrich_doi.json'
    cache_available = cache_path.is_file()
    cache_error = None
    cache = {}
    if cache_available:
        try:
            cache = json.loads(cache_path.read_text())
            if not isinstance(cache, dict):
                cache_error = 'Optional pipeline cache is not a JSON object.'
                cache = {}
        except (OSError, ValueError) as exc:
            cache_error = f'Optional pipeline cache could not be read: {exc}'
    cache_matches = {doi: cache[doi] for doi in sampled_dois if doi in cache}
    entries = []
    for row in rows:
        page = int(row['source_page'])
        entries.append({
            'occurrence_id': row['occurrence_id'],
            'paper_id': row['paper_id'],
            'selection': row['selection'],
            'sample_stratum': row['sample_stratum'],
            'report_id': row['report_id'],
            'wg_label': row['wg_label'],
            'chapter_id': row['chapter_id'],
            'source_id': row['source_id'],
            'source_page_zero_based': page,
            'viewer_page_one_based': page + 1,
            'page_evidence_key': page_keys[(row['source_id'], page)],
            'raw_reference': row['raw_reference'],
            'preferred_doi_for_lookup_only': row['paper_doi'],
            'local_cited_document_keys': cited_by_doi.get(row['paper_doi'].lower(), []),
        })
    db_sha_after = sha256(db)
    if db_sha_before != db_sha_after:
        raise SystemExit('Frozen snapshot changed during offline source extraction.')
    if entries_sha_before != sha256(entries_path):
        raise SystemExit('Frozen annotation sample changed during offline source extraction.')
    payload = {
        'schema_version': 'offline-source-evidence-1.0.0',
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'scope': 'Evidence extraction only. No annotation labels or identity judgments.',
        'frozen_inputs': {
            'database_path': str(db), 'database_sha256': db_sha_before,
            'database_sha256_before': db_sha_before, 'database_sha256_after': db_sha_after,
            'database_read_only': True, 'database_immutable': True,
            'sample_entries_path': str(entries_path), 'sample_entries_sha256': entries_sha_before,
            'script_path': str(Path(__file__).resolve()), 'script_sha256': sha256(Path(__file__)),
        },
        'page_indexing_provenance': {
            'file': str(root / 'ipccdb/refextract.py'), 'line': 65,
            'extractor_source_available': (root/'ipccdb/refextract.py').is_file(),
            'definition': 'source_page: int # 0-based page inside the source PDF',
        },
        'counts': {
            'entries': len(entries), 'paper_ids': len({r['paper_id'] for r in entries}),
            'ipcc_source_pdfs': len(sources), 'primary_source_pages': len(pages),
            'sample_preferred_dois': len(sampled_dois),
            'filename_mapped_cited_pdfs': len(cited_documents),
            'cached_doi_keys': len(cache_matches),
            'nonempty_cached_doi_metadata': sum(bool(v) for v in cache_matches.values()),
            'page_extraction_failures': sum(v['exit_code'] != 0 for v in pages.values()),
            'cited_pdf_extraction_failures': sum(v['first_two_pages']['exit_code'] != 0
                                                  for v in cited_documents.values()),
        },
        'sources': sources,
        'pages': pages,
        'entries': entries,
        'cited_documents': cited_documents,
        'cached_pipeline_metadata': {
            'path': str(cache_path),
            'available': cache_available,
            'status': 'unreadable_optional_cache' if cache_error else (
                'available' if cache_available else 'missing_optional_cache'),
            'read_error': cache_error,
            'sha256': sha256(cache_path) if cache_available else None,
            'provenance_warning': 'Existing pipeline enrichment cache. This is not an independent validation or a new API response.',
            'records': cache_matches,
        },
    }
    opts.output.parent.mkdir(parents=True, exist_ok=True)
    opts.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'output': str(opts.output), 'bytes': opts.output.stat().st_size,
                      'sha256': sha256(opts.output), 'counts': payload['counts']}, indent=2))
    if payload['counts']['page_extraction_failures']:
        raise SystemExit('Primary source extraction failures are recorded in output.')


if __name__ == '__main__':
    main()
