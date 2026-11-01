"""Meaningful guards for blinding, accounting and exact interval endpoints."""
import json, pathlib, unittest
from aggregate_panel import cp,kappa,majority,archived_rule_year
from run_panel import validate
HERE=pathlib.Path(__file__).resolve().parent
class ProtocolTests(unittest.TestCase):
    def test_blinding_allowed_fields(self):
        packet=json.loads((HERE/'frames/blind_all.json').read_text())
        for c in packet['cases']:
            self.assertEqual(set(c),{'case_id','task','records','frozen_evidence'})
            for r in c['records']:self.assertEqual(set(r),{'record_id','report_group','raw_reference'})
    def test_arms_same_raw_records(self):
        a=json.loads((HERE/'pilot/blind_50.json').read_text());b=json.loads((HERE/'pilot/metadata_50.json').read_text())
        for c in b['cases']:c.pop('pipeline_metadata')
        self.assertEqual(a,b);self.assertEqual(len(a['cases']),50)
    def test_majority_and_abstention(self):
        self.assertEqual(majority(['same_study','same_study','unresolvable']),('same_study','two_of_three'))
        self.assertEqual(majority(['same_study','different_studies','unresolvable']),('unresolvable','no_majority'))
    def test_interval_known_edges(self):
        lo,hi=cp(0,10);self.assertEqual(lo,0);self.assertAlmostEqual(hi,1-.025**.1,places=12)
        lo,hi=cp(10,10);self.assertAlmostEqual(lo,.025**.1,places=12);self.assertEqual(hi,1)
    def test_kappa_known_nonconstant(self):self.assertAlmostEqual(kappa([['a','a','a'],['b','b','b']]),1)
    def test_archived_rule_suffix(self):self.assertEqual(archived_rule_year('Spinoni, J. et al., 2018b: Changes from1981to2100.'),'2018')
    def test_frames(self):
        d=json.loads((HERE/'frames/sampling_design.json').read_text())
        self.assertEqual(d['shared_id_census'],1657)
        self.assertEqual(sum(s['sampled_occurrences'] for s in d['parser_strata'].values()),2000)
        self.assertEqual(sum(s['sampled_occurrences'] for s in d['source_extraction_strata'].values()),2000)
        roles=json.loads((HERE/'frames/sample_roles.json').read_text());self.assertEqual(sum(r['selection']=='stratified_hash_sample' for r in roles),319)
    def test_response_validator_rejects_missing_case(self):
        packet={'cases':[{'case_id':'C1','records':[{'record_id':'R001','raw_reference':'Smith, 2000. A title. Nature.'}],'frozen_evidence':[]}]}
        with self.assertRaises(ValueError):validate({'cases':[]},packet)
if __name__=='__main__':unittest.main()
