#!/usr/bin/env python3
"""Observed indexed indirect links with identical frozen AR5 raw-DOI targets."""
from __future__ import annotations
import argparse,json
from pathlib import Path
from collections import Counter
import offline_external as common
import analyze_acquisition as analysis

ROOT=Path(__file__).resolve().parent

def main():
    p=argparse.ArgumentParser();p.add_argument('--acquisition',type=Path,default=ROOT/'acquisition_v1');p.add_argument('--output',type=Path,default=ROOT/'indirect_pool_v1');a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    sample=json.loads((a.acquisition/'indirect_pool_sample.json').read_text());ipcc=json.loads((a.acquisition/'indirect_IPCC_common_scope.json').read_text());targets=json.loads((a.acquisition/'indirect_AR5_target_set.json').read_text())
    if sample['AR5_target_sorted_IDs_sha256']!=ipcc['AR5_target_sorted_IDs_sha256'] or sample['AR5_target_sorted_IDs_sha256']!=targets['sorted_IDs_sha256']:raise ValueError('Different AR5 target sets')
    rr=sample['observations'];statuses=Counter(r['reference_status'] for r in rr)
    if set(statuses)-{'missing','empty_array','nonempty_array'}:raise ValueError('Unknown reference-list status')
    if any(r['has_AR5_target'] and r['reference_status']!='nonempty_array' for r in rr):raise ValueError('Positive indexed link without a reference list')
    result=analysis.indirect_table(a.acquisition)[0]
    result.update(missing_reference_fields=statuses['missing'],empty_reference_arrays=statuses['empty_array'],nonempty_reference_arrays=statuses['nonempty_array'],fraction_among_nonempty_arrays=result['at_least_one_AR5_target']/statuses['nonempty_array'] if statuses['nonempty_array'] else None,
                  IPCC_common_scope_missing_reference_fields=ipcc['AR6_missing_reference_fields'],IPCC_common_scope_empty_reference_arrays=ipcc['AR6_empty_reference_arrays'],IPCC_common_scope_nonempty_reference_arrays=ipcc['AR6_nonempty_reference_arrays'],IPCC_fraction_among_nonempty_arrays=ipcc['AR6_at_least_one_AR5_target']/ipcc['AR6_nonempty_reference_arrays'],AR5_target_sorted_IDs_sha256=targets['sorted_IDs_sha256'],
                  interpretation='Observed indexed outgoing links only; both cohorts use the identical frozen AR5 raw-DOI work target set. Empty arrays and missing fields are distinct coverage states, neither confirms absence of a true citation. Pool Wilson interval describes sample variation, not linkage or reference coverage.')
    tables=[common.write(a.output/'indirect_pool_baseline.csv',[result])]
    inputs=[a.acquisition/x for x in ['indirect_pool_sample.json','indirect_IPCC_common_scope.json','indirect_AR5_target_set.json']]
    inputs += [a.acquisition/'raw'/(rid+suffix) for rid in sample['request_ids'] for suffix in ['.request.json','.response.json']]
    common.js(a.output/'manifest.json',{'analysis_package':'1.4.0','seed':20261011,'script_sha256':common.sha(__file__),'analysis_helper_sha256':common.sha(ROOT/'analyze_acquisition.py'),'io_helper_sha256':common.sha(ROOT/'offline_external.py'),'inputs':[{'path':str(p.relative_to(ROOT)),'sha256':common.sha(p),'bytes':p.stat().st_size} for p in inputs],'output_tables':tables,'network_used':False,'credentials_read':False,'scope':'Broad-query journal article/review 1980-2021 pool, seeded1000works; frozen raw-DOI target works exactly shared with IPCC comparator.'})
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
