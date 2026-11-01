"""Browser smoke tests for every dashboard page and the English interface.

Requires playwright and a running server: ./scripts/dev.sh
"""
import os
import csv
import io
import json
import struct
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = os.environ.get("API_BASE", "http://127.0.0.1:8077").rstrip("/")

TABS = {
    "overview": "#kpis .kpi", "reports": "#tree-reports .trow",
    "literature": "#tbl-papers tbody tr", "bi": "#bi-result table tbody tr",
    "research": "#research-summary .kpi",
    "analytics": "#analytics-index table tbody tr", "insights": "#insight-feed .insight",
    "networks": "#net-svg canvas",  # rendered by Cytoscape.js
    "quality": "#quality-cards .kpi",
    "unresolved": "#tbl-unresolved tbody tr", "progress": "#tbl-progress tbody tr",
    "ai": "#ai-status", "settings": "#set-ai",
}


@pytest.fixture(scope="module")
def page():
    try:
        with urllib.request.urlopen(BASE + "/api/health", timeout=5) as response:
            assert response.status == 200
    except urllib.error.HTTPError:
        raise
    except urllib.error.URLError:
        pytest.skip("API server not running: ./scripts/dev.sh")
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch(headless=True)
        except playwright_api.Error as error:
            if "Executable doesn't exist" in str(error):
                pytest.skip("Chromium not installed: python -m playwright install chromium")
            raise
        pg = b.new_page(viewport={"width": 1700, "height": 1050}, locale="zh-CN")
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.goto(BASE + "/", wait_until="networkidle", timeout=120000)
        pg.wait_for_selector("#kpis .kpi", timeout=60000)
        pg.errors = errors
        yield pg
        b.close()


@pytest.mark.parametrize("tab,selector", TABS.items())
def test_tab_renders(page, tab, selector):
    page.click(f'#tabs button[data-view="{tab}"]')
    page.wait_for_selector(selector, timeout=120000)
    assert page.eval_on_selector_all(selector, "e=>e.length") > 0


def test_english_interface_ignores_legacy_language_preference(page):
    page.evaluate("localStorage.setItem('ipcc.lang', 'zh-CN')")
    page.goto(BASE + "/", wait_until="networkidle")
    page.wait_for_selector("#kpis .kpi")
    assert page.evaluate("document.documentElement.lang") == "en"
    assert page.evaluate("LOCALE") == "en"
    assert page.inner_text(".brand h1") == "IPCC Literature Database"
    assert page.inner_text("#tabs button[data-view=overview]") == "Overview"
    assert page.evaluate("nf(160029)") == "160,029"
    assert page.locator("#lang, #set-lang").count() == 0


def test_theme_switching(page):
    page.select_option("#theme", "light")
    page.wait_for_function("document.documentElement.dataset.theme === 'light'")
    assert page.evaluate("document.documentElement.dataset.theme") == "light"
    page.select_option("#theme", "dark")
    page.wait_for_function("document.documentElement.dataset.theme === 'dark'")
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    assert page.evaluate("localStorage.getItem('ipcc.theme')") == "dark"


def test_menu_has_english_labels(page):
    labels = page.locator("#tabs button").all_text_contents()
    assert labels == [
        "Overview", "Assessment Reports", "Literature", "BI Explorer", "Research Comparison", "Analytics",
        "Automated Insights", "Networks", "Data Quality", "Unresolved", "Progress",
        "AI Analysis", "Settings",
    ]


def downloaded_bytes(page, selector):
    with page.expect_download() as event:
        page.click(selector)
    return Path(event.value.path()).read_bytes()


