from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

from .world_memory.capture_store import CaptureStore
from .world_memory.causal_graph import AUTOMATIC_REL_TABLES, CausalGraphProjection
from .world_memory.schema import (
    CAUSAL_PROJECTION_VERSION,
    CAUSAL_SCHEMA_VERSION,
    CHECKPOINT_CAUSAL_EDGE_TYPES,
    CausalEdge,
    EventFrame,
    RawEvent,
)


GRAPH_PROJECTION_HARDENING_VERSION = "graph_projection_hardening.v1"
PROJECTION_ROLE = "derived_kuzu_projection_not_truth"
DEFAULT_LIMIT = 100_000

NODE_TABLES = (
    "RawEvent",
    "EventFrame",
    "MemoryThread",
    "Scene",
    "Episode",
    "Surface",
    "Entity",
    "Artifact",
    "Memory",
)

VALID_PRIVACY_SCOPES = ("public", "project", "connector", "global", "private")
_SCOPE_RANK = {scope: index for index, scope in enumerate(VALID_PRIVACY_SCOPES)}


def rebuild_and_verify_projection(
    projection: CausalGraphProjection,
    capture_store: CaptureStore,
    *,
    user_id: str = "default",
    limit: int = DEFAULT_LIMIT,
) -> Dict[str, Any]:
    """Rebuild the derived Kuzu graph, write a manifest, then verify it.

    SQLite/CaptureStore remains the source of truth. The manifest is only a
    projection checksum and contract marker that helps catch stale or corrupted
    Kuzu state before callers use graph traversal results.
    """

    rebuild = projection.rebuild(capture_store, user_id=user_id)
    manifest = write_projection_manifest(
        projection,
        capture_store,
        user_id=user_id,
        rebuild_result=rebuild,
        limit=limit,
    )
    report = verify_projection(
        projection,
        capture_store,
        user_id=user_id,
        expected_manifest=manifest,
        require_manifest=True,
        limit=limit,
    )
    report["rebuild"] = rebuild
    return report


def build_projection_manifest(
    projection: CausalGraphProjection,
    capture_store: CaptureStore,
    *,
    user_id: str = "default",
    rebuild_result: Optional[Mapping[str, Any]] = None,
    limit: int = DEFAULT_LIMIT,
) -> Dict[str, Any]:
    truth = _truth_snapshot(capture_store, user_id=user_id, limit=limit)
    projection_counts = _projection_counts(projection)
    return {
        "id": _manifest_id(user_id),
        "hardening_version": GRAPH_PROJECTION_HARDENING_VERSION,
        "schema_version": CAUSAL_SCHEMA_VERSION,
        "projection_version": CAUSAL_PROJECTION_VERSION,
        "projection_role": PROJECTION_ROLE,
        "generated_at": _utcnow(),
        "user_id": user_id,
        "truth": {
            "backend": "sqlite",
            "db_path": str(capture_store.db_path),
            "active_counts": truth["active_counts"],
            "inactive_counts": truth["inactive_counts"],
            "fingerprint": truth["fingerprint"],
        },
        "projection": {
            "backend": "kuzu",
            "db_path": str(projection.db_path),
            "counts": projection_counts,
        },
        "rebuild": dict(rebuild_result or {}),
    }


