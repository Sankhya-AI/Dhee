"""Graph-backed proof bundles for context items.

This module joins three evidence layers:

* repo graph artifacts (durable files, symbols, tests, ownership, failures)
* context graph slices (localized graph paths for a task/query)
* temporal facts (validity windows and scene/event provenance)

The bundle intentionally stores pointers and structured provenance. It does
not copy raw source bodies, hidden reasoning, or large evidence payloads.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


GRAPH_PROOF_BUNDLE_SCHEMA = "dhee.graph_proof_bundle.v1"
GRAPH_PROOF_ITEM_SCHEMA = "dhee.graph_proof_item.v1"
GRAPH_PATH_SCHEMA = "dhee.graph_proof_path.v1"

_TOKEN_RE = re.compile(r"[A-Za-z0-9_@./:-]{2,}")
_NODE_ID_PREFIXES = ("file:", "symbol:", "error:", "actor:", "test:", "config:")
_PATH_RE = re.compile(
    r"(?P<path>(?:\.?/)?[A-Za-z0-9_./@-]+\."
    r"(?:py|js|jsx|ts|tsx|toml|json|yaml|yml|md|txt|ini|cfg|lock))"
)
_RELATION_PRIORITY = {
    "failed_with": 0,
    "tested_by": 1,
    "calls": 2,
    "imports": 3,
    "contains": 4,
    "owned_by": 5,
}


class GraphProofBundleError(ValueError):
    """Raised when graph proof bundle input is structurally unusable."""


def build_graph_proof_bundle(
    context_items: Optional[Any] = None,
    *,
    repo_graph: Optional[Mapping[str, Any]] = None,
    context_graph: Optional[Mapping[str, Any]] = None,
    repo_brain: Optional[Mapping[str, Any]] = None,
    temporal_facts: Optional[Any] = None,
    query: str = "",
    repo: Optional[str] = None,
    as_of: Optional[str] = None,
    limit_paths_per_item: int = 6,
) -> Dict[str, Any]:
    """Build a graph-backed proof bundle for every context item.

    Args:
        context_items: A list of context items, a compiled-context dict, or
            ``None`` to derive items from ``context_graph["proof_items"]``.
        repo_graph: A raw repo graph artifact or MCP ``dhee_repo_graph_export``
            response. Used as the broad structural fallback.
        context_graph: A raw context graph slice or MCP
            ``dhee_context_graph_query`` response. Used first because it is
            already localized to the task.
        repo_brain: Optional repo intelligence brain. When supplied and graph
            outputs are missing, this function uses ``dhee.repo_intelligence``
            helpers to derive them.
        temporal_facts: A list of temporal fact dicts, a search response with
            ``results``, or any dict containing fact-shaped values.
        query: Task/query text used for repo-brain derivation and weak matching.
        repo: Repository path to record in the bundle. Graph values win when
            present.
        as_of: ISO timestamp for temporal validity checks. Defaults to now.
        limit_paths_per_item: Maximum graph paths attached to each item.

    Returns:
        A serializable envelope with ``format`` and ``proof_bundle`` keys.
    """

    context_graph_data = _unwrap_graph(context_graph, "context_graph")
    repo_graph_data = _unwrap_graph(repo_graph, "repo_graph")

    if repo_brain and (not context_graph_data or not repo_graph_data):
        derived_repo_graph, derived_context_graph = _derive_graphs_from_brain(
            repo_brain,
            query=query,
            need_repo_graph=not repo_graph_data,
            need_context_graph=not context_graph_data,
        )
        repo_graph_data = repo_graph_data or derived_repo_graph
        context_graph_data = context_graph_data or derived_context_graph

    items = _normalize_context_items(context_items, context_graph_data)
    facts = _normalize_temporal_facts(temporal_facts)
    as_of_value = _normalize_iso(as_of, fallback_now=True)
    graph_index = _GraphIndex(context_graph_data, repo_graph_data)
    proof_items = _proof_items_by_id(context_graph_data)

    proved_items: List[Dict[str, Any]] = []
    missing_graph_paths: List[str] = []
    for ordinal, raw_item in enumerate(items, start=1):
        item = _normalize_context_item(raw_item, ordinal)
        linked_facts = _match_temporal_facts(item, facts)
        seed_ids = _seed_node_ids(item, graph_index, proof_items, linked_facts, query=query)
        graph_paths = graph_index.paths_for_seeds(seed_ids, limit=max(1, int(limit_paths_per_item or 1)))
        if not graph_paths:
            graph_paths = [_unmatched_graph_path(item, seed_ids)]
            missing_graph_paths.append(item["context_id"])

        source_refs = _source_refs_for_item(item, graph_paths, graph_index, linked_facts)
        temporal_validity = _temporal_validity_for_item(item, linked_facts, as_of_value)
        confidence = _combined_confidence(item, graph_paths, linked_facts)
        why_included = _why_included(item, proof_items, seed_ids)
        completeness = _completeness(graph_paths, source_refs, temporal_validity, confidence)

        proved_items.append(
            _clean(
                {
                    "schema_version": GRAPH_PROOF_ITEM_SCHEMA,
                    "context_id": item["context_id"],
                    "kind": item.get("kind"),
                    "title": item.get("title"),
                    "evidence_pointer": item.get("evidence_pointer"),
                    "why_included": why_included,
                    "confidence": confidence,
                    "graph_paths": graph_paths,
                    "sources": source_refs,
                    "temporal_validity": temporal_validity,
                    "temporal_facts": [_temporal_fact_ref(fact, as_of_value) for fact in linked_facts],
                    "completeness": completeness,
                    "metadata": item.get("metadata") or {},
                }
            )
        )

    graph_sources = _graph_source_summary(context_graph_data, repo_graph_data, graph_index)
    summary = _bundle_summary(proved_items, missing_graph_paths)
    bundle = _clean(
        {
            "schema_version": GRAPH_PROOF_BUNDLE_SCHEMA,
            "bundle_id": "gpb_" + _stable_hash(
                {
                    "query": query,
                    "repo": repo or graph_sources.get("repo"),
                    "items": [item.get("context_id") for item in proved_items],
                    "as_of": as_of_value,
                },
                20,
            ),
            "generated_at": _now_iso(),
            "query": query,
            "repo": repo or graph_sources.get("repo"),
            "as_of": as_of_value,
            "context_items": proved_items,
            "graph_sources": graph_sources,
            "summary": summary,
            "policy": {
                "raw_file_bodies_excluded": True,
                "hidden_reasoning_excluded": True,
                "every_context_item_gets_graph_path_record": True,
                "temporal_validity_required_when_available": True,
            },
        }
    )
    return {
        "format": "dhee_graph_proof_bundle.v1",
        "proof_bundle": bundle,
    }


def build_graph_proof_bundle_from_brain(
    repo_brain: Mapping[str, Any],
    query: str,
    *,
    context_items: Optional[Any] = None,
    temporal_facts: Optional[Any] = None,
    as_of: Optional[str] = None,
    limit_paths_per_item: int = 6,
) -> Dict[str, Any]:
    """Convenience wrapper for callers that already have a repo brain."""

    return build_graph_proof_bundle(
        context_items,
        repo_brain=repo_brain,
        temporal_facts=temporal_facts,
        query=query,
        repo=str(repo_brain.get("repo") or ""),
        as_of=as_of,
        limit_paths_per_item=limit_paths_per_item,
    )


class _GraphIndex:
    def __init__(self, context_graph: Mapping[str, Any], repo_graph: Mapping[str, Any]):
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.edges: Dict[str, Dict[str, Any]] = {}
        self.adjacency: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self.node_ids_by_path: Dict[str, List[str]] = defaultdict(list)
        self.node_ids_by_token: Dict[str, Set[str]] = defaultdict(set)
        self.graph_repo = str(context_graph.get("repo") or repo_graph.get("repo") or "")
        self._ingest_graph(repo_graph, "repo_graph")
        self._ingest_graph(context_graph, "context_graph")
        for edge in self.edges.values():
            source = str(edge.get("source") or "")
            target = str(edge.get("target") or "")
            if source:
                self.adjacency[source].append(edge)
            if target and target != source:
                self.adjacency[target].append(edge)
        for edges in self.adjacency.values():
            edges.sort(key=_edge_sort_key)

    def _ingest_graph(self, graph: Mapping[str, Any], source_name: str) -> None:
        if not isinstance(graph, Mapping):
            return
        for raw_node in graph.get("nodes") or []:
            if not isinstance(raw_node, Mapping):
                continue
            node = dict(raw_node)
            node_id = str(node.get("id") or "").strip()
            if not node_id:
                continue
            existing = self.nodes.get(node_id)
            if existing:
                existing.setdefault("_source_graphs", [])
                if source_name not in existing["_source_graphs"]:
                    existing["_source_graphs"].append(source_name)
                existing.setdefault("metadata", {}).update(node.get("metadata") or {})
                continue
            node["_source_graphs"] = [source_name]
            self.nodes[node_id] = node
            path = _node_path(node)
            if path:
                self.node_ids_by_path[path].append(node_id)
            token_text = " ".join(
                [
                    node_id,
                    str(node.get("label") or ""),
                    str((node.get("metadata") or {}).get("path") or ""),
                    str((node.get("metadata") or {}).get("qualname") or ""),
                ]
            )
            for token in _tokens(token_text):
                self.node_ids_by_token[token].add(node_id)

        for raw_edge in graph.get("edges") or []:
            if not isinstance(raw_edge, Mapping):
                continue
            edge = dict(raw_edge)
            source = str(edge.get("source") or "")
            target = str(edge.get("target") or "")
            edge_type = str(edge.get("type") or "")
            if not source or not target:
                continue
            edge_id = str(edge.get("id") or "") or "edge_" + _stable_hash(
                {"source": source, "target": target, "type": edge_type, "metadata": edge.get("metadata") or {}},
                16,
            )
            edge["id"] = edge_id
            existing = self.edges.get(edge_id)
            if existing:
                existing.setdefault("_source_graphs", [])
                if source_name not in existing["_source_graphs"]:
                    existing["_source_graphs"].append(source_name)
                continue
            edge["_source_graphs"] = [source_name]
            self.edges[edge_id] = edge

    def paths_for_seeds(self, seed_ids: Sequence[str], *, limit: int) -> List[Dict[str, Any]]:
        paths: List[Dict[str, Any]] = []
        seen_paths: Set[Tuple[str, ...]] = set()
        for seed_id in _dedupe(seed_ids):
            if seed_id not in self.nodes:
                continue
            for path in self._walk_paths(seed_id, max_hops=2):
                key = tuple(path["edge_ids"]) or tuple(path["node_ids"])
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                paths.append(path)
        paths.sort(key=_path_sort_key)
        return paths[: max(1, int(limit or 1))]

    def _walk_paths(self, seed_id: str, *, max_hops: int) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        queue: Deque[Tuple[str, List[str], List[str], List[str], List[float]]] = deque()
        queue.append((seed_id, [seed_id], [], [], []))
        found_edge_path = False
        while queue:
            node_id, node_path, edge_ids, relations, confidences = queue.popleft()
            if edge_ids:
                found_edge_path = True
                out.append(self._path_record(seed_id, node_path, edge_ids, relations, confidences))
            if len(edge_ids) >= max_hops:
                continue
            for edge in self.adjacency.get(node_id, []):
                next_id = str(edge.get("target") if edge.get("source") == node_id else edge.get("source") or "")
                if not next_id or next_id in node_path:
                    continue
                queue.append(
                    (
                        next_id,
                        [*node_path, next_id],
                        [*edge_ids, str(edge.get("id") or "")],
                        [*relations, str(edge.get("type") or "")],
                        [*confidences, _safe_float(edge.get("confidence"), 1.0)],
                    )
                )
        if not found_edge_path:
            out.append(self._path_record(seed_id, [seed_id], [], [], []))
        return out

    def _path_record(
        self,
        seed_id: str,
        node_ids: Sequence[str],
        edge_ids: Sequence[str],
        relations: Sequence[str],
        confidences: Sequence[float],
    ) -> Dict[str, Any]:
        path_edges = [self.edges[edge_id] for edge_id in edge_ids if edge_id in self.edges]
        source_graphs = sorted(
            {
                source
                for edge in path_edges
                for source in edge.get("_source_graphs", [])
                if source
            }
            or {
                source
                for node_id in node_ids
                for source in self.nodes.get(node_id, {}).get("_source_graphs", [])
                if source
            }
        )
        confidence = sum(confidences) / len(confidences) if confidences else 0.6
        return _clean(
            {
                "schema_version": GRAPH_PATH_SCHEMA,
                "path_id": "gpath_" + _stable_hash({"nodes": list(node_ids), "edges": list(edge_ids)}, 18),
                "status": "matched",
                "seed_node_id": seed_id,
                "source_node_id": node_ids[0] if node_ids else seed_id,
                "target_node_id": node_ids[-1] if node_ids else seed_id,
                "node_ids": list(node_ids),
                "edge_ids": list(edge_ids),
                "relations": list(relations),
                "confidence": round(float(confidence), 4),
                "source_graphs": source_graphs,
                "why_path": _path_reason(seed_id, relations),
                "evidence_pointers": _path_evidence_pointers(path_edges),
            }
        )


def _derive_graphs_from_brain(
    repo_brain: Mapping[str, Any],
    *,
    query: str,
    need_repo_graph: bool,
    need_context_graph: bool,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    repo_graph: Dict[str, Any] = {}
    context_graph: Dict[str, Any] = {}
    try:
        from dhee.repo_intelligence import context_graph_query, repo_graph_from_brain
    except Exception:
        return repo_graph, context_graph
    if need_repo_graph:
        repo_graph = repo_graph_from_brain(dict(repo_brain))
    if need_context_graph and query:
        context_graph = context_graph_query(dict(repo_brain), query)
    return repo_graph, context_graph


def _unwrap_graph(payload: Optional[Mapping[str, Any]], key: str) -> Dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    nested = payload.get(key)
    if isinstance(nested, Mapping):
        return dict(nested)
    if "nodes" in payload or "edges" in payload:
        return dict(payload)
    graph = payload.get("graph")
    if isinstance(graph, Mapping) and ("nodes" in graph or "edges" in graph):
        return dict(graph)
    return {}


def _normalize_context_items(context_items: Optional[Any], context_graph: Mapping[str, Any]) -> List[Dict[str, Any]]:
    if context_items is None:
        return [_context_item_from_proof_item(item, idx) for idx, item in enumerate(context_graph.get("proof_items") or [], start=1)]
    if isinstance(context_items, Mapping):
        compiled = context_items.get("compiled_context")
        if isinstance(compiled, Mapping):
            return _normalize_context_items(compiled, context_graph)
        if isinstance(context_items.get("items"), Sequence) and not isinstance(context_items.get("items"), (str, bytes)):
            return [dict(item) for item in context_items.get("items") or [] if isinstance(item, Mapping)]
        if isinstance(context_items.get("context_items"), Sequence) and not isinstance(context_items.get("context_items"), (str, bytes)):
            return [dict(item) for item in context_items.get("context_items") or [] if isinstance(item, Mapping)]
        return [dict(context_items)]
    if isinstance(context_items, Sequence) and not isinstance(context_items, (str, bytes)):
        return [dict(item) for item in context_items if isinstance(item, Mapping)]
    raise GraphProofBundleError("context_items must be a mapping, sequence of mappings, or None")


def _context_item_from_proof_item(item: Any, ordinal: int) -> Dict[str, Any]:
    if not isinstance(item, Mapping):
        return {"context_id": f"context:{ordinal}", "kind": "unknown", "title": str(item), "why_included": "context graph proof item"}
    graph_id = str(item.get("id") or "")
    path = _file_path_from_node_id(graph_id)
    return _clean(
        {
            "context_id": graph_id or f"context:{ordinal}",
            "kind": item.get("kind"),
            "title": item.get("title") or item.get("label") or graph_id,
            "path": path,
            "graph_node_id": graph_id,
            "why_included": item.get("why") or item.get("reason") or "localized context graph proof item",
            "confidence": item.get("score"),
            "evidence_pointers": item.get("evidence_pointers"),
            "command": item.get("command"),
        }
    )


def _normalize_context_item(raw_item: Mapping[str, Any], ordinal: int) -> Dict[str, Any]:
    item = dict(raw_item)
    context_id = str(
        item.get("context_id")
        or item.get("id")
        or item.get("evidence_pointer")
        or item.get("ref")
        or f"context:{ordinal}"
    )
    metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
    return {
        "context_id": context_id,
        "kind": str(item.get("kind") or metadata.get("kind") or "context"),
        "title": str(item.get("title") or item.get("label") or item.get("summary") or context_id),
        "evidence_pointer": item.get("evidence_pointer") or item.get("ref") or item.get("pointer"),
        "why_included": item.get("why_included") or item.get("why") or item.get("reason") or item.get("reasons"),
        "confidence": item.get("confidence") if item.get("confidence") is not None else item.get("score"),
        "metadata": dict(metadata),
        "raw": item,
    }


def _normalize_temporal_facts(value: Optional[Any]) -> List[Dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        if isinstance(value.get("results"), Sequence) and not isinstance(value.get("results"), (str, bytes)):
            return [dict(item) for item in value.get("results") or [] if isinstance(item, Mapping)]
        if isinstance(value.get("facts"), Sequence) and not isinstance(value.get("facts"), (str, bytes)):
            return [dict(item) for item in value.get("facts") or [] if isinstance(item, Mapping)]
        if isinstance(value.get("fact"), Mapping):
            return [dict(value["fact"])]
        if _looks_like_temporal_fact(value):
            return [dict(value)]
        facts: List[Dict[str, Any]] = []
        for item in value.values():
            if isinstance(item, Mapping) and _looks_like_temporal_fact(item):
                facts.append(dict(item))
        return facts
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def _proof_items_by_id(context_graph: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for item in context_graph.get("proof_items") or []:
        if not isinstance(item, Mapping):
            continue
        item_id = str(item.get("id") or "")
        if item_id:
            out[item_id] = dict(item)
    return out


def _seed_node_ids(
    item: Mapping[str, Any],
    graph_index: _GraphIndex,
    proof_items: Mapping[str, Mapping[str, Any]],
    linked_facts: Sequence[Mapping[str, Any]],
    *,
    query: str,
) -> List[str]:
    raw = item.get("raw") if isinstance(item.get("raw"), Mapping) else {}
    seeds: List[str] = []

    for value in _candidate_scalar_values(item, raw):
        text = str(value or "").strip()
        if text.startswith(_NODE_ID_PREFIXES):
            seeds.append(_normalize_node_id(text))
        for path in _extract_paths(text):
            seeds.extend(graph_index.node_ids_by_path.get(path, []))
            seeds.append(f"file:{path}")

    for proof_id, proof_item in proof_items.items():
        if proof_id in seeds:
            continue
        if _proof_item_matches_context_item(proof_item, item):
            seeds.append(proof_id)

    for fact in linked_facts:
        for value in _candidate_scalar_values(fact, fact):
            text = str(value or "").strip()
            if text.startswith(_NODE_ID_PREFIXES):
                seeds.append(_normalize_node_id(text))
            for path in _extract_paths(text):
                seeds.extend(graph_index.node_ids_by_path.get(path, []))
                seeds.append(f"file:{path}")
        for evidence in fact.get("evidence") or []:
            if isinstance(evidence, Mapping):
                for path in _extract_paths(json.dumps(evidence, sort_keys=True, default=str)):
                    seeds.extend(graph_index.node_ids_by_path.get(path, []))
                    seeds.append(f"file:{path}")

    existing = [seed for seed in _dedupe(seeds) if seed in graph_index.nodes]
    if existing:
        return existing

    weak_text = " ".join([str(item.get("title") or ""), str(item.get("kind") or ""), str(query or "")])
    scored: List[Tuple[int, str]] = []
    for token in _tokens(weak_text):
        for node_id in graph_index.node_ids_by_token.get(token, set()):
            scored.append((1, node_id))
    counts: Dict[str, int] = defaultdict(int)
    for score, node_id in scored:
        counts[node_id] += score
    return [node_id for node_id, _score in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:3]]


def _candidate_scalar_values(item: Mapping[str, Any], raw: Mapping[str, Any]) -> List[Any]:
    keys = (
        "context_id",
        "id",
        "graph_node_id",
        "node_id",
        "evidence_pointer",
        "ref",
        "pointer",
        "path",
        "file",
        "source_file",
        "test",
        "command",
        "title",
        "label",
        "summary",
        "fact_text",
        "subject",
        "predicate",
        "object",
        "source_scene",
        "source_event_id",
        "source_memory_id",
        "valid_from",
        "valid_to",
        "observed_at",
    )
    values: List[Any] = []
    for source in (item, raw):
        for key in keys:
            if source.get(key) not in (None, "", [], {}):
                values.append(source.get(key))
        for key in (
            "evidence_pointers",
            "source_event_ids",
            "source_memory_ids",
            "source_files",
            "source_tests",
            "tests",
            "files",
        ):
            values.extend(_as_list(source.get(key)))
    return values


def _proof_item_matches_context_item(proof_item: Mapping[str, Any], item: Mapping[str, Any]) -> bool:
    raw = item.get("raw") if isinstance(item.get("raw"), Mapping) else {}
    item_values = " ".join(str(value) for value in _candidate_scalar_values(item, raw))
    proof_values = " ".join(str(value) for value in _candidate_scalar_values(proof_item, proof_item))
    if not item_values or not proof_values:
        return False
    item_paths = set(_extract_paths(item_values))
    proof_paths = set(_extract_paths(proof_values))
    if item_paths and item_paths & proof_paths:
        return True
    item_pointers = set(str(v) for v in _as_list(raw.get("evidence_pointers")) + _as_list(item.get("evidence_pointer")) if v)
    proof_pointers = set(str(v) for v in _as_list(proof_item.get("evidence_pointers")) if v)
    if item_pointers and item_pointers & proof_pointers:
        return True
    return len(_tokens(item_values) & _tokens(proof_values)) >= 2


def _match_temporal_facts(item: Mapping[str, Any], facts: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    if not facts:
        return []
    raw = item.get("raw") if isinstance(item.get("raw"), Mapping) else {}
    item_text = " ".join(str(value) for value in _candidate_scalar_values(item, raw))
    item_tokens = _tokens(item_text)
    item_paths = set(_extract_paths(item_text))
    linked: List[Dict[str, Any]] = []
    for fact in facts:
        fact_text = " ".join(
            [
                str(fact.get("id") or ""),
                str(fact.get("subject") or ""),
                str(fact.get("predicate") or ""),
                str(fact.get("object") or ""),
                str(fact.get("fact_text") or ""),
                json.dumps(fact.get("metadata") or {}, sort_keys=True, default=str),
                json.dumps(fact.get("evidence") or [], sort_keys=True, default=str),
            ]
        )
        fact_paths = set(_extract_paths(fact_text))
        if item_paths and fact_paths and item_paths & fact_paths:
            linked.append(dict(fact))
            continue
        if str(fact.get("id") or "") and str(fact.get("id")) in item_text:
            linked.append(dict(fact))
            continue
        overlap = item_tokens & _tokens(fact_text)
        if len(overlap) >= 2:
            linked.append(dict(fact))
    linked.sort(key=lambda fact: (-_safe_float(fact.get("confidence"), 0.0), str(fact.get("observed_at") or "")))
    return linked[:5]


def _source_refs_for_item(
    item: Mapping[str, Any],
    graph_paths: Sequence[Mapping[str, Any]],
    graph_index: _GraphIndex,
    linked_facts: Sequence[Mapping[str, Any]],
) -> Dict[str, List[str]]:
    raw = item.get("raw") if isinstance(item.get("raw"), Mapping) else {}
    scenes: List[str] = []
    event_ids: List[str] = []
    files: List[str] = []
    tests: List[str] = []
    memory_ids: List[str] = []
    evidence_pointers: List[str] = []

    _extend(scenes, _as_list(raw.get("source_scene")) + _as_list(raw.get("source_scenes")))
    _extend(event_ids, _as_list(raw.get("source_event_id")) + _as_list(raw.get("source_event_ids")))
    _extend(files, _as_list(raw.get("source_file")) + _as_list(raw.get("source_files")) + _as_list(raw.get("file")))
    _extend(tests, _as_list(raw.get("source_test")) + _as_list(raw.get("source_tests")) + _as_list(raw.get("test")) + _as_list(raw.get("tests")))
    _extend(memory_ids, _as_list(raw.get("source_memory_id")) + _as_list(raw.get("source_memory_ids")))
    _extend(evidence_pointers, _as_list(item.get("evidence_pointer")) + _as_list(raw.get("evidence_pointers")))

    for path in _extract_paths(" ".join(str(value) for value in _candidate_scalar_values(item, raw))):
        if _is_test_path(path):
            tests.append(path)
        else:
            files.append(path)

    for path_record in graph_paths:
        for pointer in path_record.get("evidence_pointers") or []:
            evidence_pointers.append(str(pointer))
        for node_id in path_record.get("node_ids") or []:
            node = graph_index.nodes.get(str(node_id), {})
            path = _node_path(node)
            if not path:
                continue
            if _node_is_test(node) or _is_test_path(path):
                tests.append(path)
            elif str(node.get("type") or "") in {"file", "config", "symbol", "error"} or str(node_id).startswith("file:"):
                files.append(path)

    for fact in linked_facts:
        _extend(scenes, _as_list(fact.get("source_scene")))
        _extend(event_ids, _as_list(fact.get("source_event_ids")))
        _extend(memory_ids, _as_list(fact.get("source_memory_ids")))
        for evidence in fact.get("evidence") or []:
            if not isinstance(evidence, Mapping):
                continue
            _extend(scenes, _as_list(evidence.get("scene_id")) + _as_list(evidence.get("source_scene")))
            _extend(event_ids, _as_list(evidence.get("event_id")) + _as_list(evidence.get("source_event_id")))
            _extend(evidence_pointers, _as_list(evidence.get("ref")) + _as_list(evidence.get("evidence_pointer")))
            for path in _extract_paths(json.dumps(evidence, sort_keys=True, default=str)):
                if _is_test_path(path):
                    tests.append(path)
                else:
                    files.append(path)

    file_set = set(_normalize_path(path) for path in files if path)
    tests_clean = _dedupe(_normalize_path(path) for path in tests if path)
    files_clean = [path for path in _dedupe(sorted(file_set)) if path not in set(tests_clean)]
    return {
        "scenes": _dedupe(str(value) for value in scenes if value),
        "event_ids": _dedupe(str(value) for value in event_ids if value),
        "files": files_clean,
        "tests": tests_clean,
        "memory_ids": _dedupe(str(value) for value in memory_ids if value),
        "evidence_pointers": _dedupe(str(value) for value in evidence_pointers if value),
    }


def _temporal_validity_for_item(
    item: Mapping[str, Any],
    linked_facts: Sequence[Mapping[str, Any]],
    as_of: str,
) -> Dict[str, Any]:
    raw = item.get("raw") if isinstance(item.get("raw"), Mapping) else {}
    if linked_facts:
        starts = [_normalize_iso(str(fact.get("valid_from") or ""), fallback_now=False) for fact in linked_facts if fact.get("valid_from")]
        ends = [_normalize_iso(str(fact.get("valid_to") or ""), fallback_now=False) for fact in linked_facts if fact.get("valid_to")]
        observed = [_normalize_iso(str(fact.get("observed_at") or ""), fallback_now=False) for fact in linked_facts if fact.get("observed_at")]
        active_flags = [_fact_active_as_of(fact, as_of) for fact in linked_facts]
        statuses = sorted({str(fact.get("status") or "active") for fact in linked_facts})
        return _clean(
            {
                "as_of": as_of,
                "valid_from": max(starts) if starts else None,
                "valid_to": min(ends) if ends else None,
                "observed_at": max(observed) if observed else None,
                "active": all(active_flags) if active_flags else None,
                "status": "active" if active_flags and all(active_flags) else ",".join(statuses),
                "basis": "temporal_facts",
                "fact_ids": [str(fact.get("id")) for fact in linked_facts if fact.get("id")],
            }
        )

    temporal = raw.get("temporal_validity") if isinstance(raw.get("temporal_validity"), Mapping) else {}
    valid_from = temporal.get("valid_from") or raw.get("valid_from") or raw.get("created_at")
    valid_to = temporal.get("valid_to") or raw.get("valid_to")
    observed_at = temporal.get("observed_at") or raw.get("observed_at") or raw.get("updated_at")
    active_value = temporal.get("active") if "active" in temporal else raw.get("active")
    active = _active_window(valid_from, valid_to, as_of) if active_value is None and (valid_from or valid_to) else active_value
    return _clean(
        {
            "as_of": as_of,
            "valid_from": _normalize_iso(str(valid_from), fallback_now=False) if valid_from else None,
            "valid_to": _normalize_iso(str(valid_to), fallback_now=False) if valid_to else None,
            "observed_at": _normalize_iso(str(observed_at), fallback_now=False) if observed_at else None,
            "active": active,
            "status": temporal.get("status") or raw.get("status"),
            "basis": "context_item" if (valid_from or valid_to or observed_at or active_value is not None) else "not_temporal_fact_backed",
            "fact_ids": [],
        }
    )


def _temporal_fact_ref(fact: Mapping[str, Any], as_of: str) -> Dict[str, Any]:
    return _clean(
        {
            "id": fact.get("id"),
            "subject": fact.get("subject"),
            "predicate": fact.get("predicate"),
            "object": fact.get("object"),
            "fact_text": fact.get("fact_text"),
            "valid_from": fact.get("valid_from"),
            "valid_to": fact.get("valid_to"),
            "observed_at": fact.get("observed_at"),
            "status": fact.get("status"),
            "active_as_of": _fact_active_as_of(fact, as_of),
            "confidence": _safe_float(fact.get("confidence"), 0.0),
            "source_scene": fact.get("source_scene"),
            "source_event_ids": fact.get("source_event_ids"),
            "source_memory_ids": fact.get("source_memory_ids"),
        }
    )


def _combined_confidence(
    item: Mapping[str, Any],
    graph_paths: Sequence[Mapping[str, Any]],
    linked_facts: Sequence[Mapping[str, Any]],
) -> float:
    values: List[float] = []
    raw_confidence = _safe_float(item.get("confidence"), -1.0)
    if raw_confidence >= 0:
        values.append(raw_confidence)
    matched_path_confidences = [
        _safe_float(path.get("confidence"), 0.0)
        for path in graph_paths
        if path.get("status") == "matched"
    ]
    if matched_path_confidences:
        values.append(sum(matched_path_confidences) / len(matched_path_confidences))
    fact_confidences = [_safe_float(fact.get("confidence"), 0.0) for fact in linked_facts if fact.get("confidence") is not None]
    if fact_confidences:
        values.append(sum(fact_confidences) / len(fact_confidences))
    if not values:
        return 0.5
    return round(max(0.0, min(1.0, sum(values) / len(values))), 4)


def _why_included(
    item: Mapping[str, Any],
    proof_items: Mapping[str, Mapping[str, Any]],
    seed_ids: Sequence[str],
) -> str:
    raw = item.get("raw") if isinstance(item.get("raw"), Mapping) else {}
    explicit = item.get("why_included") or raw.get("why_included") or raw.get("why") or raw.get("reason")
    reasons = _as_list(explicit)
    if not reasons:
        for seed_id in seed_ids:
            proof = proof_items.get(seed_id)
            if proof:
                reasons.extend(_as_list(proof.get("why") or proof.get("reason")))
    if not reasons:
        reasons.append("included in supplied context for graph proof bundling")
    return "; ".join(str(reason) for reason in reasons if reason)


def _completeness(
    graph_paths: Sequence[Mapping[str, Any]],
    source_refs: Mapping[str, Sequence[str]],
    temporal_validity: Mapping[str, Any],
    confidence: float,
) -> Dict[str, Any]:
    has_graph_path = any(path.get("status") == "matched" for path in graph_paths)
    has_source_scene_or_event = bool(source_refs.get("scenes") or source_refs.get("event_ids"))
    has_source_file_or_test = bool(source_refs.get("files") or source_refs.get("tests"))
    has_temporal_validity = temporal_validity.get("basis") != "not_temporal_fact_backed"
    missing: List[str] = []
    if not has_graph_path:
        missing.append("graph_path")
    if not has_source_scene_or_event:
        missing.append("source_scene_or_event")
    if not has_source_file_or_test:
        missing.append("source_file_or_test")
    if not has_temporal_validity:
        missing.append("temporal_validity")
    if confidence is None:
        missing.append("confidence")
    return {
        "has_graph_path": has_graph_path,
        "has_source_scene_or_event": has_source_scene_or_event,
        "has_source_file_or_test": has_source_file_or_test,
        "has_temporal_validity": has_temporal_validity,
        "missing": missing,
    }


def _unmatched_graph_path(item: Mapping[str, Any], seed_ids: Sequence[str]) -> Dict[str, Any]:
    return {
        "schema_version": GRAPH_PATH_SCHEMA,
        "path_id": "gpath_unmatched_" + _stable_hash({"item": item.get("context_id"), "seeds": list(seed_ids)}, 14),
        "status": "unmatched",
        "seed_node_id": seed_ids[0] if seed_ids else None,
        "node_ids": [],
        "edge_ids": [],
        "relations": [],
        "confidence": 0.0,
        "source_graphs": [],
        "why_path": "no matching graph node found for context item",
        "evidence_pointers": [],
    }


def _graph_source_summary(
    context_graph: Mapping[str, Any],
    repo_graph: Mapping[str, Any],
    graph_index: _GraphIndex,
) -> Dict[str, Any]:
    return _clean(
        {
            "repo": graph_index.graph_repo,
            "context_graph": {
                "schema_version": context_graph.get("schema_version"),
                "brain_ref": context_graph.get("brain_ref"),
                "node_count": len(context_graph.get("nodes") or []),
                "edge_count": len(context_graph.get("edges") or []),
                "proof_item_count": len(context_graph.get("proof_items") or []),
            },
            "repo_graph": {
                "schema_version": repo_graph.get("schema_version"),
                "artifact_id": repo_graph.get("artifact_id"),
                "brain_ref": repo_graph.get("brain_ref"),
                "node_count": len(repo_graph.get("nodes") or []),
                "edge_count": len(repo_graph.get("edges") or []),
            },
            "combined": {
                "node_count": len(graph_index.nodes),
                "edge_count": len(graph_index.edges),
            },
        }
    )


def _bundle_summary(items: Sequence[Mapping[str, Any]], missing_graph_paths: Sequence[str]) -> Dict[str, Any]:
    temporal_count = sum(1 for item in items if item.get("temporal_facts"))
    graph_count = sum(1 for item in items if (item.get("completeness") or {}).get("has_graph_path"))
    source_count = sum(
        1
        for item in items
        if (item.get("completeness") or {}).get("has_source_scene_or_event")
        or (item.get("completeness") or {}).get("has_source_file_or_test")
    )
    confidences = [_safe_float(item.get("confidence"), 0.0) for item in items if item.get("confidence") is not None]
    return {
        "context_item_count": len(items),
        "graph_attached_count": graph_count,
        "temporal_fact_backed_count": temporal_count,
        "source_backed_count": source_count,
        "missing_graph_path_context_ids": list(missing_graph_paths),
        "average_confidence": round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
    }


def _path_reason(seed_id: str, relations: Sequence[str]) -> str:
    if not relations:
        return f"context item matched graph node {seed_id}"
    return f"context item matched graph node {seed_id}; expanded via " + " -> ".join(relations)


def _path_evidence_pointers(edges: Sequence[Mapping[str, Any]]) -> List[str]:
    pointers: List[str] = []
    for edge in edges:
        provenance = edge.get("provenance") if isinstance(edge.get("provenance"), Mapping) else {}
        metadata = edge.get("metadata") if isinstance(edge.get("metadata"), Mapping) else {}
        _extend(pointers, _as_list(provenance.get("evidence_pointer")))
        _extend(pointers, _as_list(metadata.get("evidence_pointers")))
        _extend(pointers, _as_list(metadata.get("reason")) + _as_list(metadata.get("reasons")))
    return _dedupe(str(pointer) for pointer in pointers if pointer)


def _edge_sort_key(edge: Mapping[str, Any]) -> Tuple[int, str, str, str]:
    edge_type = str(edge.get("type") or "")
    return (
        _RELATION_PRIORITY.get(edge_type, 50),
        str(edge.get("source") or ""),
        str(edge.get("target") or ""),
        str(edge.get("id") or ""),
    )


def _path_sort_key(path: Mapping[str, Any]) -> Tuple[int, int, str]:
    relations = [str(value) for value in path.get("relations") or []]
    first_priority = min((_RELATION_PRIORITY.get(rel, 50) for rel in relations), default=99)
    return (first_priority, -len(relations), str(path.get("path_id") or ""))


def _node_path(node: Mapping[str, Any]) -> str:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), Mapping) else {}
    path = metadata.get("path") or node.get("path") or _file_path_from_node_id(str(node.get("id") or ""))
    if not path and str(node.get("type") or "") in {"file", "test", "config"}:
        path = node.get("label")
    return _normalize_path(path)


def _node_is_test(node: Mapping[str, Any]) -> bool:
    return str(node.get("type") or "") == "test" or _is_test_path(_node_path(node))


def _file_path_from_node_id(node_id: str) -> str:
    if node_id.startswith("file:"):
        return _normalize_path(node_id.split(":", 1)[1])
    return ""


def _extract_paths(value: str) -> List[str]:
    paths = [_normalize_path(match.group("path")) for match in _PATH_RE.finditer(str(value or ""))]
    return _dedupe(path for path in paths if path)


def _normalize_node_id(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("file:"):
        return "file:" + _normalize_path(text.split(":", 1)[1])
    return text


def _normalize_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("\\", "/")
    if text.startswith("./"):
        text = text[2:]
    return os.path.normpath(text).replace("\\", "/")


def _is_test_path(path: str) -> bool:
    normalized = _normalize_path(path).lower()
    name = normalized.rsplit("/", 1)[-1]
    return normalized.startswith("tests/") or "/tests/" in normalized or name.startswith("test_") or ".test." in name or ".spec." in name


def _looks_like_temporal_fact(value: Mapping[str, Any]) -> bool:
    return any(key in value for key in ("valid_from", "valid_to", "observed_at", "fact_text", "source_scene"))


def _fact_active_as_of(fact: Mapping[str, Any], as_of: str) -> bool:
    status = str(fact.get("status") or "active")
    if status in {"rejected", "retracted"}:
        return False
    valid_from = _normalize_iso(str(fact.get("valid_from") or ""), fallback_now=False)
    valid_to = _normalize_iso(str(fact.get("valid_to") or ""), fallback_now=False)
    if valid_from and valid_from > as_of:
        return False
    if valid_to and valid_to <= as_of:
        return False
    if status in {"active", "verified"} and fact.get("active", True):
        return True
    return bool(valid_to and valid_from and valid_from <= as_of < valid_to)


def _active_window(valid_from: Any, valid_to: Any, as_of: str) -> Optional[bool]:
    start = _normalize_iso(str(valid_from), fallback_now=False) if valid_from else None
    end = _normalize_iso(str(valid_to), fallback_now=False) if valid_to else None
    if start and start > as_of:
        return False
    if end and end <= as_of:
        return False
    if start or end:
        return True
    return None


def _normalize_iso(value: Optional[str], *, fallback_now: bool) -> Optional[str]:
    if not value:
        return _now_iso() if fallback_now else None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stable_hash(data: Any, length: int = 16) -> str:
    raw = json.dumps(data, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _tokens(value: str) -> Set[str]:
    return {match.lower() for match in _TOKEN_RE.findall(str(value or "").replace("_", " "))}


def _as_list(value: Any) -> List[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return sorted(value)
    return [value]


def _extend(target: List[str], values: Iterable[Any]) -> None:
    for value in values:
        if value in (None, "", [], {}):
            continue
        target.append(str(value))


def _dedupe(values: Iterable[Any]) -> List[Any]:
    out: List[Any] = []
    seen: Set[str] = set()
    for value in values:
        key = str(value)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clean(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _clean(child)
            for key, child in value.items()
            if child not in (None, "", [], {})
        }
    if isinstance(value, list):
        return [_clean(child) for child in value]
    return value


__all__ = [
    "GRAPH_PATH_SCHEMA",
    "GRAPH_PROOF_BUNDLE_SCHEMA",
    "GRAPH_PROOF_ITEM_SCHEMA",
    "GraphProofBundleError",
    "build_graph_proof_bundle",
    "build_graph_proof_bundle_from_brain",
]
