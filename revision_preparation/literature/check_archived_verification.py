"""Check the six verified additions and archived API evidence offline.

Usage: python check_archived_verification.py [--archive-dir PATH]
The command reads files, prints a JSON result and returns a nonzero status on
failure. It makes no requests and writes no archive files.
"""

from pathlib import Path
import argparse
import hashlib
import html
import json
import re
import sys

sys.dont_write_bytecode = True
from bibtex_helpers import format_pages, render_entry

EXPECTED = {
    'price1965networks': ('10.1126/science.149.3683.510', 'Networks of Scientific Papers', 1965),
    'burton1960halflife': ('10.1002/asi.5090110105', 'The "half-life" of some scientific and technical literatures', 1960),
    'bradford1985sources': ('10.1177/016555158501000407', 'Sources of information on specific subjects 1934', 1985),
    'hill1973diversity': ('10.2307/1934352', 'Diversity and Evenness: A Unifying Notation and Its Consequences', 1973),
    'simpson1949diversity': ('10.1038/163688a0', 'Measurement of Diversity', 1949),
    'jost2006entropy': ('10.1111/j.2006.0030-1299.14714.x', 'Entropy and diversity', 2006),
}
FIXED_BIB_SHA256 = '7779d4708a4bef10e47f05f58d97849d7e85d39c3d656f77835621396bfe2563'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def clean(value):
    value = html.unescape(re.sub(r'<[^>]+>', '', str(value)))
    return value.replace('\u2010', '-').replace('\u2011', '-').replace('\u201c', '"').replace('\u201d', '"')


def braced_value(text, start):
    require(text[start] == '{', 'Expected a braced BibTeX value.')
    depth = 1
    index = start + 1
    while index < len(text):
        if text[index] == '\\':
            index += 2
            continue
        if text[index] == '{':
            depth += 1
        elif text[index] == '}':
            depth -= 1
            if depth == 0:
                return text[start + 1:index], index + 1
        index += 1
    raise ValueError('Unbalanced BibTeX braces.')


def parse_bibtex(text):
    """Read the braced article entries produced by this archive's renderer."""
    result = {}
    position = 0
    while position < len(text):
        if not text[position:].strip():
            break
        match = re.match(r'\s*@([A-Za-z]+)\s*\{\s*([^,\s]+)\s*,', text[position:])
        require(match is not None, 'Unexpected material outside a BibTeX entry.')
        entry_type, key = match.groups()
        require(key not in result, 'Duplicate BibTeX key: ' + key)
        position += match.end()
        fields = {}
        while True:
            whitespace = re.match(r'\s*', text[position:]).end()
            position += whitespace
            if position < len(text) and text[position] == '}':
                position += 1
                break
            field = re.match(r'([A-Za-z][A-Za-z0-9_-]*)\s*=\s*', text[position:])
            require(field is not None, 'Invalid BibTeX field in ' + key)
            name = field.group(1)
            require(name not in fields, 'Duplicate BibTeX field in ' + key)
            position += field.end()
            require(position < len(text), 'Missing BibTeX field value.')
            value, position = braced_value(text, position)
            fields[name] = value
            position += re.match(r'\s*', text[position:]).end()
            require(position < len(text), 'Missing BibTeX entry terminator.')
            if text[position] == ',':
                position += 1
            else:
                require(text[position] == '}', 'Missing BibTeX field separator.')
        result[key] = {'type':entry_type.lower(), 'fields':fields}
    return result


def checked_payload(root, record):
    relative = Path(record['response_payload_file'])
    require(not relative.is_absolute() and '..' not in relative.parts,
            'Payload path must remain relative to the archive.')
    path = (root / relative).resolve()
    require(root in path.parents, 'Payload path escapes the archive.')
    payload = path.read_bytes()
    require(hashlib.sha256(payload).hexdigest() == record['response_payload_sha256'],
            'Payload hash differs: ' + str(relative))
    return path, json.loads(payload)