def write_projection_manifest(
    projection: CausalGraphProjection,
    capture_store: CaptureStore,
    *,
    user_id: str = "default",
    rebuild_result: Optional[Mapping[str, Any]] = None,
    limit: int = DEFAULT_LIMIT,
) -> Dict[str, Any]:
    manifest = build_projection_manifest(
        projection,
        capture_store,
        user_id=user_id,
        rebuild_result=rebuild_result,
        limit=limit,
    )
    conn = projection._connect()
    try:
        _ensure_manifest_schema(conn)
        conn.execute(
            """
            MERGE (m:ProjectionManifest {id: $id})
            SET m.hardening_version = $hardening_version,
                m.schema_version = $schema_version,
                m.projection_version = $projection_version,
                m.user_id = $user_id,
                m.truth_backend = $truth_backend,
                m.truth_db_path = $truth_db_path,
                m.projection_backend = $projection_backend,
                m.projection_db_path = $projection_db_path,
                m.projection_role = $projection_role,
                m.generated_at = $generated_at,
                m.truth_fingerprint = $truth_fingerprint,
                m.truth_counts_json = $truth_counts_json,
                m.projection_counts_json = $projection_counts_json,
                m.manifest_json = $manifest_json
            """,
            _clean_params(
                {
                    "id": manifest["id"],
                    "hardening_version": manifest["hardening_version"],
                    "schema_version": manifest["schema_version"],
                    "projection_version": manifest["projection_version"],
                    "user_id": manifest["user_id"],
                    "truth_backend": manifest["truth"]["backend"],
                    "truth_db_path": manifest["truth"]["db_path"],
                    "projection_backend": manifest["projection"]["backend"],
                    "projection_db_path": manifest["projection"]["db_path"],
                    "projection_role": manifest["projection_role"],
                    "generated_at": manifest["generated_at"],
                    "truth_fingerprint": manifest["truth"]["fingerprint"],
                    "truth_counts_json": json.dumps(manifest["truth"]["active_counts"], sort_keys=True),
                    "projection_counts_json": json.dumps(manifest["projection"]["counts"], sort_keys=True),
                    "manifest_json": json.dumps(manifest, sort_keys=True),
                }
            ),
        )
    finally:
        projection._close(conn)
    return manifest


def load_projection_manifest(
    projection: CausalGraphProjection,
    *,
    user_id: str = "default",
) -> Optional[Dict[str, Any]]:
    conn = projection._connect()
    try:
        _ensure_manifest_schema(conn)
        return _read_manifest(conn, user_id)
    finally:
        projection._close(conn)


def verify_projection(
    projection: CausalGraphProjection,
    capture_store: CaptureStore,
    *,
    user_id: str = "default",
    expected_manifest: Optional[Mapping[str, Any]] = None,
    require_manifest: bool = True,
    limit: int = DEFAULT_LIMIT,
) -> Dict[str, Any]:
    """Verify Kuzu as a rebuildable projection of CaptureStore truth."""

    truth = _truth_snapshot(capture_store, user_id=user_id, limit=limit)
    graph = _graph_snapshot(projection, user_id=user_id)

    checks = {
        "manifest": _check_manifest(
            graph.get("manifest"),
            truth,
            projection,
            capture_store,
            user_id=user_id,
            expected_manifest=expected_manifest,
            require_manifest=require_manifest,
        ),
        "versions": _check_versions(graph),
        "source_refs": _check_source_refs(graph, truth),
        "orphan_edges": _check_orphan_edges(graph, truth),
        "privacy_scope": _check_privacy_scopes(graph, truth),
        "redaction_deletion": _redaction_deletion_report(graph, truth),
    }
    errors = _collect_errors(checks)

    return {
        "ok": not errors,
        "backend": "kuzu",
        "projection_role": PROJECTION_ROLE,
        "db_path": str(projection.db_path),
        "schema_version": CAUSAL_SCHEMA_VERSION,
        "projection_version": CAUSAL_PROJECTION_VERSION,
        "manifest": graph.get("manifest"),
        "truth": {
            "backend": "sqlite",
            "db_path": str(capture_store.db_path),
            "active_counts": truth["active_counts"],
            "inactive_counts": truth["inactive_counts"],
            "fingerprint": truth["fingerprint"],
        },
        "checks": checks,
        "errors": errors,
    }


