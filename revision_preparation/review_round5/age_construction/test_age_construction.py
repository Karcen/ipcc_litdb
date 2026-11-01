"""Small design fixtures for the additional local-only age sensitivities."""
import importlib.util
from pathlib import Path
import unittest

import numpy as np

spec = importlib.util.spec_from_file_location('round5_age', Path(__file__).with_name('analyze_age_construction.py'))
age = importlib.util.module_from_spec(spec)
spec.loader.exec_module(age)


class AgeConstructionTests(unittest.TestCase):
    def row(self, chapter, group, cited, **extra):
        return {'chapter_id': chapter, 'wg_label': group, 'raw_leading_year': cited,
                'source_publication_year': 2020, **extra}

    def test_cap_and_future_year_priority(self):
        self.assertEqual(age.status(self.row('a', 'WG1', 1960), 'source_publication_year', 60), ('included', 60))
        self.assertEqual(age.status(self.row('a', 'WG1', 1959), 'source_publication_year', 60)[0], 'age_above_cap')
        self.assertEqual(age.status(self.row('a', 'WG1', 1959), 'source_publication_year', None), ('included', 61))
        self.assertEqual(age.status(self.row('a', 'WG1', 2021), 'source_publication_year', None)[0], 'cited_year_after_origin')

    def test_first_lexical_year_not_claimed_author_year(self):
        self.assertEqual(age.prior.raw_year('Title contains 2000 before author date 2012.'), 2000)
        self.assertIsNone(age.prior.raw_year('x' * 301 + ' 2012'))

    def test_left_inverse_quantiles_with_ties(self):
        result = age.stats([0, 0, 10, 10])
        self.assertEqual(result['mean_age'], 5)
        self.assertEqual(result['median_inverse_ecdf'], 0)
        self.assertEqual(result['q75_inverse_ecdf'], 10)

    def test_stratification_preserves_fixed_singleton_groups(self):
        rows = [self.row('a', 'WG1', 2019), self.row('b', 'WG2', 2011)]
        point, draws, deletions, _, _ = age.bootstrap(rows, 'source_publication_year', 'WG_stratified', 100, np.random.default_rng(20261011))
        self.assertEqual(point[0], 5)
        np.testing.assert_array_equal(draws[:, 0], np.full(100, 5.0))
        self.assertTrue(all(not r['estimable'] and r['mean_age'] is None for r in deletions))
        _, mixed, _, _, _ = age.bootstrap(rows, 'source_publication_year', 'unstratified', 100, np.random.default_rng(20261011))
        self.assertGreater(float(np.var(mixed[:, 0])), 0)

    def test_same_ID_does_not_transfer_matching_status(self):
        rows = [{'paper_id': 7, 'match_status': 'matched_doi'}, {'paper_id': 7, 'match_status': 'unmatched'}]
        self.assertEqual(len(age.cohort_rows(rows, 'matched_occurrences')), 1)
        self.assertEqual(len(age.cohort_rows(rows, 'unmatched_occurrences')), 1)

    def test_nonfinite_histogram_rejected(self):
        with self.assertRaises(ValueError):
            age.histogram_stats(np.array([1, float('nan')]))

    def test_empty_draw_is_not_silently_dropped(self):
        with self.assertRaises(ValueError):
            age.histogram_stats(np.zeros(61))


if __name__ == '__main__':
    unittest.main()
