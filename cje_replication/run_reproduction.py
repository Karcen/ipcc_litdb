#!/usr/bin/env python3
"""Offline reproduction of the submitted CJE manuscript.

Only output/ is written. Frozen inputs/reference outputs are never modified.
No API calls, new samples, new labels, or inference choices are made.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
sys.dont_write_bytecode = True

PACKAGE = Path(__file__).resolve().parent
OUTPUT = PACKAGE / 'output'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def verify_inputs():
    manifest = json.loads((PACKAGE / 'input_manifest.json').read_text())
    for entry in manifest['files']:
        path = PACKAGE / entry['path']
        if not path.is_file() or digest(path) != entry['sha256']:
            raise RuntimeError('Frozen file missing or changed: ' + entry['path'])
    return len(manifest['files'])


def verify_runtime():
    """Reject dependency versions with different missing-value semantics."""
    try:
        import pandas as pd
        import numpy as np
    except ImportError as error:
        raise RuntimeError('Install the pinned dependencies with: python -m pip install -r requirements.txt') from error
    required = {'pandas': '3.0.1', 'numpy': '2.3.5'}
    actual = {'pandas': pd.__version__, 'numpy': np.__version__}
    mismatched = {name: {'required': required[name], 'installed': actual[name]}
                  for name in required if actual[name] != required[name]}
    if mismatched:
        raise RuntimeError('Dependency versions do not match the frozen analysis environment: '
                           + json.dumps(mismatched)
                           + '. Install them with: python -m pip install -r requirements.txt')
    return {'python': sys.version.split()[0], **actual}


def call(command, name, cwd=None):
    logdir = OUTPUT / 'logs'
    logdir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PYTHONHASHSEED'] = '20261011'
    env['CJE_REPLICATION_OUTPUT'] = str(OUTPUT)
    with (logdir / (name + '.txt')).open('w') as log:
        proc = subprocess.run(command, cwd=cwd or PACKAGE, env=env,
                              stdout=log, stderr=subprocess.STDOUT)
    if proc.returncode:
        raise RuntimeError('Stage failed: ' + name + '; see output/logs/' + name + '.txt')


def macros(path):
    return dict(re.findall(r'\\newcommand\{\\(\w+)\}\{([^\n]*)\}', path.read_text()))


def check_macros():
    expected = macros(PACKAGE / 'reference/manuscript/numbers_CJE_revised.tex')
    actual = macros(OUTPUT / 'numbers_CJE_revised.tex')
    differences = {k: {'reference': expected.get(k), 'reproduced': actual.get(k)}
                   for k in expected.keys() | actual.keys() if expected.get(k) != actual.get(k)}
    report = {'passed': not differences, 'checked_macros': len(expected),
              'reproduced_macros': len(actual), 'differences': differences}
    (OUTPUT / 'macro_reproduction_check.json').write_text(json.dumps(report, indent=2) + '\n')
    if differences:
        raise RuntimeError('Submitted numerical definitions changed; inspect output/macro_reproduction_check.json')
    return report


def make_figures():
    if not shutil.which('Rscript'):
        raise RuntimeError('Rscript is required for --figures; see r_packages.txt')
    call(['Rscript', str(PACKAGE / 'analysis/make_figures.R')], 'make_figures')
    expected = ['F1_three_frames.pdf', 'F2_coverage_by_frame.pdf',
                'F3_type_composition.pdf', 'F4_decomposition_ladder.pdf']
    missing = [f for f in expected if not (OUTPUT / 'figures' / f).is_file()]
    if missing:
        raise RuntimeError('Missing regenerated figures: ' + ', '.join(missing))
    return {'passed': True, 'regenerated_figures': expected,
            'note': 'Plot data are checked numerically; binary PDFs can vary with fonts and R graphics libraries.'}


def compile_manuscript(use_rebuilt_figures):
    if not shutil.which('pdflatex'):
        raise RuntimeError('pdflatex is required for --compile')
    source = PACKAGE.parent / 'latex_source'
    target = OUTPUT / 'manuscript'
    shutil.copytree(source, target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('._*', '*.aux', '*.log', '*.out', '*.fff', '*.ttt', '*.pdf~'))
    shutil.copyfile(OUTPUT / 'numbers_CJE_revised.tex', target / 'numbers_CJE_revised.tex')
    if use_rebuilt_figures:
        for fig in (OUTPUT / 'figures').glob('*.pdf'):
            shutil.copyfile(fig, target / 'figures' / fig.name)
    for n in range(1, 4):
        call(['pdflatex', '-interaction=nonstopmode', '-halt-on-error', 'main_document.tex'],
             'pdflatex_pass_' + str(n), cwd=target)
    log = (target / 'main_document.log').read_text(errors='replace')
    if re.search(r'undefined|LaTeX Error|^!', log, re.I | re.M):
        raise RuntimeError('Compilation left an error or undefined citation/reference')
    return {'passed': True, 'pdf': 'output/manuscript/main_document.pdf',
            'undefined_references_or_citations': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--all', action='store_true', help='Rebuild numbers, figures and manuscript PDF')
    parser.add_argument('--figures', action='store_true', help='Also regenerate all four figures with R')
    parser.add_argument('--compile', action='store_true', help='Also compile with pdflatex')
    parser.add_argument('--verify-only', action='store_true', help='Check packaged frozen-file hashes only')
    parser.add_argument('--finish-only', action='store_true', help='Check existing regenerated outputs, then generate macros and requested figures/PDF')
    args = parser.parse_args()
    verified = verify_inputs()
    if args.verify_only:
        print(f'Verified {verified} frozen files.')
        return
    runtime = verify_runtime()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if not args.finish_only:
        call([sys.executable, str(PACKAGE / 'run_types.py')], 'type_reproduction')
        call([sys.executable, str(PACKAGE / 'run_core.py')], 'core_reproduction')
    from run_types import compare_tables
    from run_core import STAGES, compare_csv
    types_report = compare_tables()
    core_tables = [compare_csv(name) for _, names in STAGES for name in names]
    if not all(row['passed'] for row in core_tables):
        raise RuntimeError('Core outputs do not match frozen references')
    call([sys.executable, str(PACKAGE / 'analysis/07_generate_numbers.py')], 'macro_generation')
    macro_report = check_macros()
    report = {'passed': True, 'offline': True, 'frozen_files_verified': verified,
              'current_type_audit_tables': types_report['checked_tables'],
              'core_tables': len(core_tables), 'numerical_definitions': macro_report['checked_macros'],
              'no_new_samples_or_annotations': True,
              'excluded_legacy_table': 'AUDIT_types_by_group.csv; see replay_scope.json'}
    rebuild_figures = args.figures or args.all
    if rebuild_figures:
        report['figures'] = make_figures()
    if args.compile or args.all:
        report['manuscript'] = compile_manuscript(rebuild_figures)
    report['runtime'] = runtime
    (OUTPUT / 'reproduction_report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