def _truth_snapshot(capture_store: CaptureStore, *, user_id: str, limit: int) -> Dict[str, Any]:
    all_events = capture_store.list_raw_events(
        user_id=user_id,
        limit=limit,
        include_deleted=True,
        include_redacted=True,
        order="asc",
    )
    all_frames = capture_store.list_event_frames(
        user_id=user_id,
        limit=limit,
        include_deleted=True,
        include_redacted=True,
    )
    all_edges = capture_store.list_causal_edges(
        user_id=user_id,
        limit=limit,
        include_deleted=True,
        include_redacted=True,
    )
    active_events = [event for event in all_events if _active(event)]
    active_frames = [frame for frame in all_frames if _active(frame)]
    active_edges = [edge for edge in all_edges if _active(edge)]

    active_event_ids = {event.id for event in active_events}
    active_frame_ids = {frame.id for frame in active_frames}
    active_edge_ids = {edge.id for edge in active_edges}
    inactive_event_ids = {event.id for event in all_events if not _active(event)}
    inactive_frame_ids = {frame.id for frame in all_frames if not _active(frame)}
    inactive_edge_ids = {edge.id for edge in all_edges if not _active(edge)}

    return {
        "events": {event.id: event for event in all_events},
        "frames": {frame.id: frame for frame in all_frames},
        "edges": {edge.id: edge for edge in all_edges},
        "active_events": {event.id: event for event in active_events},
        "active_frames": {frame.id: frame for frame in active_frames},
        "active_edges": {edge.id: edge for edge in active_edges},
        "active_event_ids": active_event_ids,
        "active_frame_ids": active_frame_ids,
        "active_edge_ids": active_edge_ids,
        "inactive_event_ids": inactive_event_ids,
        "inactive_frame_ids": inactive_frame_ids,
        "inactive_edge_ids": inactive_edge_ids,
        "active_counts": {
            "RawEvent": len(active_events),
            "EventFrame": len(active_frames),
            "CausalEdge": len(active_edges),
        },
        "inactive_counts": {
            "RawEvent": len(inactive_event_ids),
            "EventFrame": len(inactive_frame_ids),
            "CausalEdge": len(inactive_edge_ids),
        },
        "fingerprint": _truth_fingerprint(active_events, active_frames, active_edges),
    }


def _graph_snapshot(projection: CausalGraphProjection, *, user_id: str) -> Dict[str, Any]:
    conn = projection._connect()
    try:
        projection._create_schema(conn)
        _ensure_manifest_schema(conn)
        nodes = {table: _read_nodes(projection, conn, table, user_id=user_id) for table in NODE_TABLES}
        rels: Dict[str, List[Dict[str, Any]]] = {}
        for rel, (source, target) in AUTOMATIC_REL_TABLES.items():
            rels[rel] = _read_rels(projection, conn, rel, source, target, user_id=user_id)
        for rel in CHECKPOINT_CAUSAL_EDGE_TYPES:
            rels[rel] = _read_rels(projection, conn, rel, "EventFrame", "EventFrame", user_id=user_id)
        return {
            "nodes": nodes,
            "rels": rels,
            "manifest": _read_manifest(conn, user_id),
            "counts": projection._projection_counts(conn),
        }
    finally:
        projection._close(conn)


def _read_nodes(
    projection: CausalGraphProjection,
    conn: Any,
    table: str,
    *,
    user_id: str,
) -> List[Dict[str, Any]]:
    rows = projection._rows(
        conn,
        f"""
        MATCH (n:{table})
        WHERE n.user_id = $user_id
        RETURN n.id, n.schema_version, n.projection_version, n.sqlite_id,
               n.user_id, n.privacy_scope, n.deleted_at, n.redacted_at,
               n.redaction_reason, n.metadata_json
        """,
        {"user_id": user_id},
    )
    return [
        {
            "table": table,
            "id": row[0],
            "schema_version": row[1],
            "projection_version": row[2],
            "sqlite_id": row[3],
            "user_id": row[4],
            "privacy_scope": row[5],
            "deleted_at": row[6],
            "redacted_at": row[7],
            "redaction_reason": row[8],
            "metadata": _loads_dict(row[9]),
        }
        for row in rows
    ]


def _read_rels(
    projection: CausalGraphProjection,
    conn: Any,
    rel: str,
    source: str,
    target: str,
    *,
    user_id: str,
) -> List[Dict[str, Any]]:
    rows = projection._rows(
        conn,
        f"""
        MATCH (a:{source})-[r:{rel}]->(b:{target})
        WHERE r.user_id = $user_id
        RETURN a.id, b.id, a.privacy_scope, b.privacy_scope,
               r.id, r.schema_version, r.projection_version, r.sqlite_id,
               r.source_sqlite_id, r.user_id, r.privacy_scope, r.confidence,
               r.status, r.evidence_json, r.explanation, r.created_at
        """,
        {"user_id": user_id},
    )
    return [
        {
            "rel": rel,
            "source_table": source,
            "target_table": target,
            "source_id": row[0],
            "target_id": row[1],
            "source_scope": row[2],
            "target_scope": row[3],
            "id": row[4],
            "schema_version": row[5],
            "projection_version": row[6],
            "sqlite_id": row[7],
            "source_sqlite_id": row[8],
            "user_id": row[9],
            "privacy_scope": row[10],
            "confidence": row[11],
            "status": row[12],
            "evidence": _loads_list(row[13]),
            "explanation": row[14],
            "created_at": row[15],
        }
        for row in rows
    ]


