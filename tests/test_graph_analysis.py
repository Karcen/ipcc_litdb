"""Displayed-graph metrics and GraphML/GEXF/CSV interoperability."""
from copy import deepcopy
import csv
import io
import json
from xml.etree import ElementTree as ET

import networkx as nx
import pytest

from analytics.graph_analysis import EXACT_NODE_LIMIT, MAX_SAMPLES, graph_download, summarise


def payload(node_ids, edges):
    return {
        "nodes": [{"id": node_id, "label": f"Node {node_id}", "kind": "paper", "size": 2} for node_id in node_ids],
        "edges": [{"source": source, "target": target, "weight": weight} for source, target, weight in edges],
        "parameters": {"kind": "paper-chapter", "limit": 300, "node_population": 100,
                       "isolates_after_filters": 12, "omitted_connected_nodes": 84,
                       "eligible_edge_count": 50, "truncated": True, "omitted_edge_count": 48},
        "provenance": {"weight_definition": "Distinct shared papers", "population_scope": "Fixture catalogue"},
    }


def test_path_metrics_use_displayed_graph_and_preserve_population():
    data = payload(["A", "B", "C"], [("A", "B", 2), ("B", "C", 5)])
    summary = summarise(data)
    metrics = {node["id"]: node for node in data["nodes"]}
    assert metrics["B"]["degree"] == 2
    assert metrics["B"]["strength"] == 7
    assert metrics["B"]["betweenness"] == 1
    assert metrics["A"]["betweenness"] == metrics["C"]["betweenness"] == 0
    assert summary["graph"]["node_count"] == 3
    assert summary["graph"]["edge_count"] == 2
    assert summary["graph"]["density"] == pytest.approx(2 / 3)
    assert summary["graph"]["average_degree"] == pytest.approx(4 / 3)
    assert summary["graph"]["average_strength"] == pytest.approx(14 / 3)
    assert summary["graph"]["total_edge_weight"] == 7
    assert summary["graph"]["connected_components"] == 1
    assert summary["population"]["node_population"] == 100
    assert summary["scope"] == "displayed_graph"
    assert summary["rankings"]["betweenness"][0]["id"] == "B"
    assert summary["methods"]["betweenness"]["weight"] is None


def test_strength_weights_are_not_used_as_shortest_path_distances():
    data = payload(["A", "B", "C"], [("A", "B", 1), ("B", "C", 1), ("A", "C", 100)])
    summarise(data)
    assert all(node["betweenness"] == 0 for node in data["nodes"])
    assert {node["id"]: node["strength"] for node in data["nodes"]} == {"A": 101, "B": 2, "C": 101}


@pytest.mark.parametrize("node_ids, expected_components, connected", [([], 0, None), (["solo"], 1, True), (["A", "B"], 2, False)])
def test_empty_and_isolated_nodes_have_defined_metrics(node_ids, expected_components, connected):
    data = payload(node_ids, [])
    summary = summarise(data)
    assert summary["graph"]["connected_components"] == expected_components
    assert summary["graph"]["isolates"] == len(node_ids)
    assert summary["graph"]["is_connected"] is connected
    assert summary["graph"]["community_count"] == len(node_ids)
    assert summary["graph"]["modularity"] is None
    assert summary["methods"]["communities"]["applied"] is False
    assert all(node["degree"] == node["strength"] == node["betweenness"] == 0 for node in data["nodes"])


def test_components_and_community_ids_are_stable_for_disconnected_bipartite_graph():
    data = payload(["D", "B", "A", "C", "isolated"], [("A", "B", 3), ("C", "D", 1)])
    for node in data["nodes"]:
        node["kind"] = "chapter" if node["id"] in {"B", "D"} else "paper"
    summary = summarise(data)
    assert [component["node_ids"] for component in summary["components"]] == [["A", "B"], ["C", "D"], ["isolated"]]
    assert [community["node_ids"] for community in summary["communities"]] == [["A", "B"], ["C", "D"], ["isolated"]]
    assert [community["id"] for community in summary["communities"]] == [1, 2, 3]
    assert summary["graph"]["largest_component_nodes"] == 2
    assert summary["graph"]["isolates"] == 1
    assert summary["graph"]["modularity"] == pytest.approx(0.375)


def test_zero_weight_edges_remain_in_topology_but_not_weighted_communities():
    data = payload(["A", "B", "C"], [("A", "B", 2), ("B", "C", 0)])
    summary = summarise(data)
    assert summary["graph"]["connected_components"] == 1
    assert summary["graph"]["community_count"] == 2
    assert summary["methods"]["communities"]["zero_weight_edges_ignored"] == 1
    assert {node["id"]: node["degree"] for node in data["nodes"]} == {"A": 1, "B": 2, "C": 1}


def test_large_graph_uses_bounded_reproducible_sampling_and_top20():
    nodes = [f"N{i:04}" for i in range(EXACT_NODE_LIMIT + 30)]
    data = payload(nodes, [(nodes[i], nodes[i + 1], 1) for i in range(len(nodes) - 1)])
    reversed_data = deepcopy(data)
    reversed_data["nodes"].reverse()
    reversed_data["edges"].reverse()
    first, second = summarise(data), summarise(reversed_data)
    assert first == second
    assert first["methods"]["betweenness"]["mode"] == "sampled"
    assert first["methods"]["betweenness"]["sample_size"] == MAX_SAMPLES
    assert first["methods"]["betweenness"]["seed"] == 42
    assert len(first["rankings"]["degree"]) == len(first["rankings"]["betweenness"]) == 20
    assert all(node["degree"] in (1, 2) for node in data["nodes"])


