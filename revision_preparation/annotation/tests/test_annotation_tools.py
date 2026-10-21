"""Independent synthetic annotation frames; no production manual labels."""
from copy import deepcopy
import json
from pathlib import Path
import random
import sqlite3
import sys
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import annotation_tools as tools
from local_annotation_app import AnnotationStore, make_server, search_database


@pytest.fixture
def bundle(tmp_path):
    source, root = tmp_path/"source", tmp_path/"annotation"
    source.mkdir()
    papers, entries = [], []
    for pid in range(1, 7):
        shared = pid in {1, 2, 5}
        reports = ["AR5", "AR6"] if shared else ["AR5"]
        stratum = "shared_matched|doi_only|WG1" if pid in {1, 2} else "shared_matched|title_only|WG1" if pid == 5 else "AR5_only_matched|doi_only|WG1"
        targeted = pid == 6
        papers.append({"paper_id": str(pid), "stratum": "targeted_review" if targeted else stratum,
            "selection": tools.TARGETED if targeted else tools.PROBABILITY,
            "title_best": f"Synthetic work {pid}", "year_best": "2012", "paper_doi": f"10.1234/{pid}",
            "AR5_status_set": "doi_only", "AR6_status_set": "doi_only" if shared else "absent",
            "occurrences_reviewed": str(len(reports)), "human_label": "unreviewed"})
        for report in reports:
            entries.append({"occurrence_id": f"{report}-{pid}", "paper_id": str(pid), "report_id": report,
                "match_status": "matched_doi", "raw_reference": f"Author (2012). Synthetic work {pid}.",
                "sample_stratum": "targeted_review" if targeted else stratum,
                "selection": tools.TARGETED if targeted else tools.PROBABILITY, "human_identity_label": "unreviewed"})
    strata = [
        {"stratum": "shared_matched|doi_only|WG1", "population_ids": "10", "sampled_ids": "2", "inclusion_probability": "0.2"},
        {"stratum": "shared_matched|title_only|WG1", "population_ids": "1", "sampled_ids": "1", "inclusion_probability": "1"},
        {"stratum": "AR5_only_matched|doi_only|WG1", "population_ids": "20", "sampled_ids": "2", "inclusion_probability": "0.1"},
    ]
    for kind, rows in [("papers", papers), ("occurrences", entries), ("strata", strata)]:
        tools.write_csv(source/tools.SOURCE_NAMES[kind], rows)
    tools.write_json(source/tools.SOURCE_NAMES["summary"], {
        "provenance": {"database_sha256": "synthetic-database"},
        "sample": {"seed": tools.SEED, "probability_sample_ids": 5},
        "main_counts": {"shared_matched": 11, "matched_AR5": 31, "matched_AR6": 11}})
    tools.prepare(source, root)
    return root


def completed_labels(root):
    ids = tools.read_csv(root/"templates/id_annotations.csv")
    entries = tools.read_csv(root/"templates/occurrence_annotations.csv")
    for r in ids:
        pid = int(r["paper_id"])
        r.update({"annotator": "synthetic-reviewer", "review_complete": "yes",
            "identity_label": "false_merge" if pid in {1, 3, 6} else "correct_merge",
            "shared_link_label": "false_shared" if pid == 1 else "same_work" if pid in {2, 5} else "not_shared",
            "review_notes": "Synthetic decision for estimator tests.",
            "missed_merge_search_status": "complete", "missed_merge_result": "missed_merge_found" if pid == 4 else "none_found",
            "missed_merge_search_methods": "raw_reference | title | author_year | candidate_raw_entries",
            "missed_merge_candidate_ids": "123" if pid == 4 else "",
            "missed_merge_search_notes": "Synthetic completed title, author-year and candidate checks."})
    for r in entries:
        pid = int(r["paper_id"])
        group = "B" if pid == 1 and r["report_id"] == "AR6" else "A | B" if pid in {3, 6} else "A"
        wrong = pid == 2 and r["report_id"] == "AR6"
        r.update({"annotator": "synthetic-reviewer", "work_groups": group,
            "metadata_judgment": "metadata_error" if wrong else "correct", "metadata_error_fields": "doi" if wrong else "",
            "review_notes": "Synthetic metadata discrepancy." if wrong else ""})
    return ids, entries


def save_labels(root, ids, entries):
    a, b = root/"test_id_labels.csv", root/"test_occurrence_labels.csv"
    tools.write_csv(a, ids); tools.write_csv(b, entries)
    return a, b


