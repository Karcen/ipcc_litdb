"""Research routes and checksummed CLI exports on the packaged working input."""
from hashlib import sha256
import json
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient

import app
from analytics import cli


@pytest.fixture
def client(db_path):
    return TestClient(app.app)


def test_report_comparison_uses_distinct_cohorts(client):
    response = client.get("/api/research/compare", params={"report_a": "AR5", "report_b": "AR6", "top": 2})
    assert response.status_code == 200
    data = response.json()
    overlap = data["overlap"]
    assert overlap["shared_papers"] + overlap["only_a_papers"] == data["report_a"]["unique_papers"]
    assert overlap["shared_papers"] + overlap["only_b_papers"] == data["report_b"]["unique_papers"]
    assert len(data["top_shared_papers"]) <= 2
    assert data["provenance"]["queries"]


def test_quality_route_preserves_bounded_evidence(client):
    response = client.get("/api/data-quality/audit", params={"sample_limit": 2})
    assert response.status_code == 200
    data = response.json()
    assert data["checks"] and data["by_report"]
    for check in data["checks"]:
        assert len(check["samples"]) <= 2
        assert check["count"] <= check["denominator"]
        assert check["rate"] is None or 0 <= check["rate"] <= 100
        assert check["sql"] and check["sample_sql"] and check["grain"]


@pytest.mark.parametrize("fmt,root", [("graphml", "graphml"), ("gexf", "gexf")])
def test_graph_exports_are_xml_with_current_scope(client, fmt, root):
    response = client.get("/api/networks/report-report/export", params={"fmt": fmt, "limit": 3})
    assert response.status_code == 200
    document = ET.fromstring(response.content)
    assert document.tag.rsplit("}", 1)[-1] == root
    assert response.headers["X-Row-Count"] == "3"
    assert response.headers["X-Export-Limit"] == "3"
    assert "attachment;" in response.headers["content-disposition"]
    assert b"displayed_graph" in response.content


@pytest.mark.parametrize("path", [
    "/api/networks/report-report?limit=0",
    "/api/networks/report-report/export?limit=2001",
    "/api/data-quality/audit?sample_limit=0",
    "/api/research/compare?report_a=AR5&report_b=AR6&top=101",
])
def test_research_routes_reject_out_of_range_requests(client, path):
    assert client.get(path).status_code == 422


def test_unknown_cohort_and_format_have_clear_errors(client):
    assert client.get("/api/research/compare?report_a=unknown&report_b=AR6").status_code == 400
    assert client.get("/api/networks/report-report/export?fmt=unknown").status_code == 400


def test_bundle_manifest_matches_every_file_and_does_not_change_input(db_path, tmp_path):
    output = tmp_path / "research-export"
    before = sha256(db_path.read_bytes()).hexdigest()
    assert cli.cmd_bundle(output, "AR5", "AR6", database=db_path, limit=3, top=2) == 0
    assert sha256(db_path.read_bytes()).hexdigest() == before
    manifest = json.loads((output / "manifest.json").read_text())
    assert len(manifest["files"]) == 22
    for record in manifest["files"]:
        content = (output / record["path"]).read_bytes()
        assert len(content) == record["bytes"]
        assert sha256(content).hexdigest() == record["sha256"]
    assert not list(output.rglob("*.sqlite"))
    assert not list(tmp_path.glob(".ipcc-*"))
    with pytest.raises(ValueError, match="already exists"):
        cli.cmd_bundle(output, "AR5", "AR6", database=db_path, limit=3, top=2)


def test_bundle_failure_removes_partial_outputs(db_path, tmp_path):
    output = tmp_path / "research-export"
    with pytest.raises(ValueError):
        cli.cmd_bundle(output, "unknown", "AR6", database=db_path, limit=3)
    assert not output.exists()
    assert not list(tmp_path.glob(".ipcc-*"))


def test_cli_compare_writes_a_complete_document(db_path, tmp_path):
    output = tmp_path / "comparison.json"
    assert cli.main(["compare", "AR5", "AR6", "--database", str(db_path), "--top", "2",
                     "--output", str(output)]) == 0
    data = json.loads(output.read_text())
    assert data["report_a"]["report_id"] == "AR5"
    assert data["report_b"]["report_id"] == "AR6"
    assert data["generated_at"] and data["provenance"]
