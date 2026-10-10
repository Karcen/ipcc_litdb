"""Analysis results, calculations, and stored provenance."""
import sqlite3

import pytest

from analytics.engine import Engine


@pytest.fixture(scope="module")
def eng(db_path):
    engine = Engine(db_path)
    yield engine
    engine.con.close()


@pytest.fixture(scope="module")
def analysis_payload(eng):
    return eng.run_all()


def test_engine_runs_without_errors(eng, analysis_payload):
    assert analysis_payload["errors"] == 0, eng.warnings
    assert len(analysis_payload["results"]) >= 11


def test_every_result_records_its_sql_or_rule(eng, analysis_payload):
    for r in eng.results:
        assert r["analysis_type"]
        assert r["query"] or r["parameters"], r["analysis_type"]
        assert r["output"] is not None


def test_analysis_results_are_stored_with_provenance(db_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    n = con.execute("SELECT COUNT(*) FROM analysis_results").fetchone()[0]
    assert n > 0
    row = con.execute("""SELECT analysis_type, query, parameters, input_version,
                                software_version, created_at
                         FROM analysis_results LIMIT 1""").fetchone()
    assert all(x is not None for x in row)


def test_coverage_numbers_match_the_database(db_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    e = Engine(db_path)
    e.a_coverage_by_report()
    out = e.results[-1]["output"]
    db_total = con.execute("SELECT COUNT(*) FROM references_occurrence").fetchone()[0]
    assert sum(r["n_references"] for r in out) == db_total


def test_persistence_analysis_matches_papers_table(db_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db_multi = con.execute("SELECT COUNT(*) FROM papers WHERE n_reports > 1").fetchone()[0]
    e = Engine(db_path); e.a_persistence()
    assert e.results[-1]["output"]["multi_report_papers"] == db_multi


def test_duplicate_detection_is_multilevel(eng):
    eng.a_duplicate_detection()
    out = eng.results[-1]["output"]
    assert set(out) >= {"L1_exact_doi", "L2_title_year", "L3_fuzzy_title", "L4_title_author_year"}


def test_duplicate_units_finds_the_known_case(eng):
    """AR6 WG2 Annex I exists twice, extracted from two different PDFs."""
    eng.a_duplicate_units()
    pairs = eng.results[-1]["output"]
    ids = {frozenset((p["chapter_a"], p["chapter_b"])) for p in pairs}
    assert frozenset(("AR6-WG2-Annex-I", "AR6-WG2-AnnexI")) in ids


def test_chapter_overlap_is_a_valid_jaccard(eng):
    eng.a_chapter_overlap()
    for p in eng.results[-1]["output"]:
        assert 0.0 <= p["jaccard"] <= 1.0
        assert p["shared"] >= 1


def test_reference_age_is_plausible(eng):
    eng.a_reference_age()
    for r in eng.results[-1]["output"]:
        assert 0 <= r["median_age"] <= 60, r
        assert r["n"] > 0


def test_insights_have_english_text_and_evidence(eng, analysis_payload):
    assert eng.insights
    for i in eng.insights:
        assert i["title_en"] and i["body_en"]
        assert i["title_zh"] == i["title_en"]
        assert i["body_zh"] == i["body_en"]
        assert i["evidence"]
