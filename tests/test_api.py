"""API contract tests. Skipped when the server is not running."""
import json
import os
import urllib.error
import urllib.request

import pytest

BASE = os.environ.get("API_BASE", "http://127.0.0.1:8077").rstrip("/")


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=120) as r:
        return r.status, json.loads(r.read().decode())


@pytest.fixture(scope="module", autouse=True)
def server_up():
    try:
        get("/api/health")
    except urllib.error.HTTPError:
        raise
    except urllib.error.URLError:
        pytest.skip("API server not running: ./scripts/dev.sh")


READ_PATHS = [
    "/api/health", "/api/statistics", "/api/reports", "/api/working-groups",
    "/api/chapters?report_id=AR6&limit=5", "/api/papers?size=3", "/api/papers/1",
    "/api/papers/1/citations", "/api/references?size=3", "/api/unresolved?size=3",
    "/api/data-quality", "/api/duplicates?limit=2", "/api/collection-progress",
    "/api/metrics", "/api/analysis", "/api/analysis/run", "/api/insights",
    "/api/networks", "/api/networks/chapter-chapter", "/api/ai/status",
    "/api/changelog",
]


@pytest.mark.parametrize("path", READ_PATHS)
def test_endpoint_returns_200(path):
    st, _ = get(path)
    assert st == 200


def test_statistics_totals_match_the_database(db_path):
    import sqlite3
    _, d = get("/api/statistics")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    assert d["totals"]["references"] == con.execute(
        "SELECT COUNT(*) FROM references_occurrence").fetchone()[0]
    assert d["totals"]["papers"] == con.execute("SELECT COUNT(*) FROM papers").fetchone()[0]


def test_bi_query_returns_sql_and_rows():
    _, d = get("/api/bi/query?metric=reference_count&dimension=report")
    assert "SELECT" in d["sql"].upper()
    assert d["rows"] and all("dim" in r and "value" in r for r in d["rows"])
    assert d["chart"] in ("bar", "hbar", "line", "kpi")


def test_bi_query_rejects_unknown_metric():
    with pytest.raises(urllib.error.HTTPError) as e:
        get("/api/bi/query?metric=drop_table")
    assert e.value.code == 400


def test_insights_return_english_text_and_evidence():
    _, en = get("/api/insights?lang=en")
    _, legacy_preference = get("/api/insights?lang=zh-CN")
    assert en and en == legacy_preference
    assert all(i["title"] and i["body"] and i["evidence"] for i in en)


def test_ai_status_is_graceful_without_key():
    _, d = get("/api/ai/status")
    assert "configured" in d
    if not d["configured"]:
        assert d["note"]


def test_export_csv_has_provenance_header():
    with urllib.request.urlopen(BASE + "/api/export/papers.csv?limit=5", timeout=120) as r:
        body = r.read().decode("utf-8-sig")
    first = body.split("\n")[0]
    assert first.startswith("# provenance:"), first
    assert "IPCC Literature Database" in first
    assert "filters:" in first
    assert len(body.strip().split("\n")) >= 3


def test_export_formats_available():
    for f in ("csv", "json", "xlsx", "bibtex", "ris"):
        with urllib.request.urlopen(BASE + f"/api/export/papers.{f}?limit=3", timeout=120) as r:
            assert r.status == 200
            assert len(r.read()) > 0


def test_export_rejects_unknown_format():
    with pytest.raises(urllib.error.HTTPError) as e:
        get("/api/export/papers.docx")
    assert e.value.code == 400


def test_network_payload_is_wellformed():
    _, d = get("/api/networks/chapter-chapter")
    assert d["nodes"] and d["edges"]
    ids = {n["id"] for n in d["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in d["edges"])
    assert "parameters" in d
