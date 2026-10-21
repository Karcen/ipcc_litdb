#!/usr/bin/env python3
"""Verify one citation, keeping the full API payload in private storage.

Replay performs no network calls. Only bibliographic fields, request provenance
without credentials, and the full-payload hash are written to the public output.
An abstract in a Crossref response is not assumed to have metadata reuse rights.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DOI = '10.1002/asi.20744'
TITLE = ('Long-term variations in the aging of scientific literature: '
         'From exponential growth to steady-state science (1900–2004)')
ENDPOINT = 'https://api.crossref.org/works/' + DOI
BIBLIOGRAPHIC_FIELDS = ('DOI','title','author','container-title','volume','issue','page',
                        'published-print','published-online','issued','accepted',
                        'type','publisher','URL','ISSN')


def normalized(text):
    return re.sub(r'[^a-z0-9]', '', unicodedata.normalize('NFKD', text).lower())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='Fresh public output directory')
    parser.add_argument('--replay', type=Path, help='Existing private full response; no network access')
    parser.add_argument('--private-output', type=Path, help='Private archive directory, required for live acquisition')
    args = parser.parse_args()
    output = args.output.resolve()
    subset_path = output / 'lariviere2008_crossref_bibliographic.json'
    record_path = output / 'lariviere2008_verification.json'
    if subset_path.exists() or record_path.exists():
        parser.error('Choose a fresh public output; archived outputs are preserved.')
    if args.replay:
        body = args.replay.read_bytes()
        record={'mode':'offline_replay','replayed_utc':datetime.now(timezone.utc).isoformat(),
                'api_calls':0,'original_endpoint':ENDPOINT,'full_response_sha256':hashlib.sha256(body).hexdigest()}
    else:
        if not args.private_output:
            parser.error('--private-output is required for full response archival')
        private = args.private_output.resolve()
        if private==output or output in private.parents:
            parser.error('Private archive must be outside the public output directory')
        full_path=private/'lariviere2008_crossref_response.json'
        request_path=private/'lariviere2008_crossref_request.json'
        if full_path.exists() or request_path.exists():parser.error('Choose a fresh private archive')
        private.mkdir(parents=True,exist_ok=True)
        headers={'Accept':'application/json','User-Agent':'ipcc_litdb-revision-check/1.0 (public metadata verification)'}
        record={'mode':'live','method':'GET','endpoint':ENDPOINT,'parameters':{},
                'request_headers':headers,'credentials_used':False,'requested_utc':datetime.now(timezone.utc).isoformat(),
                'retry_count':0,'fallback_api_calls':0}
        try:
            with urlopen(Request(ENDPOINT,headers=headers),timeout=45) as response:
                body=response.read();record['http_status']=response.status
                record['response_headers']=dict(response.headers.items())
        except HTTPError as error:
            body=error.read();record['http_status']=error.code
        except (URLError,TimeoutError,OSError):
            body=b'';record['http_status']=None
        full_path.write_bytes(body)
        record['received_utc']=datetime.now(timezone.utc).isoformat()
        record['full_response_sha256']=hashlib.sha256(body).hexdigest()
        request_path.write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
        if record['http_status']!=200:
            raise SystemExit('STOP: Crossref request failed; no retries or fallback calls performed.')
    message=json.loads(body)['message']
    if (str(message.get('DOI','')).lower()!=DOI or
        not any(normalized(t)==normalized(TITLE) for t in message.get('title',[]))):
        raise SystemExit('STOP: DOI/title verification failed.')
    subset={k:message[k] for k in BIBLIOGRAPHIC_FIELDS if k in message}
    if 'author' in subset:
        subset['author']=[{k:a[k] for k in ('given','family','sequence') if k in a} for a in subset['author']]
    subset['provenance']={'full_response_sha256':record['full_response_sha256'],
                          'endpoint':ENDPOINT,'doi_and_title_verified':True,
                          'abstract_excluded':True}
    output.mkdir(parents=True,exist_ok=True)
    subset_path.write_text(json.dumps(subset,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    record.update({'doi_and_title_verified':True,'bibliographic_file':subset_path.name,
                   'bibliographic_sha256':hashlib.sha256(subset_path.read_bytes()).hexdigest(),
                   'full_payload_public':False,'abstract_present_in_full_response':'abstract' in message})
    record_path.write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'doi_and_title_verified':True,'mode':record['mode'],'full_payload_public':False}))


if __name__=='__main__':main()
