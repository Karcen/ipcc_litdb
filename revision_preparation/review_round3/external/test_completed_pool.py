#!/usr/bin/env python3
"""Raw-byte completeness and estimand checks for the partial acquisition deliverable."""
import copy,json,pathlib,tempfile,unittest
from collections import Counter
import completed_pool_sources as m

class Tests(unittest.TestCase):
    def packet(self,p):
        (p/'raw').mkdir();g=[{'key':'S1','count':2,'key_display_name':'One'},{'key':'unknown','count':3,'key_display_name':'unknown'}]
        (p/'pool.json').write_text(json.dumps({'total':5,'request_ids':['a'],'groups':g}))
        (p/'raw/a.request.json').write_text(json.dumps({'parameters':{'cursor':'*'}}))
        (p/'raw/a.response.json').write_text(json.dumps({'meta':{'count':5,'next_cursor':None},'group_by':g}))
    def test_complete_replay(self):
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d);self.packet(p);b,c,n,paths=m.replay(p,'pool.json');self.assertEqual(c,Counter({'S1':2,'unknown':3}));self.assertEqual(len(paths),3)
    def changed(self,kind,value):
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d);self.packet(p)
            q=p/('raw/a.'+kind+'.json');x=json.loads(q.read_text());value(x);q.write_text(json.dumps(x))
            with self.assertRaises(ValueError):m.replay(p,'pool.json')
    def test_incomplete_cursor_rejected(self):self.changed('response',lambda d:d['meta'].update(next_cursor='more'))
    def test_population_drift_rejected(self):self.changed('response',lambda d:d['meta'].update(count=6))
    def test_broken_cursor_chain_rejected(self):self.changed('request',lambda d:d['parameters'].update(cursor='elsewhere'))
    def test_missing_source_exclusion_explicit(self):self.assertEqual(m.known_counts(Counter({'S1':2,'unknown':3,None:4})),{'S1':2})
    def test_effective_number_integer_safe(self):self.assertEqual(m.diversity({'one':10**10}),(1,1.))
    def test_empty_source_population_rejected(self):
        with self.assertRaises(ValueError):m.diversity({})

if __name__=='__main__':unittest.main()
