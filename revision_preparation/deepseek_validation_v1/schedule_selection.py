"""Validate an explicitly selected frozen schedule without changing default plans."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def within(work, relative, *, required=True):
    path = Path(relative)
    if not path.is_absolute():
        path = work / path
    path = path.resolve()
    if not path.is_relative_to(work):
        raise ValueError('Selected schedule provenance path escapes work directory')
    if required and not path.is_file():
        raise ValueError('Selected schedule provenance file is missing')
    return path


def select_schedule_path(work_dir, stage, supplied=None):
    """Return the unchanged default, or verify every supplied schedule dependency.

    Explicit relative paths are resolved against work_dir. Only schedules inside
    work_dir/schedules are accepted. No file is created or modified here.
    """
    if stage not in ('pilot', 'full', 'candidates'):
        raise ValueError('Unknown schedule stage')
    work = Path(work_dir).resolve()
    if supplied is None:
        return work / 'schedules' / (stage + '.json')
    schedule_path = within(work, supplied)
    if not schedule_path.is_relative_to((work / 'schedules').resolve()):
        raise ValueError('Explicit schedule must be inside work/schedules')
    schedule = json.loads(schedule_path.read_text())
    if schedule.get('stage') != stage:
        raise ValueError('Explicit schedule stage differs from requested stage')
    if schedule.get('input_manifest_sha256') != digest(work / 'input_manifest.json'):
        raise ValueError('Explicit schedule input-manifest hash differs')
    jobs = schedule.get('jobs')
    if not isinstance(jobs, list) or not jobs:
        raise ValueError('Explicit schedule has no jobs')
    job_ids = [job['job_id'] for job in jobs]
    if len(job_ids) != len(set(job_ids)):
        raise ValueError('Explicit schedule contains duplicate job IDs')
    packets = {}
    for job in jobs:
        path = within(work, job['packet_path'])
        relative = path.relative_to(work).as_posix()
        actual = digest(path)
        if actual != job.get('packet_sha256'):
            raise ValueError('Explicit schedule packet hash differs')
        if relative in packets and packets[relative] != actual:
            raise ValueError('Explicit schedule repeats a packet with inconsistent hashes')
        packets[relative] = actual
    path_key = 'derived_provenance_manifest_path'
    hash_key = 'derived_provenance_manifest_sha256'
    if (path_key in schedule) != (hash_key in schedule):
        raise ValueError('Derived provenance requires both path and hash')
    if path_key in schedule:
        provenance_path = within(work, schedule[path_key])
        if digest(provenance_path) != schedule[hash_key]:
            raise ValueError('Derived provenance manifest hash differs')
        provenance = json.loads(provenance_path.read_text())
        declared = provenance.get('packet_sha256', provenance.get('output_sha256'))
        if not isinstance(declared, dict) or declared != packets:
            raise ValueError('Derived provenance packet hashes differ from scheduled packets')
        if 'packet_sha256' in provenance and 'output_sha256' in provenance and provenance['packet_sha256'] != provenance['output_sha256']:
            raise ValueError('Derived provenance packet mappings disagree')
        sources = provenance.get('source_input_sha256')
        if not isinstance(sources, dict) or not sources:
            raise ValueError('Derived provenance has no source input hashes')
        for relative, expected in sources.items():
            if digest(within(work, relative)) != expected:
                raise ValueError('Derived provenance source input hash differs')
        canonical = provenance.get('canonical_candidate_input_sha256')
        candidate_key = 'inputs/candidates/blind_candidates.json'
        if stage == 'candidates' and (not canonical or sources.get(candidate_key) != canonical):
            raise ValueError('Derived candidate provenance lacks its canonical input digest')
    return schedule_path