def _check_manifest(
    manifest: Optional[Mapping[str, Any]],
    truth: Mapping[str, Any],
    projection: CausalGraphProjection,
    capture_store: CaptureStore,
    *,
    user_id: str,
    expected_manifest: Optional[Mapping[str, Any]],
    require_manifest: bool,
) -> Dict[str, Any]:
    issues: List[Dict[str, Any]] = []
    if not manifest:
        if require_manifest:
            issues.append({"code": "manifest_missing", "message": "projection manifest is missing"})
        return {"ok": not issues, "issues": issues}

    expected_values = {
        "hardening_version": GRAPH_PROJECTION_HARDENING_VERSION,
        "schema_version": CAUSAL_SCHEMA_VERSION,
        "projection_version": CAUSAL_PROJECTION_VERSION,
        "projection_role": PROJECTION_ROLE,
        "user_id": user_id,
    }
    for key, expected in expected_values.items():
        if manifest.get(key) != expected:
            issues.append(
                {
                    "code": "manifest_value_mismatch",
                    "field": key,
                    "expected": expected,
                    "actual": manifest.get(key),
                }
            )

    truth_info = manifest.get("truth") if isinstance(manifest.get("truth"), dict) else {}
    projection_info = manifest.get("projection") if isinstance(manifest.get("projection"), dict) else {}
    if truth_info.get("backend") != "sqlite":
        issues.append({"code": "manifest_truth_backend", "actual": truth_info.get("backend")})
    if truth_info.get("db_path") != str(capture_store.db_path):
        issues.append(
            {
                "code": "manifest_truth_db_path",
                "expected": str(capture_store.db_path),
                "actual": truth_info.get("db_path"),
            }
        )
    if projection_info.get("backend") != "kuzu":
        issues.append({"code": "manifest_projection_backend", "actual": projection_info.get("backend")})
    if projection_info.get("db_path") != str(projection.db_path):
        issues.append(
            {
                "code": "manifest_projection_db_path",
                "expected": str(projection.db_path),
                "actual": projection_info.get("db_path"),
            }
        )
    if truth_info.get("fingerprint") != truth.get("fingerprint"):
        issues.append(
            {
                "code": "manifest_truth_fingerprint_stale",
                "expected": truth.get("fingerprint"),
                "actual": truth_info.get("fingerprint"),
            }
        )
    if truth_info.get("active_counts") != truth.get("active_counts"):
        issues.append(
            {
                "code": "manifest_truth_counts_stale",
                "expected": truth.get("active_counts"),
                "actual": truth_info.get("active_counts"),
            }
        )
    if expected_manifest and manifest.get("id") != expected_manifest.get("id"):
        issues.append(
            {
                "code": "manifest_id_mismatch",
                "expected": expected_manifest.get("id"),
                "actual": manifest.get("id"),
            }
        )
    return {
        "ok": not issues,
        "manifest_id": manifest.get("id"),
        "projection_role": manifest.get("projection_role"),
        "truth_fingerprint": truth_info.get("fingerprint"),
        "issues": issues,
    }


def _check_versions(graph: Mapping[str, Any]) -> Dict[str, Any]:
    bad_schema_nodes: List[Dict[str, Any]] = []
    bad_projection_nodes: List[Dict[str, Any]] = []
    bad_schema_rels: List[Dict[str, Any]] = []
    bad_projection_rels: List[Dict[str, Any]] = []

    for table, nodes in graph["nodes"].items():
        for node in nodes:
            if node.get("schema_version") != CAUSAL_SCHEMA_VERSION:
                bad_schema_nodes.append(_identity(node, table=table))
            if node.get("projection_version") != CAUSAL_PROJECTION_VERSION:
                bad_projection_nodes.append(_identity(node, table=table))
    for rel, rows in graph["rels"].items():
        for row in rows:
            if row.get("schema_version") != CAUSAL_SCHEMA_VERSION:
                bad_schema_rels.append(_identity(row, rel=rel))
            if row.get("projection_version") != CAUSAL_PROJECTION_VERSION:
                bad_projection_rels.append(_identity(row, rel=rel))

    return {
        "ok": not (bad_schema_nodes or bad_projection_nodes or bad_schema_rels or bad_projection_rels),
        "expected_schema_version": CAUSAL_SCHEMA_VERSION,
        "expected_projection_version": CAUSAL_PROJECTION_VERSION,
        "bad_schema_nodes": bad_schema_nodes,
        "bad_projection_nodes": bad_projection_nodes,
        "bad_schema_rels": bad_schema_rels,
        "bad_projection_rels": bad_projection_rels,
    }