def verify(root):
    root = root.resolve()
    ledger = json.loads((root / 'reference_verification.json').read_text(encoding='utf-8'))
    require(ledger['query_date'] == '2026-10-12', 'Unexpected fixed-archive query date.')
    accepted = [r for r in ledger['candidates'] if r['included_in_bib']]
    require([r['candidate_key'] for r in accepted] == list(EXPECTED),
            'The six accepted keys or their order differ.')
    bibliography = (root / 'references_additions.bib').read_bytes()
    bibliography_hash = hashlib.sha256(bibliography).hexdigest()
    require(bibliography_hash == ledger['bibliography_sha256'] == FIXED_BIB_SHA256,
            'The fixed bibliography hash differs.')
    entries = parse_bibtex(bibliography.decode('utf-8'))
    require(set(entries) == set(EXPECTED), 'BibTeX keys differ from the declared set.')
    work_paths = set()
    for record in ledger['candidates']:
        if 'response_payload_file' not in record:
            continue
        path, response = checked_payload(root, record)
        work_paths.add(path)
        source = response['message']
        require(source['DOI'].lower() == record['doi'], 'Archived work DOI differs.')
        require(source['title'][0] == record['title'], 'Archived work title differs.')
        require(source.get('container-title', [None])[0] == record['container_title'],
                'Archived outlet differs.')
        require(source.get('published') == record['published'], 'Archived publication date differs.')
        if not record['included_in_bib']:
            continue
        key = record['candidate_key']
        expected_doi, expected_title, expected_year = EXPECTED[key]
        fields = entries[key]['fields']
        require(entries[key]['type'] == 'article', 'Unexpected BibTeX entry type.')
        require(record['doi'] == expected_doi, 'DOI differs for ' + key)
        require(clean(record['title']).casefold() == clean(expected_title).casefold(),
                'Normalized title differs for ' + key)
        require(record['published']['date-parts'][0][0] == expected_year,
                'Publication year differs for ' + key)
        require(fields == record['bibtex_fields'], 'BibTeX fields differ from the ledger: ' + key)
        require(fields['doi'] == expected_doi and fields['year'] == str(expected_year),
                'BibTeX DOI or year differs for ' + key)
        require(fields['title'] == clean(record['title']), 'BibTeX title differs for ' + key)
        require(fields['pages'] == format_pages(record['pages']), 'BibTeX pages differ for ' + key)
        if key == 'bradford1985sources':
            enrichment = record['author_enrichment']
            require(fields['author'] == enrichment['value'] == 'Bradford, S. C.',
                    'Bradford author enrichment differs.')
            support = enrichment['primary_supporting_records']
            require(len(support) == 2, 'Bradford enrichment requires two supporting API records.')
            for supporting in support:
                supporting_path, supporting_response = checked_payload(root, supporting)
                work_paths.add(supporting_path)
                require(supporting_response['message']['DOI'].lower() == supporting['doi'],
                        'Supporting DOI differs.')
                require(supporting_response['message']['title'][0] == supporting['title'],
                        'Supporting title differs.')
            require(support[0]['authors'][0]['family'] == 'Bradford',
                    'The author-source record does not identify Bradford.')
            require('by S.C. Bradford' in support[1]['title'],
                    'The publisher introduction does not credit Bradford.')
    expected_bib = '\n\n'.join(render_entry('article', r['candidate_key'], r['bibtex_fields'])
                              for r in accepted) + '\n'
    require(expected_bib.encode('utf-8') == bibliography, 'Portable rendering changes bibliography bytes.')
    searches = ledger['search_records']
    require(len(searches) == 3, 'Expected three successful archived search responses.')
    for record in searches:
        _, response = checked_payload(root, record)
        source_items = response['message']['items']
        expected_items = [
            {'doi':item.get('DOI'), 'title':item.get('title'),
             'author':item.get('author'), 'published':item.get('published')}
            for item in source_items
        ]
        require(expected_items == record['returned_records'], 'Archived search records differ.')
    require(len(work_paths) == 8, 'Expected eight distinct successful work payloads.')
    inventory = json.loads((root / 'api_response_inventory.json').read_text(encoding='utf-8'))
    inventory_payloads = 0
    for record in inventory:
        if 'raw_response_path' not in record:
            continue
        relative = Path(record['raw_response_path'])
        require(not relative.is_absolute() and '..' not in relative.parts,
                'Inventory payload path must be archive-relative.')
        path = (root / relative).resolve()
        require(root in path.parents, 'Inventory payload path escapes the archive.')
        require(hashlib.sha256(path.read_bytes()).hexdigest() == record['payload_sha256'],
                'Inventory payload hash differs: ' + str(relative))
        inventory_payloads += 1
    rejected = [r for r in ledger['candidates'] if r['decision'] == 'rejected_for_bib']
    require({r['candidate_key'] for r in rejected} == {'price1970citationmeasures', 'bradford1934original'},
            'Rejected candidate set differs.')
    return {'passed':True, 'mode':'offline_read_only', 'archive_dir':str(root),
            'accepted_bibtex_entries':len(accepted), 'work_payload_hashes_checked':len(work_paths),
            'search_payload_hashes_checked':len(searches), 'rejected_candidates':len(rejected),
            'inventory_payload_hashes_checked':inventory_payloads,
            'bibliography_sha256':bibliography_hash}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-dir', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    try:
        result = verify(args.archive_dir)
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        print(json.dumps({'passed':False, 'mode':'offline_read_only', 'error':str(error)}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
