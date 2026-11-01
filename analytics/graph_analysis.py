"""Metrics and portable exports for the graph returned by a network view."""
from __future__ import annotations

from copy import deepcopy
import csv
import io
import json
import math
from xml.etree import ElementTree as ET

import networkx as nx

SEED = 42
EXACT_NODE_LIMIT = 200
MAX_SAMPLES = 64
RANKING_LIMIT = 20


def _kind(data, node):
    if node.get("kind"):
        return str(node["kind"])
    graph_kind = data.get("parameters", {}).get("kind", "")
    if graph_kind == "chapter-chapter":
        return "chapter"
    if graph_kind == "report-report":
        return "report"
    if graph_kind == "author-author":
        return "author"
    return str(node.get("group", "node"))


def _graph(data):
    """Validate and insert nodes and edges in a reproducible order."""
    graph = nx.Graph()
    node_rows = {}
    for node in data.get("nodes", []):
        node_id = str(node["id"])
        if node_id in node_rows:
            raise ValueError(f"duplicate network node ID: {node_id}")
        node_rows[node_id] = node
    for node_id in sorted(node_rows):
        node = node_rows[node_id]
        attributes = {key: value for key, value in node.items() if key != "id" and value is not None}
        attributes.setdefault("label", node_id)
        attributes["kind"] = _kind(data, node)
        graph.add_node(node_id, **attributes)
    rows = []
    for edge in data.get("edges", []):
        source, target = str(edge["source"]), str(edge["target"])
        if source not in graph or target not in graph:
            raise ValueError("network edge endpoint is absent from the node table")
        if source == target:
            raise ValueError("network analysis expects a simple graph without self-loops")
        weight = edge.get("weight", 1)
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or weight < 0:
            raise ValueError("network edge weights must be finite nonnegative numbers")
        source, target = sorted((source, target))
        attributes = {key: value for key, value in edge.items() if key not in {"source", "target"} and value is not None}
        attributes["weight"] = weight
        rows.append((source, target, attributes))
    for source, target, attributes in sorted(rows, key=lambda row: row[:2]):
        if graph.has_edge(source, target):
            raise ValueError("network analysis expects one edge per undirected node pair")
        graph.add_edge(source, target, **attributes)
    if not math.isfinite(graph.size(weight="weight")):
        raise ValueError("network total edge weight must be finite")
    return graph, node_rows


def _partition_rows(graph, partition):
    ordered = sorted((sorted(members) for members in partition), key=lambda members: tuple(members))
    return [{"id": index, "node_count": len(members),
             "edge_count": graph.subgraph(members).number_of_edges(), "node_ids": members}
            for index, members in enumerate(ordered, 1)]


