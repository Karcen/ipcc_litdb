"""Fetch a new dated reference-query archive into a fresh directory.

This command never updates the fixed verification archive beside the script.
It requires --output-dir outside that archive and refuses an existing directory.
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import argparse
import hashlib
import json
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
CANDIDATES = [
    ('price1965networks', '10.1126/science.149.3683.510'),
    ('burton1960halflife', '10.1002/asi.5090110105'),
    ('bradford1985sources', '10.1177/016555158501000407'),
    ('hill1973diversity', '10.2307/1934352'),
    ('simpson1949diversity', '10.1038/163688a0'),
    ('jost2006entropy', '10.1111/j.2006.0030-1299.14714.x'),
    ('bradford1976sources', '10.1300/j105v01n03_06'),
    ('brookes1985bradford', '10.1177/016555158501000406'),
]

def fetch(key, provider, url, raw_dir):
    when = datetime.now(timezone.utc).isoformat()
    request = urllib.request.Request(url, headers={
        'User-Agent': 'IPCC-LitDB-Reference-Verification/1.0',
        'Accept': 'application/json',
    })
    path = raw_dir / f'{key}_{provider}.json'
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            payload = response.read()
            status = response.status
            content_type = response.headers.get('Content-Type')
        path.write_bytes(payload)
        value = json.loads(payload)
        record = value.get('message', {}) if provider.startswith('crossref') else value
        if provider == 'crossref_search':
            fields = {'items':record.get('items', []),
                      'total_results':record.get('total-results')}
        elif provider == 'crossref':
            fields = {name: record.get(name) for name in [
                'DOI', 'title', 'author', 'container-title', 'published',
                'published-print', 'published-online', 'volume', 'issue', 'page',
                'type', 'URL', 'link', 'abstract', 'publisher', 'license']}
        else:
            fields = {name: record.get(name) for name in [
                'id','doi','title','display_name','publication_year',
                'publication_date','authorships','primary_location','biblio',
                'type','open_access','abstract_inverted_index','locations']}
        return {'key':key, 'provider':provider,'query_date':when[:10],
                'queried_at_utc':when,'request_url':url,'http_status':status,
                'content_type':content_type,'raw_response_path':'raw/'+path.name,
                'payload_sha256':hashlib.sha256(payload).hexdigest(),
                'bibliographic_fields':fields}
    except Exception as exc:
        result = {'key':key,'provider':provider,'query_date':when[:10],
                  'queried_at_utc':when,'request_url':url,'error':str(exc)}
        path.write_text(json.dumps(result,indent=2)+'\n')
        return result

def query_jobs():
    jobs = []
    for key, doi in CANDIDATES:
        jobs.append((key, 'crossref', 'https://api.crossref.org/works/' + urllib.parse.quote(doi, safe='')))
        jobs.append((key, 'openalex', 'https://api.openalex.org/works/https://doi.org/' + doi))
    for key, title in [
        ('price1970citationmeasures', 'Citation measures of hard science soft science technology and nonscience'),
        ('bradford1934original', 'Sources of information on specific subjects'),
    ]:
        jobs.append((key, 'crossref_search', 'https://api.crossref.org/works?' + urllib.parse.urlencode({'query.bibliographic':title, 'rows':5})))
        jobs.append((key, 'openalex_search', 'https://api.openalex.org/works?' + urllib.parse.urlencode({'search':title, 'per-page':5})))
    for key, title, author in [
        ('price1970title_author', 'Citation measures of hard science soft science technology and nonscience', 'Price'),
        ('bradford1934title_author', 'Sources of information on specific subjects', 'Bradford'),
    ]:
        jobs.append((key, 'crossref_search', 'https://api.crossref.org/works?' + urllib.parse.urlencode({'query.title':title, 'query.author':author, 'rows':8})))
    return jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path,
                        help='A new directory outside the fixed archive; existing paths are refused.')
    parser.add_argument('--workers', type=int, default=6)
    args = parser.parse_args()
    destination = args.output_dir.resolve()
    if destination == ROOT or ROOT in destination.parents:
        parser.error('The output directory must be outside the fixed archive.')
    if not 1 <= args.workers <= 6:
        parser.error('--workers must be between 1 and 6.')
    try:
        destination.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error('The output directory already exists; choose a new directory.')
    raw_dir = destination / 'raw'
    raw_dir.mkdir()
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch, *job, raw_dir):job for job in query_jobs()}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(result['key'], result['provider'], result.get('http_status', result.get('error')))
    results.sort(key=lambda item:(item['key'], item['provider']))
    (destination/'api_response_inventory.json').write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output_dir':str(destination), 'queries':len(results),
                      'query_dates':sorted({record['query_date'] for record in results})}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
