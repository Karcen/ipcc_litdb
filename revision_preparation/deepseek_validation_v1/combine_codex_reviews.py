"""Join hash-bound blinded review shards without changing any judgement."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from validate import validate_response

HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_fixed(path, value):
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
    if path.exists() and path.read_bytes() != raw:
        raise ValueError('Refuse to replace frozen Codex output: ' + str(path))
    path.write_bytes(raw)


def combine(work):
    root = work / 'codex_comparison_v1'
    manifest_path = root / 'sample_preparation_manifest.json'
    manifest = json.loads(manifest_path.read_text())
    protocol = root / 'protocol.json'
    schema_path = root / 'inputs/response_schema.json'
    schema = json.loads(schema_path.read_text())
    cases, source_reports = [], []
    seen = set()
    for shard in manifest['shards']:
        packet_path = (work / shard['path']).resolve()
        if not packet_path.is_relative_to(work.resolve()) or sha(packet_path) != shard['sha256']:
            raise ValueError('Frozen review packet changed')
        prefix = packet_path.stem
        response = root / (prefix + '_response.json')
        report_path = root / (prefix + '_reviewer_report.json')
        report = json.loads(report_path.read_text())
        required = {'status': 'complete', 'deepseek_outputs_visible_to_reviewer': False,
                    'human_adjudication': False, 'packet_sha256': sha(packet_path),
                    'response_sha256': sha(response), 'protocol_sha256': sha(protocol),
                    'schema_sha256': sha(schema_path)}
        if any(report.get(k) != v for k, v in required.items()):
            raise ValueError('Incomplete or changed blinded reviewer provenance: ' + prefix)
        packet = json.loads(packet_path.read_text())
        result = json.loads(response.read_text())
        validate_response(result, packet, schema)
        for case in result['cases']:
            if case['case_id'] in seen:
                raise ValueError('Repeated case across blinded review shards')
            seen.add(case['case_id']); cases.append(case)
        source_reports.append({'packet': shard['path'], 'packet_sha256': sha(packet_path),
                               'response': response.relative_to(work).as_posix(), 'response_sha256': sha(response),
                               'reviewer_report': report_path.relative_to(work).as_posix(),
                               'reviewer_report_sha256': sha(report_path)})
    packet_path = root / 'inputs/sample_blind_331.json'
    packet = json.loads(packet_path.read_text())
    result = {'cases': cases}
    validate_response(result, packet, schema)
    if len(cases) != manifest['cases'] or sum(len(c['records']) for c in cases) != manifest['records']:
        raise ValueError('Combined case or record count differs from frozen selection')
    response_path = root / 'sample_codex_response.json'
    write_fixed(response_path, result)
    report = {'status': 'complete', 'reviewer': 'Codex session model',
              'deepseek_outputs_visible_to_reviewer': False, 'human_adjudication': False,
              'packet_sha256': sha(packet_path), 'response_sha256': sha(response_path),
              'protocol_sha256': sha(protocol), 'schema_sha256': sha(schema_path),
              'case_count': len(cases), 'record_count': manifest['records'],
              'review_method': 'Direct blinded model review in disjoint shards; no automatic relabelling or inter-reviewer adjudication',
              'generation_seed_available': False, 'external_evidence_retrieved': False,
              'selection_manifest_sha256': sha(manifest_path), 'source_reviews': source_reports,
              'combiner_sha256': sha(Path(__file__))}
    write_fixed(root / 'sample_reviewer_report.json', report)
    return {'status': 'complete', 'cases': len(cases), 'records': manifest['records'],
            'response_sha256': sha(response_path)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--work-dir', type=Path, default=HERE / 'work')
    args = parser.parse_args()
    print(json.dumps(combine(args.work_dir.resolve()), indent=2))
