"""Check the conditional sharing model by enumerating labelled memberships.

Each fixture treats papers as distinct rows. The independent baseline visits
every allowed group assignment with the observed row degrees and column totals.
It counts matrices directly and uses no combinatorial weights from the model.
"""
from collections import Counter
from fractions import Fraction
from itertools import combinations, product

import numpy as np
import pytest

from reproducibility.revision_age_networks import conditional_states
from reproducibility.analyse_corpus import write_csv


FIXTURES = (
    ((1, 2, 4), 6, 1),
    ((1, 3, 5), 2, 1),
    ((1, 2, 3, 5), 8, 3),
    ((1, 2, 4, 3, 5), 31, 6),
    ((1, 2, 3, 6, 7), 8, 3),
    ((3, 5, 6), 6, 1),
    ((1, 1, 2, 3, 6), 18, 3),
    ((1, 4, 3, 5, 7), 8, 3),
    ((2, 2, 4, 3, 5), 31, 6),
    ((1, 2, 4, 7), 6, 1),
)
PAIRS = ((0, 1), (0, 2), (1, 2))


def test_csv_control_character_round_trip(tmp_path):
    """Retain source control characters in a format older CSV readers accept."""
    import csv

    original = 'Printed citation, "quoted"\nwith embedded\x00NUL and a second\x00NUL.'
    path = tmp_path / "control_character.csv"
    write_csv(path, [{"occurrence_id": "fixture-1", "raw_reference": original}])
    assert b"\x00" not in path.read_bytes()
    with path.open(newline="", encoding="utf-8") as stream:
        row = next(csv.DictReader(stream))
    assert row["occurrence_id"] == "fixture-1"
    assert row["raw_reference"].replace("\\u0000", "\x00") == original


def enumerate_labelled_matrices(patterns):
    """Return overlap frequencies among every feasible labelled matrix."""
    observed = [{group for group in range(3) if mask & (1 << group)} for mask in patterns]
    target = tuple(sum(group in row for row in observed) for group in range(3))
    allowed = [tuple(combinations(range(3), len(row))) for row in observed]
    frequencies = Counter()
    for rows in product(*allowed):
        columns = tuple(sum(group in row for row in rows) for group in range(3))
        if columns == target:
            overlaps = tuple(sum(a in row and b in row for row in rows) for a, b in PAIRS)
            frequencies[overlaps] += 1
    return frequencies, target


@pytest.mark.parametrize("patterns,matrix_count,state_count", FIXTURES)
def test_conditional_probabilities_match_all_labelled_matrices(patterns, matrix_count, state_count):
    # A zero-degree row represents a synthesis-only work. It has one assignment
    # and cannot change the distribution or any group total.
    for fixture in (patterns, (*patterns, 0)):
        frequencies, target = enumerate_labelled_matrices(fixture)
        assert sum(frequencies.values()) == matrix_count
        assert len(frequencies) == state_count

        values, probabilities, degrees, margins = conditional_states(fixture)
        assert tuple(margins) == target
        assert degrees == tuple(sum(mask.bit_count() == degree for mask in fixture) for degree in (1, 2, 3))
        assert np.all(np.isfinite(probabilities))
        assert np.all(probabilities > 0)
        assert float(probabilities.sum()) == pytest.approx(1, abs=1e-13)
        assert np.array_equal(values, values.astype(int))
        modeled = {tuple(int(value) for value in row): float(p) for row, p in zip(values, probabilities)}
        assert len(modeled) == len(values) == state_count
        assert set(modeled) == set(frequencies)

        for state, count in frequencies.items():
            exact_probability = float(Fraction(count, matrix_count))
            assert modeled[state] == pytest.approx(exact_probability, rel=1e-13, abs=1e-13)
        exact_mean = [float(sum(Fraction(count * state[column], matrix_count)
                                for state, count in frequencies.items())) for column in range(3)]
        np.testing.assert_allclose(probabilities @ values, exact_mean, rtol=1e-13, atol=1e-13)

        # Row degrees fix the total sharing across all three group pairs.
        fixed_sharing = sum(len(tuple(combinations(range(mask.bit_count()), 2))) for mask in fixture)
        assert all(sum(state) == fixed_sharing for state in frequencies)
        assert np.all(values.sum(axis=1) == fixed_sharing)
