from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from .schema import ActionTransition, EvidenceChunk, TransitionMatch, WorldState


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorldMemoryStore:
    """File-backed store for visual world-state and action-transition memories."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _tx(self):
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def _init_db(self) -> None:
        with self._tx() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS world_states (
                    id TEXT PRIMARY KEY,
                    frame_ref TEXT NOT NULL,
                    latent_json TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS action_transitions (
                    id TEXT PRIMARY KEY,
                    ptr TEXT UNIQUE NOT NULL,
                    source_state_id TEXT NOT NULL,
                    target_state_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    action_payload_json TEXT NOT NULL,
                    instruction_context TEXT NOT NULL,
                    action_trace_json TEXT NOT NULL,
                    predicted_next_latent_json TEXT NOT NULL,
                    surprise REAL NOT NULL,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    FOREIGN KEY(source_state_id) REFERENCES world_states(id),
                    FOREIGN KEY(target_state_id) REFERENCES world_states(id)
                );

                CREATE INDEX IF NOT EXISTS idx_world_states_user_time
                    ON world_states(user_id, timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_action_transitions_user_time
                    ON action_transitions(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_action_transitions_surprise
                    ON action_transitions(user_id, surprise DESC);

                CREATE TABLE IF NOT EXISTS evidence_chunks (
                    id TEXT PRIMARY KEY,
                    state_id TEXT NOT NULL,
                    chunk_type TEXT NOT NULL,
                    role TEXT NOT NULL,
                    label TEXT NOT NULL,
                    text TEXT NOT NULL,
                    selector_hint TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    embedding_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    FOREIGN KEY(state_id) REFERENCES world_states(id)
                );

                CREATE INDEX IF NOT EXISTS idx_evidence_chunks_state
                    ON evidence_chunks(state_id, position ASC);
                """
            )

    def put_world_state(
        self,
        frame_ref: str,
        latent: List[float],
        *,
        user_id: str = "default",
        timestamp: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> WorldState:
        state = WorldState(
            id=str(uuid.uuid4()),
            frame_ref=frame_ref,
            latent=list(latent),
            user_id=user_id,
            timestamp=timestamp or _utcnow(),
            metadata=dict(metadata or {}),
        )
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO world_states (
                    id, frame_ref, latent_json, user_id, timestamp, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    state.id,
                    state.frame_ref,
                    json.dumps(state.latent),
                    state.user_id,
                    state.timestamp,
                    json.dumps(state.metadata),
                ),
            )
        return state

    def put_evidence_chunks(self, state_id: str, chunks: Iterable[Dict[str, Any]]) -> List[EvidenceChunk]:
        stored: List[EvidenceChunk] = []
        with self._tx() as conn:
            for raw in chunks:
                chunk = EvidenceChunk(
                    id=str(uuid.uuid4()),
                    state_id=state_id,
                    chunk_type=str(raw.get("chunk_type", "text")),
                    role=str(raw.get("role", "")),
                    label=str(raw.get("label", "")),
                    text=str(raw.get("text", "")),
                    selector_hint=str(raw.get("selector_hint", "")),
                    position=int(raw.get("position", len(stored))),
                    embedding=list(raw.get("embedding", []) or []),
                    metadata=dict(raw.get("metadata", {}) or {}),
                )
                conn.execute(
                    """
                    INSERT INTO evidence_chunks (
                        id, state_id, chunk_type, role, label, text,
                        selector_hint, position, embedding_json, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk.id,
                        chunk.state_id,
                        chunk.chunk_type,
                        chunk.role,
                        chunk.label,
                        chunk.text,
                        chunk.selector_hint,
                        chunk.position,
                        json.dumps(chunk.embedding),
                        json.dumps(chunk.metadata),
                    ),
                )
                stored.append(chunk)
        return stored

    def record_transition(
        self,
        *,
        source_state: WorldState,
        target_state: WorldState,
        action_type: str,
        action_payload: Optional[Dict[str, Any]] = None,
        instruction_context: str = "",
        action_trace: Optional[Iterable[str]] = None,
        predicted_next_latent: Optional[List[float]] = None,
        surprise: float = 0.0,
        user_id: str = "default",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ActionTransition:
        transition_id = str(uuid.uuid4())
        transition = ActionTransition(
            id=transition_id,
            ptr=f"wm-{transition_id[:8]}",
            source_state_id=source_state.id,
            target_state_id=target_state.id,
            action_type=action_type,
            action_payload=dict(action_payload or {}),
            instruction_context=instruction_context,
            action_trace=list(action_trace or []),
            predicted_next_latent=list(predicted_next_latent or []),
            surprise=float(surprise),
            user_id=user_id,
            created_at=_utcnow(),
            metadata=dict(metadata or {}),
        )
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO action_transitions (
                    id, ptr, source_state_id, target_state_id, action_type,
                    action_payload_json, instruction_context, action_trace_json,
                    predicted_next_latent_json, surprise, user_id, created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transition.id,
                    transition.ptr,
                    transition.source_state_id,
                    transition.target_state_id,
                    transition.action_type,
                    json.dumps(transition.action_payload),
                    transition.instruction_context,
                    json.dumps(transition.action_trace),
                    json.dumps(transition.predicted_next_latent),
                    transition.surprise,
                    transition.user_id,
                    transition.created_at,
                    json.dumps(transition.metadata),
                ),
            )
        return transition

    def search_transitions(
        self,
        *,
        query_latent: List[float],
        task_instruction: str = "",
        recent_actions: Optional[Iterable[str]] = None,
        user_id: str = "default",
        limit: int = 5,
        surprise_weight: float = 0.05,
        surprise_multiplier: float = 0.35,
    ) -> List[TransitionMatch]:
        recent = list(recent_actions or [])
        with self._tx() as conn:
            rows = conn.execute(
                """
                SELECT
                    t.*,
                    s1.frame_ref AS source_frame_ref,
                    s1.latent_json AS source_latent_json,
                    s1.timestamp AS source_timestamp,
                    s1.metadata_json AS source_metadata_json,
                    s2.frame_ref AS target_frame_ref,
                    s2.latent_json AS target_latent_json,
                    s2.timestamp AS target_timestamp,
                    s2.metadata_json AS target_metadata_json
                FROM action_transitions t
                JOIN world_states s1 ON s1.id = t.source_state_id
                JOIN world_states s2 ON s2.id = t.target_state_id
                WHERE t.user_id = ?
                ORDER BY t.created_at DESC
                """,
                (user_id,),
            ).fetchall()

        if not rows:
            return []

        max_surprise = max(float(row["surprise"]) for row in rows) or 1.0
        query_terms = _tokenize_text(task_instruction)
        matches: List[TransitionMatch] = []
        for row in rows:
            source_state = WorldState(
                id=row["source_state_id"],
                frame_ref=row["source_frame_ref"],
                latent=_loads_floats(row["source_latent_json"]),
                user_id=user_id,
                timestamp=row["source_timestamp"],
                metadata=_loads_dict(row["source_metadata_json"]),
            )
            target_state = WorldState(
                id=row["target_state_id"],
                frame_ref=row["target_frame_ref"],
                latent=_loads_floats(row["target_latent_json"]),
                user_id=user_id,
                timestamp=row["target_timestamp"],
                metadata=_loads_dict(row["target_metadata_json"]),
            )
            transition = ActionTransition(
                id=row["id"],
                ptr=row["ptr"],
                source_state_id=row["source_state_id"],
                target_state_id=row["target_state_id"],
                action_type=row["action_type"],
                action_payload=_loads_dict(row["action_payload_json"]),
                instruction_context=row["instruction_context"],
                action_trace=_loads_list(row["action_trace_json"]),
                predicted_next_latent=_loads_floats(row["predicted_next_latent_json"]),
                surprise=float(row["surprise"]),
                user_id=row["user_id"],
                created_at=row["created_at"],
                metadata=_loads_dict(row["metadata_json"]),
            )
            latent_score = _cosine_similarity(query_latent, source_state.latent)
            instruction_score = _keyword_overlap(task_instruction, transition.instruction_context)
            action_score = _action_overlap(recent, transition.action_trace)
            surprise_score = min(transition.surprise / max_surprise, 1.0)
            evidence_chunks = self._match_evidence_chunks(
                state_ids=[source_state.id, target_state.id],
                query_embedding=query_latent,
                query_terms=query_terms,
                limit=3,
            )
            evidence_score = evidence_chunks[0][1] if evidence_chunks else 0.0
            matched_chunks = [chunk for chunk, _ in evidence_chunks]
            score = (
                (0.45 * latent_score)
                + (0.15 * instruction_score)
                + (0.08 * action_score)
                + (0.22 * evidence_score)
                + (surprise_weight * surprise_score)
            ) * (1.0 + surprise_score * surprise_multiplier)
            matches.append(
                TransitionMatch(
                    transition=transition,
                    source_state=source_state,
                    target_state=target_state,
                    score=round(score, 6),
                    latent_score=round(latent_score, 6),
                    instruction_score=round(instruction_score, 6),
                    action_score=round(action_score, 6),
                    surprise_score=round(surprise_score, 6),
                    evidence_score=round(evidence_score, 6),
                    evidence_chunks=matched_chunks,
                )
            )
        matches.sort(key=lambda item: item.score, reverse=True)
        return matches[: max(1, int(limit))]

    def build_digest(self, matches: List[TransitionMatch], *, max_items: int = 5) -> str:
        if not matches:
            return "No world-memory transitions retrieved."
        lines = ["World memory digest:"]
        for idx, match in enumerate(matches[: max_items], start=1):
            transition = match.transition
            source = os.path.basename(match.source_state.frame_ref) or match.source_state.frame_ref
            target = os.path.basename(match.target_state.frame_ref) or match.target_state.frame_ref
            lines.append(
                f"- [{idx}] ptr={transition.ptr} action={transition.action_type} "
                f"score={match.score:.3f} surprise={transition.surprise:.3f} "
                f"{source} -> {target}"
            )
            if transition.instruction_context:
                lines.append(f"  task={transition.instruction_context[:140]}")
            for chunk in match.evidence_chunks[:2]:
                lines.append(
                    "  focus="
                    f"{chunk.role or chunk.chunk_type}"
                    f" label={chunk.label[:80] or 'n/a'}"
                    f" pos={chunk.position}"
                    f" selector={chunk.selector_hint[:80] or 'n/a'}"
                )
        return "\n".join(lines)

    def expand_transition(self, ptr: str) -> Optional[Dict[str, Any]]:
        with self._tx() as conn:
            row = conn.execute(
                """
                SELECT
                    t.*,
                    s1.frame_ref AS source_frame_ref,
                    s1.latent_json AS source_latent_json,
                    s2.frame_ref AS target_frame_ref,
                    s2.latent_json AS target_latent_json
                FROM action_transitions t
                JOIN world_states s1 ON s1.id = t.source_state_id
                JOIN world_states s2 ON s2.id = t.target_state_id
                WHERE t.ptr = ?
                """,
                (ptr,),
            ).fetchone()
        if row is None:
            return None
        return {
            "ptr": row["ptr"],
            "transition_id": row["id"],
            "action_type": row["action_type"],
            "action_payload": _loads_dict(row["action_payload_json"]),
            "instruction_context": row["instruction_context"],
            "action_trace": _loads_list(row["action_trace_json"]),
            "predicted_next_latent": _loads_floats(row["predicted_next_latent_json"]),
            "surprise": float(row["surprise"]),
            "source_frame_ref": row["source_frame_ref"],
            "source_latent": _loads_floats(row["source_latent_json"]),
            "target_frame_ref": row["target_frame_ref"],
            "target_latent": _loads_floats(row["target_latent_json"]),
            "metadata": _loads_dict(row["metadata_json"]),
            "source_evidence_chunks": [asdict_chunk(chunk) for chunk in self.get_evidence_chunks(row["source_state_id"])],
            "target_evidence_chunks": [asdict_chunk(chunk) for chunk in self.get_evidence_chunks(row["target_state_id"])],
        }

    def list_recent_transitions(self, *, user_id: str = "default", limit: int = 20) -> List[Dict[str, Any]]:
        with self._tx() as conn:
            rows = conn.execute(
                """
                SELECT ptr, action_type, instruction_context, surprise, created_at, metadata_json
                FROM action_transitions
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, int(limit)),
            ).fetchall()
        return [
            {
                "ptr": row["ptr"],
                "action_type": row["action_type"],
                "instruction_context": row["instruction_context"],
                "surprise": float(row["surprise"]),
                "created_at": row["created_at"],
                "metadata": _loads_dict(row["metadata_json"]),
            }
            for row in rows
        ]

    def get_evidence_chunks(self, state_id: str, *, limit: int = 100) -> List[EvidenceChunk]:
        with self._tx() as conn:
            rows = conn.execute(
                """
                SELECT * FROM evidence_chunks
                WHERE state_id = ?
                ORDER BY position ASC
                LIMIT ?
                """,
                (state_id, int(limit)),
            ).fetchall()
        return [_row_to_chunk(row) for row in rows]

    def _match_evidence_chunks(
        self,
        *,
        state_ids: List[str],
        query_embedding: List[float],
        query_terms: set[str],
        limit: int,
    ) -> List[tuple[EvidenceChunk, float]]:
        if not state_ids:
            return []
        placeholders = ",".join("?" for _ in state_ids)
        with self._tx() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM evidence_chunks
                WHERE state_id IN ({placeholders})
                ORDER BY position ASC
                """,
                tuple(state_ids),
            ).fetchall()
        scored: List[tuple[EvidenceChunk, float]] = []
        for row in rows:
            chunk = _row_to_chunk(row)
            semantic = _cosine_similarity(query_embedding, chunk.embedding)
            lexical = _overlap_ratio(query_terms, _tokenize_text(" ".join([chunk.label, chunk.text, chunk.selector_hint])))
            score = (0.7 * semantic) + (0.3 * lexical)
            if score <= 0.0:
                continue
            scored.append((chunk, round(score, 6)))
        scored.sort(key=lambda item: (item[1], -item[0].position), reverse=True)
        return scored[: max(1, int(limit))]


def _loads_floats(raw: str) -> List[float]:
    return [float(item) for item in json.loads(raw or "[]")]


def _loads_dict(raw: str) -> Dict[str, Any]:
    value = json.loads(raw or "{}")
    return value if isinstance(value, dict) else {}


def _loads_list(raw: str) -> List[str]:
    value = json.loads(raw or "[]")
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _row_to_chunk(row: sqlite3.Row) -> EvidenceChunk:
    return EvidenceChunk(
        id=row["id"],
        state_id=row["state_id"],
        chunk_type=row["chunk_type"],
        role=row["role"],
        label=row["label"],
        text=row["text"],
        selector_hint=row["selector_hint"],
        position=int(row["position"]),
        embedding=_loads_floats(row["embedding_json"]),
        metadata=_loads_dict(row["metadata_json"]),
    )


def asdict_chunk(chunk: EvidenceChunk) -> Dict[str, Any]:
    return {
        "id": chunk.id,
        "state_id": chunk.state_id,
        "chunk_type": chunk.chunk_type,
        "role": chunk.role,
        "label": chunk.label,
        "text": chunk.text,
        "selector_hint": chunk.selector_hint,
        "position": chunk.position,
        "embedding": list(chunk.embedding),
        "metadata": dict(chunk.metadata),
    }


def _cosine_similarity(left: List[float], right: List[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    num = sum(a * b for a, b in zip(left, right))
    den_left = sum(a * a for a in left) ** 0.5
    den_right = sum(b * b for b in right) ** 0.5
    if den_left <= 0.0 or den_right <= 0.0:
        return 0.0
    return max(-1.0, min(1.0, num / (den_left * den_right)))


def _keyword_overlap(left: str, right: str) -> float:
    left_terms = {token for token in left.lower().split() if token}
    right_terms = {token for token in right.lower().split() if token}
    if not left_terms or not right_terms:
        return 0.0
    intersection = left_terms & right_terms
    union = left_terms | right_terms
    return len(intersection) / max(1, len(union))


def _action_overlap(recent_actions: List[str], stored_trace: List[str]) -> float:
    if not recent_actions or not stored_trace:
        return 0.0
    recent = {item.strip().lower() for item in recent_actions if item}
    stored = {item.strip().lower() for item in stored_trace if item}
    if not recent or not stored:
        return 0.0
    return len(recent & stored) / max(1, len(recent | stored))


def _tokenize_text(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(text).lower())
        if token and len(token) > 1
    }


def _overlap_ratio(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, len(left))
