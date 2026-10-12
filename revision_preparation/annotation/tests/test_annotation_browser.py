"""Browser workflow in a disposable work directory; production labels stay blank."""
import os
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