def test_open_review_queue_shows_matched_candidates_and_exports_the_applied_queue(page):
    page.click('#tabs button[data-view="quality"]')
    page.wait_for_selector('#quality-cards [data-flag="open_review_cases"]')
    with page.expect_response(lambda response: "/api/unresolved?" in response.url and "review_queue=true" in response.url) as event:
        page.click('#quality-cards [data-flag="open_review_cases"]')
    queue = event.value.json()
    page.wait_for_function("UNRESOLVED_QUERY?.includes('review_queue=true')")
    assert page.is_checked("#u-review-queue")
    assert queue["review_queue"] is True and queue["total"] > 0
    assert all(row["review_status"] in {"open", "in_progress"} for row in queue["items"])
    assert any(row["match_status"] == "matched_doi" for row in queue["items"])
    assert "WoS · DOI" in page.inner_text("#tbl-unresolved")
    assert queue["items"][0]["review_reason"] in page.inner_text("#tbl-unresolved")
    text = downloaded_bytes(page, '[data-export="unresolved.csv"]').decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text.split("\n", 1)[1])))
    assert len(rows) == queue["total"]
    assert len({row["occurrence_id"] for row in rows}) == len(rows)
    assert all(row["review_status"] in {"open", "in_progress"} and row["review_reason"] for row in rows)
    page.uncheck("#u-review-queue")
    with page.expect_response(lambda response: "/api/unresolved?" in response.url and "review_queue=true" not in response.url) as event:
        page.click("#btn-u-search")
    unmatched = event.value.json()
    page.wait_for_function("UNRESOLVED_QUERY && !UNRESOLVED_QUERY.includes('review_queue=true')")
    assert unmatched["review_queue"] is False
    assert all(row["match_status"] == "unmatched" for row in unmatched["items"])


@pytest.mark.parametrize("chart", ["chart-reports", "chart-wg", "chart-year", "chart-completeness"])
def test_overview_chart_csv_matches_source_data(page, chart):
    page.click('#tabs button[data-view="overview"]')
    page.wait_for_function("id => CHART_EXPORTS.has(id)", arg=chart)
    text = downloaded_bytes(page, f'#{chart} [data-chart-download="csv"]').decode("utf-8-sig")
    provenance_line, body = text.split("\n", 1)
    provenance = json.loads(provenance_line.removeprefix("# provenance: "))
    expected = page.evaluate("id => CHART_EXPORTS.get(id).rows", chart)
    rows = list(csv.DictReader(io.StringIO(body)))
    assert len(rows) == len(expected) == provenance["row_count"]
    assert provenance["source"] == "/api/statistics"
    assert provenance["title"] and provenance["unit"] and provenance["source_generated_at"]
    for exported, source in zip(rows, expected):
        assert exported == {key: "" if value is None else str(value) for key, value in source.items()}


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_standalone_svg_has_styles_caption_and_provenance(page, theme):
    page.click('#tabs button[data-view="overview"]')
    page.select_option("#theme", theme)
    page.wait_for_function("document.documentElement.dataset.theme === '" + theme + "'")
    contents = downloaded_bytes(page, '#chart-reports [data-chart-download="svg"]').decode()
    root = ET.fromstring(contents)
    namespace = {"svg": "http://www.w3.org/2000/svg"}
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert root.find("svg:title", namespace).text == "References by Assessment Report"
    assert "Reference occurrences" in root.find("svg:desc", namespace).text
    provenance = json.loads(root.find("svg:metadata", namespace).text)
    assert provenance["source"] == "/api/statistics"
    assert "var(" not in contents
    assert "font-family" in contents
    assert float(root.attrib["width"]) > 300
    assert float(root.attrib["height"]) > 100
    assert page.eval_on_selector_all("#chart-reports .bar-label, #chart-wg .bar-label", "labels => labels.every(label => label.getBBox().x >= 0)")


def assert_png(contents):
    assert contents.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", contents[16:24])
    assert width > 300 and height > 100
    assert b"IEND" in contents


def test_overview_png_download_is_an_image(page):
    page.click('#tabs button[data-view="overview"]')
    assert_png(downloaded_bytes(page, '#chart-year [data-chart-download="png"]'))