def summarise(data: dict) -> dict:
    """Return displayed-graph metrics and add those metrics to its node rows.

    The supplied node table defines the population for these calculations.
    The source-population counts in ``parameters`` remain separate.
    """
    graph, node_rows = _graph(data)
    node_count, edge_count = graph.number_of_nodes(), graph.number_of_edges()
    components = _partition_rows(graph, nx.connected_components(graph))
    exact = node_count <= EXACT_NODE_LIMIT or edge_count == 0
    samples = node_count if exact else min(MAX_SAMPLES, node_count)
    betweenness = nx.betweenness_centrality(
        graph, k=None if exact else samples, normalized=True,
        weight=None, endpoints=False, seed=SEED,
    ) if edge_count else dict.fromkeys(graph, 0.0)
    degree = dict(graph.degree())
    strength = dict(graph.degree(weight="weight"))
    if any(not math.isfinite(value) for value in strength.values()):
        raise ValueError("network node strengths must be finite")

    # Zero-weight links remain in topology metrics but contribute no strength.
    positive_graph = graph.copy()
    zero_edges = [(source, target) for source, target, attrs in graph.edges(data=True) if attrs["weight"] == 0]
    positive_graph.remove_edges_from(zero_edges)
    has_strength = positive_graph.number_of_edges() > 0
    partition = nx.community.louvain_communities(
        positive_graph, weight="weight", resolution=1, threshold=1e-7, seed=SEED,
    ) if has_strength else [{node_id} for node_id in graph]
    communities = _partition_rows(graph, partition)
    membership = {node_id: community["id"] for community in communities for node_id in community["node_ids"]}
    for node_id, node in node_rows.items():
        node.update(community=membership[node_id], degree=degree[node_id],
                    strength=strength[node_id], betweenness=betweenness[node_id])

    def ranking(values):
        return [{"id": node_id, "label": graph.nodes[node_id]["label"],
                 "kind": graph.nodes[node_id]["kind"], "community": membership[node_id],
                 "value": values[node_id]}
                for node_id in sorted(graph, key=lambda node_id: (-values[node_id], node_id))[:RANKING_LIMIT]]

    population_keys = ("node_population", "isolates_after_filters", "omitted_connected_nodes",
                       "eligible_edge_count", "truncated", "limit", "omitted_edge_count")
    parameters = data.get("parameters", {})
    return {
        "scope": "displayed_graph",
        "scope_note": "Metrics use the returned nodes and edges after filters and the edge limit. Source-population isolates and omitted nodes are reported separately.",
        "graph": {
            "node_count": node_count, "edge_count": edge_count,
            "density": nx.density(graph),
            "average_degree": sum(degree.values()) / node_count if node_count else 0.0,
            "average_strength": sum(strength.values()) / node_count if node_count else 0.0,
            "total_edge_weight": graph.size(weight="weight"),
            "connected_components": len(components),
            "largest_component_nodes": max((row["node_count"] for row in components), default=0),
            "isolates": nx.number_of_isolates(graph),
            "is_connected": len(components) == 1 if node_count else None,
            "community_count": len(communities),
            "modularity": nx.community.modularity(positive_graph, partition, weight="weight", resolution=1) if has_strength else None,
        },
        "components": components,
        "communities": communities,
        "rankings": {"degree": ranking(degree), "strength": ranking(strength), "betweenness": ranking(betweenness)},
        "population": {key: parameters[key] for key in population_keys if key in parameters},
        "methods": {
            "networkx_version": nx.__version__,
            "graph_type": "simple undirected",
            "degree_definition": "Number of displayed neighbors.",
            "strength_definition": "Sum of displayed incident edge weights; units follow provenance.weight_definition.",
            "betweenness": {
                "algorithm": "Brandes", "mode": "exact" if exact else "sampled",
                "sample_size": samples, "seed": SEED, "weight": None,
                "normalized": True, "endpoints": False,
                "exact_node_limit": EXACT_NODE_LIMIT, "max_samples": MAX_SAMPLES,
                "node_order": "String node IDs ascending before sampling.",
                "definition": "Normalized share of shortest paths through the node. Paths use edge counts; citation weights express strength or similarity, not distance.",
            },
            "communities": {
                "algorithm": "weighted_louvain", "weight": "weight", "resolution": 1,
                "threshold": 1e-7, "seed": SEED, "applied": has_strength,
                "zero_weight_edges_ignored": len(zero_edges),
                "id_order": "Communities sorted by their sorted node ID lists; IDs start at one.",
                "definition": "A descriptive weighted-modularity partition of the displayed graph, including bipartite views without projection.",
            },
            "ranking_limit": RANKING_LIMIT,
        },
    }


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _valid_xml_character(character):
    code = ord(character)
    return code in (9, 10, 13) or 0x20 <= code <= 0xD7FF or 0xE000 <= code <= 0xFFFD or 0x10000 <= code <= 0x10FFFF