def _check_source_refs(graph: Mapping[str, Any], truth: Mapping[str, Any]) -> Dict[str, Any]:
    raw_nodes = _by_id(graph["nodes"]["RawEvent"])
    frame_nodes = _by_id(graph["nodes"]["EventFrame"])
    checkpoint_rels = _checkpoint_rels(graph)
    expected_checkpoint_edges = {
        edge_id: edge
        for edge_id, edge in truth["active_edges"].items()
        if edge.edge_type in CHECKPOINT_CAUSAL_EDGE_TYPES
        and edge.source_id in truth["active_frame_ids"]
        and edge.target_id in truth["active_frame_ids"]
    }

    raw_missing = sorted(truth["active_event_ids"] - set(raw_nodes))
    raw_extra = sorted(set(raw_nodes) - truth["active_event_ids"])
    frame_missing = sorted(truth["active_frame_ids"] - set(frame_nodes))
    frame_extra = sorted(set(frame_nodes) - truth["active_frame_ids"])
    checkpoint_missing = sorted(set(expected_checkpoint_edges) - {row["id"] for row in checkpoint_rels})
    checkpoint_extra = sorted({row["id"] for row in checkpoint_rels} - set(expected_checkpoint_edges))

    raw_bad_sqlite = [
        _identity(node, table="RawEvent")
        for node in raw_nodes.values()
        if node.get("sqlite_id") != node.get("id")
    ]
    frame_bad_sqlite = [
        _identity(node, table="EventFrame")
        for node in frame_nodes.values()
        if node.get("sqlite_id") != node.get("id")
    ]
    frames_with_missing_sources = []
    for frame_id, frame in truth["active_frames"].items():
        missing = sorted(set(frame.source_event_ids) - truth["active_event_ids"])
        if missing:
            frames_with_missing_sources.append({"frame_id": frame_id, "missing_event_ids": missing})

    checkpoint_mismatches = []
    for row in checkpoint_rels:
        edge = expected_checkpoint_edges.get(str(row.get("id") or ""))
        if not edge:
            continue
        if row.get("source_sqlite_id") != edge.id:
            checkpoint_mismatches.append(
                {
                    "edge_id": row.get("id"),
                    "field": "source_sqlite_id",
                    "expected": edge.id,
                    "actual": row.get("source_sqlite_id"),
                }
            )
        if row.get("source_id") != edge.source_id or row.get("target_id") != edge.target_id:
            checkpoint_mismatches.append(
                {
                    "edge_id": row.get("id"),
                    "field": "endpoints",
                    "expected": [edge.source_id, edge.target_id],
                    "actual": [row.get("source_id"), row.get("target_id")],
                }
            )

    ok = not (
        raw_missing
        or raw_extra
        or frame_missing
        or frame_extra
        or checkpoint_missing
        or checkpoint_extra
        or raw_bad_sqlite
        or frame_bad_sqlite
        or frames_with_missing_sources
        or checkpoint_mismatches
    )
    return {
        "ok": ok,
        "raw_event_missing": raw_missing,
        "raw_event_extra": raw_extra,
        "event_frame_missing": frame_missing,
        "event_frame_extra": frame_extra,
        "checkpoint_edge_missing": checkpoint_missing,
        "checkpoint_edge_extra": checkpoint_extra,
        "raw_event_bad_sqlite_refs": raw_bad_sqlite,
        "event_frame_bad_sqlite_refs": frame_bad_sqlite,
        "frames_with_missing_source_events": frames_with_missing_sources,
        "checkpoint_edge_mismatches": checkpoint_mismatches,
    }