def test_bi_downloads_keep_query_filters_and_results(page):
    page.click('#tabs button[data-view="bi"]')
    page.wait_for_selector("#bi-result table tbody tr")
    page.select_option("#bi-metric", "match_rate")
    page.select_option("#bi-dim", "report")
    page.select_option("#bi-report", "AR2")
    with page.expect_response(lambda response: "/api/bi/query?" in response.url) as event:
        page.click("#bi-run")
    expected = event.value.json()
    page.wait_for_function("BI_RESULT?.metric === 'match_rate' && BI_RESULT?.filters.report === 'AR2'")
    payload = json.loads(downloaded_bytes(page, '#bi-chart [data-chart-download="json"]'))
    assert payload["rows"] == expected["rows"]
    assert payload["provenance"]["sql"] == expected["sql"]
    assert payload["provenance"]["filters"]["report"] == "AR2"
    assert payload["provenance"]["metric"] == "match_rate"
    assert payload["provenance"]["axis_min"] == 0 and payload["provenance"]["axis_max"] == 100
    assert page.eval_on_selector("#bi-chart rect[data-tip]", "bar => Number(bar.getAttribute('width'))") < 20
    contents = downloaded_bytes(page, '#bi-chart [data-chart-download="svg"]').decode()
    root = ET.fromstring(contents)
    provenance = json.loads(root.find("{http://www.w3.org/2000/svg}metadata").text)
    assert provenance["sql"] == expected["sql"]
    assert "report=AR2" in contents
    assert_png(downloaded_bytes(page, '#bi-chart [data-chart-download="png"]'))


def test_bi_line_follows_chronological_year_order(page):
    page.click('#tabs button[data-view="bi"]')
    page.select_option("#bi-metric", "reference_count")
    page.select_option("#bi-dim", "publication_year")
    page.select_option("#bi-report", "")
    page.click("#bi-run")
    page.wait_for_function("BI_RESULT?.metric === 'reference_count' && BI_RESULT?.dimension === 'publication_year'")
    years = page.eval_on_selector_all("#bi-chart circle[data-tip]", "nodes => nodes.map(node => Number(node.dataset.tip.split(':')[0]))")
    assert len(years) > 10
    assert years == sorted(years)
    assert page.evaluate("valueLabel(null, '%')") == "—"
    assert page.evaluate("valueLabel(0, '%')") == "0%"


def test_network_downloads_include_visible_payload_and_image(page):
    page.click('#tabs button[data-view="networks"]')
    page.wait_for_selector("#net-svg canvas")
    page.wait_for_function("NET?.nodes.length > 0 && !document.querySelector('#net-export-json').disabled")
    payload = json.loads(downloaded_bytes(page, "#net-export-json"))
    expected = page.evaluate("NET")
    for key in ("nodes", "edges", "parameters", "node_count", "edge_count"):
        assert payload[key] == expected[key]
    for key, value in expected.get("provenance", {}).items():
        assert payload["provenance"][key] == value
    assert len(payload["positions"]) == len(payload["nodes"])
    assert payload["provenance"]["source"].startswith("/api/networks/")
    assert_png(downloaded_bytes(page, "#net-export-png"))


def test_network_caption_explains_truncated_view(page):
    caption = page.evaluate("""networkCaption({node_count: 18, edge_count: 300, parameters: {
        min_weight: 0.25, limit: 300, eligible_edge_count: 1234, truncated: true,
        omitted_edge_count: 934, node_population: 91, isolates_after_filters: 2,
        omitted_connected_nodes: 71
    }})""")
    assert "18 displayed nodes" in caption and "300 displayed edges" in caption
    assert "minimum overlap 0.25" in caption and "1,234 eligible edges" in caption
    assert "edge limit 300" in caption and "Truncated view: 934 eligible edges omitted" in caption
    assert "node_population" not in caption and "isolates_after_filters" not in caption