def test_blank_templates_and_missing_paths_never_produce_estimates(bundle):
    a, b = bundle/"templates/id_annotations.csv", bundle/"templates/occurrence_annotations.csv"
    blank = tools.estimate(bundle, a, b)
    missing = tools.estimate(bundle)
    for result in [blank, missing]:
        assert result["status"] == "pending_annotation"
        assert result["rates"] == result["recurrence_stress"] == []
    assert blank["integrity"]["expected"] == {"sampled_ids": 6, "probability_ids": 5, "targeted_ids": 1, "occurrences": 9, "strata": 3, "frame_ids": 31}
    assert missing["integrity"]["missing_files"]


def test_empty_label_cli_has_nonzero_exit_and_only_integrity_output(bundle,monkeypatch,capsys):
    output = bundle/"empty_cli_output"
    monkeypatch.setattr(sys,"argv",["annotation_tools.py","estimate","--root",str(bundle),
        "--id-labels",str(bundle/"templates/id_annotations.csv"),"--occurrence-labels",str(bundle/"templates/occurrence_annotations.csv"),
        "--output",str(output)])
    assert tools.main() == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "pending_annotation" and "rates" not in report
    assert {p.name for p in output.iterdir()} == {"annotation_integrity.json"}


def test_csv_round_trip_preserves_standalone_carriage_returns(tmp_path):
    rows = [{"occurrence_id":"original", "chapter_title":"Chapter\rTitle",
        "raw_reference":'Original\rraw text with \r\nlines, "quotes" and https://example.org.'}]
    path = tmp_path/"raw.csv"
    tools.write_csv(path, rows)
    assert tools.read_csv(path) == rows


def test_frozen_production_templates_have_no_structural_errors():
    root = Path(__file__).resolve().parents[1]
    report, ids, occurrences = tools.validate(root,root/"templates/id_annotations.csv",root/"templates/occurrence_annotations.csv")
    assert report["status"] == "pending_annotation"
    assert not report["errors"] and not report["missing_files"]
    assert len(ids) == 331 and len(occurrences) == 801
    assert all(not r["identity_label"] for r in ids.values())