def _check_orphan_edges(graph: Mapping[str, Any], truth: Mapping[str, Any]) -> Dict[str, Any]:
    node_ids = {table: set(_by_id(nodes)) for table, nodes in graph["nodes"].items()}
    structural_orphans: List[Dict[str, Any]] = []
    missing_truth_refs: List[Dict[str, Any]] = []
    inactive_truth_refs: List[Dict[str, Any]] = []

    for rel, rows in graph["rels"].items():
        for row in rows:
            if row["source_id"] not in node_ids.get(row["source_table"], set()):
                structural_orphans.append({**_identity(row, rel=rel), "missing_endpoint": "source"})
            if row["target_id"] not in node_ids.get(row["target_table"], set()):
                structural_orphans.append({**_identity(row, rel=rel), "missing_endpoint": "target"})

            if rel in AUTOMATIC_REL_TABLES:
                source_ref = str(row.get("source_sqlite_id") or "")
                if source_ref in truth["inactive_event_ids"]:
                    inactive_truth_refs.append({**_identity(row, rel=rel), "source_sqlite_id": source_ref})
                elif source_ref not in truth["active_event_ids"]:
                    missing_truth_refs.append({**_identity(row, rel=rel), "source_sqlite_id": source_ref})
                for evidence_id in _string_items(row.get("evidence")):
                    if evidence_id in truth["inactive_event_ids"]:
                        inactive_truth_refs.append({**_identity(row, rel=rel), "evidence_event_id": evidence_id})
                    elif evidence_id not in truth["active_event_ids"]:
                        missing_truth_refs.append({**_identity(row, rel=rel), "evidence_event_id": evidence_id})
            else:
                edge_ref = str(row.get("source_sqlite_id") or row.get("id") or "")
                if edge_ref in truth["inactive_edge_ids"]:
                    inactive_truth_refs.append({**_identity(row, rel=rel), "source_sqlite_id": edge_ref})
                elif edge_ref not in truth["active_edge_ids"]:
                    missing_truth_refs.append({**_identity(row, rel=rel), "source_sqlite_id": edge_ref})

    return {
        "ok": not (structural_orphans or missing_truth_refs or inactive_truth_refs),
        "structural_orphans": structural_orphans,
        "missing_truth_refs": missing_truth_refs,
        "inactive_truth_refs": inactive_truth_refs,
    }


def _check_privacy_scopes(graph: Mapping[str, Any], truth: Mapping[str, Any]) -> Dict[str, Any]:
    invalid_node_scopes: List[Dict[str, Any]] = []
    invalid_rel_scopes: List[Dict[str, Any]] = []
    raw_mismatches: List[Dict[str, Any]] = []
    frame_mismatches: List[Dict[str, Any]] = []
    checkpoint_mismatches: List[Dict[str, Any]] = []
    rel_scope_leaks: List[Dict[str, Any]] = []
    target_scope_leaks: List[Dict[str, Any]] = []

    for table, nodes in graph["nodes"].items():
        for node in nodes:
            scope = str(node.get("privacy_scope") or "")
            if scope not in _SCOPE_RANK:
                invalid_node_scopes.append({**_identity(node, table=table), "privacy_scope": scope})
            if table == "RawEvent" and node.get("id") in truth["active_events"]:
                expected = truth["active_events"][node["id"]].privacy_scope
                if scope != expected:
                    raw_mismatches.append(
                        {
                            **_identity(node, table=table),
                            "expected": expected,
                            "actual": scope,
                        }
                    )
            if table == "EventFrame" and node.get("id") in truth["active_frames"]:
                expected = truth["active_frames"][node["id"]].privacy_scope
                if scope != expected:
                    frame_mismatches.append(
                        {
                            **_identity(node, table=table),
                            "expected": expected,
                            "actual": scope,
                        }
                    )

    for rel, rows in graph["rels"].items():
        for row in rows:
            scope = str(row.get("privacy_scope") or "")
            source_scope = str(row.get("source_scope") or "")
            target_scope = str(row.get("target_scope") or "")
            if scope not in _SCOPE_RANK:
                invalid_rel_scopes.append({**_identity(row, rel=rel), "privacy_scope": scope})
                continue
            strictest_endpoint_scope = _strictest_scope([source_scope, target_scope])
            if _scope_rank(scope) < _scope_rank(strictest_endpoint_scope):
                rel_scope_leaks.append(
                    {
                        **_identity(row, rel=rel),
                        "expected_at_least": strictest_endpoint_scope,
                        "actual": scope,
                        "source_scope": source_scope,
                        "target_scope": target_scope,
                    }
                )
            if rel in AUTOMATIC_REL_TABLES and _scope_rank(target_scope) < _scope_rank(scope):
                target_scope_leaks.append(
                    {
                        **_identity(row, rel=rel),
                        "target_id": row.get("target_id"),
                        "target_scope": target_scope,
                        "edge_scope": scope,
                    }
                )
            if rel in CHECKPOINT_CAUSAL_EDGE_TYPES and row.get("id") in truth["active_edges"]:
                expected = truth["active_edges"][row["id"]].privacy_scope
                if scope != expected:
                    checkpoint_mismatches.append(
                        {
                            **_identity(row, rel=rel),
                            "expected": expected,
                            "actual": scope,
                        }
                    )

    return {
        "ok": not (
            invalid_node_scopes
            or invalid_rel_scopes
            or raw_mismatches
            or frame_mismatches
            or checkpoint_mismatches
            or rel_scope_leaks
            or target_scope_leaks
        ),
        "valid_scopes": list(VALID_PRIVACY_SCOPES),
        "invalid_node_scopes": invalid_node_scopes,
        "invalid_rel_scopes": invalid_rel_scopes,
        "raw_event_scope_mismatches": raw_mismatches,
        "event_frame_scope_mismatches": frame_mismatches,
        "checkpoint_edge_scope_mismatches": checkpoint_mismatches,
        "rel_scope_leaks": rel_scope_leaks,
        "target_scope_leaks": target_scope_leaks,
    }