def test_record_downloads_use_applied_filters(page):
    page.click('#tabs button[data-view="literature"]')
    page.wait_for_selector("#tbl-papers tbody tr")
    page.select_option("#f-report", "AR6")
    page.select_option("#f-wg", "WG1")
    page.fill("#f-year-from", "2018")
    page.fill("#f-year-to", "2018")
    page.select_option("#f-minreports", "1")
    page.select_option("#f-sort", "title")
    with page.expect_response(lambda response: "/api/papers?" in response.url) as event:
        page.click("#btn-search")
    expected = event.value.json()
    page.wait_for_function("PAPER_QUERY?.includes('year_from=2018')")
    # Editing a control without applying it should not change the displayed export.
    page.fill("#f-year-from", "2020")
    payload = json.loads(downloaded_bytes(page, '[data-export="papers.json"]'))
    assert payload["rows"]
    assert [row["paper_id"] for row in payload["rows"][:25]] == [row["paper_id"] for row in expected["items"]]
    assert all(str(row["year_best"]) == "2018" and row["n_reports"] >= 2 for row in payload["rows"])
    assert len({row["paper_id"] for row in payload["rows"]}) == len(payload["rows"])
    page.click('#tabs button[data-view="unresolved"]')
    page.wait_for_selector("#tbl-unresolved tbody tr")
    page.select_option("#u-report", "AR2")
    page.check("#u-dionly")
    page.click("#btn-u-search")
    page.wait_for_function("UNRESOLVED_QUERY?.includes('report_id=AR2') && UNRESOLVED_QUERY?.includes('only_doi_less=true')")
    text = downloaded_bytes(page, '[data-export="unresolved.csv"]').decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text.split("\n", 1)[1])))
    assert rows and all(row["report_id"] == "AR2" and not row["doi_norm"] for row in rows)


def test_research_comparison_controls_and_downloads(page):
    page.click('#tabs button[data-view="research"]')
    page.wait_for_function("RESEARCH?.report_a.report_id === 'AR5' && RESEARCH?.report_b.report_id === 'AR6'")
    page.select_option("#research-a", "AR2")
    page.select_option("#research-b", "AR3")
    with page.expect_response(lambda response: "/api/research/compare?" in response.url) as event:
        page.click("#research-run")
    expected = event.value.json()
    page.wait_for_function("RESEARCH?.report_a.report_id === 'AR2' && RESEARCH?.report_b.report_id === 'AR3'")
    payload = json.loads(downloaded_bytes(page, "#research-json"))
    for key in ("report_a", "report_b", "overlap", "by_year", "journal_distribution", "top_shared_papers"):
        assert payload[key] == expected[key]
    assert "Jaccard = shared / union" in page.inner_text("#research-definitions")
    assert str(expected["overlap"]["shared_papers"]) in page.inner_text("#research-summary").replace(",", "")
    assert payload["provenance"]["queries"] and payload["provenance"]["endpoint"].startswith("/api/research/compare?")
    contents = downloaded_bytes(page, '#research-year [data-chart-download="csv"]').decode("utf-8-sig")
    metadata, body = contents.split("\n", 1)
    provenance = json.loads(metadata.removeprefix("# provenance: "))
    rows = list(csv.DictReader(io.StringIO(body)))
    plotted = [row for row in expected["by_year"] if row["year"] >= 1850]
    assert [int(row["year"]) for row in rows] == [row["year"] for row in plotted]
    assert [int(row["count_a"]) for row in rows] == [row["count_a"] for row in plotted]
    assert provenance["filters"] == {"report_a": "AR2", "report_b": "AR3", "year_from": 1850, "year_to": None}
    svg = downloaded_bytes(page, '#research-year [data-chart-download="svg"]').decode()
    root = ET.fromstring(svg)
    assert len([node for node in root.iter() if node.tag.endswith("}polyline")]) == 2
    assert "AR2" in svg and "AR3" in svg and "Unique papers" in svg
    assert_png(downloaded_bytes(page, '#research-year [data-chart-download="png"]'))
    journals = json.loads(downloaded_bytes(page, '#research-journals [data-chart-download="json"]'))
    assert journals["rows"] == expected["journal_distribution"]
    shared = json.loads(downloaded_bytes(page, '#research-shared [data-chart-download="json"]'))
    assert shared["rows"] == expected["top_shared_papers"]
    assert shared["provenance"]["shared_paper_limit"] == 20


