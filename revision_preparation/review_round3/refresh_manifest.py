#!/usr/bin/env python3
"""Refresh round-three component and code hashes without changing analysis data."""
from pathlib import Path
import hashlib,json
HERE=Path(__file__).resolve().parent

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def excluded(path,roots):
    relative=path.relative_to(HERE)
    return (any(part.startswith('._') or part=='__pycache__' for part in relative.parts)
            or str(relative).startswith('figures/work/')
            or any(path==root or root in path.parents for root in roots))
def main():
    package=HERE/'manifest.json';data=json.loads(package.read_text())
    frozen=json.loads((HERE/'frozen_inputs/manifest.json').read_text())
    roots=[HERE/name for name in frozen['selection_roots']]
    components=[]
    for path in sorted(HERE.rglob('manifest.json')):
        if path==package or excluded(path,roots):continue
        rel=path.relative_to(HERE).as_posix()
        item={'path':rel,'sha256':sha(path)}
        if rel=='external/new_api_results_v1/manifest.json':
            item['status']='superseded; retained for provenance'
        if rel.startswith('panel/results/'):
            item['status']='archived pilot; invalid for inference; full validation canceled'
        components.append(item)
    data['components']=components
    data['code_inventory']=[{'path':path.relative_to(HERE).as_posix(),'sha256':sha(path)}
        for path in sorted(HERE.rglob('*')) if path.is_file() and path.suffix in {'.py','.sh','.R'} and not excluded(path,roots)]
    previous=[x['path'] for x in data.get('scope_records',[])]
    extra=['frozen_inputs_verification.json','legacy_tables_verification.json','panel/results/pilot_20261021/INVALID_FOR_INFERENCE.json']
    data['scope_records']=[{'path':name,'sha256':sha(HERE/name)} for name in sorted(set(previous+extra)) if (HERE/name).is_file()]
    data['frozen_input_archives']={'manifest':'frozen_inputs/manifest.json','archives':len(frozen['archives']),'members':len(frozen['members']),'tar_member_paths':len(frozen['members']),'split_file_contents':len(frozen.get('split_files',[])),'split_file_paths':sum(len(x['paths']) for x in frozen.get('split_files',[])),'split_chunks':sum(len(x['chunks']) for x in frozen.get('split_files',[])),'restored_paths':len(frozen['members'])+sum(len(x['paths']) for x in frozen.get('split_files',[])),'raw_selection_roots_excluded_from_code_inventory':frozen['selection_roots']}
    data['protected_legacy_registered_tables']=45
    package.write_text(json.dumps(data,indent=2)+'\n')
    print(json.dumps({'components':len(components),'code_files':len(data['code_inventory']),'scope_records':len(data['scope_records']),'manifest_sha256':sha(package)},indent=2))
if __name__=='__main__':main()
