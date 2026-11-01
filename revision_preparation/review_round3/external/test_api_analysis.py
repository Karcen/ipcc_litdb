#!/usr/bin/env python3
"""Synthetic checks do not fabricate empirical results or observed labels."""
import importlib.util,json,pathlib,tempfile,unittest
import numpy as np
P=pathlib.Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('analysis',P/'analyze_acquisition.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)

class Tests(unittest.TestCase):
    def test_risk_ratio_is_not_odds_ratio(self):
        rr,keep=m.mh_ratio(np.array([[20.,80.,10.,90.]]))
        self.assertEqual(rr,2.)
        self.assertNotAlmostEqual(rr,20*90/(80*10))
    def test_no_cross_group_support_no_estimate(self):
        rr,keep=m.mh_ratio(np.array([[20.,80.,0.,0.],[0.,0.,10.,90.]]))
        self.assertIsNone(rr);self.assertFalse(np.any(keep))
    def test_citation_bins_cover_boundaries(self):
        self.assertEqual([m.citation_bin(x) for x in [0,1,9,10,99,100]],['0','1-9','1-9','10-99','10-99','100+'])
    def test_issn_collapse_and_unknown_accounting(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=pathlib.Path(tmp)
            metadata={'sources':{'https://openalex.org/S1':{'type':'journal','issn_l':'1111-1111'},'https://openalex.org/S2':{'type':'journal','issn_l':'1111-1111'}}}
            (p/'source_metadata.json').write_text(json.dumps(metadata))
            record={'total':55,'groups':[{'key':'https://openalex.org/S1','key_display_name':'One','count':20},{'key':'https://openalex.org/S2','key_display_name':'Two','count':30},{'key':'unknown','key_display_name':'Unknown','count':5}]}
            for q in ['broad','narrow']:
                for rep in ['AR5','AR6']:(p/('pool_sources_'+q+'_'+rep+'.json')).write_text(json.dumps(record))
            summary,delta,buckets=m.pool_diversity(p)
            for r in summary:
                self.assertEqual(r['pool_total'],55);self.assertEqual(r['included_works'],50);self.assertEqual(r['missing_source_works'],5)
                if r['identity']=='issn_l_with_source_ID_fallback':self.assertEqual(r['D2'],1.)
                else:self.assertAlmostEqual(r['D2'],2500/1300)
    def test_truncated_pool_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=pathlib.Path(tmp);(p/'source_metadata.json').write_text('{"sources":{}}')
            for q in ['broad','narrow']:
                for rep in ['AR5','AR6']:(p/('pool_sources_'+q+'_'+rep+'.json')).write_text('{"total":4,"groups":[]}')
            with self.assertRaises(ValueError):m.pool_diversity(p)

if __name__=='__main__':unittest.main()
