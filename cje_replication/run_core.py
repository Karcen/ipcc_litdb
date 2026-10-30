"""Offline replay of the manuscript's identity sensitivity and standardisation.

Run with Python 3.11+ from any working directory. Inputs are hash-checked and
expanded only under output/. No API keys or internet connections are used.
"""
from pathlib import Path
import argparse
import gzip
import hashlib
import json
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / 'output'
STAGES = [
    ('B_mechanical_linkage.py', ['B_mechanical_linkage.csv']),
    ('C_identity_error_sensitivity.py', [
        'C_frame_verification.csv', 'C_identity_rule_levels.csv',
        'C_break_even.csv', 'C_fuzzy_reliance.csv',
        'C_error_plausibility_scale.csv', 'C_sensitivity.csv']),
    ('D_venue_strata.py', ['D_venue_strata.csv']),
    ('D_decomposition_recheck.py', ['D_decomposition.csv', 'D_leave_one_venue_out.csv']),
]


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def prepare_inputs():
    """Verify and expand the two frozen source inputs, without changing archives."""
    target_dir = OUTPUT / 'inputs'
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ROOT / 'inputs/core_input_manifest.json').read_text())
    for item in manifest['inputs']:
        target = target_dir / item['uncompressed_name']
        if target.exists():
            if sha256(target) != item['sha256']:
                raise RuntimeError(f'Existing decompressed input has the wrong hash: {target}')
            continue
        temp = target.with_suffix(target.suffix + '.part')
        with gzip.open(ROOT / item['archive'], 'rb') as src, temp.open('wb') as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        if sha256(temp) != item['sha256']:
            temp.unlink()
            raise RuntimeError(f'Input archive failed SHA-256 verification: {item["archive"]}')
        temp.replace(target)
    return target_dir


def compare_csv(name):
    import pandas as pd
    actual_path = OUTPUT / 'tables' / name
    expected_path = ROOT / 'reference/tables' / name
    actual = pd.read_csv(actual_path, low_memory=False)
    expected = pd.read_csv(expected_path, low_memory=False)
    try:
        pd.testing.assert_frame_equal(actual, expected, check_dtype=False,
                                      check_exact=False, rtol=1e-10, atol=1e-10)
    except AssertionError as e:
        return {'table': name, 'passed': False, 'reason': str(e)}
    return {'table': name, 'passed': True, 'rows': len(actual),
            'numeric_relative_tolerance': 1e-10, 'numeric_absolute_tolerance': 1e-10}


def run():
    prepare_inputs()
    for sub in ['tables', 'logs', 'cache']:
        (OUTPUT / sub).mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT / 'analysis'))
    import BCD_lib as L
    frame = L.identity_frame(force=True)
    print(f'Rebuilt {len(frame):,} identities from the frozen source inputs.', flush=True)
    comparisons = []
    for script, tables in STAGES:
        print(f'Running {script}', flush=True)
        log = OUTPUT / 'logs' / (Path(script).stem + '.txt')
        with log.open('w') as f:
            proc = subprocess.run([sys.executable, '-B', str(ROOT / 'analysis' / script)],
                                  cwd=ROOT, stdout=f, stderr=subprocess.STDOUT)
        if proc.returncode:
            raise RuntimeError(f'{script} failed; see {log}')
        for table in tables:
            comparisons.append(compare_csv(table))
    report = {'offline': True, 'source_identity_frame_rebuilt': True,
              'identity_count': len(frame), 'tables': comparisons,
              'passed': all(c['passed'] for c in comparisons)}
    (OUTPUT / 'core_replay_report.json').write_text(json.dumps(report, indent=2) + '\n')
    failures = [c for c in comparisons if not c['passed']]
    if failures:
        for c in failures:
            print(c['table'] + ': ' + c['reason'], file=sys.stderr)
        raise RuntimeError('One or more core tables differ from their frozen references.')
    print(f'All {len(comparisons)} core tables match the frozen references.', flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    if args.prepare_only:
        prepare_inputs()
    else:
        run()


if __name__ == '__main__':
    main()
