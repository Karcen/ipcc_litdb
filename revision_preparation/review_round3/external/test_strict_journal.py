import unittest
import strict_journal_diversity as m

class Tests(unittest.TestCase):
    def row(self,**kw):
        r={'report':'AR5','paper_id':'1','occurrence_status_group':'matched','openalex_id':'W1','source_id':'S1','work_type':'article','source_type':'journal','journal_article_review':'True'};r.update(kw);return r
    def test_occurrence_status_overlap_is_not_double_counted(self):
        d,a=m.identities([self.row(),self.row(occurrence_status_group='unmatched')],'AR5','matched_plus_DOI_recovered_unmatched');self.assertEqual(d,{'1':'S1'});self.assertEqual(a['duplicate_rows_collapsed'],1)
    def test_matched_scope_excludes_recovered_unmatched(self):
        d,a=m.identities([self.row(),self.row(paper_id='2',occurrence_status_group='unmatched')],'AR5','matched');self.assertEqual(len(d),1)
    def test_conflicting_work_resolution_stops(self):
        with self.assertRaises(ValueError):m.identities([self.row(),self.row(openalex_id='W2',occurrence_status_group='unmatched')],'AR5','matched_plus_DOI_recovered_unmatched')
    def test_nonarticle_in_journal_is_excluded(self):
        d,a=m.identities([self.row(work_type='editorial',journal_article_review='False')],'AR5','matched');self.assertEqual(len(d),0)
    def test_article_in_nonjournal_is_excluded(self):
        d,a=m.identities([self.row(source_type='repository',journal_article_review='False')],'AR5','matched');self.assertEqual(len(d),0)
    def test_eligibility_disagreement_stops(self):
        with self.assertRaises(ValueError):m.identities([self.row(work_type='editorial')],'AR5','matched')
    def test_distinct_assigned_IDs_are_distinct_units(self):
        d,a=m.identities([self.row(),self.row(paper_id='2')],'AR5','matched');self.assertEqual(len(d),2)
    def test_d2_integer_safe(self):self.assertEqual(m.d2([10**10]),1.)

if __name__=='__main__':unittest.main()