def test_research_year_range_changes_plot_and_export_only(page):
    page.click('#tabs button[data-view="research"]')
    page.wait_for_function("RESEARCH && !document.querySelector('#research-run').disabled")
    original = page.evaluate("RESEARCH")
    page.fill("#research-year-from", "1990")
    page.fill("#research-year-to", "2000")
    page.click("#research-year-apply")
    data = json.loads(downloaded_bytes(page, '#research-year [data-chart-download="json"]'))
    assert data["rows"] == [row for row in original["by_year"] if 1990 <= row["year"] <= 2000]
    assert data["provenance"]["filters"]["year_from"] == 1990
    assert data["provenance"]["filters"]["year_to"] == 2000
    assert "Outside this range" in page.inner_text("#research-year-note")
    full = json.loads(downloaded_bytes(page, "#research-json"))
    assert full["by_year"] == original["by_year"]
    assert full["provenance"]["view"]["publication_year_range"] == {"from": 1990, "to": 2000}
    page.click("#research-year-all")
    assert page.evaluate("CHART_EXPORTS.get('research-year').rows") == original["by_year"]


def test_research_empty_cohort_preserves_undefined_ratios(page):
    page.click('#tabs button[data-view="research"]')
    page.wait_for_function("RESEARCH && !document.querySelector('#research-run').disabled")
    page.select_option("#research-a", "AR7")
    page.select_option("#research-b", "AR6")
    page.click("#research-run")
    page.wait_for_function("RESEARCH?.report_a.report_id === 'AR7' && RESEARCH?.report_b.report_id === 'AR6'")
    payload = json.loads(downloaded_bytes(page, "#research-json"))
    assert payload["report_a"]["unique_papers"] == 0
    assert payload["overlap"]["share_of_a"] is None
    assert payload["overlap"]["overlap_coefficient"] is None
    assert "—" in page.inner_text("#research-summary")


def test_live_audit_runs_on_request_and_downloads_evidence(page):
    page.click('#tabs button[data-view="quality"]')
    assert page.evaluate("AUDIT_RESULT") is None
    with page.expect_response(lambda response: "/api/data-quality/audit" in response.url) as event:
        page.click("#audit-run")
    assert event.value.request.method == "GET"
    expected = event.value.json()
    page.wait_for_selector("#audit-checks tbody tr")
    payload = json.loads(downloaded_bytes(page, "#audit-json"))
    assert payload["checks"] == expected["checks"]
    assert payload["by_report"] == expected["by_report"]
    assert len(expected["checks"]) >= 15
    assert page.locator("#audit-checks tbody tr").count() == len(expected["checks"])
    assert page.locator("#audit-reports tbody tr").count() == len(expected["by_report"])
    assert all(check["definition"] and check["grain"] and check["sql"] for check in payload["checks"])
    assert payload["provenance"]["endpoint"] == "/api/data-quality/audit"
    assert "Read-only database observations" in page.inner_text("#audit-status")