def _redaction_deletion_report(graph: Mapping[str, Any], truth: Mapping[str, Any]) -> Dict[str, Any]:
    raw_nodes = _by_id(graph["nodes"]["RawEvent"])
    frame_nodes = _by_id(graph["nodes"]["EventFrame"])
    projected_inactive_raw = sorted(set(raw_nodes) & truth["inactive_event_ids"])
    projected_inactive_frames = sorted(set(frame_nodes) & truth["inactive_frame_ids"])
    projected_marked_raw = sorted(
        node_id
        for node_id, node in raw_nodes.items()
        if _present(node.get("deleted_at")) or _present(node.get("redacted_at"))
    )
    projected_marked_frames = sorted(
        node_id
        for node_id, node in frame_nodes.items()
        if _present(node.get("deleted_at")) or _present(node.get("redacted_at"))
    )

    projected_edges_from_inactive_refs: List[Dict[str, Any]] = []
    for rel, rows in graph["rels"].items():
        for row in rows:
            if rel in AUTOMATIC_REL_TABLES:
                source_ref = str(row.get("source_sqlite_id") or "")
                if source_ref in truth["inactive_event_ids"]:
                    projected_edges_from_inactive_refs.append(
                        {**_identity(row, rel=rel), "source_sqlite_id": source_ref}
                    )
                for evidence_id in _string_items(row.get("evidence")):
                    if evidence_id in truth["inactive_event_ids"]:
                        projected_edges_from_inactive_refs.append(
                            {**_identity(row, rel=rel), "evidence_event_id": evidence_id}
                        )
            elif str(row.get("source_sqlite_id") or row.get("id") or "") in truth["inactive_edge_ids"]:
                projected_edges_from_inactive_refs.append(
                    {
                        **_identity(row, rel=rel),
                        "source_sqlite_id": str(row.get("source_sqlite_id") or row.get("id") or ""),
                    }
                )

    return {
        "ok": not (
            projected_inactive_raw
            or projected_inactive_frames
            or projected_marked_raw
            or projected_marked_frames
            or projected_edges_from_inactive_refs
        ),
        "truth_inactive_counts": truth["inactive_counts"],
        "projected_inactive_raw_event_ids": projected_inactive_raw,
        "projected_inactive_event_frame_ids": projected_inactive_frames,
        "projected_marked_raw_event_ids": projected_marked_raw,
        "projected_marked_event_frame_ids": projected_marked_frames,
        "projected_edges_from_inactive_source_refs": projected_edges_from_inactive_refs,
    }


def _projection_counts(projection: CausalGraphProjection) -> Dict[str, int]:
    conn = projection._connect()
    try:
        projection._create_schema(conn)
        return projection._projection_counts(conn)
    finally:
        projection._close(conn)


