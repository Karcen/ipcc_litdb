"""DOI normalisation and the paper identity rule."""
import pytest

from ipccdb_normalisation import normalise_doi, normalise_title, is_valid_doi  # noqa


def test_doi_prefix_variants_collapse():
    for v in ("10.1000/example", "https://doi.org/10.1000/example",
              "doi:10.1000/example", "http://dx.doi.org/10.1000/example",
              "  10.1000/EXAMPLE  "):
        assert normalise_doi(v) == "10.1000/example", v


def test_doi_validation():
    assert is_valid_doi("10.1000/example")
    assert is_valid_doi("10.1016/j.rser.2019.109620")
    assert not is_valid_doi("not-a-doi")
    assert not is_valid_doi("10.1000/")
    assert not is_valid_doi("")


def test_title_normalisation_is_stable():
    a = normalise_title("Respiration in a future, higher‐CO<sub>2</sub> world")
    b = normalise_title("Respiration in a future higher CO2 world")
    assert a == b


def test_title_normalisation_strips_accents_and_punctuation():
    assert normalise_title("Piano Nacional de Energía") == "piano nacional de energia"


def test_empty_title_is_empty():
    assert normalise_title("") == ""
    assert normalise_title(None) == ""