def _xml_attributes(attributes):
    result = {}
    for key, value in attributes.items():
        if value is None:
            continue
        if not isinstance(value, (str, int, float, bool)):
            value = _json(value)
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("export attributes must be finite")
        if isinstance(value, str) and any(not _valid_xml_character(character) for character in value):
            result[key + "_original_json"] = _json(value)
            value = "".join(character if _valid_xml_character(character) else f"\\u{ord(character):04x}" for character in value)
        result[key] = value
    return result


def graph_download(data: dict, fmt: str) -> tuple[bytes, str, str]:
    """Export the displayed graph with provenance, without changing its payload."""
    fmt = fmt.lower()
    if fmt not in {"graphml", "gexf", "csv"}:
        raise ValueError("graph format must be graphml|gexf|csv")
    payload = deepcopy(data)
    if "analysis" not in payload or any("community" not in node for node in payload.get("nodes", [])):
        payload["analysis"] = summarise(payload)
    graph, _ = _graph(payload)
    metadata = {
        "scope": "displayed_graph", "parameters": payload.get("parameters", {}),
        "provenance": payload.get("provenance", {}), "analysis": payload["analysis"],
        "xml_character_encoding": "XML 1.0 control characters in attributes are rendered as literal Unicode escapes; original text is retained in *_original_json attributes.",
    }
    if fmt == "csv":
        metadata["analysis"] = {key: payload["analysis"][key] for key in ("scope", "scope_note", "graph", "population", "methods")}
        metadata["edgelist_isolates_omitted"] = nx.number_of_isolates(graph)
        metadata["isolated_nodes"] = [{"id": node_id, **graph.nodes[node_id]} for node_id in nx.isolates(graph)]
        provenance = _json(metadata)
        text = io.StringIO(newline="")
        writer = csv.writer(text)
        # An empty edgelist stores provenance in its final header cell so it
        # remains a table with Source/Target columns and no invented edge row.
        provenance_header = "provenance" if graph.number_of_edges() else "provenance=" + provenance
        writer.writerow(["Source", "Target", "Weight", "Type", "source_label", "target_label",
                         "source_kind", "target_kind", "source_community", "target_community", provenance_header])
        for source, target, attrs in graph.edges(data=True):
            source_node, target_node = graph.nodes[source], graph.nodes[target]
            writer.writerow([source, target, attrs["weight"], "Undirected", source_node["label"], target_node["label"],
                             source_node["kind"], target_node["kind"], source_node["community"], target_node["community"], provenance])
        return text.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", "csv"

    for node_id in graph:
        if any(not _valid_xml_character(character) for character in node_id):
            raise ValueError("XML graph export requires node IDs without XML 1.0 control characters")
        attributes = _xml_attributes(graph.nodes[node_id])
        graph.nodes[node_id].clear()
        graph.nodes[node_id].update(attributes)
    for _, _, attributes in graph.edges(data=True):
        safe = _xml_attributes(attributes)
        attributes.clear()
        attributes.update(safe)
    graph.graph.update({"scope": metadata["scope"], "parameters": _json(metadata["parameters"]),
                        "provenance": _json(metadata["provenance"]), "analysis": _json(metadata["analysis"]),
                        "xml_character_encoding": metadata["xml_character_encoding"]})
    output = io.BytesIO()
    if fmt == "graphml":
        nx.write_graphml_xml(graph, output, encoding="utf-8", infer_numeric_types=True)
        return output.getvalue(), "application/graphml+xml", "graphml"
    nx.write_gexf(graph, output, encoding="utf-8", version="1.2draft")
    tree = ET.fromstring(output.getvalue())
    namespace = "http://www.gexf.net/1.2draft"
    meta = tree.find(f"{{{namespace}}}meta")
    if meta is None:
        meta = ET.SubElement(tree, f"{{{namespace}}}meta")
    ET.SubElement(meta, f"{{{namespace}}}description").text = _json(metadata)
    return ET.tostring(tree, encoding="utf-8", xml_declaration=True), "application/gexf+xml", "gexf"