def _ensure_manifest_schema(conn: Any) -> None:
    conn.execute(
        """
        CREATE NODE TABLE IF NOT EXISTS ProjectionManifest(
            id STRING,
            hardening_version STRING,
            schema_version STRING,
            projection_version STRING,
            user_id STRING,
            truth_backend STRING,
            truth_db_path STRING,
            projection_backend STRING,
            projection_db_path STRING,
            projection_role STRING,
            generated_at STRING,
            truth_fingerprint STRING,
            truth_counts_json STRING,
            projection_counts_json STRING,
            manifest_json STRING,
            PRIMARY KEY(id)
        );
        """
    )


def _read_manifest(conn: Any, user_id: str) -> Optional[Dict[str, Any]]:
    result = conn.execute(
        """
        MATCH (m:ProjectionManifest {id: $id})
        RETURN m.manifest_json
        LIMIT 1
        """,
        {"id": _manifest_id(user_id)},
    )
    rows = result.get_all()
    if not rows:
        return None
    return _loads_dict(rows[0][0])


def _checkpoint_rels(graph: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for rel in CHECKPOINT_CAUSAL_EDGE_TYPES:
        rows.extend(graph["rels"].get(rel, []))
    return rows


def _truth_fingerprint(
    events: Sequence[RawEvent],
    frames: Sequence[EventFrame],
    edges: Sequence[CausalEdge],
) -> str:
    payload = {
        "schema_version": CAUSAL_SCHEMA_VERSION,
        "projection_version": CAUSAL_PROJECTION_VERSION,
        "events": [
            {
                "id": event.id,
                "schema_version": event.schema_version,
                "user_id": event.user_id,
                "timestamp": event.timestamp,
                "privacy_scope": event.privacy_scope,
                "metadata": event.metadata,
            }
            for event in sorted(events, key=lambda item: item.id)
        ],
        "frames": [
            {
                "id": frame.id,
                "schema_version": frame.schema_version,
                "user_id": frame.user_id,
                "source_event_ids": sorted(frame.source_event_ids),
                "privacy_scope": frame.privacy_scope,
            }
            for frame in sorted(frames, key=lambda item: item.id)
        ],
        "edges": [
            {
                "id": edge.id,
                "schema_version": edge.schema_version,
                "user_id": edge.user_id,
                "source_id": edge.source_id,
                "target_id": edge.target_id,
                "edge_type": edge.edge_type,
                "status": edge.status,
                "evidence_event_ids": sorted(edge.evidence_event_ids),
                "privacy_scope": edge.privacy_scope,
            }
            for edge in sorted(edges, key=lambda item: item.id)
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _collect_errors(checks: Mapping[str, Mapping[str, Any]]) -> List[str]:
    errors: List[str] = []
    for name, check in checks.items():
        if check.get("ok"):
            continue
        issue_count = 0
        for key, value in check.items():
            if key == "ok" or not value:
                continue
            if isinstance(value, list):
                issue_count += len(value)
            elif key == "issues":
                issue_count += len(value)
        errors.append(f"{name} failed ({issue_count or 1} issue(s))")
    return errors


def _active(item: Any) -> bool:
    return not _present(getattr(item, "deleted_at", None)) and not _present(getattr(item, "redacted_at", None))


def _present(value: Any) -> bool:
    return value is not None and str(value) != ""


def _manifest_id(user_id: str) -> str:
    return f"projection-manifest:{user_id or 'default'}"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _by_id(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(row.get("id")): dict(row) for row in rows}


def _identity(row: Mapping[str, Any], *, table: Optional[str] = None, rel: Optional[str] = None) -> Dict[str, Any]:
    result: Dict[str, Any] = {"id": row.get("id")}
    if table:
        result["table"] = table
    if rel:
        result["rel"] = rel
        result["source_id"] = row.get("source_id")
        result["target_id"] = row.get("target_id")
    return result


def _string_items(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return []


def _loads_dict(raw: Any) -> Dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _loads_list(raw: Any) -> List[Any]:
    if isinstance(raw, list):
        return raw
    try:
        value = json.loads(raw or "[]")
    except Exception:
        return []
    return value if isinstance(value, list) else []


def _strictest_scope(scopes: Iterable[str]) -> str:
    return max((str(scope or "global") for scope in scopes), key=_scope_rank)


def _scope_rank(scope: str) -> int:
    return _SCOPE_RANK.get(str(scope or "global"), _SCOPE_RANK["private"])


def _clean_params(params: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: ("" if value is None else value) for key, value in params.items()}
