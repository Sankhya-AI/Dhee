from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import kuzu

from .capture_store import CaptureStore
from .schema import (
    CAUSAL_PROJECTION_VERSION,
    CAUSAL_SCHEMA_VERSION,
    CHECKPOINT_CAUSAL_EDGE_TYPES,
    CausalEdge,
    EventFrame,
    RawEvent,
)


AUTOMATIC_REL_TABLES = {
    "TEMPORAL_NEXT": ("RawEvent", "RawEvent"),
    "OBSERVED_ON": ("RawEvent", "Surface"),
    "MENTIONS": ("RawEvent", "Entity"),
    "CREATED": ("RawEvent", "Artifact"),
    "UPDATED": ("RawEvent", "Memory"),
    "BELONGS_TO": ("RawEvent", "Scene"),
    "PROJECTED_INTO": ("RawEvent", "MemoryThread"),
}


class CausalGraphProjection:
    """Kuzu projection over the SQLite causal-scene truth tables."""

    def __init__(self, db_path: str):
        path = Path(db_path).expanduser()
        if path.suffix:
            self.db_path = path
            self.root_dir = path.parent
        else:
            self.root_dir = path
            self.db_path = path / "causal_scene.kuzu"
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def rebuild(self, capture_store: CaptureStore, *, user_id: str = "default") -> Dict[str, Any]:
        self.delete()
        conn = self._connect()
        try:
            self._create_schema(conn)
            events = capture_store.list_raw_events(
                user_id=user_id,
                limit=100_000,
                include_deleted=False,
                include_redacted=False,
                order="asc",
            )
            frames = capture_store.list_event_frames(
                user_id=user_id,
                limit=100_000,
                include_deleted=False,
                include_redacted=False,
            )
            edges = capture_store.list_causal_edges(
                user_id=user_id,
                limit=100_000,
                include_deleted=False,
                include_redacted=False,
            )
            counts = self._project(conn, events=events, frames=frames, edges=edges)
            return {
                "status": "rebuilt",
                "backend": "kuzu",
                "db_path": str(self.db_path),
                "schema_version": CAUSAL_SCHEMA_VERSION,
                "projection_version": CAUSAL_PROJECTION_VERSION,
                "counts": counts,
            }
        finally:
            self._close(conn)

    def sync(self, capture_store: CaptureStore, *, user_id: str = "default") -> Dict[str, Any]:
        # V1 keeps sync rebuild-backed so projection corruption never becomes memory loss.
        result = self.rebuild(capture_store, user_id=user_id)
        result["status"] = "synced"
        return result

    def delete(self) -> None:
        for candidate in (self.db_path, Path(str(self.db_path) + ".wal")):
            if candidate.is_dir():
                shutil.rmtree(candidate)
            elif candidate.exists():
                candidate.unlink()

    def verify(self, capture_store: CaptureStore, *, user_id: str = "default") -> Dict[str, Any]:
        conn = self._connect()
        errors: List[str] = []
        checks: Dict[str, Any] = {}
        try:
            self._create_schema(conn)
            expected_events = capture_store.list_raw_events(
                user_id=user_id,
                limit=100_000,
                include_deleted=False,
                include_redacted=False,
                order="asc",
            )
            expected_ids = {event.id for event in expected_events}
            graph_ids = set(
                self._single_column(
                    conn,
                    """
                    MATCH (e:RawEvent)
                    WHERE e.user_id = $user_id
                    RETURN e.id
                    """,
                    {"user_id": user_id},
                )
            )
            checks["node_count"] = {
                "expected_raw_events": len(expected_ids),
                "projected_raw_events": len(graph_ids),
                "ok": len(expected_ids) == len(graph_ids),
            }
            missing = sorted(expected_ids - graph_ids)
            extra = sorted(graph_ids - expected_ids)
            if missing:
                errors.append(f"missing RawEvent projection(s): {', '.join(missing[:5])}")
            if extra:
                errors.append(f"extra RawEvent projection(s): {', '.join(extra[:5])}")

            bad_schema = self._single_column(
                conn,
                """
                MATCH (e:RawEvent)
                WHERE e.user_id = $user_id AND e.schema_version <> $schema_version
                RETURN e.id
                """,
                {"user_id": user_id, "schema_version": CAUSAL_SCHEMA_VERSION},
            )
            checks["schema_version"] = {"bad_nodes": bad_schema, "ok": not bad_schema}
            if bad_schema:
                errors.append("schema-version mismatch in RawEvent projection")

            bad_projection = self._single_column(
                conn,
                """
                MATCH (e:RawEvent)
                WHERE e.user_id = $user_id AND e.projection_version <> $projection_version
                RETURN e.id
                """,
                {"user_id": user_id, "projection_version": CAUSAL_PROJECTION_VERSION},
            )
            checks["projection_version"] = {"bad_nodes": bad_projection, "ok": not bad_projection}
            if bad_projection:
                errors.append("projection-version mismatch in RawEvent projection")

            active_redactions = self._single_column(
                conn,
                """
                MATCH (e:RawEvent)
                WHERE e.user_id = $user_id
                  AND (e.deleted_at <> '' OR e.redacted_at <> '')
                RETURN e.id
                """,
                {"user_id": user_id},
            )
            checks["privacy"] = {"redacted_or_deleted_active_nodes": active_redactions, "ok": not active_redactions}
            if active_redactions:
                errors.append("redacted/deleted RawEvent projected as active")

            duplicate_rows = self._rows(
                conn,
                """
                MATCH (e:RawEvent)
                WHERE e.user_id = $user_id
                RETURN e.id, count(*) AS c
                """,
                {"user_id": user_id},
            )
            duplicates = [row[0] for row in duplicate_rows if len(row) > 1 and int(row[1] or 0) > 1]
            checks["duplicate_nodes"] = {"duplicates": duplicates, "ok": not duplicates}
            if duplicates:
                errors.append("duplicate RawEvent projection nodes")

            orphan_errors = self._orphan_errors(conn)
            checks["orphan_edges"] = {"errors": orphan_errors, "ok": not orphan_errors}
            errors.extend(orphan_errors)

            source_ref_missing = self._single_column(
                conn,
                """
                MATCH (e:RawEvent)
                WHERE e.user_id = $user_id AND e.sqlite_id = ''
                RETURN e.id
                """,
                {"user_id": user_id},
            )
            checks["source_refs"] = {"missing": source_ref_missing, "ok": not source_ref_missing}
            if source_ref_missing:
                errors.append("RawEvent projection missing SQLite source refs")

            return {
                "ok": not errors,
                "backend": "kuzu",
                "db_path": str(self.db_path),
                "schema_version": CAUSAL_SCHEMA_VERSION,
                "projection_version": CAUSAL_PROJECTION_VERSION,
                "checks": checks,
                "errors": errors,
            }
        finally:
            self._close(conn)

    def show_event(self, event_id: str) -> Dict[str, Any]:
        conn = self._connect()
        try:
            rows = self._rows(
                conn,
                """
                MATCH (e:RawEvent {id: $event_id})
                RETURN e.id, e.schema_version, e.projection_version, e.sqlite_id,
                       e.user_id, e.session_id, e.source_app, e.event_type,
                       e.timestamp, e.privacy_scope, e.metadata_json
                """,
                {"event_id": event_id},
            )
            if not rows:
                return {"status": "not_found", "event_id": event_id}
            row = rows[0]
            return {
                "status": "ok",
                "event": {
                    "id": row[0],
                    "schema_version": row[1],
                    "projection_version": row[2],
                    "sqlite_id": row[3],
                    "user_id": row[4],
                    "session_id": row[5],
                    "source_app": row[6],
                    "event_type": row[7],
                    "timestamp": row[8],
                    "privacy_scope": row[9],
                    "metadata": _loads_dict(row[10]),
                },
                "incoming": self._event_relations(conn, event_id, "incoming"),
                "outgoing": self._event_relations(conn, event_id, "outgoing"),
                "threads": self._threads_for_event(conn, event_id),
            }
        finally:
            self._close(conn)

    def show_cone(self, event_id: str, *, direction: str = "backward", depth: int = 3) -> Dict[str, Any]:
        conn = self._connect()
        try:
            frontier = [event_id]
            seen = {event_id}
            hops: List[Dict[str, Any]] = []
            for hop in range(max(int(depth), 1)):
                next_frontier: List[str] = []
                for current in frontier:
                    relations = self._temporal_relations(conn, current, direction)
                    for relation in relations:
                        other = relation["from"] if direction == "backward" else relation["to"]
                        if other not in seen:
                            next_frontier.append(other)
                            seen.add(other)
                        hops.append({"hop": hop + 1, **relation})
                frontier = next_frontier
                if not frontier:
                    break
            return {
                "event_id": event_id,
                "direction": direction,
                "depth": depth,
                "causal_path": hops,
                "evidence": [{"event_id": item} for item in sorted(seen)],
            }
        finally:
            self._close(conn)

    def show_scene(self, scene_id: str) -> Dict[str, Any]:
        conn = self._connect()
        try:
            scene_rows = self._rows(
                conn,
                "MATCH (s:Scene {id: $scene_id}) RETURN s.id, s.title, s.status, s.summary, s.privacy_scope, s.metadata_json",
                {"scene_id": scene_id},
            )
            if not scene_rows:
                return {"status": "not_found", "scene_id": scene_id}
            events = self._rows(
                conn,
                """
                MATCH (e:RawEvent)-[r:BELONGS_TO]->(s:Scene {id: $scene_id})
                RETURN e.id, e.timestamp, e.source_app, e.event_type, r.evidence_json
                ORDER BY e.timestamp ASC
                """,
                {"scene_id": scene_id},
            )
            return {
                "status": "ok",
                "scene": {
                    "id": scene_rows[0][0],
                    "title": scene_rows[0][1],
                    "status": scene_rows[0][2],
                    "summary": scene_rows[0][3],
                    "privacy_scope": scene_rows[0][4],
                    "metadata": _loads_dict(scene_rows[0][5]),
                },
                "supporting_events": [
                    {
                        "event_id": row[0],
                        "timestamp": row[1],
                        "source_app": row[2],
                        "event_type": row[3],
                        "evidence": _loads_list(row[4]),
                    }
                    for row in events
                ],
            }
        finally:
            self._close(conn)

    def show_thread(self, thread_id: str) -> Dict[str, Any]:
        conn = self._connect()
        try:
            thread_rows = self._rows(
                conn,
                "MATCH (t:MemoryThread {id: $thread_id}) RETURN t.id, t.thread_type, t.title, t.status, t.summary, t.privacy_scope, t.metadata_json",
                {"thread_id": thread_id},
            )
            if not thread_rows:
                return {"status": "not_found", "thread_id": thread_id}
            events = self._rows(
                conn,
                """
                MATCH (e:RawEvent)-[r:PROJECTED_INTO]->(t:MemoryThread {id: $thread_id})
                RETURN e.id, e.timestamp, e.source_app, e.event_type, r.evidence_json
                ORDER BY e.timestamp ASC
                """,
                {"thread_id": thread_id},
            )
            return {
                "status": "ok",
                "thread": {
                    "id": thread_rows[0][0],
                    "thread_type": thread_rows[0][1],
                    "title": thread_rows[0][2],
                    "status": thread_rows[0][3],
                    "summary": thread_rows[0][4],
                    "privacy_scope": thread_rows[0][5],
                    "metadata": _loads_dict(thread_rows[0][6]),
                },
                "events": [
                    {
                        "event_id": row[0],
                        "timestamp": row[1],
                        "source_app": row[2],
                        "event_type": row[3],
                        "evidence": _loads_list(row[4]),
                    }
                    for row in events
                ],
            }
        finally:
            self._close(conn)

    def explain_retrieval(self, query_id: str) -> Dict[str, Any]:
        return {
            "query_id": query_id,
            "status": "not_recorded",
            "traversal": [],
            "evidence": [],
        }

    def get_active_frontier(self, *, user_id: str = "default", scope: str = "global") -> Dict[str, Any]:
        conn = self._connect()
        try:
            allowed = _allowed_scopes(scope)
            thread_scope_clause, thread_scope_params = _scope_where("t", allowed)
            threads = self._rows(
                conn,
                f"""
                MATCH (t:MemoryThread)
                WHERE t.user_id = $user_id
                  {thread_scope_clause}
                RETURN t.id, t.thread_type, t.title, t.status, t.summary, t.privacy_scope
                ORDER BY t.updated_at DESC
                LIMIT 12
                """,
                {"user_id": user_id, **thread_scope_params},
            )
            active_threads = [
                {
                    "thread_id": row[0],
                    "thread_type": row[1],
                    "title": row[2],
                    "status": row[3],
                    "summary": row[4],
                    "privacy_scope": row[5],
                    "evidence": self._thread_evidence(conn, row[0], allowed),
                }
                for row in threads
            ]
            event_scope_clause, event_scope_params = _scope_where("e", allowed)
            recent = self._rows(
                conn,
                f"""
                MATCH (e:RawEvent)
                WHERE e.user_id = $user_id
                  {event_scope_clause}
                RETURN e.id, e.timestamp, e.source_app, e.event_type, e.privacy_scope
                ORDER BY e.timestamp DESC
                LIMIT 1
                """,
                {"user_id": user_id, **event_scope_params},
            )
            recent_scene = None
            if recent:
                scene_rows = self._rows(
                    conn,
                    """
                    MATCH (e:RawEvent {id: $event_id})-[r:BELONGS_TO]->(s:Scene)
                    RETURN s.id, s.title, s.summary, s.privacy_scope
                    LIMIT 1
                    """,
                    {"event_id": recent[0][0]},
                )
                if scene_rows and scene_rows[0][3] in allowed:
                    recent_scene = {
                        "scene_id": scene_rows[0][0],
                        "title": scene_rows[0][1],
                        "summary": scene_rows[0][2],
                        "privacy_scope": scene_rows[0][3],
                    }
            preference_gems = sorted(
                self._list_gems(conn, user_id=user_id, scope=scope, kind="preference", limit=8).get("gems", []),
                key=lambda gem: _rank_preference_gem(gem, ""),
                reverse=True,
            )
            preference_signals = [_preference_signal_from_gem(gem) for gem in preference_gems]
            if not preference_signals:
                preference_signals = [
                    thread for thread in active_threads if thread.get("thread_type") == "preference"
                ]
            return {
                "active_threads": active_threads,
                "recent_scene": recent_scene,
                "last_verified_state": _last_event_state(recent[0]) if recent else "",
                "open_questions": [],
                "next_likely_need": "",
                "high_confidence_preferences": preference_signals,
                "evidence": [{"event_id": recent[0][0]}] if recent else [],
            }
        finally:
            self._close(conn)

    def why(self, *, event_id: Optional[str] = None, query: str = "", user_id: str = "default", scope: str = "global") -> Dict[str, Any]:
        target = event_id or self._find_event_for_query(query=query, user_id=user_id, scope=scope)
        if not target:
            return {
                "target_event_id": "",
                "likely_causes": [],
                "rejected_causes": [],
                "confidence": 0.0,
                "causal_path": [],
                "evidence": [],
            }
        cone = self.show_cone(target, direction="backward", depth=3)
        likely = [
            {
                "event_id": item["from"],
                "relation": item["edge_type"],
                "confidence": item.get("confidence", 0.0),
                "evidence": item.get("evidence", []),
            }
            for item in cone.get("causal_path", [])
        ]
        return {
            "target_event_id": target,
            "likely_causes": likely,
            "rejected_causes": [],
            "confidence": max([item.get("confidence", 0.0) for item in likely] or [0.0]),
            "causal_path": cone.get("causal_path", []),
            "evidence": cone.get("evidence", []),
        }

    def what_happened(self, *, target_id: str, user_id: str = "default", scope: str = "global") -> Dict[str, Any]:
        conn = self._connect()
        try:
            allowed = _allowed_scopes(scope)
            event_scope_clause, event_scope_params = _scope_where("e", allowed)
            events = self._rows(
                conn,
                f"""
                MATCH (e:RawEvent)-[r:PROJECTED_INTO]->(t:MemoryThread {{id: $target_id}})
                WHERE e.user_id = $user_id
                  {event_scope_clause}
                RETURN e.id, e.timestamp, e.source_app, e.event_type, e.privacy_scope
                ORDER BY e.timestamp ASC
                """,
                {"target_id": target_id, "user_id": user_id, **event_scope_params},
            )
            if not events:
                events = self._rows(
                    conn,
                    f"""
                    MATCH (e:RawEvent)-[r:BELONGS_TO]->(s:Scene {{id: $target_id}})
                    WHERE e.user_id = $user_id
                      {event_scope_clause}
                    RETURN e.id, e.timestamp, e.source_app, e.event_type, e.privacy_scope
                    ORDER BY e.timestamp ASC
                    """,
                    {"target_id": target_id, "user_id": user_id, **event_scope_params},
                )
            timeline = [
                {
                    "event_id": row[0],
                    "timestamp": row[1],
                    "source_app": row[2],
                    "event_type": row[3],
                    "privacy_scope": row[4],
                }
                for row in events
            ]
            return {
                "target_id": target_id,
                "ordered_timeline": timeline,
                "scene_boundaries": [],
                "source_events": [{"event_id": item["event_id"]} for item in timeline],
                "summary": _timeline_summary(timeline),
            }
        finally:
            self._close(conn)

    def handoff(self, *, user_id: str = "default", scope: str = "global") -> Dict[str, Any]:
        frontier = self.get_active_frontier(user_id=user_id, scope=scope)
        return {
            "active_causal_frontier": frontier,
            "blockers": [],
            "last_verified_state": frontier.get("last_verified_state", ""),
            "next_action_candidates": [],
            "evidence": frontier.get("evidence", []),
        }

    def preference(self, *, query: str = "", user_id: str = "default", scope: str = "global") -> Dict[str, Any]:
        gem_result = self.list_gems(user_id=user_id, scope=scope, kind="preference", limit=100)
        ranked_gems = sorted(
            gem_result.get("gems", []),
            key=lambda gem: _rank_preference_gem(gem, query),
            reverse=True,
        )
        if ranked_gems:
            top = ranked_gems[0]
            return {
                "query": query,
                "preference_signal": _preference_signal_from_gem(top),
                "confidence": _float(top.get("confidence"), 0.7),
                "supporting_events": [_supporting_event_from_gem(top)],
                "contradictions": [],
                "scope": scope,
                "retrieval_path": [
                    {
                        "mode": "preference",
                        "step": "list_gems",
                        "kind": "preference",
                        "candidate_count": gem_result.get("count", len(ranked_gems)),
                    },
                    {
                        "mode": "preference",
                        "step": "rank_preference_gems",
                        "query_tokens": _query_tokens(query),
                        "selected_event_id": top.get("event_id"),
                    },
                ],
            }

        frontier = self.get_active_frontier(user_id=user_id, scope=scope)
        preferences = frontier.get("high_confidence_preferences", [])
        return {
            "query": query,
            "preference_signal": preferences[0] if preferences else None,
            "confidence": 0.7 if preferences else 0.0,
            "supporting_events": (preferences[0].get("evidence", []) if preferences else []),
            "contradictions": [],
            "scope": scope,
            "retrieval_path": [
                {
                    "mode": "preference",
                    "step": "frontier_thread_fallback",
                    "candidate_count": len(preferences),
                }
            ],
        }

    def list_gems(
        self,
        *,
        user_id: str = "default",
        scope: str = "global",
        kind: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        conn = self._connect()
        try:
            return self._list_gems(conn, user_id=user_id, scope=scope, kind=kind, limit=limit)
        finally:
            self._close(conn)

    def show_gem(self, target: str, *, user_id: str = "default", scope: str = "global") -> Dict[str, Any]:
        event_id = _normalize_gem_event_id(target)
        conn = self._connect()
        try:
            allowed = _allowed_scopes(scope)
            scope_clause, scope_params = _scope_where("e", allowed)
            rows = self._rows(
                conn,
                f"""
                MATCH (e:RawEvent)
                WHERE e.user_id = $user_id
                  AND e.id = $event_id
                  AND e.source_app = 'memory-gem'
                  {scope_clause}
                RETURN e.id, e.schema_version, e.projection_version, e.sqlite_id,
                       e.timestamp, e.event_type, e.privacy_scope, e.content_ref, e.metadata_json
                LIMIT 1
                """,
                {"user_id": user_id, "event_id": event_id, **scope_params},
            )
            if not rows:
                return {
                    "status": "not_found",
                    "target": target,
                    "event_id": event_id,
                    "scope": scope,
                    "retrieval_path": [
                        {
                            "mode": "show_gem",
                            "step": "match_scoped_memory_gem",
                            "matched": False,
                        }
                    ],
                }
            row = rows[0]
            metadata = _loads_dict(row[8])
            gem = {
                "event_id": row[0],
                "schema_version": row[1],
                "projection_version": row[2],
                "sqlite_id": row[3],
                "gem_id": metadata.get("gem_id"),
                "kind": metadata.get("kind") or str(row[5]).replace("gem_", ""),
                "title": metadata.get("title") or "",
                "summary": metadata.get("summary") or "",
                "score": metadata.get("score"),
                "confidence": metadata.get("confidence"),
                "timestamp": row[4],
                "event_type": row[5],
                "privacy_scope": row[6],
                "content_ref": row[7],
            }
            source_memory_id = str(metadata.get("source_memory_id") or "").strip()
            source_event_id = str(metadata.get("source_event_id") or "").strip()
            evidence = metadata.get("evidence") or []
            threads = self._threads_for_event(conn, row[0])
            return {
                "status": "ok",
                "target": target,
                "gem": gem,
                "source_memory": {
                    "memory_id": source_memory_id,
                    "content_ref": row[7],
                    "source_event_id": source_event_id,
                    "source_app": metadata.get("source_app") or "",
                    "memory_type": metadata.get("memory_type") or "",
                    "categories": metadata.get("categories") or [],
                },
                "supporting_events": [
                    {
                        "event_id": row[0],
                        "source_memory_id": source_memory_id,
                        "source_event_id": source_event_id,
                        "content_ref": row[7],
                        "evidence": evidence,
                    }
                ],
                "threads": threads,
                "derived_summaries": [],
                "retrieval_path": [
                    {
                        "mode": "show_gem",
                        "step": "match_scoped_memory_gem",
                        "matched": True,
                        "event_id": row[0],
                    },
                    {
                        "mode": "show_gem",
                        "step": "load_thread_memberships",
                        "thread_count": len(threads),
                    },
                ],
            }
        finally:
            self._close(conn)

    def _list_gems(
        self,
        conn: kuzu.Connection,
        *,
        user_id: str,
        scope: str,
        kind: Optional[str],
        limit: int,
    ) -> Dict[str, Any]:
        allowed = _allowed_scopes(scope)
        scope_clause, scope_params = _scope_where("e", allowed)
        kind_clause = ""
        kind_filter = str(kind or "").strip().lower() or None
        params: Dict[str, Any] = {
            "user_id": user_id,
            "limit": max(1, int(limit or 50)),
            **scope_params,
        }
        if kind_filter:
            kind_clause = "AND e.event_type = $event_type"
            params["event_type"] = f"gem_{kind_filter}"
        rows = self._rows(
            conn,
            f"""
            MATCH (e:RawEvent)
            WHERE e.user_id = $user_id
              AND e.source_app = 'memory-gem'
              {scope_clause}
              {kind_clause}
            RETURN e.id, e.timestamp, e.event_type, e.privacy_scope, e.content_ref, e.metadata_json
            ORDER BY e.timestamp DESC
            LIMIT $limit
            """,
            params,
        )
        gems: List[Dict[str, Any]] = []
        by_kind: Dict[str, int] = {}
        for row in rows:
            metadata = _loads_dict(row[5])
            gem_kind = str(metadata.get("kind") or str(row[2]).replace("gem_", "") or "unknown")
            if kind_filter and gem_kind != kind_filter:
                continue
            by_kind[gem_kind] = by_kind.get(gem_kind, 0) + 1
            gems.append(
                {
                    "event_id": row[0],
                    "gem_id": metadata.get("gem_id"),
                    "kind": gem_kind,
                    "title": metadata.get("title") or "",
                    "summary": metadata.get("summary") or "",
                    "score": metadata.get("score"),
                    "confidence": metadata.get("confidence"),
                    "source_memory_id": metadata.get("source_memory_id"),
                    "source_event_id": metadata.get("source_event_id"),
                    "content_ref": row[4],
                    "timestamp": row[1],
                    "privacy_scope": row[3],
                    "evidence": metadata.get("evidence") or [],
                }
            )
        return {
            "count": len(gems),
            "by_kind": by_kind,
            "scope": scope,
            "kind": kind_filter,
            "gems": gems,
            "evidence": [
                {"event_id": item["event_id"], "source_memory_id": item["source_memory_id"]}
                for item in gems
            ],
        }

    def _connect(self) -> kuzu.Connection:
        db = kuzu.Database(str(self.db_path))
        conn = kuzu.Connection(db)
        self._last_db = db
        return conn

    @staticmethod
    def _close(conn: kuzu.Connection) -> None:
        try:
            conn.close()
        except Exception:
            pass

    def _create_schema(self, conn: kuzu.Connection) -> None:
        common_fields = """
            id STRING,
            schema_version STRING,
            projection_version STRING,
            sqlite_id STRING,
            user_id STRING,
            privacy_scope STRING,
            deleted_at STRING,
            redacted_at STRING,
            redaction_reason STRING,
            metadata_json STRING
        """
        common = f"{common_fields}, PRIMARY KEY(id)"
        conn.execute(
            """
            CREATE NODE TABLE IF NOT EXISTS RawEvent(
                id STRING,
                schema_version STRING,
                projection_version STRING,
                sqlite_id STRING,
                user_id STRING,
                session_id STRING,
                source_app STRING,
                namespace STRING,
                event_type STRING,
                timestamp STRING,
                content_ref STRING,
                content_hash STRING,
                privacy_scope STRING,
                deleted_at STRING,
                redacted_at STRING,
                redaction_reason STRING,
                metadata_json STRING,
                PRIMARY KEY(id)
            );
            """
        )
        conn.execute(
            """
            CREATE NODE TABLE IF NOT EXISTS EventFrame(
                id STRING,
                schema_version STRING,
                projection_version STRING,
                sqlite_id STRING,
                user_id STRING,
                frame_type STRING,
                summary STRING,
                source_event_ids_json STRING,
                confidence DOUBLE,
                privacy_scope STRING,
                deleted_at STRING,
                redacted_at STRING,
                redaction_reason STRING,
                metadata_json STRING,
                PRIMARY KEY(id)
            );
            """
        )
        conn.execute(
            f"""
            CREATE NODE TABLE IF NOT EXISTS Scene(
                {common_fields},
                title STRING,
                status STRING,
                summary STRING,
                updated_at STRING,
                PRIMARY KEY(id)
            );
            """
        )
        conn.execute(
            f"""
            CREATE NODE TABLE IF NOT EXISTS Episode(
                {common_fields},
                title STRING,
                status STRING,
                summary STRING,
                updated_at STRING,
                PRIMARY KEY(id)
            );
            """
        )
        conn.execute(
            f"""
            CREATE NODE TABLE IF NOT EXISTS MemoryThread(
                {common_fields},
                thread_type STRING,
                title STRING,
                status STRING,
                summary STRING,
                updated_at STRING,
                PRIMARY KEY(id)
            );
            """
        )
        for table in ["Memory", "Artifact", "Actor", "Entity", "Surface"]:
            conn.execute(f"CREATE NODE TABLE IF NOT EXISTS {table}({common});")
        rel_cols = """
            id STRING,
            schema_version STRING,
            projection_version STRING,
            sqlite_id STRING,
            source_sqlite_id STRING,
            user_id STRING,
            privacy_scope STRING,
            confidence DOUBLE,
            status STRING,
            evidence_json STRING,
            explanation STRING,
            created_at STRING
        """
        for rel, (source, target) in AUTOMATIC_REL_TABLES.items():
            conn.execute(f"CREATE REL TABLE IF NOT EXISTS {rel}(FROM {source} TO {target}, {rel_cols});")
        for rel in CHECKPOINT_CAUSAL_EDGE_TYPES:
            conn.execute(f"CREATE REL TABLE IF NOT EXISTS {rel}(FROM EventFrame TO EventFrame, {rel_cols});")

    def _project(
        self,
        conn: kuzu.Connection,
        *,
        events: Sequence[RawEvent],
        frames: Sequence[EventFrame],
        edges: Sequence[CausalEdge],
    ) -> Dict[str, int]:
        counts = {
            "RawEvent": 0,
            "EventFrame": 0,
            "MemoryThread": 0,
            "Scene": 0,
            "Episode": 0,
            "Surface": 0,
            "Entity": 0,
            "Artifact": 0,
            "Memory": 0,
            "automatic_edges": 0,
            "causal_edges": 0,
        }
        for event in events:
            self._insert_raw_event(conn, event)
            counts["RawEvent"] += 1
            counts["Surface"] += self._project_surface(conn, event)
            counts["Entity"] += self._project_entities(conn, event)
            artifact_count, memory_count = self._project_artifacts_and_memories(conn, event)
            counts["Artifact"] += artifact_count
            counts["Memory"] += memory_count
            scene_count, episode_count, scene_edge_count = self._project_scene_episode(conn, event)
            counts["Scene"] += scene_count
            counts["Episode"] += episode_count
            counts["automatic_edges"] += scene_edge_count
            thread_count, thread_edge_count = self._project_threads(conn, event)
            counts["MemoryThread"] += thread_count
            counts["automatic_edges"] += thread_edge_count

        for previous, current in zip(events, events[1:]):
            if previous.user_id != current.user_id:
                continue
            self._insert_rel(
                conn,
                "TEMPORAL_NEXT",
                "RawEvent",
                "RawEvent",
                previous.id,
                current.id,
                rel_id=f"temporal:{previous.id}:{current.id}",
                user_id=current.user_id,
                privacy_scope=_strictest_scope([previous.privacy_scope, current.privacy_scope]),
                confidence=1.0,
                status="observed",
                evidence=[previous.id, current.id],
                explanation="Events were adjacent in SQLite RawEvent timestamp order.",
                source_sqlite_id=current.id,
                created_at=current.timestamp,
            )
            counts["automatic_edges"] += 1

        for frame in frames:
            self._insert_event_frame(conn, frame)
            counts["EventFrame"] += 1

        frame_ids = {frame.id for frame in frames}
        for edge in edges:
            if edge.edge_type not in CHECKPOINT_CAUSAL_EDGE_TYPES:
                continue
            if edge.source_id not in frame_ids or edge.target_id not in frame_ids:
                continue
            self._insert_rel(
                conn,
                edge.edge_type,
                "EventFrame",
                "EventFrame",
                edge.source_id,
                edge.target_id,
                rel_id=edge.id,
                user_id=edge.user_id,
                privacy_scope=edge.privacy_scope,
                confidence=edge.confidence,
                status=edge.status,
                evidence=edge.evidence_event_ids,
                explanation=edge.explanation,
                source_sqlite_id=edge.id,
                created_at=edge.created_at,
            )
            counts["causal_edges"] += 1
        return self._projection_counts(conn)

    def _insert_raw_event(self, conn: kuzu.Connection, event: RawEvent) -> None:
        conn.execute(
            """
            MERGE (e:RawEvent {id: $id})
            SET e.schema_version = $schema_version,
                e.projection_version = $projection_version,
                e.sqlite_id = $sqlite_id,
                e.user_id = $user_id,
                e.session_id = $session_id,
                e.source_app = $source_app,
                e.namespace = $namespace,
                e.event_type = $event_type,
                e.timestamp = $timestamp,
                e.content_ref = $content_ref,
                e.content_hash = $content_hash,
                e.privacy_scope = $privacy_scope,
                e.deleted_at = $deleted_at,
                e.redacted_at = $redacted_at,
                e.redaction_reason = $redaction_reason,
                e.metadata_json = $metadata_json
            """,
            _clean_params(
                {
                    "id": event.id,
                    "schema_version": event.schema_version,
                    "projection_version": CAUSAL_PROJECTION_VERSION,
                    "sqlite_id": event.id,
                    "user_id": event.user_id,
                    "session_id": event.session_id,
                    "source_app": event.source_app,
                    "namespace": event.namespace,
                    "event_type": event.event_type,
                    "timestamp": event.timestamp,
                    "content_ref": event.content_ref,
                    "content_hash": event.content_hash,
                    "privacy_scope": event.privacy_scope,
                    "deleted_at": event.deleted_at,
                    "redacted_at": event.redacted_at,
                    "redaction_reason": event.redaction_reason,
                    "metadata_json": json.dumps(event.metadata, sort_keys=True),
                }
            ),
        )

    def _insert_event_frame(self, conn: kuzu.Connection, frame: EventFrame) -> None:
        conn.execute(
            """
            MERGE (f:EventFrame {id: $id})
            SET f.schema_version = $schema_version,
                f.projection_version = $projection_version,
                f.sqlite_id = $sqlite_id,
                f.user_id = $user_id,
                f.frame_type = $frame_type,
                f.summary = $summary,
                f.source_event_ids_json = $source_event_ids_json,
                f.confidence = $confidence,
                f.privacy_scope = $privacy_scope,
                f.deleted_at = $deleted_at,
                f.redacted_at = $redacted_at,
                f.redaction_reason = $redaction_reason,
                f.metadata_json = $metadata_json
            """,
            _clean_params(
                {
                    "id": frame.id,
                    "schema_version": frame.schema_version,
                    "projection_version": CAUSAL_PROJECTION_VERSION,
                    "sqlite_id": frame.id,
                    "user_id": frame.user_id,
                    "frame_type": frame.frame_type,
                    "summary": frame.summary,
                    "source_event_ids_json": json.dumps(frame.source_event_ids),
                    "confidence": float(frame.confidence),
                    "privacy_scope": frame.privacy_scope,
                    "deleted_at": frame.deleted_at,
                    "redacted_at": frame.redacted_at,
                    "redaction_reason": frame.redaction_reason,
                    "metadata_json": json.dumps(frame.metadata, sort_keys=True),
                }
            ),
        )

    def _insert_generic_node(
        self,
        conn: kuzu.Connection,
        table: str,
        node_id: str,
        *,
        user_id: str,
        privacy_scope: str,
        sqlite_id: str,
        metadata: Optional[Dict[str, Any]] = None,
        deleted_at: Optional[str] = None,
        redacted_at: Optional[str] = None,
        redaction_reason: Optional[str] = None,
    ) -> None:
        conn.execute(
            f"""
            MERGE (n:{table} {{id: $id}})
            SET n.schema_version = $schema_version,
                n.projection_version = $projection_version,
                n.sqlite_id = $sqlite_id,
                n.user_id = $user_id,
                n.privacy_scope = $privacy_scope,
                n.deleted_at = $deleted_at,
                n.redacted_at = $redacted_at,
                n.redaction_reason = $redaction_reason,
                n.metadata_json = $metadata_json
            """,
            _clean_params(
                {
                    "id": node_id,
                    "schema_version": CAUSAL_SCHEMA_VERSION,
                    "projection_version": CAUSAL_PROJECTION_VERSION,
                    "sqlite_id": sqlite_id,
                    "user_id": user_id,
                    "privacy_scope": privacy_scope,
                    "deleted_at": deleted_at,
                    "redacted_at": redacted_at,
                    "redaction_reason": redaction_reason,
                    "metadata_json": json.dumps(metadata or {}, sort_keys=True),
                }
            ),
        )

    def _insert_scene(
        self,
        conn: kuzu.Connection,
        node_id: str,
        *,
        user_id: str,
        privacy_scope: str,
        sqlite_id: str,
        title: str,
        status: str,
        summary: str,
        updated_at: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        conn.execute(
            """
            MERGE (n:Scene {id: $id})
            SET n.schema_version = $schema_version,
                n.projection_version = $projection_version,
                n.sqlite_id = $sqlite_id,
                n.user_id = $user_id,
                n.privacy_scope = $privacy_scope,
                n.deleted_at = '',
                n.redacted_at = '',
                n.redaction_reason = '',
                n.metadata_json = $metadata_json,
                n.title = $title,
                n.status = $status,
                n.summary = $summary,
                n.updated_at = $updated_at
            """,
            _clean_params(
                {
                    "id": node_id,
                    "schema_version": CAUSAL_SCHEMA_VERSION,
                    "projection_version": CAUSAL_PROJECTION_VERSION,
                    "sqlite_id": sqlite_id,
                    "user_id": user_id,
                    "privacy_scope": privacy_scope,
                    "metadata_json": json.dumps(metadata or {}, sort_keys=True),
                    "title": title,
                    "status": status,
                    "summary": summary,
                    "updated_at": updated_at,
                }
            ),
        )

    def _insert_episode(
        self,
        conn: kuzu.Connection,
        node_id: str,
        *,
        user_id: str,
        privacy_scope: str,
        sqlite_id: str,
        title: str,
        status: str,
        summary: str,
        updated_at: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        conn.execute(
            """
            MERGE (n:Episode {id: $id})
            SET n.schema_version = $schema_version,
                n.projection_version = $projection_version,
                n.sqlite_id = $sqlite_id,
                n.user_id = $user_id,
                n.privacy_scope = $privacy_scope,
                n.deleted_at = '',
                n.redacted_at = '',
                n.redaction_reason = '',
                n.metadata_json = $metadata_json,
                n.title = $title,
                n.status = $status,
                n.summary = $summary,
                n.updated_at = $updated_at
            """,
            _clean_params(
                {
                    "id": node_id,
                    "schema_version": CAUSAL_SCHEMA_VERSION,
                    "projection_version": CAUSAL_PROJECTION_VERSION,
                    "sqlite_id": sqlite_id,
                    "user_id": user_id,
                    "privacy_scope": privacy_scope,
                    "metadata_json": json.dumps(metadata or {}, sort_keys=True),
                    "title": title,
                    "status": status,
                    "summary": summary,
                    "updated_at": updated_at,
                }
            ),
        )

    def _project_surface(self, conn: kuzu.Connection, event: RawEvent) -> int:
        source = event.source_app or "unknown"
        node_id = f"surface:{source}"
        self._insert_generic_node(
            conn,
            "Surface",
            node_id,
            user_id=event.user_id,
            privacy_scope=event.privacy_scope,
            sqlite_id=event.id,
            metadata={"source_app": source, "surface_type": "app"},
        )
        self._insert_rel(
            conn,
            "OBSERVED_ON",
            "RawEvent",
            "Surface",
            event.id,
            node_id,
            rel_id=f"observed:{event.id}:{node_id}",
            user_id=event.user_id,
            privacy_scope=event.privacy_scope,
            confidence=1.0,
            status="observed",
            evidence=[event.id],
            explanation="RawEvent source_app was deterministically projected as the observed surface.",
            source_sqlite_id=event.id,
            created_at=event.timestamp,
        )
        return 1

    def _project_entities(self, conn: kuzu.Connection, event: RawEvent) -> int:
        entities = _metadata_list(event.metadata, "entities")
        if not entities and event.metadata.get("entity"):
            entities = [str(event.metadata["entity"])]
        count = 0
        for entity in entities:
            node_id = f"entity:{_slug(entity)}"
            self._insert_generic_node(
                conn,
                "Entity",
                node_id,
                user_id=event.user_id,
                privacy_scope=event.privacy_scope,
                sqlite_id=event.id,
                metadata={"name": entity},
            )
            self._insert_rel(
                conn,
                "MENTIONS",
                "RawEvent",
                "Entity",
                event.id,
                node_id,
                rel_id=f"mentions:{event.id}:{node_id}",
                user_id=event.user_id,
                privacy_scope=event.privacy_scope,
                confidence=1.0,
                status="observed",
                evidence=[event.id],
                explanation="Entity was extracted from deterministic RawEvent metadata.",
                source_sqlite_id=event.id,
                created_at=event.timestamp,
            )
            count += 1
        return count

    def _project_artifacts_and_memories(self, conn: kuzu.Connection, event: RawEvent) -> Tuple[int, int]:
        artifact_count = 0
        memory_count = 0
        artifact_id = str(event.metadata.get("artifact_id") or "").strip()
        if artifact_id:
            node_id = f"artifact:{artifact_id}"
            self._insert_generic_node(
                conn,
                "Artifact",
                node_id,
                user_id=event.user_id,
                privacy_scope=event.privacy_scope,
                sqlite_id=artifact_id,
                metadata={"artifact_id": artifact_id},
            )
            self._insert_rel(
                conn,
                "CREATED",
                "RawEvent",
                "Artifact",
                event.id,
                node_id,
                rel_id=f"created:{event.id}:{node_id}",
                user_id=event.user_id,
                privacy_scope=event.privacy_scope,
                confidence=1.0,
                status="observed",
                evidence=[event.id],
                explanation="RawEvent metadata points at a created artifact.",
                source_sqlite_id=event.id,
                created_at=event.timestamp,
            )
            artifact_count += 1
        memory_id = str(event.metadata.get("memory_id") or "").strip()
        if memory_id:
            node_id = f"memory:{memory_id}"
            self._insert_generic_node(
                conn,
                "Memory",
                node_id,
                user_id=event.user_id,
                privacy_scope=event.privacy_scope,
                sqlite_id=memory_id,
                metadata={"memory_id": memory_id},
            )
            self._insert_rel(
                conn,
                "UPDATED",
                "RawEvent",
                "Memory",
                event.id,
                node_id,
                rel_id=f"updated:{event.id}:{node_id}",
                user_id=event.user_id,
                privacy_scope=event.privacy_scope,
                confidence=1.0,
                status="observed",
                evidence=[event.id],
                explanation="RawEvent metadata points at an updated Dhee memory.",
                source_sqlite_id=event.id,
                created_at=event.timestamp,
            )
            memory_count += 1
        return artifact_count, memory_count

    def _project_scene_episode(self, conn: kuzu.Connection, event: RawEvent) -> Tuple[int, int, int]:
        if not event.session_id:
            return 0, 0, 0
        scene_id = f"scene:{event.session_id}"
        episode_id = f"episode:{event.session_id}"
        self._insert_scene(
            conn,
            scene_id,
            user_id=event.user_id,
            privacy_scope=event.privacy_scope,
            sqlite_id=event.session_id,
            title=f"{event.source_app or 'app'} session",
            status="active",
            summary="Session-local raw event cluster.",
            updated_at=event.timestamp,
            metadata={
                "episode_id": episode_id,
            },
        )
        self._insert_episode(
            conn,
            episode_id,
            user_id=event.user_id,
            privacy_scope=event.privacy_scope,
            sqlite_id=event.session_id,
            title=f"{event.source_app or 'app'} episode",
            status="active",
            summary="Session-local episode projection.",
            updated_at=event.timestamp,
            metadata={"scene_id": scene_id},
        )
        self._insert_rel(
            conn,
            "BELONGS_TO",
            "RawEvent",
            "Scene",
            event.id,
            scene_id,
            rel_id=f"belongs:{event.id}:{scene_id}",
            user_id=event.user_id,
            privacy_scope=event.privacy_scope,
            confidence=1.0,
            status="observed",
            evidence=[event.id],
            explanation="RawEvent session_id deterministically groups it into a session scene.",
            source_sqlite_id=event.id,
            created_at=event.timestamp,
        )
        return 1, 1, 1

    def _project_threads(self, conn: kuzu.Connection, event: RawEvent) -> Tuple[int, int]:
        gem_kind = _gem_kind(event)
        thread_specs = [
            ("source", f"source:{event.source_app or 'unknown'}", f"{event.source_app or 'unknown'} events"),
            ("event_type", f"event_type:{event.event_type or 'unknown'}", f"{event.event_type or 'unknown'} events"),
        ]
        for raw_thread in _metadata_list(event.metadata, "threads"):
            thread_specs.append(("custom", f"thread:{_slug(raw_thread)}", raw_thread))
        if event.source_app in {"gmail", "mail"}:
            thread_specs.append(("gmail", "gmail", "Gmail"))
        if event.source_app in {"chrome", "arc", "firefox", "safari", "browser"}:
            thread_specs.append(("browser", "browser", "Browser"))
        if event.event_type in {"preference", "user_correction", "correction"}:
            thread_specs.append(("preference", "preference", "User preference signals"))
        if event.event_type in {"artifact", "artifact_created"} or event.metadata.get("artifact_id"):
            thread_specs.append(("artifact", "artifact", "Artifacts"))
        if event.metadata.get("project"):
            thread_specs.append(("project", f"project:{_slug(str(event.metadata['project']))}", str(event.metadata["project"])))
        if event.metadata.get("contact"):
            thread_specs.append(("contact", f"contact:{_slug(str(event.metadata['contact']))}", str(event.metadata["contact"])))
        if event.event_type in {"learning", "lesson", "skill"}:
            thread_specs.append(("learning", "learning", "Learning"))
        if gem_kind:
            thread_specs.append(("gem", "gems", "Memory gems"))
            thread_specs.append(("gem_kind", f"gem:{gem_kind}", f"{gem_kind.title()} gems"))
            if gem_kind == "preference":
                thread_specs.append(("preference", "preference", "User preference signals"))
            elif gem_kind == "decision":
                thread_specs.append(("decision", "decision", "Decisions"))
            elif gem_kind == "learning":
                thread_specs.append(("learning", "learning", "Learning"))
            elif gem_kind == "task":
                thread_specs.append(("task", "task", "Tasks"))
            elif gem_kind == "artifact":
                thread_specs.append(("artifact", "artifact", "Artifacts"))
            source_memory_id = str(event.metadata.get("source_memory_id") or "").strip()
            if source_memory_id:
                thread_specs.append(("source_memory", f"memory:{source_memory_id}", f"Source memory {source_memory_id[:8]}"))
        seen: set[str] = set()
        node_count = 0
        edge_count = 0
        for thread_type, thread_key, title in thread_specs:
            node_id = f"thread:{thread_key}"
            if node_id in seen:
                continue
            seen.add(node_id)
            self._insert_memory_thread(
                conn,
                node_id,
                user_id=event.user_id,
                privacy_scope=event.privacy_scope,
                sqlite_id=event.id,
                thread_type=thread_type,
                title=title,
                updated_at=event.timestamp,
            )
            self._insert_rel(
                conn,
                "PROJECTED_INTO",
                "RawEvent",
                "MemoryThread",
                event.id,
                node_id,
                rel_id=f"projected:{event.id}:{node_id}",
                user_id=event.user_id,
                privacy_scope=event.privacy_scope,
                confidence=1.0,
                status="observed",
                evidence=[event.id],
                explanation="Thread projection was deterministically derived from RawEvent metadata/source/type.",
                source_sqlite_id=event.id,
                created_at=event.timestamp,
            )
            node_count += 1
            edge_count += 1
        return node_count, edge_count

    def _insert_memory_thread(
        self,
        conn: kuzu.Connection,
        node_id: str,
        *,
        user_id: str,
        privacy_scope: str,
        sqlite_id: str,
        thread_type: str,
        title: str,
        updated_at: str,
    ) -> None:
        metadata = {
            "thread_type": thread_type,
            "title": title,
            "status": "active",
            "summary": title,
            "updated_at": updated_at,
        }
        conn.execute(
            """
            MERGE (n:MemoryThread {id: $id})
            SET n.schema_version = $schema_version,
                n.projection_version = $projection_version,
                n.sqlite_id = $sqlite_id,
                n.user_id = $user_id,
                n.privacy_scope = $privacy_scope,
                n.deleted_at = '',
                n.redacted_at = '',
                n.redaction_reason = '',
                n.metadata_json = $metadata_json,
                n.thread_type = $thread_type,
                n.title = $title,
                n.status = 'active',
                n.summary = $summary,
                n.updated_at = $updated_at
            """,
            _clean_params(
                {
                    "id": node_id,
                    "schema_version": CAUSAL_SCHEMA_VERSION,
                    "projection_version": CAUSAL_PROJECTION_VERSION,
                    "sqlite_id": sqlite_id,
                    "user_id": user_id,
                    "privacy_scope": privacy_scope,
                    "metadata_json": json.dumps(metadata, sort_keys=True),
                    "thread_type": thread_type,
                    "title": title,
                    "summary": title,
                    "updated_at": updated_at,
                }
            ),
        )

    def _insert_rel(
        self,
        conn: kuzu.Connection,
        rel: str,
        source_table: str,
        target_table: str,
        source_id: str,
        target_id: str,
        *,
        rel_id: str,
        user_id: str,
        privacy_scope: str,
        confidence: float,
        status: str,
        evidence: Sequence[str],
        explanation: str,
        source_sqlite_id: str,
        created_at: str,
    ) -> None:
        conn.execute(
            f"""
            MATCH (source:{source_table} {{id: $source_id}}), (target:{target_table} {{id: $target_id}})
            CREATE (source)-[:{rel} {{
                id: $id,
                schema_version: $schema_version,
                projection_version: $projection_version,
                sqlite_id: $sqlite_id,
                source_sqlite_id: $source_sqlite_id,
                user_id: $user_id,
                privacy_scope: $privacy_scope,
                confidence: $confidence,
                status: $status,
                evidence_json: $evidence_json,
                explanation: $explanation,
                created_at: $created_at
            }}]->(target)
            """,
            _clean_params(
                {
                    "source_id": source_id,
                    "target_id": target_id,
                    "id": rel_id,
                    "schema_version": CAUSAL_SCHEMA_VERSION,
                    "projection_version": CAUSAL_PROJECTION_VERSION,
                    "sqlite_id": rel_id,
                    "source_sqlite_id": source_sqlite_id,
                    "user_id": user_id,
                    "privacy_scope": privacy_scope,
                    "confidence": float(confidence),
                    "status": status,
                    "evidence_json": json.dumps(list(evidence)),
                    "explanation": explanation,
                    "created_at": created_at,
                }
            ),
        )

    def _rows(self, conn: kuzu.Connection, query: str, params: Optional[Dict[str, Any]] = None) -> List[List[Any]]:
        result = conn.execute(query, _clean_params(params or {}))
        return result.get_all()

    def _single_column(self, conn: kuzu.Connection, query: str, params: Optional[Dict[str, Any]] = None) -> List[Any]:
        return [row[0] for row in self._rows(conn, query, params)]

    def _projection_counts(self, conn: kuzu.Connection) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for table in [
            "RawEvent",
            "EventFrame",
            "MemoryThread",
            "Scene",
            "Episode",
            "Surface",
            "Entity",
            "Artifact",
            "Memory",
        ]:
            counts[table] = self._count(conn, f"MATCH (n:{table}) RETURN count(n)")
        counts["automatic_edges"] = sum(
            self._count(conn, f"MATCH ()-[r:{rel}]->() RETURN count(r)")
            for rel in AUTOMATIC_REL_TABLES
        )
        counts["causal_edges"] = sum(
            self._count(conn, f"MATCH ()-[r:{rel}]->() RETURN count(r)")
            for rel in CHECKPOINT_CAUSAL_EDGE_TYPES
        )
        return counts

    def _count(self, conn: kuzu.Connection, query: str) -> int:
        rows = self._rows(conn, query)
        return int(rows[0][0] or 0) if rows else 0

    def _orphan_errors(self, conn: kuzu.Connection) -> List[str]:
        errors: List[str] = []
        for rel, (source, target) in AUTOMATIC_REL_TABLES.items():
            try:
                self._rows(conn, f"MATCH (a:{source})-[r:{rel}]->(b:{target}) RETURN r.id LIMIT 1")
            except Exception as exc:
                errors.append(f"{rel} orphan/schema check failed: {exc}")
        for rel in CHECKPOINT_CAUSAL_EDGE_TYPES:
            try:
                self._rows(conn, f"MATCH (a:EventFrame)-[r:{rel}]->(b:EventFrame) RETURN r.id LIMIT 1")
            except Exception as exc:
                errors.append(f"{rel} orphan/schema check failed: {exc}")
        return errors

    def _event_relations(self, conn: kuzu.Connection, event_id: str, direction: str) -> List[Dict[str, Any]]:
        relations: List[Dict[str, Any]] = []
        for rel, (source, target) in AUTOMATIC_REL_TABLES.items():
            if source != "RawEvent" and target != "RawEvent":
                continue
            if direction == "incoming":
                query = f"MATCH (a:{source})-[r:{rel}]->(b:{target} {{id: $event_id}}) RETURN a.id, r.id, r.confidence, r.evidence_json, r.explanation"
            else:
                query = f"MATCH (a:{source} {{id: $event_id}})-[r:{rel}]->(b:{target}) RETURN b.id, r.id, r.confidence, r.evidence_json, r.explanation"
            for row in self._rows(conn, query, {"event_id": event_id}):
                relations.append(
                    {
                        "edge_type": rel,
                        "other_id": row[0],
                        "edge_id": row[1],
                        "confidence": row[2],
                        "evidence": _loads_list(row[3]),
                        "explanation": row[4],
                    }
                )
        return relations

    def _temporal_relations(self, conn: kuzu.Connection, event_id: str, direction: str) -> List[Dict[str, Any]]:
        if direction == "forward":
            rows = self._rows(
                conn,
                """
                MATCH (a:RawEvent {id: $event_id})-[r:TEMPORAL_NEXT]->(b:RawEvent)
                RETURN a.id, b.id, r.id, r.confidence, r.evidence_json, r.explanation
                """,
                {"event_id": event_id},
            )
        else:
            rows = self._rows(
                conn,
                """
                MATCH (a:RawEvent)-[r:TEMPORAL_NEXT]->(b:RawEvent {id: $event_id})
                RETURN a.id, b.id, r.id, r.confidence, r.evidence_json, r.explanation
                """,
                {"event_id": event_id},
            )
        return [
            {
                "from": row[0],
                "to": row[1],
                "edge_id": row[2],
                "edge_type": "TEMPORAL_NEXT",
                "confidence": row[3],
                "evidence": _loads_list(row[4]),
                "explanation": row[5],
            }
            for row in rows
        ]

    def _threads_for_event(self, conn: kuzu.Connection, event_id: str) -> List[Dict[str, Any]]:
        rows = self._rows(
            conn,
            """
            MATCH (e:RawEvent {id: $event_id})-[r:PROJECTED_INTO]->(t:MemoryThread)
            RETURN t.id, t.metadata_json, r.evidence_json
            """,
            {"event_id": event_id},
        )
        return [
            {
                "thread_id": row[0],
                **_loads_dict(row[1]),
                "evidence": _loads_list(row[2]),
            }
            for row in rows
        ]

    def _thread_evidence(self, conn: kuzu.Connection, thread_id: str, allowed_scopes: Sequence[str]) -> List[Dict[str, Any]]:
        scope_clause, scope_params = _scope_where("e", allowed_scopes)
        rows = self._rows(
            conn,
            f"""
            MATCH (e:RawEvent)-[r:PROJECTED_INTO]->(t:MemoryThread)
            WHERE t.id = $thread_id
              {scope_clause}
            RETURN e.id, e.privacy_scope, r.evidence_json
            ORDER BY e.timestamp DESC
            LIMIT 5
            """,
            {"thread_id": thread_id, **scope_params},
        )
        return [
            {"event_id": row[0], "evidence": _loads_list(row[2])}
            for row in rows
        ]

    def _find_event_for_query(self, *, query: str, user_id: str, scope: str) -> str:
        conn = self._connect()
        try:
            allowed = _allowed_scopes(scope)
            scope_clause, scope_params = _scope_where("e", allowed)
            rows = self._rows(
                conn,
                f"""
                MATCH (e:RawEvent)
                WHERE e.user_id = $user_id
                  {scope_clause}
                RETURN e.id, e.event_type, e.source_app, e.metadata_json, e.privacy_scope
                ORDER BY e.timestamp DESC
                LIMIT 100
                """,
                {"user_id": user_id, **scope_params},
            )
            q = query.lower()
            for row in rows:
                haystack = " ".join([str(row[1]), str(row[2]), row[3] or ""]).lower()
                if q and q in haystack:
                    return str(row[0])
            return str(rows[0][0]) if rows else ""
        finally:
            self._close(conn)


def _clean_params(params: Dict[str, Any]) -> Dict[str, Any]:
    clean: Dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            clean[key] = ""
        elif isinstance(value, bool):
            clean[key] = bool(value)
        elif isinstance(value, (int, float, str)):
            clean[key] = value
        else:
            clean[key] = json.dumps(value, sort_keys=True)
    return clean


def _rank_preference_gem(gem: Dict[str, Any], query: str) -> float:
    score = _float(gem.get("score"), 0.0)
    score += _float(gem.get("confidence"), 0.0) * 0.25
    tokens = _query_tokens(query)
    if not tokens:
        return score

    haystack = " ".join(
        [
            str(gem.get("title") or ""),
            str(gem.get("summary") or ""),
            str(gem.get("source_memory_id") or ""),
            str(gem.get("content_ref") or ""),
        ]
    ).lower()
    phrase = str(query or "").strip().lower()
    if phrase and phrase in haystack:
        score += 0.75
    matches = sum(1 for token in tokens if token in haystack)
    score += matches / max(len(tokens), 1)
    return score


def _preference_signal_from_gem(gem: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "signal_type": "memory_gem",
        "thread_type": "preference",
        "event_id": gem.get("event_id"),
        "gem_id": gem.get("gem_id"),
        "title": gem.get("title") or "",
        "summary": gem.get("summary") or "",
        "score": gem.get("score"),
        "confidence": gem.get("confidence"),
        "source_memory_id": gem.get("source_memory_id"),
        "content_ref": gem.get("content_ref"),
        "privacy_scope": gem.get("privacy_scope"),
        "evidence": gem.get("evidence") or [],
    }


def _supporting_event_from_gem(gem: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "event_id": gem.get("event_id"),
        "source_memory_id": gem.get("source_memory_id"),
        "content_ref": gem.get("content_ref"),
        "evidence": gem.get("evidence") or [],
    }


def _normalize_gem_event_id(target: str) -> str:
    value = str(target or "").strip()
    if value.startswith("gem:"):
        return value
    return f"gem:{value}"


def _query_tokens(query: str) -> List[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]*", str(query or "").lower())
        if len(token) > 2
    ][:12]


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _loads_dict(raw: Any) -> Dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _loads_list(raw: Any) -> List[Any]:
    try:
        value = json.loads(raw or "[]")
    except Exception:
        return []
    return value if isinstance(value, list) else []


def _metadata_list(metadata: Dict[str, Any], key: str) -> List[str]:
    value = metadata.get(key)
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _gem_kind(event: RawEvent) -> str:
    if event.source_app != "memory-gem" and not str(event.event_type or "").startswith("gem_"):
        return ""
    kind = str(event.metadata.get("kind") or "").strip().lower()
    if not kind and str(event.event_type or "").startswith("gem_"):
        kind = str(event.event_type).replace("gem_", "", 1).strip().lower()
    return kind


def _slug(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value)).strip("-") or "unknown"


def _strictest_scope(scopes: Iterable[str]) -> str:
    order = ["public", "project", "connector", "global", "private"]
    ranked = {scope: idx for idx, scope in enumerate(order)}
    return max((str(scope or "global") for scope in scopes), key=lambda item: ranked.get(item, 3))


def _scope_where(alias: str, scopes: Sequence[str]) -> Tuple[str, Dict[str, str]]:
    safe_alias = "".join(ch for ch in str(alias or "n") if ch.isalnum() or ch == "_") or "n"
    params: Dict[str, str] = {}
    clauses: List[str] = []
    for idx, scope in enumerate(scopes or ["global"]):
        key = f"scope_{idx}"
        params[key] = str(scope)
        clauses.append(f"{safe_alias}.privacy_scope = ${key}")
    return f"AND ({' OR '.join(clauses)})", params


def _allowed_scopes(scope: str) -> List[str]:
    requested = str(scope or "global")
    if requested == "private":
        return ["public", "project", "connector", "global", "private"]
    if requested == "global":
        return ["public", "project", "connector", "global"]
    if requested == "connector":
        return ["public", "project", "connector"]
    if requested == "project":
        return ["public", "project"]
    return [requested]


def _last_event_state(row: Sequence[Any]) -> str:
    return f"Last event {row[0]} from {row[2]}:{row[3]} at {row[1]}"


def _timeline_summary(timeline: Sequence[Dict[str, Any]]) -> str:
    if not timeline:
        return ""
    first = timeline[0]
    last = timeline[-1]
    return (
        f"{len(timeline)} event(s) from {first.get('timestamp')} to {last.get('timestamp')}; "
        f"latest event type: {last.get('event_type')}."
    )