def test_network_analysis_controls_and_layouts(page):
    page.click('#tabs button[data-view="networks"]')
    page.wait_for_function("NET?.analysis && !NET_BUSY")
    page.select_option("#net-kind", "report-report")
    page.wait_for_function("NET?.parameters.kind === 'report-report' && !NET_BUSY")
    page.select_option("#net-limit", "2000")
    page.wait_for_function("NET?.parameters.limit === 2000 && !NET_BUSY")
    data = page.evaluate("NET")
    assert data["analysis"]["scope"] == "displayed_graph"
    assert data["analysis"]["graph"]["node_count"] == data["node_count"]
    assert page.locator("#net-analysis-stats .kpi").count() >= 8
    assert "displayed" in page.inner_text("#net-analysis-note").lower()
    page.select_option("#net-color", "community")
    assert "Community" in page.inner_text("#net-legend")
    assert page.evaluate("NET.nodes.every(node => CY.getElementById(node.id).data('colour') === networkColor(node, document.documentElement.dataset.theme === 'dark'))")
    positions = []
    for layout in ("circle", "breadthfirst", "concentric", "cose"):
        page.select_option("#net-layout", layout)
        current = page.evaluate("CY.nodes().map(node => node.position())")
        assert len(current) == data["node_count"]
        assert all(isinstance(position[axis], (int, float)) for position in current for axis in ("x", "y"))
        positions.append(current)
    assert positions[0] != positions[1]
    page.select_option("#net-ranking", "betweenness")
    assert "unweighted shortest paths" in page.inner_text("#net-ranking-note")
    ranked = data["analysis"]["rankings"]["betweenness"]
    assert page.locator("#net-top-nodes tbody tr").count() == len(ranked)
    assert page.locator("#net-top-nodes tbody tr").first.locator("td").first.inner_text() == ranked[0]["label"]
    assert_png(downloaded_bytes(page, "#net-export-png"))


@pytest.mark.parametrize("format", ["graphml", "gexf", "csv"])
def test_network_interchange_downloads_match_displayed_graph(page, format):
    page.click('#tabs button[data-view="networks"]')
    page.wait_for_function("NET?.analysis && !NET_BUSY")
    data = page.evaluate("NET")
    contents = downloaded_bytes(page, f'[data-network-download="{format}"]')
    if format == "csv":
        rows = list(csv.DictReader(io.StringIO(contents.decode("utf-8-sig"))))
        assert len(rows) == data["edge_count"]
        expected = {frozenset((str(edge["source"]), str(edge["target"]))): edge["weight"] for edge in data["edges"]}
        for row in rows:
            assert row["Type"] == "Undirected"
            assert float(row["Weight"]) == pytest.approx(expected[frozenset((row["Source"], row["Target"]))])
            provenance = json.loads(row["provenance"])
            assert provenance["parameters"]["limit"] == data["parameters"]["limit"]
        return
    root = ET.fromstring(contents)
    nodes = [element for element in root.iter() if element.tag.rsplit("}", 1)[-1] == "node"]
    edges = [element for element in root.iter() if element.tag.rsplit("}", 1)[-1] == "edge"]
    assert len(nodes) == data["node_count"] and len(edges) == data["edge_count"]
    assert {node.attrib["id"] for node in nodes} == {str(node["id"]) for node in data["nodes"]}
    assert b"community" in contents and b"betweenness" in contents


def test_network_stale_responses_cannot_replace_newer_settings(page):
    page.click('#tabs button[data-view="networks"]')
    page.wait_for_function("NET?.analysis && !NET_BUSY")
    page.select_option("#net-kind", "report-report")
    page.wait_for_function("NET?.parameters.kind === 'report-report' && !NET_BUSY")
    page.evaluate("""async () => {
        const original = window.fetch;
        window.fetch = async (input, options) => {
            const response = await original(input, {...options, signal: undefined});
            if (String(input).includes('/api/networks/report-report?limit=100&'))
                await new Promise(resolve => setTimeout(resolve, 300));
            return response;
        };
        try {
            document.querySelector('#net-limit').value = '100'; const first = loadNetwork(false);
            document.querySelector('#net-limit').value = '500'; const second = loadNetwork(false);
            await Promise.all([first, second]);
        } finally { window.fetch = original; }
    }""")
    assert page.evaluate("NET.parameters.limit") == 500
    assert "limit=500" in page.evaluate("NET_SOURCE")
    assert "edge limit 500" in page.inner_text("#net-stats")
    assert page.evaluate("NET_BUSY") is False


def test_no_console_errors(page):
    assert page.errors == [], page.errors
