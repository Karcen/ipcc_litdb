#!/usr/bin/env python3
"""Check every additional robustness table against its separate registry."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from coverage_robustness import csv_compare, sha256, write_json


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--reference-dir',type=Path,required=True,
                   help='Directory containing manifest.json and its relative results/ table paths')
    p.add_argument('--report',type=Path,required=True,help='New JSON report path')
    a=p.parse_args()
    actual=a.output_dir.expanduser().resolve()
    reference=a.reference_dir.expanduser().resolve()
    report=a.report.expanduser().resolve()
    if report.exists():raise ValueError('Choose a new report path')
    reg=json.loads((reference/'manifest.json').read_text())
    observed=json.loads((actual/'manifest.json').read_text())
    for key in ['analysis_version','database_sha256_before','database_sha256_after',
                'script_sha256','numpy_version','seed','age_bootstrap_replicates']:
        if observed[key]!=reg[key]:raise ValueError(f'Analysis provenance differs: {key}')
    if not observed['baseline_gate']['all_29_passed']:
        raise ValueError('Baseline gate has not passed')
    results=[]
    for table in reg['registered_tables']:
        path=Path(table['filename'])
        if path.is_absolute() or '..' in path.parts:raise ValueError('Registry path must remain relative')
        expected=reference/path;generated=actual/path
        if sha256(expected)!=table['sha256']:raise ValueError(f'Reference table changed: {path}')
        csv_compare(generated,expected)
        with generated.open(newline='',encoding='utf-8') as f:
            rows=sum(1 for _ in csv.reader(f))-1
        if rows!=table['rows']:raise ValueError(f'Row count differs: {path}')
        results.append({'filename':path.as_posix(),'rows':rows,'passed':True,
                        'generated_sha256':sha256(generated),'registered_sha256':table['sha256'],
                        'byte_identical':sha256(generated)==table['sha256']})
    if len(results)!=10:raise ValueError('The ten-table robustness registry is required')
    write_json(report,{'all_passed':True,'tables_passed':len(results),
                       'baseline_tables_passed':29,'database_unchanged':True,
                       'reference_manifest_sha256':sha256(reference/'manifest.json'),
                       'tables':results})
    print(json.dumps({'all_passed':True,'tables_passed':len(results)},indent=2))


if __name__=='__main__':main()
