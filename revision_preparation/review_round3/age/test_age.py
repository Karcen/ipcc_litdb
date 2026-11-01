"""Focused mathematical fixtures for chapter reweighting and decomposition."""
import importlib.util
from pathlib import Path
import unittest

import numpy as np

spec = importlib.util.spec_from_file_location("round3_age", Path(__file__).with_name("analyze_age.py"))
age = importlib.util.module_from_spec(spec)
spec.loader.exec_module(age)


class AgeTests(unittest.TestCase):
    def packed(self):
        return {"chapter":np.array([0,1,1]),"paper":np.array([0,0,1]),
                "age":np.array([1,9,5]),"matched":np.array([True,False,True]),"n_papers":2}

    def test_equal_ID_recomputed_after_chapter_resampling(self):
        weights = age.occurrence_weights(self.packed(), np.array([2,1]), "equal_assigned_ID")
        np.testing.assert_allclose(weights, [2/3,1/3,1])
        np.testing.assert_allclose(np.bincount(self.packed()["paper"],weights=weights),[1,1])

    def test_absent_ID_has_zero_and_present_ID_total_one(self):
        weights = age.occurrence_weights(self.packed(), np.array([1,0]), "equal_assigned_ID")
        np.testing.assert_allclose(weights,[1,0,0])

    def test_occurrence_multiplicity_preserved(self):
        np.testing.assert_array_equal(age.occurrence_weights(self.packed(),np.array([2,1]),"occurrence"),[2,1,1])

    def test_mixed_status_ID_partition_reconstructs_pooled_mean(self):
        p = self.packed()
        weights = age.occurrence_weights(p,np.array([2,1]),"equal_assigned_ID")
        components = age.status_components(p,weights)
        np.testing.assert_allclose(components[:,0].sum(),1)
        self.assertAlmostEqual(float(np.dot(components[:,0],components[:,1])),age.prior.metrics(age.histogram(p,weights))["mean_age"])

    def test_symmetric_three_term_reconstruction_and_reversal(self):
        a = np.array([[.6,4,.7],[.4,8,.5]])
        b = np.array([[.8,5,.6],[.2,6,.6]])
        for k in (1,2):
            v = age.symmetric_status_decomposition(a,b,k)
            self.assertAlmostEqual(v[0],v[1]+v[2]+v[3])
            np.testing.assert_allclose(age.symmetric_status_decomposition(b,a,k),-v,atol=1e-14)

    def test_constant_status_means_changes_only_composition(self):
        a = np.array([[.6,4,.7],[.4,8,.5]])
        b = np.array([[.8,4,.7],[.2,8,.5]])
        v = age.symmetric_status_decomposition(a,b,1)
        np.testing.assert_allclose(v,[ -.8,0,0,-.8,0],atol=1e-14)

    def test_left_inverse_quartile_with_ties(self):
        hist = np.zeros(61)
        hist[[1,4,9]] = [1,2,1]
        np.testing.assert_allclose(age.metric_vector(hist)[2:],[1,4,4])

    def test_empty_draw_fails(self):
        with self.assertRaises(ValueError):
            age.histogram(self.packed(),np.zeros(3))


if __name__ == "__main__":
    unittest.main()
