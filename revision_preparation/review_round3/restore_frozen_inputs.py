#!/usr/bin/env python3
"""Restore byte-identical frozen inputs without network access or credentials."""
from __future__ import annotations
import argparse,hashlib,json,tarfile,tempfile
from pathlib import Path,PurePosixPath
HERE=Path(__file__).resolve().parent

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def safe_path(name):
    if not isinstance(name,str) or not name or '\x00' in name or '\\' in name:
        raise ValueError('Unsafe archive member path')
    relative=PurePosixPath(name)
    if relative.is_absolute() or '..' in relative.parts or str(relative)=='.':
        raise ValueError('Unsafe archive member path')
    target=HERE/name
    if not target.resolve().is_relative_to(HERE.resolve()):
        raise ValueError('Unsafe archive member path')
    return target

def matches(name,prefixes):
    return not prefixes or any(name==p or name.startswith(p.rstrip('/')+'/') for p in prefixes)

def verify_payload(raw,descriptor,label):
    if len(raw)!=descriptor['bytes'] or hashlib.sha256(raw).hexdigest()!=descriptor['sha256']:
        raise ValueError(label+' hash mismatch')

def write_exact(name,raw,descriptor):
    target=safe_path(name)
    if target.exists():
        if not target.is_file() or sha(target)!=descriptor['sha256']:
            raise ValueError('Existing frozen input differs: '+name)
        return 0
    target.parent.mkdir(parents=True,exist_ok=True)
    safe_path(name)  # Recheck after creating the directory hierarchy.
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent,prefix=target.name+'.restoring-',delete=False) as stream:
            temporary=Path(stream.name);stream.write(raw)
        if target.exists():
            if not target.is_file() or sha(target)!=descriptor['sha256']:
                raise ValueError('Existing frozen input differs: '+name)
            return 0
        temporary.replace(target)
        return 1
    finally:
        if temporary is not None and temporary.exists():temporary.unlink()

def ensure_inputs(prefixes=()):
    for prefix in prefixes:safe_path(prefix)
    manifest_path=HERE/'frozen_inputs/manifest.json'
    if not manifest_path.exists():
        if prefixes and all((HERE/p).exists() for p in prefixes):return {'restored':0}
        raise FileNotFoundError('Frozen-input manifest is unavailable')
    manifest=json.loads(manifest_path.read_text());wanted={};all_paths=set()
    for member in manifest.get('members',[]):
        name=member['path'];safe_path(name);safe_path(member['archive'])
        if name in all_paths:raise ValueError('Duplicate frozen input path')
        all_paths.add(name)
        if matches(name,prefixes):wanted[name]=member
    splits=[]
    for descriptor in manifest.get('split_files',[]):
        names=[]
        for name in descriptor['paths']:
            safe_path(name)
            if name in all_paths:raise ValueError('Duplicate frozen input path')
            all_paths.add(name)
            if matches(name,prefixes):wanted[name]=descriptor;names.append(name)
        for chunk in descriptor['chunks']:safe_path(chunk['path'])
        if names:splits.append((descriptor,names))
    if prefixes and not wanted:raise ValueError('No archived input matches the requested path')
    for name,member in wanted.items():
        target=safe_path(name)
        if target.exists() and (not target.is_file() or sha(target)!=member['sha256']):
            raise ValueError('Existing frozen input differs: '+name)
    restored=0;verified_archives=0;verified_chunks=0
    for archive in manifest.get('archives',[]):
        source=safe_path(archive['path'])
        selected={name:item for name,item in wanted.items() if item.get('archive')==archive['path']}
        if not selected:continue
        if sha(source)!=archive['sha256'] or ('bytes' in archive and source.stat().st_size!=archive['bytes']):
            raise ValueError('Archive hash mismatch: '+archive['path'])
        verified_archives+=1
        with tarfile.open(source,'r:gz') as tar:
            for entry in tar:
                if entry.name not in selected:continue
                if not entry.isfile():raise ValueError('Only regular files may be restored')
                raw=tar.extractfile(entry).read();member=selected.pop(entry.name)
                verify_payload(raw,member,'Archived member')
                restored+=write_exact(entry.name,raw,member)
        if selected:raise ValueError('Archive omitted a declared member')
    for descriptor,names in splits:
        payload=[]
        for chunk in descriptor['chunks']:
            raw=safe_path(chunk['path']).read_bytes();verify_payload(raw,chunk,'Split chunk')
            payload.append(raw);verified_chunks+=1
        raw=b''.join(payload);verify_payload(raw,descriptor,'Reassembled split file')
        for name in names:restored+=write_exact(name,raw,descriptor)
    return {'verified_members':len(wanted),'restored':restored,'verified_archives':verified_archives,
            'verified_split_files':len(splits),'verified_split_chunks':verified_chunks}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--prefix',action='append',default=[])
    print(json.dumps(ensure_inputs(parser.parse_args().prefix)))