def test_raw_reference_search_finds_candidates_hidden_by_bad_preferred_metadata(tmp_path):
    database = tmp_path/"search.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE papers (paper_id INTEGER PRIMARY KEY,title_best TEXT,year_best TEXT,doi_norm TEXT,authors_best TEXT);
            CREATE TABLE chapters (chapter_id TEXT PRIMARY KEY,report_id TEXT,chapter_label TEXT,source_id TEXT);
            CREATE TABLE sources (source_id TEXT PRIMARY KEY,file_url TEXT);
            CREATE TABLE references_occurrence (occurrence_id TEXT PRIMARY KEY,paper_id INTEGER,chapter_id TEXT,raw_reference TEXT,match_status TEXT,source_page INTEGER);
            INSERT INTO papers VALUES (1,'Incorrect preferred title','2020','10.1234/wrong','Wrong Author');
            INSERT INTO sources VALUES ('source','https://example.org/report.pdf');
            INSERT INTO chapters VALUES ('chapter','AR5','Chapter 1','source');
            INSERT INTO references_occurrence VALUES ('raw-1',1,'chapter','Original Author (2012). Original climate title. DOI 10.1234/original.','matched_doi',1);
        """)
    assert not search_database(database,"10.1234/original","preferred_doi")["results"]
    raw = search_database(database,"10.1234/original","raw_reference")
    assert [r["paper_id"] for r in raw["results"]] == [1]
    assert "original raw_reference" in raw["search_basis"]
    author = search_database(database,"Original Author","raw_reference","2012")
    assert [r["paper_id"] for r in author["results"]] == [1]


def test_known_weighted_example_and_targeted_separation(bundle):
    a, b = save_labels(bundle, *completed_labels(bundle))
    result = tools.estimate(bundle, a, b)
    rates = {r["metric"]: r for r in result["rates"]}
    assert result["status"] == "all_planned_estimates_ready"
    assert rates["ID_false_merge_rate"]["rate"] == pytest.approx(15/31)
    assert rates["within_ID_identity_precision"]["rate"] == pytest.approx(16/31)
    assert rates["ID_any_metadata_error_rate"]["rate"] == pytest.approx(5/31)
    assert rates["audited_ID_confirmed_missed_merge_rate"]["rate"] == pytest.approx(10/31)
    assert rates["shared_ID_false_shared_rate"]["rate"] == pytest.approx(5/11)
    assert result["targeted_descriptive"]["IDs"] == 1
    assert result["targeted_descriptive"]["identity_labels"] == {"false_merge": 1}
    for r in result["recurrence_stress"]:
        assert r["AR5_denominator_fixed"] == 31 and r["AR6_denominator_fixed"] == 11
        assert r["retention_stress"] == pytest.approx(6/31)
        assert r["reuse_stress"] == pytest.approx(6/11)


def test_shuffled_rows_and_identical_duplicates_preserve_weights(bundle):
    ids, entries = completed_labels(bundle)
    a, b = save_labels(bundle, ids, entries)
    baseline = tools.estimate(bundle, a, b)
    rng = random.Random(20261011)
    rng.shuffle(ids); rng.shuffle(entries)
    ids.append(deepcopy(ids[0])); entries.append(deepcopy(entries[0]))
    a, b = save_labels(bundle, ids, entries)
    shuffled = tools.estimate(bundle, a, b)
    assert shuffled["rates"] == baseline["rates"]
    assert shuffled["recurrence_stress"] == baseline["recurrence_stress"]
    assert shuffled["integrity"]["observed"]["paper_id"]["consistent_duplicate_rows"] == 1


@pytest.mark.parametrize("mutation", ["invalid_label", "altered_weight", "missing_key", "conflicting_duplicate", "false_shared_contradiction"])
def test_integrity_errors_block_estimates(bundle, mutation):
    ids, entries = completed_labels(bundle)
    if mutation == "invalid_label":
        ids[0]["identity_label"] = "probably_correct"
    elif mutation == "altered_weight":
        ids[0]["design_weight"] = "7"
    elif mutation == "missing_key":
        entries.pop()
    elif mutation == "conflicting_duplicate":
        duplicate = deepcopy(ids[0]); duplicate["review_notes"] = "A different annotation"
        ids.append(duplicate)
    elif mutation == "false_shared_contradiction":
        ids[0]["shared_link_label"] = "same_work"
    result = tools.estimate(bundle, *save_labels(bundle, ids, entries))
    assert result["status"] == "invalid"
    assert result["integrity"]["errors"]
    assert not result["rates"] and not result["recurrence_stress"]


def test_within_id_completion_does_not_imply_no_missed_merges(bundle):
    ids, entries = completed_labels(bundle)
    for row in ids:
        row["missed_merge_search_status"] = "incomplete"
        row["missed_merge_result"] = ""
    output = bundle/"incomplete_results"
    result = tools.estimate(bundle, *save_labels(bundle, ids, entries),output=output)
    assert result["status"] == "pending_annotation"
    assert result["rates"] == result["recurrence_stress"] == []
    assert {p.name for p in output.iterdir()} == {"annotation_integrity.json"}
    assert not result["integrity"]["readiness"]["probability_missed_merge_search"]


def test_targeted_search_incomplete_also_blocks_default_estimator(bundle):
    ids, entries = completed_labels(bundle)
    ids[-1]["missed_merge_search_status"] = "incomplete"
    result = tools.estimate(bundle,*save_labels(bundle,ids,entries))
    assert result["status"] == "pending_annotation" and not result["rates"]
    assert result["integrity"]["readiness"]["probability_missed_merge_search"]
    assert not result["integrity"]["readiness"]["targeted_missed_merge_search"]


def test_search_completion_requires_adequate_search_and_candidate_evidence(bundle):
    ids, entries = completed_labels(bundle)
    ids[0]["missed_merge_search_methods"] = "candidate_raw_entries"
    ids[3]["missed_merge_candidate_ids"] = ""
    result = tools.estimate(bundle, *save_labels(bundle, ids, entries))
    assert result["status"] == "invalid" and not result["rates"]
    assert any("DOI/title" in e for e in result["integrity"]["errors"])
    assert any("confirmed missed merge" in e for e in result["integrity"]["errors"])


def test_hypergeometric_census_zero_errors_and_singleton():
    assert tools.finite_binary_interval(10, 10, 3, .05) == (3, 3)
    low, high = tools.finite_binary_interval(100, 12, 0, .05)
    assert low == 0 and 0 < high < 100
    low, high = tools.finite_binary_interval(100, 12, 12, .05)
    assert 0 < low < 100 and high == 100
    assert tools.finite_binary_interval(2, 1, 0, .05) == (0, 1)
    rate, _ = tools.stratified_binary([
        {"stratum": "singleton", "population_ids": "10", "sampled_ids": "1"},
        {"stratum": "other", "population_ids": "20", "sampled_ids": "2"}],
        [{"paper_id": "1", "stratum": "singleton"}, {"paper_id": "2", "stratum": "other"}, {"paper_id": "3", "stratum": "other"}],
        lambda r: 0, metric="synthetic_zero_errors")
    assert rate["rate"] == 0 and rate["upper_95"] > 0
    assert rate["design_standard_error"] is None


def test_exact_small_population_interval_has_nominal_or_higher_coverage():
    N, n, alpha = 9, 3, .05
    for K in range(N+1):
        coverage = 0.0
        for k in range(n+1):
            lower, upper = tools.finite_binary_interval(N, n, k, alpha)
            if lower <= K <= upper:
                mass = tools.hypergeom_tail(N, K, n, k, False)-tools.hypergeom_tail(N, K, n, k-1, False)
                coverage += mass
        assert coverage >= 1-alpha-1e-12


def test_original_sources_and_empty_templates_are_immutable(bundle):
    hashes = {p: tools.digest(p) for p in (bundle/"inputs").iterdir()}
    templates = {p: tools.digest(p) for p in (bundle/"templates").iterdir()}
    store = AnnotationStore(bundle)
    store.save({"paper_id": "1", "id_labels": {"identity_label": "unresolved"}, "occurrence_labels": {}})
    assert all(tools.digest(p) == h for p, h in {**hashes, **templates}.items())
    assert AnnotationStore(bundle).state["ids"]["1"]["identity_label"] == "unresolved"
    with pytest.raises(ValueError, match="Only annotation fields"):
        store.save({"paper_id": "1", "id_labels": {"design_weight": "1"}})
    with pytest.raises(ValueError, match="outside this sampled ID"):
        store.save({"paper_id": "1", "occurrence_labels": {"AR5-2": {"work_groups": "A"}}})


def test_local_http_autosave_export_and_token_guard(bundle):
    store = AnnotationStore(bundle)
    server = make_server(store, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        data = json.load(urlopen(base+"/api/dataset"))
        payload = json.dumps({"paper_id": "1", "id_labels": {"annotator": "synthetic-reviewer"}}).encode()
        with pytest.raises(HTTPError) as error:
            urlopen(Request(base+"/api/save", payload, {"Content-Type": "application/json"}))
        assert error.value.code == 403
        saved = json.load(urlopen(Request(base+"/api/save", payload, {"Content-Type": "application/json", "X-Annotation-Token": data["token"]})))
        assert saved["saved"] and saved["integrity"]["status"] == "pending_annotation"
        exported = urlopen(base+"/export/id_annotations.csv").read().decode()
        assert "synthetic-reviewer" in exported and "design_weight" in exported
        with pytest.raises(HTTPError) as error:
            urlopen(Request(base+"/api/dataset", headers={"Host": "remote.example"}))
        assert error.value.code == 403
    finally:
        server.shutdown(); server.server_close(); thread.join()


def ai_proposals(root):
    """Synthetic suggestions, separate from all production annotations."""
    ids = tools.read_csv(root/"templates/id_annotations.csv")
    entries = tools.read_csv(root/"templates/occurrence_annotations.csv")
    cases = []
    for row in ids:
        pid = row["paper_id"]
        cases.append({"paper_id": int(pid), "annotator": "Codex (AI-assisted initial review)",
            "review_complete": "no", "identity_label": "correct_merge",
            "shared_link_label": "same_work" if tools.is_shared(row) else "not_shared",
            "review_notes": "Synthetic AI suggestion. Check the sources before confirming.",
            "missed_merge_search_status": "incomplete", "missed_merge_result": "unresolved",
            "missed_merge_candidate_ids": [123], "missed_merge_search_notes": "Synthetic offline retrieval.",
            "occurrences": [{"occurrence_id": r["occurrence_id"], "work_groups": "A",
                "metadata_judgment": "correct", "metadata_error_fields": "",
                "review_notes": "Synthetic suggested occurrence check.", "evidence_urls": ["https://example.org/source"]}
                for r in entries if r["paper_id"] == pid]})
    payload = {"schema_version": "ipcc-ai-initial-review-1", "review_type": "ai_assisted_initial_review",
               "human_validated": False, "cases": cases}
    path = root/"ai_review"/"proposals.json"; path.parent.mkdir(exist_ok=True)
    tools.write_json(path, payload)
    return payload


def test_ai_proposals_are_read_only_and_missing_file_is_normal(bundle):
    store = AnnotationStore(bundle)
    assert store.ai_review() == {"available": False, "human_validated": False, "cases": []}
    ai_proposals(bundle)
    before = {p: tools.digest(p) for p in (bundle/"work").iterdir()}
    response = store.ai_review()
    assert response["available"] and not response["human_validated"]
    assert response["case_count"] == 6
    assert all(tools.digest(p) == h for p, h in before.items())
    assert all(not row["identity_label"] for row in store.state["ids"].values())


@pytest.mark.parametrize("problem", ["wrong_provenance", "claimed_human_validation", "unknown_id", "duplicate_id", "missing_id", "missing_occurrence", "wrong_occurrence", "duplicate_occurrence", "completed_AI", "invalid_label"])
def test_ai_proposal_keys_and_provenance_must_match_frozen_sample(bundle, problem):
    store = AnnotationStore(bundle)
    payload = ai_proposals(bundle)
    if problem == "wrong_provenance": payload["review_type"] = "human_review"
    elif problem == "claimed_human_validation": payload["human_validated"] = True
    elif problem == "unknown_id": payload["cases"][0]["paper_id"] = 999999
    elif problem == "duplicate_id": payload["cases"][1]["paper_id"] = payload["cases"][0]["paper_id"]
    elif problem == "missing_id": payload["cases"].pop()
    elif problem == "missing_occurrence": payload["cases"][0]["occurrences"].pop()
    elif problem == "wrong_occurrence": payload["cases"][0]["occurrences"][0]["occurrence_id"] = "AR5-2"
    elif problem == "duplicate_occurrence": payload["cases"][0]["occurrences"].append(payload["cases"][0]["occurrences"][0])
    elif problem == "completed_AI": payload["cases"][0]["review_complete"] = "yes"
    elif problem == "invalid_label": payload["cases"][0]["identity_label"] = "automatic_gold"
    tools.write_json(bundle/"ai_review"/"proposals.json", payload)
    with pytest.raises(ValueError):
        store.ai_review()


def test_ai_draft_copies_only_blank_fields_and_never_completes_review_or_search(bundle):
    store = AnnotationStore(bundle)
    payload = ai_proposals(bundle)
    payload["cases"][0]["missed_merge_search_status"] = "complete"
    tools.write_json(bundle/"ai_review"/"proposals.json", payload)
    store.save({"paper_id": "1", "id_labels": {"annotator": "JZ", "review_notes": "Existing author note", "identity_label": "unresolved"},
        "occurrence_labels": {"AR5-1": {"annotator": "JZ", "review_notes": "Existing occurrence note", "metadata_judgment": "unresolved"}}})
    result = store.use_ai_draft("1")
    assert result["draft_only"] and not result["human_validated"]
    row = store.state["ids"]["1"]
    assert row["annotator"] == "JZ" and row["review_notes"] == "Existing author note" and row["identity_label"] == "unresolved"
    assert row["review_complete"] == "no" and row["missed_merge_search_status"] == "incomplete"
    assert row["missed_merge_candidate_ids"] == "123"
    assert store.state["occurrences"]["AR5-1"]["review_notes"] == "Existing occurrence note"
    assert store.state["occurrences"]["AR5-1"]["metadata_judgment"] == "unresolved"
    assert store.state["occurrences"]["AR6-1"]["annotator"] == "Codex (AI-assisted initial review)"
    assert store.state["occurrences"]["AR6-1"]["evidence_urls"] == "https://example.org/source"
    log = [json.loads(line) for line in (bundle/"work/change_log.jsonl").read_text().splitlines()]
    assert log[-1]["origin"] == "ai_proposal_draft"


def test_ai_draft_will_not_modify_an_already_completed_human_id(bundle):
    store = AnnotationStore(bundle)
    ai_proposals(bundle)
    ids, occurrences = completed_labels(bundle)
    row = next(r for r in ids if r["paper_id"] == "1")
    store.save({"paper_id": "1", "id_labels": {k: row[k] for k in tools.ID_FIELDS},
        "occurrence_labels": {r["occurrence_id"]: {k: r[k] for k in tools.OCCURRENCE_FIELDS}
                              for r in occurrences if r["paper_id"] == "1"}})
    before = deepcopy(store.state)
    with pytest.raises(ValueError, match="already has a completed review"):
        store.use_ai_draft("1")
    assert store.state == before


def test_backend_provenance_guard_requires_actual_reviewer_after_AI_draft(bundle):
    store = AnnotationStore(bundle)
    ai_proposals(bundle)
    store.use_ai_draft("1")
    before = deepcopy(store.state)
    with pytest.raises(ValueError, match="actual reviewer's name or code"):
        store.save({"paper_id": "1", "id_labels": {"review_complete": "yes"}})
    assert store.state == before
    with pytest.raises(ValueError, match="actual reviewer's name or code"):
        store.save({"paper_id": "1", "id_labels": {"annotator": "JZ", "review_complete": "yes"}})
    store.save({"paper_id": "1", "id_labels": {"annotator": "JZ", "review_complete": "yes", "review_notes": "Author checked the sources with AI assistance disclosed."},
        "occurrence_labels": {oid: {"annotator": "JZ"} for oid, row in store.occurrences.items() if row["paper_id"] == "1"}})
    assert store.state["ids"]["1"]["review_complete"] == "yes"
    assert "AI assistance" in store.state["ids"]["1"]["review_notes"]


@pytest.mark.parametrize("annotator", ["Codex", "Codex (AI-assisted initial review)", "AI initial review", "ChatGPT"])
def test_CLI_estimator_rejects_AI_completion_even_if_imported_as_CSV(bundle, annotator):
    ids, entries = completed_labels(bundle)
    ids[0]["annotator"] = annotator
    result = tools.estimate(bundle, *save_labels(bundle, ids, entries))
    assert result["status"] == "invalid" and not result["rates"] and not result["recurrence_stress"]
    assert any("AI suggestions cannot complete" in e for e in result["integrity"]["errors"])
    assert result["integrity"]["completed_ids"] == 5


def test_AI_occurrence_provenance_also_blocks_CLI_but_AI_disclosure_notes_do_not(bundle):
    ids, entries = completed_labels(bundle)
    entries[0]["annotator"] = "Codex (AI-assisted initial review)"
    assert tools.estimate(bundle, *save_labels(bundle, ids, entries))["status"] == "invalid"
    entries[0]["annotator"] = "JZ"
    ids[0]["review_notes"] += " AI assistance was used, then the author checked the original sources."
    assert tools.estimate(bundle, *save_labels(bundle, ids, entries))["status"] == "all_planned_estimates_ready"


def test_AI_routes_are_read_only_unless_user_requests_a_token_protected_draft(bundle):
    store = AnnotationStore(bundle)
    server = make_server(store, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert json.load(urlopen(base+"/api/ai-review"))["available"] is False
        ai_proposals(bundle)
        assert json.load(urlopen(base+"/api/ai-review"))["available"] is True
        assert all(not r["identity_label"] for r in store.state["ids"].values())
        payload = json.dumps({"paper_id": "1"}).encode()
        with pytest.raises(HTTPError) as error:
            urlopen(Request(base+"/api/ai-review/draft", payload, {"Content-Type": "application/json"}))
        assert error.value.code == 403
        token = json.load(urlopen(base+"/api/dataset"))["token"]
        result = json.load(urlopen(Request(base+"/api/ai-review/draft", payload,
            {"Content-Type": "application/json", "X-Annotation-Token": token})))
        assert result["draft_only"] and not result["human_validated"]
        assert store.state["ids"]["1"]["review_complete"] == "no"
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_PDF_source_index_zero_links_to_first_page_without_changing_source_values():
    import os
    import re
    import shutil
    import subprocess
    page = (Path(__file__).resolve().parents[1]/"app.html").read_text()
    helper = re.search(r"function pdfPage\(value\)\{.*?\n\}", page, re.S).group(0)
    configured = os.environ.get("NODE_BINARY")
    node = shutil.which(configured) if configured else shutil.which("node")
    if not node:
        pytest.skip("Set NODE_BINARY or install Node.js to exercise the JavaScript PDF page helper")
    script = helper + "\nconsole.log(JSON.stringify([0,'0',1,'17',null,'',-1,'x',2.5].map(pdfPage)));"
    result = subprocess.run([node, "-e", script], text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == [1, 1, 2, 18, None, None, None, None, None]
