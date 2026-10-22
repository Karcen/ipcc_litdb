"""Enumerate labelled binary matrices to check the conditional distribution."""
from collections import Counter
from fractions import Fraction
import importlib.util
import itertools
from pathlib import Path
import unittest

import numpy as np

spec = importlib.util.spec_from_file_location("round3_null",Path(__file__).with_name("analyze_conditional_null.py"))
model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)


class ConditionalTests(unittest.TestCase):
    def brute(self,patterns):
        margins = [sum(bool(p & (1<<j)) for p in patterns) for j in range(3)]
        choices = [[p for p in range(1,8) if p.bit_count() == old.bit_count()] for old in patterns]
        counts = [Counter(),Counter(),Counter()]
        total = 0
        for assignment in itertools.product(*choices):
            if any(sum(bool(p&(1<<j)) for p in assignment) != margins[j] for j in range(3)):
                continue
            total += 1
            for k,(i,j) in enumerate(model.PAIRS):
                counts[k][sum(bool(p&(1<<i)) and bool(p&(1<<j)) for p in assignment)] += 1
        return counts,total

    def test_enumerated_integer_marginals_against_all_labelled_matrices(self):
        for patterns in ([1,2,3],[1,2,4,3,5],[1,1,2,3,6,7],[3,5,6,7]):
            actual,den,*_ = model.counted_marginals(patterns)
            expected,total = self.brute(patterns)
            self.assertEqual(den,total)
            self.assertEqual(actual,expected)

    def test_independent_convolution_fixture(self):
        offset,pmf,_ = model.direct_convolution([(Counter({0:1,1:1}),2),(Counter({1:1,2:1}),2)])
        self.assertEqual(offset,1)
        np.testing.assert_allclose(pmf,[.25,.5,.25])

    def test_exact_tail_threshold_includes_equal_distance(self):
        self.assertEqual(model.tail_support(4,Fraction(5,2)),(1,4))
        self.assertEqual(model.tail_support(1,Fraction(5,2)),(1,4))
        self.assertEqual(model.tail_support(4,Fraction(25000000000001,10000000000000)),(1,4))

    def test_decimal_convolution_and_float_agree(self):
        factors = [(Counter({0:2,1:1}),3)]*4
        offset,pmf,_ = model.direct_convolution(factors)
        doffset,decimal_pmf = model.decimal_convolution(factors)
        self.assertEqual(offset,doffset)
        np.testing.assert_allclose(pmf,[float(x) for x in decimal_pmf])

    def test_small_positive_tail_is_not_monte_carlo_floor_or_zero(self):
        counts = Counter({0:10**20-1,1:1})
        row,_,_ = model.summarize_distribution([(counts,10**20)],1,Fraction(1,10**20))
        self.assertEqual(float(row["conditional_two_sided_tail"]),1e-20)
        self.assertTrue(row["tail_high_precision_verified"])

    def test_holm_nine_works_below_float_precision(self):
        result = model.holm_decimal(["1e-400",".2",".5"])
        self.assertEqual(result[0],"3E-400")
        self.assertEqual(float(result[1]),.4)
        self.assertEqual(float(result[2]),.5)

    def test_fixed_pairwise_total(self):
        patterns = [1,2,3,5,6,7]
        _,_,means,degree,_,_ = model.counted_marginals(patterns)
        self.assertEqual(sum(means),degree[1]+3*degree[2])


if __name__ == "__main__":
    unittest.main()
