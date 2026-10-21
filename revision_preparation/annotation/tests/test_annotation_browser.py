"""Browser workflow in a disposable work directory; production labels stay blank."""
import os
import json
from pathlib import Path
import shutil
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from annotation_tools import ROOT, MATCHED_STATUS_SETS, read_csv
from local_annotation_app import AnnotationStore, locate_database, make_server

playwright = pytest.importorskip("playwright.sync_api")


def test_annotation_browser_filters_autosave_reload_export_and_search(tmp_path):
    database = Path(os.environ["ANNOTATION_DB"]) if os.environ.get("ANNOTATION_DB") else locate_database()
    if database is None or not database.is_file():
        pytest.skip("Set ANNOTATION_DB to the saved snapshot for the full-database browser test")
    for name in ("inputs", "templates"):
        shutil.copytree(ROOT/name, tmp_path/name)
    shutil.copyfile(ROOT/"input_manifest.json", tmp_path/"input_manifest.json")
    store = AnnotationStore(tmp_path)
    server = make_server(store, database, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    errors = []
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1600, "height": 1050})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base, wait_until="networkidle")
            page.wait_for_selector(".queue-item")
            assert page.locator(".queue-item").count() == store.manifest["counts"]["sampled_ids"]
            assert page.locator(".occurrence").count() > 0
            page.select_option("#filter-sample", "targeted_supplement_not_probability_sample")
            assert page.locator(".queue-item").count() == store.manifest["counts"]["targeted_ids"]
            page.select_option("#filter-sample", "stratified_hash_sample")
            assert page.locator(".queue-item").count() == store.manifest["counts"]["probability_ids"]
            page.select_option("#filter-sample", "all")
            page.fill("#reviewer", "SMOKE TEST")
            page.fill("#id-review_notes", "Temporary synthetic UI test. This is not a scientific annotation.")
            page.click("#one-work"); page.click("#metadata-correct")
            page.select_option("#id-identity_label", "correct_merge")
            pid = page.evaluate("activeId")
            paper = next(r for r in store.id_rows if r["paper_id"] == pid)
            shared = all(paper[k] in MATCHED_STATUS_SETS for k in ("AR5_status_set", "AR6_status_set"))
            page.select_option("#id-shared_link_label", "same_work" if shared else "not_shared")
            page.select_option("#id-review_complete", "yes")
            playwright.expect(page.locator("#save-status")).to_have_text("Saved locally")
            assert not store.integrity()["errors"]
            assert store.state["ids"][pid]["review_complete"] == "yes"
            page.reload(wait_until="networkidle")
            assert page.input_value("#id-review_notes").startswith("Temporary synthetic")
            assert page.input_value("#id-annotator") == "SMOKE TEST"
            page.click("#check-integrity")
            page.wait_for_selector("#integrity-dialog[open]")
            assert "1 / 319" in page.inner_text("#integrity-results")
            page.click("#close-integrity")
            page.click("#open-search")
            page.select_option("#search-mode","title")
            page.fill("#search-q", paper["title_best"][:55])
            page.click("#search-form button")
            page.wait_for_selector(".search-result", timeout=30000)
            assert page.locator(".search-result").count() > 0
            page.select_option("#search-mode","raw_reference")
            page.fill("#search-q","climate")
            page.click("#search-form button")
            playwright.expect(page.locator("#search-results")).to_contain_text("Preserved original raw_reference")
            assert page.locator(".search-result").count() > 0
            page.click("#close-search")
            page.screenshot(path=str(tmp_path/"annotation_workspace.png"))
            page.set_viewport_size({"width": 650, "height": 950})
            assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
            assert not errors
            browser.close()
        exported = read_csv(tmp_path/"work/id_annotations.csv")
        assert next(r for r in exported if r["paper_id"] == pid)["review_complete"] == "yes"
        assert all(not r["identity_label"] for r in read_csv(ROOT/"templates/id_annotations.csv"))
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_AI_panel_is_separate_until_explicit_draft_and_human_confirmation(tmp_path):
    for name in ("inputs", "templates"):
        shutil.copytree(ROOT/name, tmp_path/name)
    shutil.copyfile(ROOT/"input_manifest.json", tmp_path/"input_manifest.json")
    store = AnnotationStore(tmp_path)
    first, second, third = store.id_rows[:3]
    cases = []
    for paper in store.id_rows:
        pid = paper["paper_id"]
        shared = all(paper[k] in MATCHED_STATUS_SETS for k in ("AR5_status_set", "AR6_status_set"))
        cases.append({"paper_id": pid, "annotator": "Codex (AI-assisted initial review)", "review_complete": "no",
            "identity_label": "false_merge" if pid == second["paper_id"] else "correct_merge",
            "shared_link_label": "same_work" if shared else "not_shared", "review_notes": "Synthetic AI UI fixture; not a scientific label.",
            "missed_merge_search_status": "incomplete", "missed_merge_result": "missed_merge_found" if pid == first["paper_id"] else "unresolved",
            "missed_merge_candidate_ids": ["999999"] if pid == first["paper_id"] else [],
            "unresolved_candidate_ids": ["888888"] if pid == third["paper_id"] else [],
            "unresolved_author_questions": ["Check the printed publication year."] if pid == first["paper_id"] else [],
            "missed_merge_search_notes": "Synthetic retrieval notes; source checks pending.",
            "additional_source_locations": [{"candidate_paper_id": "999999", "candidate_occurrence_id": "synthetic-candidate",
                "recorded_start_page_zero_based": 0, "located_page_zero_based": 1, "viewer_page_one_based": 2}]
                if pid == first["paper_id"] else [],
            "candidate_judgments": [{"candidate_paper_id": "999999", "verdict": "unresolved", "reason": "Synthetic candidate comparison.",
                "original_entries_inspected": [{"occurrence_id": "synthetic-candidate", "report_id": "AR6", "source_page": 0,
                    "source_file_url": "https://example.org/source.pdf", "raw_reference": "Synthetic candidate citation <script>literal text</script>."}]}],
            "occurrences": [{"occurrence_id": r["occurrence_id"], "work_groups": "A", "metadata_judgment": "correct",
                             "metadata_unresolved_fields": ["year"] if pid == first["paper_id"] else [],
                             "metadata_error_fields": "", "review_notes": "Synthetic AI suggestion."}
                            for r in store.occurrence_rows if r["paper_id"] == pid]})
    folder = tmp_path/"ai_review"; folder.mkdir()
    (folder/"proposals.json").write_text(json.dumps({"schema_version": "ipcc-ai-initial-review-1", "review_type": "ai_assisted_initial_review",
        "human_validated": False, "cases": cases}))
    server = make_server(store, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    errors = []
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width":1600,"height":1050})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{server.server_port}", wait_until="networkidle")
            playwright.expect(page.locator("#ai-panel")).to_contain_text("Codex initial review. Awaiting your confirmation.")
            playwright.expect(page.get_by_text("Do these entries describe one study?", exact=True)).to_be_visible()
            playwright.expect(page.locator("#paper-panel")).to_contain_text("including verified publication versions")
            playwright.expect(page.locator("#paper-panel")).to_contain_text("Use the actually cited version’s year for age")
            playwright.expect(page.locator("#ai-panel")).to_contain_text("Same-study counterpart proposed")
            playwright.expect(page.locator("#ai-panel")).not_to_contain_text("Confirmed missed merge")
            playwright.expect(page.locator("#ai-panel")).to_contain_text("Metadata fields awaiting confirmation: year")
            playwright.expect(page.locator("#ai-panel")).to_contain_text("Check the printed publication year.")
            playwright.expect(page.locator("#progress")).to_contain_text("0 / 331 human ID reviews complete")
            playwright.expect(page.locator("#progress")).to_contain_text("331 AI suggestions available")
            assert all(not r["identity_label"] for r in store.state["ids"].values())
            page.select_option("#filter-progress","ai_issues")
            assert page.locator(".queue-item").count() == 3
            assert page.locator(".queue-item").evaluate_all("rows=>rows.map(r=>r.dataset.paper)") == [first["paper_id"],second["paper_id"],third["paper_id"]]
            page.select_option("#filter-progress","ai_unresolved")
            assert page.locator(".queue-item").count() == 2
            assert page.locator(".queue-item").evaluate_all("rows=>rows.map(r=>r.dataset.paper)") == [first["paper_id"],third["paper_id"]]
            page.locator(f'.queue-item[data-paper="{third["paper_id"]}"]').click()
            playwright.expect(page.locator("#ai-panel")).to_contain_text("Candidates awaiting confirmation: 888888")
            page.select_option("#filter-progress","all")
            page.locator(f'.queue-item[data-paper="{first["paper_id"]}"]').click()
            page.locator("#ai-panel summary").filter(has_text="Duplicate-search evidence").click()
            page.locator("#ai-panel summary").filter(has_text="Candidate ID 999999").click()
            playwright.expect(page.locator('#ai-panel a[href="https://example.org/source.pdf#page=1"]')).to_be_visible()
            page.locator("#ai-panel summary").filter(has_text="Inspected citation locations").click()
            playwright.expect(page.locator('#ai-panel a[href="https://example.org/source.pdf#page=2"]')).to_have_text("Inspected citation on PDF page 2")
            playwright.expect(page.locator("#ai-panel")).to_contain_text("Synthetic candidate citation <script>literal text</script>.")
            page.click("#use-ai-draft")
            playwright.expect(page.locator("#ai-draft-status")).to_contain_text("Draft copied")
            assert store.state["ids"][first["paper_id"]]["annotator"] == "Codex (AI-assisted initial review)"
            assert store.state["ids"][first["paper_id"]]["review_complete"] == "no"
            assert store.state["ids"][first["paper_id"]]["missed_merge_search_status"] == "incomplete"
            page.select_option("#id-review_complete","yes")
            playwright.expect(page.locator("#save-status")).to_contain_text("AI suggestions cannot complete")
            assert store.state["ids"][first["paper_id"]]["review_complete"] == "no"
            page.select_option("#id-review_complete","no")
            playwright.expect(page.locator("#save-status")).to_have_text("Saved locally")
            page.fill("#reviewer","SYNTHETIC HUMAN CHECK")
            playwright.expect(page.locator("#save-status")).to_have_text("Saved locally")
            page.select_option("#id-review_complete","yes")
            playwright.expect(page.locator("#save-status")).to_have_text("Saved locally")
            assert store.state["ids"][first["paper_id"]]["annotator"] == "SYNTHETIC HUMAN CHECK"
            assert store.state["ids"][first["paper_id"]]["review_complete"] == "yes"
            assert all(r["annotator"] == "SYNTHETIC HUMAN CHECK" for oid,r in store.state["occurrences"].items()
                       if store.occurrences[oid]["paper_id"] == first["paper_id"])
            assert not errors
            browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join()