@pytest.mark.parametrize("weight", [float("nan"), float("inf"), -1, True, "2"])
def test_invalid_weights_are_rejected(weight):
    with pytest.raises(ValueError, match="finite nonnegative"):
        summarise(payload(["A", "B"], [("A", "B", weight)]))


@pytest.mark.parametrize("mutator, error", [
    (lambda data: data["nodes"].append(deepcopy(data["nodes"][0])), "duplicate"),
    (lambda data: data["edges"].append({"source": "B", "target": "A", "weight": 2}), "one edge"),
    (lambda data: data["edges"][0].update(target="missing"), "absent"),
    (lambda data: data["edges"][0].update(target="A"), "self-loops"),
])
def test_invalid_simple_graph_structure_is_rejected(mutator, error):
    data = payload(["A", "B"], [("A", "B", 2)])
    mutator(data)
    with pytest.raises(ValueError, match=error):
        summarise(data)


@pytest.mark.parametrize("fmt, reader", [("graphml", nx.read_graphml), ("gexf", nx.read_gexf)])
def test_xml_roundtrip_preserves_labels_ids_kinds_metrics_weights_and_isolates(fmt, reader):
    data = payload(['A<&"', "B", "isolated"], [('A<&"', "B", 0.125)])
    data["nodes"][0]["label"] = 'Quoted, <title> & "author"'
    original = deepcopy(data)
    content, mime, extension = graph_download(data, fmt)
    assert data == original
    assert mime.endswith("+xml") and extension == fmt
    ET.fromstring(content)
    graph = reader(io.BytesIO(content))
    assert set(graph) == {'A<&"', "B", "isolated"}
    assert graph.nodes['A<&"']["label"] == 'Quoted, <title> & "author"'
    assert graph.nodes['A<&"']["kind"] == "paper"
    assert graph.nodes['A<&"']["degree"] == 1
    assert graph.nodes["isolated"]["degree"] == 0
    assert graph['A<&"']["B"]["weight"] == 0.125
    if fmt == "graphml":
        metadata = {key: json.loads(graph.graph[key]) for key in ("parameters", "provenance", "analysis")}
    else:
        metadata = json.loads(ET.fromstring(content).find("{http://www.gexf.net/1.2draft}meta/{http://www.gexf.net/1.2draft}description").text)
    assert metadata["parameters"]["node_population"] == 100
    assert metadata["analysis"]["scope"] == "displayed_graph"
    assert metadata["provenance"]["weight_definition"] == "Distinct shared papers"


@pytest.mark.parametrize("fmt, reader", [("graphml", nx.read_graphml), ("gexf", nx.read_gexf)])
def test_xml_control_characters_have_valid_encoded_and_original_text(fmt, reader):
    data = payload(["A", "B"], [("A", "B", 1)])
    data["nodes"][0]["label"] = "Title\x00with\x01control"
    content, _, _ = graph_download(data, fmt)
    graph = reader(io.BytesIO(content))
    assert graph.nodes["A"]["label"] == "Title\\u0000with\\u0001control"
    assert json.loads(graph.nodes["A"]["label_original_json"]) == data["nodes"][0]["label"]


def test_csv_has_one_row_per_edge_and_compact_provenance():
    data = payload(["A", "B", "isolated"], [("A", "B", 3)])
    data["nodes"][0]["label"] = 'Comma, quote " and\nnewline'
    original = deepcopy(data)
    content, mime, extension = graph_download(data, "csv")
    assert data == original
    rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
    assert len(rows) == 1
    assert rows[0]["Source"] == "A" and rows[0]["Target"] == "B"
    assert float(rows[0]["Weight"]) == 3
    assert rows[0]["source_label"] == data["nodes"][0]["label"]
    assert rows[0]["source_kind"] == "paper" and rows[0]["Type"] == "Undirected"
    metadata = json.loads(rows[0]["provenance"])
    assert metadata["analysis"]["scope"] == "displayed_graph"
    assert "rankings" not in metadata["analysis"]
    assert metadata["edgelist_isolates_omitted"] == 1
    assert metadata["isolated_nodes"][0]["id"] == "isolated"
    assert extension == "csv" and mime.startswith("text/csv")


def test_empty_csv_stores_provenance_without_an_invented_edge():
    content, _, _ = graph_download(payload(["isolated"], []), "csv")
    rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
    assert len(rows) == 1
    assert rows[0][:2] == ["Source", "Target"]
    metadata = json.loads(rows[0][-1].removeprefix("provenance="))
    assert metadata["isolated_nodes"][0]["id"] == "isolated"


@pytest.mark.parametrize("fmt", ["graphml", "gexf"])
def test_empty_xml_graph_is_valid(fmt):
    content, _, _ = graph_download(payload([], []), fmt)
    reader = nx.read_graphml if fmt == "graphml" else nx.read_gexf
    assert reader(io.BytesIO(content)).number_of_nodes() == 0


def test_unsupported_format_is_rejected():
    with pytest.raises(ValueError, match="graphml\\|gexf\\|csv"):
        graph_download(payload([], []), "pdf")
