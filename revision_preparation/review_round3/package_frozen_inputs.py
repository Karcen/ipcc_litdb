#!/usr/bin/env python3
"""Package frozen raw bytes in deterministic gzip archives; retain local files."""
from __future__ import annotations
import gzip,hashlib,io,json,tarfile
from pathlib import Path
HERE=Path(__file__).resolve().parent
SPLIT_PATHS=['external/new_api_sources_v1/pool_source_counts.csv.gz','external/new_api_results_v2/pool_source_counts.csv.gz']
SPLIT_CHUNK_BYTES=1024*1024
ROOTS=['external/acquisition_v1','panel/calls','panel/frames','panel/handshake','panel/pilot','panel/pilot_v1_failed_qc','panel/candidates','external/offline_v1/resolved_journal_source_assignments.csv']

def store_exact(path,raw):
    if path.exists():
        if path.read_bytes()!=raw:raise ValueError('Existing frozen archive or chunk differs: '+str(path))
        return
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)

def split_files():
    paths=[HERE/name for name in SPLIT_PATHS]
    raw=paths[0].read_bytes()
    if any(path.read_bytes()!=raw for path in paths[1:]):
        raise ValueError('The deduplicated pool source files do not have identical bytes')
    full_sha=hashlib.sha256(raw).hexdigest();chunks=[]
    for index,start in enumerate(range(0,len(raw),SPLIT_CHUNK_BYTES),1):
        payload=raw[start:start+SPLIT_CHUNK_BYTES]
        name=f'frozen_inputs/pool_source_counts_{full_sha[:16]}_{index:03d}.bin'
        store_exact(HERE/name,payload)
        chunks.append({'path':name,'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()})
    return [{'paths':SPLIT_PATHS,'bytes':len(raw),'sha256':full_sha,'chunks':chunks}]

def main():
    destination=HERE/'frozen_inputs';destination.mkdir(exist_ok=True)
    paths=[]
    for name in ROOTS:
        p=HERE/name
        paths.extend([p] if p.is_file() else [x for x in p.rglob('*') if x.is_file() and not x.name.startswith('._') and '__pycache__' not in x.parts])
    groups=[];current=[];size=0
    for p in sorted(set(paths)):
        n=p.stat().st_size
        if current and size+n>4*1024*1024:groups.append(current);current=[];size=0
        current.append(p);size+=n
    if current:groups.append(current)
    archives=[];members=[]
    for index,group in enumerate(groups,1):
        name=f'frozen_inputs/inputs_{index:03d}.tar.gz';buffer=io.BytesIO()
        with gzip.GzipFile(fileobj=buffer,mode='wb',filename='',mtime=0,compresslevel=9) as compressed:
            with tarfile.open(fileobj=compressed,mode='w',format=tarfile.PAX_FORMAT) as tar:
                for p in group:
                    raw=p.read_bytes();relative=p.relative_to(HERE).as_posix();entry=tarfile.TarInfo(relative);entry.size=len(raw);entry.mode=0o644;entry.mtime=0
                    tar.addfile(entry,io.BytesIO(raw));members.append({'path':relative,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'archive':name})
        raw=buffer.getvalue();store_exact(HERE/name,raw);archives.append({'path':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
    splits=split_files()
    manifest={'analysis_package':'1.4.0','format':'deterministic gzip tar and deduplicated binary chunks; original member bytes unchanged','archives':archives,'members':members,'split_files':splits,'split_chunk_bytes':SPLIT_CHUNK_BYTES,'restored_paths':len(members)+sum(len(x['paths']) for x in splits),'selection_roots':ROOTS+SPLIT_PATHS,'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (destination/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'archives':len(archives),'members':len(members),'compressed_bytes':sum(x['bytes'] for x in archives),'split_file_paths':sum(len(x['paths']) for x in splits),'split_chunks':sum(len(x['chunks']) for x in splits),'split_payload_bytes':sum(x['bytes'] for x in splits)}))
if __name__=='__main__':main()
