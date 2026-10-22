#!/usr/bin/env python3
"""Invariants that protect denominators and the interpretation of scenarios."""
import csv, json, pathlib, tempfile, unittest, importlib.util

P=pathlib.Path(__file__).resolve().parent
REPO=P.parents[2]
spec=importlib.util.spec_from_file_location('offline_external',P/'offline_external.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.REPO=REPO

def rows(name):
    with (P/'offline_v1'/name).open(newline='') as f:return list(csv.DictReader(f))

class Tests(unittest.TestCase):
    def test_replays_original_group_cutoffs(self):
        old=REPO/'revision_preparation/review_round2/openalex_v1/activated_attempt_03/results_v1/publication_window_reuse.csv'
        with old.open(newline='') as f:previous={(r['cohort'],r['working_group']):r for r in csv.DictReader(f)}
        for r in rows('cutoff_reuse_sensitivity.csv'):
            if r['date_exclusion']!='none' or r['cohort'] not in ['matched_assigned','all_raw_DOI']:continue
            p=previous[r['cohort'],r['working_group']]
            self.assertEqual(r['AR6_eligible_keys'],p['later_published_by_boundary'])
            self.assertEqual(r['shared_eligible_keys'],p['shared_published_by_boundary'])
    def test_exclusions_nest(self):
        rr=rows('cutoff_reuse_sensitivity.csv')
        for cohort in {r['cohort'] for r in rr}:
            for group in ['WG1','WG2','WG3']:
                d={r['date_exclusion']:r for r in rr if r['cohort']==cohort and r['working_group']==group}
                self.assertGreaterEqual(int(d['none']['AR6_eligible_keys']),int(d['exclude_january_first']['AR6_eligible_keys']))
                self.assertGreaterEqual(int(d['exclude_january_first']['AR6_eligible_keys']),int(d['exclude_every_month_first']['AR6_eligible_keys']))
    def test_exact_thirty_annual_bins(self):
        for r in rows('equal_window_age_comparison.csv'):
            if r['window']=='equal_30_annual_bins':self.assertEqual(int(r['publication_year_upper'])-int(r['publication_year_lower'])+1,30)
    def test_nested_age_cohorts_reconstruct_all(self):
        rr=rows('equal_window_age_comparison.csv')
        for key in {(r['query'],r['report'],r['window'],r['working_group']) for r in rr}:
            d={r['cohort']:r for r in rr if (r['query'],r['report'],r['window'],r['working_group'])==key}
            n={c:int(r['IPCC_occurrences']) for c,r in d.items()}
            self.assertEqual(n['all'],n['matched']+n['unmatched'])
            if n['all']:
                mean=sum(n[c]*float(d[c]['IPCC_mean_age']) for c in ['matched','unmatched'] if n[c])/n['all']
                self.assertAlmostEqual(mean,float(d['all']['IPCC_mean_age']),12)
    def test_complete_18_sources(self):
        rr=rows('top18_pool_shares.csv');self.assertEqual(len(rr),18)
        self.assertTrue(all(r['AR5_pool_share']!='' and r['AR6_pool_share']!='' for r in rr))
    def test_unknown_classification_not_silently_journal(self):
        for r in rows('unmatched_DOI_source_recovery.csv'):
            self.assertEqual(int(r['assigned_IDs_in_group']),sum(int(r[k]) for k in ['resolved_IDs','unresolved_IDs','ambiguous_IDs']))
            self.assertEqual(int(r['resolved_IDs']),int(r['journal_article_review_IDs'])+int(r['nonjournal_or_missing_source_IDs']))
    def test_sensitivity_endpoints_and_fixed_denominators(self):
        rr=rows('missing_source_allocation_curve.csv')
        for rep in ['AR5','AR6']:
            r=[x for x in rr if x['report']==rep]
            self.assertEqual(len({x['denominator'] for x in r}),1)
            zero=[float(x['D2']) for x in r if float(x['alpha'])==0]
            self.assertEqual(zero[0],zero[1])
            a=next(float(x['D2']) for x in r if x['alpha']=='1.0' and x['direction']=='toward_largest_source')
            b=next(float(x['D2']) for x in r if x['alpha']=='1.0' and x['direction']=='toward_distinct_sources')
            self.assertLess(a,zero[0]);self.assertGreater(b,zero[0])

if __name__=='__main__':unittest.main()
