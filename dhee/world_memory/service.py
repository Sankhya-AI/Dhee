from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover
    BeautifulSoup = None

from .capture_store import CaptureStore
from .encoder import DeterministicFrameEncoder, create_default_encoder
from .predictor import ActionConditionedPredictor, compute_surprise
from .schema import (
    CaptureAction,
    CaptureEvent,
    CaptureLink,
    CapturedArtifact,
    CapturedObservation,
    CapturedSurface,
)
from .session_graph import SessionGraphStore
from .store import WorldMemoryStore, asdict_chunk


DEFAULT_CAPTURE_POLICIES = [
    ("chrome", True, "sampled"),
    ("vscode", True, "sampled"),
    ("cursor", True, "sampled"),
    ("terminal", True, "sampled"),
    ("iterm", True, "sampled"),
    ("apple-notes", True, "sampled"),
    ("obsidian", True, "sampled"),
    ("slack", False, "sampled"),
]


class DheeMemoryClient:
    """Adapter over Dhee's remember/recall APIs for memory-os flows."""

    def __init__(self, memory: Any):
        self.memory = memory

    def remember(
        self,
        content: str,
        *,
        user_id: str = "default",
        metadata: Optional[Dict[str, Any]] = None,
        namespace: str = "default",
        source_app: str = "memory-os",
        scope: str = "global",
        agent_id: Optional[str] = None,
        agent_category: Optional[str] = None,
        connector_id: Optional[str] = None,
        categories: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        enriched = dict(metadata or {})
        enriched.setdefault("namespace", namespace)
        result = self.memory.add(
            messages=content,
            user_id=user_id,
            agent_id=agent_id,
            metadata=enriched,
            infer=False,
            source_app=source_app,
            scope=scope,
            agent_category=agent_category,
            connector_id=connector_id,
            categories=categories or [],
        )
        rows = result.get("results", []) if isinstance(result, dict) else []
        return rows[0] if rows else {"stored": False}

    def recall(
        self,
        query: str,
        *,
        user_id: str = "default",
        limit: int = 5,
        agent_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        scope_filter: Optional[List[str]] = None,
        agent_category: Optional[str] = None,
        connector_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        result = self.memory.search(
            query=query,
            user_id=user_id,
            agent_id=agent_id,
            filters=filters or {},
            limit=limit,
            scope_filter=scope_filter,
            agent_category=agent_category,
            connector_ids=connector_ids,
            rerank=True,
        )
        rows = result.get("results", []) if isinstance(result, dict) else []
        return [row for row in rows if isinstance(row, dict)]

    def recent(
        self,
        *,
        user_id: str = "default",
        limit: int = 12,
        source_app: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        result = self.memory.get_all(user_id=user_id, limit=max(limit * 3, 20))
        rows = result.get("results", []) if isinstance(result, dict) else []
        items: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") or {}
            if source_app and str(row.get("source_app") or metadata.get("source_app") or "") != source_app:
                continue
            items.append(row)
            if len(items) >= limit:
                break
        return items


class MemoryOSService:
    """Capture + world-memory orchestration for the macOS memory shell."""

    def __init__(
        self,
        *,
        capture_store: CaptureStore,
        world_store: WorldMemoryStore,
        graph_store: SessionGraphStore,
        memory_client: Any,
        encoder: Any | None = None,
        predictor: Any | None = None,
    ):
        self.capture_store = capture_store
        self.world_store = world_store
        self.graph_store = graph_store
        self.memory_client = memory_client
        self.encoder = encoder or create_default_encoder()
        self.predictor = predictor or ActionConditionedPredictor()
        self._ensure_default_policies()

    @classmethod
    def from_default_runtime(cls, *, memory: Any, data_dir: Optional[str] = None) -> "MemoryOSService":
        runtime_root = Path(data_dir or os.environ.get("DHEE_DATA_DIR") or (Path.home() / ".dhee"))
        runtime_root.mkdir(parents=True, exist_ok=True)
        memory_os_dir = runtime_root / "memory_os"
        memory_os_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            capture_store=CaptureStore(str(memory_os_dir / "capture.db")),
            world_store=WorldMemoryStore(str(memory_os_dir / "world_memory.db")),
            graph_store=SessionGraphStore(str(runtime_root / "capture" / "sessions")),
            memory_client=DheeMemoryClient(memory),
        )

    def _ensure_default_policies(self) -> None:
        if self.capture_store.list_policies():
            return
        for source_app, enabled, mode in DEFAULT_CAPTURE_POLICIES:
            self.capture_store.upsert_policy(
                source_app=source_app,
                enabled=enabled,
                mode=mode,
                metadata={"seeded": True},
            )

    def start_capture_session(
        self,
        *,
        user_id: str = "default",
        source_app: str,
        namespace: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self.cleanup_expired_artifacts()
        session = self.capture_store.start_session(
            user_id=user_id,
            source_app=source_app,
            namespace=namespace or _default_namespace_for_app(source_app),
            metadata=metadata,
        )
        manifest = self.graph_store.init_session(session, mode="pointer-capture")
        return {
            "session": asdict(session),
            "manifest": manifest,
            "sessionPath": str(self.graph_store.session_dir(session.id)),
        }

    def end_capture_session(
        self,
        session_id: str,
        *,
        distill: bool = True,
        summary_hint: str = "",
    ) -> Dict[str, Any]:
        session = self.capture_store.get_session(session_id)
        if not session:
            raise ValueError("Unknown capture session")
        graph = self.graph_store.load_graph(session_id)
        events = self.capture_store.list_events(user_id=session.user_id, session_id=session_id, limit=500)
        summary_memory = None
        surface_memories: List[Dict[str, Any]] = []
        if distill:
            surface_memories = self._distill_surfaces(session=session, graph=graph)
            if events or graph["actions"] or graph["observations"]:
                summary = _build_session_summary_v2(
                    session=session,
                    events=events,
                    graph=graph,
                    summary_hint=summary_hint,
                )
                summary_memory = self.memory_client.remember(
                    summary,
                    user_id=session.user_id,
                    namespace=session.namespace,
                    source_app=session.source_app,
                    scope="global",
                    categories=["session_summary", "world_memory"],
                    metadata={
                        "memory_type": "capture_session_summary",
                        "session_id": session.id,
                        "event_count": len(events),
                        "action_count": len(graph["actions"]),
                        "surface_count": len(graph["surfaces"]),
                        "source_app": session.source_app,
                    },
                )
        closed = self.capture_store.end_session(
            session_id,
            status="completed",
            metadata_updates={
                "distilled": bool(summary_memory),
                "summary_memory_id": (summary_memory or {}).get("id"),
                "surface_memory_ids": [
                    row.get("id")
                    for row in surface_memories
                    if isinstance(row, dict) and row.get("id")
                ],
            },
        )
        if closed:
            self.graph_store.patch_manifest(
                session_id,
                ended_at=closed.ended_at,
                status=closed.status,
                artifact_bytes=self.graph_store.artifact_bytes(session_id),
            )
        cleanup = self.cleanup_expired_artifacts()
        return {
            "session": asdict(closed) if closed else None,
            "eventCount": len(events),
            "surfaceMemories": surface_memories,
            "summaryMemory": summary_memory,
            "graph": self.graph_store.load_graph(session_id),
            "cleanup": cleanup,
        }

    def get_capture_session(self, session_id: str) -> Dict[str, Any]:
        session = self.capture_store.get_session(session_id)
        if not session:
            raise ValueError("Unknown capture session")
        return {
            "session": asdict(session),
            "graph": self.graph_store.load_graph(session_id),
        }

    def record_action(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        session = self._require_session(str(payload.get("session_id") or ""))
        source_app = str(payload.get("source_app") or session.source_app or "").strip().lower()
        namespace = str(payload.get("namespace") or session.namespace or _default_namespace_for_app(source_app)).strip()
        surface = self._upsert_surface(session=session, payload=payload, source_app=source_app, namespace=namespace)
        action_type = str(payload.get("action_type") or "click").strip()
        created_at = str(payload.get("created_at") or _now_iso())
        target = dict(payload.get("target") or {})
        capture_mode = str(payload.get("capture_mode") or ("dom+screenshot" if action_type == "double_click" else "dom"))
        confidence = float(payload.get("confidence") or 0.0)
        metadata = dict(payload.get("metadata") or {})
        previous_surface_id = str(payload.get("previous_surface_id") or metadata.get("previous_surface_id") or "").strip()
        action = CaptureAction(
            id=str(uuid.uuid4()),
            session_id=session.id,
            user_id=session.user_id,
            source_app=source_app,
            namespace=namespace,
            created_at=created_at,
            action_type=action_type,
            target=target,
            surface_id=surface.id,
            capture_mode=capture_mode,
            confidence=confidence,
            metadata=metadata,
        )
        world_record = self._maybe_record_world_transition(
            session=session,
            source_app=source_app,
            namespace=namespace,
            action_type=action_type,
            payload=payload,
        )
        if world_record:
            action.world_ptr = str(world_record.get("ptr") or "")
        self.graph_store.append_action(action)
        self.graph_store.bump_manifest(session.id, action_count=1)
        self.graph_store.patch_manifest(
            session.id,
            active_surface_id=surface.id,
            last_activity_at=created_at,
            artifact_bytes=self.graph_store.artifact_bytes(session.id),
        )
        if previous_surface_id and previous_surface_id != surface.id:
            self.graph_store.append_link(
                CaptureLink(
                    id=str(uuid.uuid4()),
                    session_id=session.id,
                    user_id=session.user_id,
                    source_app=source_app,
                    created_at=created_at,
                    from_id=previous_surface_id,
                    to_id=surface.id,
                    relation="navigated_to",
                    metadata={"action_type": action_type},
                )
            )
        event = self.capture_store.record_event(
            CaptureEvent(
                id=str(uuid.uuid4()),
                session_id=session.id,
                user_id=session.user_id,
                source_app=source_app,
                namespace=namespace,
                event_type="action",
                created_at=created_at,
                text_payload=str(target.get("text") or target.get("label") or ""),
                structured_payload={
                    "target": target,
                    "surface_id": surface.id,
                    "capture_mode": capture_mode,
                    "surface_type": surface.surface_type,
                },
                window_title=surface.title,
                url=surface.url,
                action_type=action_type,
                action_payload=dict(payload.get("action_payload") or {}),
                confidence=confidence,
                source_kind=str(payload.get("source_kind") or "pointer"),
                world_ptr=(world_record or {}).get("ptr"),
                metadata=metadata,
            )
        )
        return {
            "action": asdict(action),
            "surface": asdict(surface),
            "event": asdict(event),
            "worldTransition": world_record,
        }

    def record_navigation(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        patched = dict(payload)
        patched.setdefault("action_type", "navigation")
        return self.record_action(patched)

    def record_observation(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        session = self._require_session(str(payload.get("session_id") or ""))
        source_app = str(payload.get("source_app") or session.source_app or "").strip().lower()
        namespace = str(payload.get("namespace") or session.namespace or _default_namespace_for_app(source_app)).strip()
        surface = self._upsert_surface(session=session, payload=payload, source_app=source_app, namespace=namespace)
        created_at = str(payload.get("created_at") or _now_iso())
        text = str(payload.get("text") or payload.get("text_payload") or "").strip()
        structured = dict(payload.get("structured") or payload.get("structured_payload") or {})
        observation = CapturedObservation(
            id=str(uuid.uuid4()),
            session_id=session.id,
            surface_id=surface.id,
            user_id=session.user_id,
            source_app=source_app,
            namespace=namespace,
            created_at=created_at,
            action_id=str(payload.get("action_id") or "") or None,
            source_kind=str(payload.get("source_kind") or "dom"),
            kind=str(payload.get("kind") or "capture"),
            text=text,
            structured=structured,
            confidence=float(payload.get("confidence") or 0.0),
            metadata=dict(payload.get("metadata") or {}),
        )
        remembered = None
        if self._should_promote_observation(observation):
            remembered = self._remember_surface_observation(session=session, surface=surface, observation=observation)
            observation.memory_id = (remembered or {}).get("id")
        self.graph_store.append_observation(observation)
        self.graph_store.bump_manifest(session.id, observation_count=1)
        self.graph_store.patch_manifest(session.id, last_activity_at=created_at, active_surface_id=surface.id)
        if observation.action_id:
            self.graph_store.append_link(
                CaptureLink(
                    id=str(uuid.uuid4()),
                    session_id=session.id,
                    user_id=session.user_id,
                    source_app=source_app,
                    created_at=created_at,
                    from_id=observation.action_id,
                    to_id=observation.id,
                    relation="described_by",
                    metadata={"surface_id": surface.id},
                )
            )
        event = self.capture_store.record_event(
            CaptureEvent(
                id=str(uuid.uuid4()),
                session_id=session.id,
                user_id=session.user_id,
                source_app=source_app,
                namespace=namespace,
                event_type="observation",
                created_at=created_at,
                text_payload=text,
                structured_payload=structured,
                window_title=surface.title,
                url=surface.url,
                action_type=str(payload.get("action_type") or ""),
                action_payload={},
                confidence=float(payload.get("confidence") or 0.0),
                source_kind=observation.source_kind,
                memory_id=observation.memory_id,
                metadata={
                    **observation.metadata,
                    "surface_id": surface.id,
                    "observation_kind": observation.kind,
                },
            )
        )
        return {
            "observation": asdict(observation),
            "surface": asdict(surface),
            "memory": remembered,
            "event": asdict(event),
        }

    def record_artifact(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        session = self._require_session(str(payload.get("session_id") or ""))
        source_app = str(payload.get("source_app") or session.source_app or "").strip().lower()
        surface = self._upsert_surface(
            session=session,
            payload=payload,
            source_app=source_app,
            namespace=str(payload.get("namespace") or session.namespace or _default_namespace_for_app(source_app)).strip(),
        )
        created_at = str(payload.get("created_at") or _now_iso())
        raw_bytes = _decode_artifact_bytes(payload)
        sha256 = hashlib.sha256(raw_bytes).hexdigest()
        existing = self.graph_store.find_artifact_by_hash(session.id, sha256)
        if existing:
            return {"artifact": existing, "deduped": True}
        mime_type = str(payload.get("mime_type") or payload.get("mimeType") or "image/png")
        extension = _extension_for_mime(mime_type)
        artifact_id = str(uuid.uuid4())
        stored_path = self.graph_store.save_artifact_bytes(session.id, f"{sha256}{extension}", raw_bytes)
        ttl_hours = int(payload.get("ttl_hours") or payload.get("ttlHours") or 48)
        expires_at = _expire_at(created_at, ttl_hours)
        artifact = CapturedArtifact(
            id=artifact_id,
            session_id=session.id,
            surface_id=surface.id,
            user_id=session.user_id,
            source_app=source_app,
            created_at=created_at,
            action_id=str(payload.get("action_id") or "") or None,
            artifact_type=str(payload.get("artifact_type") or "screenshot"),
            path=stored_path,
            mime_type=mime_type,
            sha256=sha256,
            ttl_hours=ttl_hours,
            expires_at=expires_at,
            retention=str(payload.get("retention") or "temporary"),
            metadata=dict(payload.get("metadata") or {}),
        )
        self.graph_store.append_artifact(artifact)
        self.graph_store.bump_manifest(session.id, artifact_count=1)
        self.graph_store.patch_manifest(
            session.id,
            last_activity_at=created_at,
            active_surface_id=surface.id,
            artifact_bytes=self.graph_store.artifact_bytes(session.id),
        )
        self.capture_store.record_event(
            CaptureEvent(
                id=str(uuid.uuid4()),
                session_id=session.id,
                user_id=session.user_id,
                source_app=source_app,
                namespace=session.namespace,
                event_type="artifact",
                created_at=created_at,
                text_payload=str(payload.get("label") or artifact.artifact_type),
                structured_payload={
                    "artifact_id": artifact.id,
                    "surface_id": surface.id,
                    "mime_type": artifact.mime_type,
                    "path": artifact.path,
                    "ttl_hours": artifact.ttl_hours,
                },
                window_title=surface.title,
                url=surface.url,
                action_type="artifact_capture",
                action_payload={},
                confidence=1.0,
                source_kind="artifact",
                metadata=artifact.metadata,
            )
        )
        if artifact.action_id:
            self.graph_store.append_link(
                CaptureLink(
                    id=str(uuid.uuid4()),
                    session_id=session.id,
                    user_id=session.user_id,
                    source_app=source_app,
                    created_at=created_at,
                    from_id=artifact.action_id,
                    to_id=artifact.id,
                    relation="captured_artifact",
                    metadata={"surface_id": surface.id},
                )
            )
        return {"artifact": asdict(artifact), "deduped": False}

    def cleanup_expired_artifacts(self) -> Dict[str, Any]:
        removed = 0
        checked = 0
        now = datetime.now(timezone.utc)
        for session_id in self.graph_store.list_session_ids():
            graph = self.graph_store.load_graph(session_id)
            for artifact in graph["artifacts"]:
                checked += 1
                expires_at = _coerce_iso(artifact.get("expires_at"))
                if expires_at is None or expires_at > now:
                    continue
                if self.graph_store.remove_artifact_path(str(artifact.get("path") or "")):
                    removed += 1
            self.graph_store.patch_manifest(session_id, artifact_bytes=self.graph_store.artifact_bytes(session_id))
        return {"checked": checked, "removed": removed}

    def record_capture_event(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        session_id = str(payload.get("session_id") or "").strip()
        session = self.capture_store.get_session(session_id)
        if not session:
            raise ValueError("Unknown capture session")

        source_app = str(payload.get("source_app") or session.source_app or "").strip().lower()
        namespace = str(payload.get("namespace") or session.namespace or _default_namespace_for_app(source_app)).strip()
        text_payload = str(payload.get("text_payload") or "").strip()
        event_type = str(payload.get("event_type") or "capture_event").strip()
        metadata = dict(payload.get("metadata") or {})
        structured_payload = dict(payload.get("structured_payload") or {})
        action_type = str(payload.get("action_type") or "").strip()
        action_payload = dict(payload.get("action_payload") or {})
        before_context = str(payload.get("before_context") or "")
        after_context = str(payload.get("after_context") or "")
        recent_actions = [str(item) for item in (payload.get("recent_actions") or []) if str(item).strip()]
        task_instruction = str(payload.get("task_instruction") or "")
        confidence = float(payload.get("confidence") or 0.0)
        source_kind = str(payload.get("source_kind") or "event")
        before_frame_ref = str(payload.get("before_frame_ref") or metadata.get("before_frame_ref") or "")
        after_frame_ref = str(payload.get("after_frame_ref") or metadata.get("after_frame_ref") or "")

        world_record = None
        if before_context and after_context and action_type:
            world_record = self.record_transition(
                user_id=session.user_id,
                source_app=source_app,
                namespace=namespace,
                before_context=before_context,
                after_context=after_context,
                before_frame_ref=before_frame_ref or f"text://before/{uuid.uuid4().hex}",
                after_frame_ref=after_frame_ref or f"text://after/{uuid.uuid4().hex}",
                action_type=action_type,
                action_payload=action_payload,
                task_instruction=task_instruction,
                recent_actions=recent_actions,
                metadata={
                    **metadata,
                    "session_id": session.id,
                    "source_app": source_app,
                    "namespace": namespace,
                },
            )

        remembered = None
        if self._should_mirror_text_event(event_type=event_type, text_payload=text_payload, structured_payload=structured_payload):
            remembered = self.memory_client.remember(
                text_payload or _summarize_structured_payload(structured_payload),
                user_id=session.user_id,
                namespace=namespace,
                source_app=source_app,
                scope="global",
                categories=["capture_event"],
                metadata={
                    **metadata,
                    "memory_type": "app_capture_event",
                    "event_type": event_type,
                    "session_id": session.id,
                    "source_app": source_app,
                    "window_title": payload.get("window_title"),
                    "url": payload.get("url"),
                },
            )

        event = self.capture_store.record_event(
            CaptureEvent(
                id=str(uuid.uuid4()),
                session_id=session.id,
                user_id=session.user_id,
                source_app=source_app,
                namespace=namespace,
                event_type=event_type,
                created_at=str(payload.get("created_at") or _now_iso()),
                text_payload=text_payload,
                structured_payload=structured_payload,
                window_title=str(payload.get("window_title") or ""),
                url=str(payload.get("url") or ""),
                action_type=action_type,
                action_payload=action_payload,
                confidence=confidence,
                source_kind=source_kind,
                world_ptr=(world_record or {}).get("ptr"),
                memory_id=(remembered or {}).get("id"),
                metadata=metadata,
            )
        )
        return {
            "event": asdict(event),
            "worldTransition": world_record,
            "memory": remembered,
        }

    def record_transition(
        self,
        *,
        user_id: str,
        source_app: str,
        namespace: str,
        before_context: str,
        after_context: str,
        before_frame_ref: str,
        after_frame_ref: str,
        action_type: str,
        action_payload: Optional[Dict[str, Any]] = None,
        task_instruction: str = "",
        recent_actions: Optional[Iterable[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        source_latent = self.encoder.encode_frame(before_frame_ref, "\n".join([task_instruction, before_context]).strip())
        target_latent = self.encoder.encode_frame(after_frame_ref, "\n".join([task_instruction, after_context]).strip())
        predicted_next = self.predictor.predict(
            source_latent,
            action_type=action_type,
            action_payload=action_payload or {},
            action_trace=recent_actions,
        )
        surprise = compute_surprise(predicted_next, target_latent)
        world_metadata = {
            **(metadata or {}),
            "source_app": source_app,
            "namespace": namespace,
            "memory_type": "world_transition",
            "before_context_text": before_context[:1000],
            "after_context_text": after_context[:1000],
        }
        source_state = self.world_store.put_world_state(
            before_frame_ref,
            source_latent,
            user_id=user_id,
            metadata={"kind": "world_state", **world_metadata},
        )
        source_focus = _extract_dom_evidence(html=before_context, frame_ref=before_frame_ref, encoder=self.encoder)
        self.world_store.put_evidence_chunks(source_state.id, source_focus)
        target_state = self.world_store.put_world_state(
            after_frame_ref,
            target_latent,
            user_id=user_id,
            metadata={"kind": "world_state", **world_metadata},
        )
        target_focus = _extract_dom_evidence(html=after_context, frame_ref=after_frame_ref, encoder=self.encoder)
        self.world_store.put_evidence_chunks(target_state.id, target_focus)
        transition = self.world_store.record_transition(
            source_state=source_state,
            target_state=target_state,
            action_type=action_type,
            action_payload=action_payload,
            instruction_context=task_instruction,
            action_trace=recent_actions,
            predicted_next_latent=predicted_next,
            surprise=surprise,
            user_id=user_id,
            metadata=world_metadata,
        )
        card = _build_structured_memory_card(
            ptr=transition.ptr,
            action_type=action_type,
            task_instruction=task_instruction,
            surprise=surprise,
            source_state_id=source_state.id,
            target_state_id=target_state.id,
            source_focus=source_focus,
            target_focus=target_focus,
            recent_actions=list(recent_actions or []),
        )
        remembered = self.memory_client.remember(
            card,
            user_id=user_id,
            namespace=namespace,
            source_app=source_app,
            scope="connector",
            agent_category="screen_state_agent",
            connector_id=source_app,
            categories=["world_memory", "screen_state", "computer_use"],
            metadata={
                "memory_type": "world_memory_card",
                "ptr": transition.ptr,
                "surprise": round(surprise, 6),
                "task_instruction": task_instruction,
                "action_type": action_type,
                "before_keywords": _extract_focus_keywords(source_focus, before_frame_ref, before_context),
                "after_keywords": _extract_focus_keywords(target_focus, after_frame_ref, after_context),
                "source_state_id": source_state.id,
                "target_state_id": target_state.id,
                "source_focus": _slim_focus(source_focus),
                "target_focus": _slim_focus(target_focus),
                "before_frame_ref": before_frame_ref,
                "after_frame_ref": after_frame_ref,
                "source_app": source_app,
            },
        )
        return {
            "ptr": transition.ptr,
            "surprise": round(surprise, 6),
            "memoryId": remembered.get("id") if isinstance(remembered, dict) else None,
            "transition": {
                "ptr": transition.ptr,
                "actionType": transition.action_type,
                "instructionContext": transition.instruction_context,
                "surprise": transition.surprise,
            },
        }

    def world_context_pack(
        self,
        *,
        user_id: str,
        current_frame_ref: str,
        current_context_text: str,
        task_instruction: str,
        recent_actions: Optional[Iterable[str]] = None,
        limit: int = 5,
    ) -> Dict[str, Any]:
        query_context = "\n".join(part for part in [task_instruction, current_context_text] if part).strip()
        query_latent = self.encoder.encode_frame(current_frame_ref, query_context)
        matches = self.world_store.search_transitions(
            query_latent=query_latent,
            task_instruction=task_instruction,
            recent_actions=recent_actions,
            user_id=user_id,
            limit=limit,
        )
        focus_targets = _collect_focus_targets(matches)
        page_chunks = _extract_dom_evidence(html=current_context_text, frame_ref=current_frame_ref, encoder=self.encoder)
        text_query = _build_text_recall_query(
            task_instruction=task_instruction,
            frame_keywords=_extract_focus_keywords(page_chunks, current_frame_ref, current_context_text),
            recent_actions=list(recent_actions or []),
        )
        mirrored = self.memory_client.recall(
            text_query,
            user_id=user_id,
            limit=min(max(limit, 3), 8),
            scope_filter=["connector", "global"],
            agent_category="screen_state_agent",
            connector_ids=["chrome", "browser", "safari", "firefox", "arc"],
        )
        return {
            "digest": self.world_store.build_digest(matches, max_items=limit),
            "results": [_match_to_dict(match) for match in matches],
            "focusTargets": focus_targets,
            "currentPageSkim": _build_current_page_skim(page_chunks),
            "mirroredMemories": mirrored,
        }

    def memory_now(
        self,
        *,
        user_id: str = "default",
        source_app: Optional[str] = None,
        limit: int = 8,
    ) -> Dict[str, Any]:
        sessions = [asdict(session) for session in self.capture_store.list_sessions(user_id=user_id, source_app=source_app, limit=limit)]
        events = [asdict(event) for event in self.capture_store.list_events(user_id=user_id, source_app=source_app, limit=limit)]
        memories = self.memory_client.recent(user_id=user_id, limit=limit, source_app=source_app)
        transitions = self.world_store.list_recent_transitions(user_id=user_id, limit=limit)
        active_graphs: List[Dict[str, Any]] = []
        for session in self.capture_store.list_sessions(user_id=user_id, source_app=source_app, status="active", limit=max(limit, 4)):
            active_graphs.append(
                {
                    "session": asdict(session),
                    "graph": self.graph_store.load_graph(session.id),
                }
            )
        return {
            "live": True,
            "sessions": sessions,
            "events": events,
            "activeCapture": active_graphs,
            "memories": memories,
            "transitions": transitions,
        }

    def memory_ask(
        self,
        *,
        user_id: str,
        query: str,
        source_app: Optional[str] = None,
        limit: int = 6,
    ) -> Dict[str, Any]:
        filters = {"source_app": source_app} if source_app else {}
        memories = self.memory_client.recall(
            query,
            user_id=user_id,
            limit=limit,
            filters=filters,
            scope_filter=["global", "connector"],
        )
        transition_matches = self.world_store.search_transitions(
            query_latent=self.encoder.encode_text(query),
            task_instruction=query,
            user_id=user_id,
            limit=min(limit, 5),
        )
        session_hits = self._search_session_graph(user_id=user_id, source_app=source_app, query=query, limit=limit)
        return {
            "query": query,
            "memories": memories,
            "sessionGraph": session_hits,
            "worldMemory": {
                "digest": self.world_store.build_digest(transition_matches, max_items=min(limit, 5)),
                "results": [_match_to_dict(match) for match in transition_matches],
            },
        }

    def agent_context_pack(
        self,
        *,
        user_id: str,
        agent_id: Optional[str],
        task_instruction: str,
        source_app: Optional[str] = None,
        current_frame_ref: str = "",
        current_context_text: str = "",
        recent_actions: Optional[Iterable[str]] = None,
        limit: int = 5,
    ) -> Dict[str, Any]:
        global_rows = self.memory_client.recall(
            task_instruction,
            user_id=user_id,
            limit=max(limit, 6),
            filters={"source_app": source_app} if source_app else {},
            scope_filter=["global", "connector"],
            agent_category="screen_state_agent",
            connector_ids=[source_app] if source_app else None,
        )
        agent_rows: List[Dict[str, Any]] = []
        if agent_id:
            agent_rows = self.memory_client.recall(
                task_instruction,
                user_id=user_id,
                agent_id=agent_id,
                limit=max(limit, 4),
                scope_filter=["agent"],
            )
        world_pack = None
        if current_context_text or current_frame_ref:
            frame_ref = current_frame_ref or f"text://context/{uuid.uuid4().hex}"
            world_pack = self.world_context_pack(
                user_id=user_id,
                current_frame_ref=frame_ref,
                current_context_text=current_context_text,
                task_instruction=task_instruction,
                recent_actions=recent_actions,
                limit=limit,
            )
        return {
            "query": task_instruction,
            "activeContext": self.memory_now(user_id=user_id, source_app=source_app, limit=min(limit, 6)),
            "globalMemories": global_rows,
            "agentMemories": agent_rows,
            "sessionGraph": self._search_session_graph(
                user_id=user_id,
                source_app=source_app,
                query=task_instruction,
                limit=limit,
            ),
            "worldMemory": world_pack,
        }

    def list_capture_policies(self) -> Dict[str, Any]:
        return {"items": [asdict(item) for item in self.capture_store.list_policies()]}

    def set_capture_policy(self, *, source_app: str, enabled: bool, mode: str = "sampled", metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        policy = self.capture_store.upsert_policy(
            source_app=source_app.strip().lower(),
            enabled=enabled,
            mode=mode,
            metadata=metadata,
        )
        return {"policy": asdict(policy)}

    def timeline(self, *, user_id: str = "default", source_app: Optional[str] = None, limit: int = 30) -> Dict[str, Any]:
        events = [asdict(event) for event in self.capture_store.list_events(user_id=user_id, source_app=source_app, limit=limit)]
        transitions = self.world_store.list_recent_transitions(user_id=user_id, limit=limit)
        memories = self.memory_client.recent(user_id=user_id, limit=limit, source_app=source_app)
        items: List[Dict[str, Any]] = []
        for event in events:
            items.append({"kind": "event", "timestamp": event["created_at"], "item": event})
        for transition in transitions:
            items.append({"kind": "transition", "timestamp": transition["created_at"], "item": transition})
        for memory in memories:
            items.append({"kind": "memory", "timestamp": str(memory.get("created_at") or memory.get("createdAt") or ""), "item": memory})
        for session in self.capture_store.list_sessions(user_id=user_id, source_app=source_app, limit=max(limit, 4)):
            graph = self.graph_store.load_graph(session.id)
            for action in graph["actions"][-limit:]:
                items.append({"kind": "action", "timestamp": str(action.get("created_at") or ""), "item": action})
            for observation in graph["observations"][-limit:]:
                items.append({"kind": "observation", "timestamp": str(observation.get("created_at") or ""), "item": observation})
            for artifact in graph["artifacts"][-limit:]:
                items.append({"kind": "artifact", "timestamp": str(artifact.get("created_at") or ""), "item": artifact})
        items.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
        return {"items": items[:limit]}

    def _require_session(self, session_id: str) -> Any:
        session = self.capture_store.get_session(session_id)
        if not session:
            raise ValueError("Unknown capture session")
        return session

    def _upsert_surface(self, *, session: Any, payload: Dict[str, Any], source_app: str, namespace: str) -> CapturedSurface:
        graph = self.graph_store.load_graph(session.id)
        surface_payload = dict(payload.get("surface") or {})
        title = str(surface_payload.get("title") or payload.get("title") or payload.get("window_title") or "").strip()
        url = str(surface_payload.get("url") or payload.get("url") or "").strip()
        app_path = str(surface_payload.get("app_path") or payload.get("app_path") or payload.get("path") or "").strip()
        surface_type = str(surface_payload.get("surface_type") or payload.get("surface_type") or "page").strip()
        path_hint = _listify_path_hint(surface_payload.get("path_hint") or payload.get("path_hint") or [])
        stable_source = url or app_path or title or f"{source_app}:{surface_type}"
        surface_id = str(
            surface_payload.get("surface_id")
            or payload.get("surface_id")
            or _stable_surface_id(session.id, source_app, surface_type, stable_source, path_hint)
        )
        existing = self.graph_store.load_surface(session.id, surface_id) or {}
        content_hash = _content_hash(
            payload.get("text")
            or payload.get("text_payload")
            or json.dumps(payload.get("structured") or payload.get("structured_payload") or {}, sort_keys=True)
            or stable_source
        )
        created_at = str(payload.get("created_at") or _now_iso())
        surface = CapturedSurface(
            id=surface_id,
            session_id=session.id,
            user_id=session.user_id,
            source_app=source_app,
            namespace=namespace,
            surface_type=surface_type,
            title=title or str(existing.get("title") or ""),
            url=url or str(existing.get("url") or ""),
            app_path=app_path or str(existing.get("app_path") or ""),
            content_hash=content_hash,
            path_hint=path_hint or list(existing.get("path_hint") or []),
            parent_surface_id=str(
                surface_payload.get("parent_surface_id")
                or payload.get("parent_surface_id")
                or existing.get("parent_surface_id")
                or ""
            )
            or None,
            first_seen_at=str(existing.get("first_seen_at") or created_at),
            last_seen_at=created_at,
            metadata={
                **dict(existing.get("metadata") or {}),
                **dict(surface_payload.get("metadata") or payload.get("surface_metadata") or {}),
            },
        )
        self.graph_store.append_surface(surface)
        if not existing:
            self.graph_store.bump_manifest(session.id, page_count=1)
        if surface.parent_surface_id:
            self.graph_store.append_link(
                CaptureLink(
                    id=str(uuid.uuid4()),
                    session_id=session.id,
                    user_id=session.user_id,
                    source_app=source_app,
                    created_at=created_at,
                    from_id=surface.parent_surface_id,
                    to_id=surface.id,
                    relation="contains",
                    metadata={"surface_type": surface.surface_type},
                )
            )
        return surface

    def _should_promote_observation(self, observation: CapturedObservation) -> bool:
        if observation.structured.get("persist_hint"):
            return True
        if observation.kind in {"selection", "double_click_capture", "code_capture"} and len(observation.text) >= 24:
            return True
        return len(observation.text) >= 120

    def _remember_surface_observation(self, *, session: Any, surface: CapturedSurface, observation: CapturedObservation) -> Dict[str, Any]:
        card = _build_surface_memory_card(surface=surface, observation=observation)
        return self.memory_client.remember(
            card,
            user_id=session.user_id,
            namespace=session.namespace,
            source_app=session.source_app,
            scope="global",
            categories=["surface_memory", "pointer_capture"],
            metadata={
                "memory_type": "surface_memory_card",
                "session_id": session.id,
                "surface_id": surface.id,
                "surface_type": surface.surface_type,
                "url": surface.url,
                "path_hint": surface.path_hint,
                "source_kind": observation.source_kind,
                "observation_kind": observation.kind,
            },
        )

    def _distill_surfaces(self, *, session: Any, graph: Dict[str, Any]) -> List[Dict[str, Any]]:
        observations_by_surface: Dict[str, List[Dict[str, Any]]] = {}
        for observation in graph["observations"]:
            observations_by_surface.setdefault(str(observation.get("surface_id") or ""), []).append(observation)
        distilled: List[Dict[str, Any]] = []
        for surface in graph["surfaces"]:
            surface_id = str(surface.get("id") or "")
            observations = observations_by_surface.get(surface_id, [])
            if not observations:
                continue
            representative = _pick_representative_observation(observations)
            if not representative:
                continue
            distilled.append(
                self.memory_client.remember(
                    _build_surface_memory_card_dict(surface=surface, observation=representative, observation_count=len(observations)),
                    user_id=session.user_id,
                    namespace=session.namespace,
                    source_app=session.source_app,
                    scope="global",
                    categories=["surface_memory", "pointer_capture"],
                    metadata={
                        "memory_type": "surface_memory_card",
                        "session_id": session.id,
                        "surface_id": surface_id,
                        "surface_type": surface.get("surface_type"),
                        "observation_count": len(observations),
                        "url": surface.get("url"),
                        "path_hint": surface.get("path_hint") or [],
                    },
                )
            )
        return distilled

    def _maybe_record_world_transition(
        self,
        *,
        session: Any,
        source_app: str,
        namespace: str,
        action_type: str,
        payload: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        before_context = str(payload.get("before_context") or "")
        after_context = str(payload.get("after_context") or "")
        if not (before_context and after_context and action_type):
            return None
        metadata = dict(payload.get("metadata") or {})
        return self.record_transition(
            user_id=session.user_id,
            source_app=source_app,
            namespace=namespace,
            before_context=before_context,
            after_context=after_context,
            before_frame_ref=str(payload.get("before_frame_ref") or f"text://before/{uuid.uuid4().hex}"),
            after_frame_ref=str(payload.get("after_frame_ref") or f"text://after/{uuid.uuid4().hex}"),
            action_type=action_type,
            action_payload=dict(payload.get("action_payload") or {}),
            task_instruction=str(payload.get("task_instruction") or ""),
            recent_actions=[str(item) for item in (payload.get("recent_actions") or []) if str(item).strip()],
            metadata={
                **metadata,
                "session_id": session.id,
                "surface_id": payload.get("surface_id"),
            },
        )

    def _search_session_graph(
        self,
        *,
        user_id: str,
        source_app: Optional[str],
        query: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        tokens = [token for token in re.findall(r"[a-z0-9]+", query.lower()) if len(token) > 2]
        if not tokens:
            return []
        hits: List[Dict[str, Any]] = []
        for session in self.capture_store.list_sessions(user_id=user_id, source_app=source_app, limit=max(limit, 8)):
            graph = self.graph_store.load_graph(session.id)
            surfaces_by_id = {str(item.get("id") or ""): item for item in graph["surfaces"]}
            for observation in graph["observations"]:
                haystack = " ".join(
                    [
                        str(observation.get("text") or ""),
                        json.dumps(observation.get("structured") or {}, ensure_ascii=True),
                    ]
                ).lower()
                score = sum(1 for token in tokens if token in haystack)
                if score <= 0:
                    continue
                hits.append(
                    {
                        "score": score,
                        "session_id": session.id,
                        "observation": observation,
                        "surface": surfaces_by_id.get(str(observation.get("surface_id") or ""), {}),
                    }
                )
        hits.sort(key=lambda item: (item["score"], str(item["observation"].get("created_at") or "")), reverse=True)
        return hits[:limit]

    @staticmethod
    def _should_mirror_text_event(*, event_type: str, text_payload: str, structured_payload: Dict[str, Any]) -> bool:
        if len(text_payload.strip()) >= 24 and event_type in {
            "selection",
            "manual_capture",
            "note",
            "navigation",
            "ax_summary",
            "dom_summary",
        }:
            return True
        return bool(structured_payload.get("persist_hint"))


def _default_namespace_for_app(source_app: str) -> str:
    app = str(source_app or "").strip().lower()
    if app in {"chrome", "arc", "firefox", "safari"}:
        return "global.browser.chrome"
    if app in {"apple-notes", "notes", "obsidian"}:
        return "global.notes.apple-notes"
    if app in {"vscode", "cursor"}:
        return "global.dev.vscode"
    if app in {"slack", "discord"}:
        return "global.comm.slack"
    if "browser" in app:
        return "world.browser.transitions"
    return f"global.app.{app or 'unknown'}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_iso(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _expire_at(created_at: str, ttl_hours: int) -> str:
    base = _coerce_iso(created_at) or datetime.now(timezone.utc)
    return (base + timedelta(hours=max(int(ttl_hours), 0))).astimezone(timezone.utc).isoformat()


def _extension_for_mime(mime_type: str) -> str:
    guessed = mimetypes.guess_extension(mime_type or "")
    return guessed or ".bin"


def _decode_artifact_bytes(payload: Dict[str, Any]) -> bytes:
    if isinstance(payload.get("content_bytes"), (bytes, bytearray)):
        return bytes(payload["content_bytes"])
    if payload.get("content_base64"):
        raw = str(payload.get("content_base64") or "")
        if "," in raw and raw.startswith("data:"):
            raw = raw.split(",", 1)[1]
        return base64.b64decode(raw)
    path = str(payload.get("path") or "").strip()
    if path:
        return Path(path).read_bytes()
    raise ValueError("Artifact payload requires content_base64, content_bytes, or path")


def _content_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _listify_path_hint(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part for part in [segment.strip() for segment in value.split("/")] if part]
    return []


def _stable_surface_id(session_id: str, source_app: str, surface_type: str, stable_source: str, path_hint: List[str]) -> str:
    payload = "|".join([session_id, source_app, surface_type, stable_source, "/".join(path_hint)])
    return f"surf_{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:16]}"


def _pick_representative_observation(observations: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not observations:
        return None
    ranked = sorted(
        observations,
        key=lambda item: (
            1 if (item.get("structured") or {}).get("persist_hint") else 0,
            len(str(item.get("text") or "")),
            float(item.get("confidence") or 0.0),
        ),
        reverse=True,
    )
    return ranked[0]


def _build_surface_memory_card(*, surface: CapturedSurface, observation: CapturedObservation) -> str:
    return _build_surface_memory_card_dict(
        surface=asdict(surface),
        observation=asdict(observation),
        observation_count=1,
    )


def _build_surface_memory_card_dict(
    *,
    surface: Dict[str, Any],
    observation: Dict[str, Any],
    observation_count: int,
) -> str:
    payload = {
        "type": "surface_memory_card",
        "surface_id": surface.get("id"),
        "surface_type": surface.get("surface_type"),
        "title": surface.get("title"),
        "url": surface.get("url"),
        "app_path": surface.get("app_path"),
        "path_hint": surface.get("path_hint") or [],
        "observation_count": observation_count,
        "source_kind": observation.get("source_kind"),
        "observation_kind": observation.get("kind"),
        "text": str(observation.get("text") or "")[:2000],
        "structured": observation.get("structured") or {},
    }
    return json.dumps(payload, ensure_ascii=True, sort_keys=True)


def _build_session_summary_v2(
    *,
    session: Any,
    events: List[CaptureEvent],
    graph: Dict[str, Any],
    summary_hint: str = "",
) -> str:
    surfaces = graph.get("surfaces") or []
    observations = graph.get("observations") or []
    actions = graph.get("actions") or []
    top_titles = [str(item.get("title") or "") for item in surfaces if str(item.get("title") or "").strip()][:3]
    top_urls = [str(item.get("url") or "") for item in surfaces if str(item.get("url") or "").strip()][:3]
    action_types = ", ".join(sorted({str(item.get("action_type") or "") for item in actions if str(item.get("action_type") or "")})[:6])
    observation_kinds = ", ".join(sorted({str(item.get("kind") or "") for item in observations if str(item.get("kind") or "")})[:6])
    lines = [
        f"Pointer capture session summary for {session.source_app}.",
        f"Namespace: {session.namespace}. Actions: {len(actions)}. Surfaces: {len(surfaces)}. Observations: {len(observations)}. Legacy events: {len(events)}.",
    ]
    if action_types:
        lines.append(f"User actions: {action_types}.")
    if observation_kinds:
        lines.append(f"Observation kinds: {observation_kinds}.")
    if top_titles:
        lines.append(f"Top surfaces: {' | '.join(top_titles)}.")
    if top_urls:
        lines.append(f"Surface URLs: {' | '.join(top_urls)}.")
    if summary_hint:
        lines.append(summary_hint.strip())
    return " ".join(lines)


def _build_session_summary(*, session: Any, events: List[CaptureEvent], summary_hint: str = "") -> str:
    top_urls = []
    top_titles = []
    for event in events[:12]:
        if event.url and event.url not in top_urls:
            top_urls.append(event.url)
        if event.window_title and event.window_title not in top_titles:
            top_titles.append(event.window_title)
    event_types = ", ".join(sorted({event.event_type for event in events if event.event_type})[:6])
    actions = ", ".join(sorted({event.action_type for event in events if event.action_type})[:6])
    lines = [
        f"Capture session summary for {session.source_app}.",
        f"Namespace: {session.namespace}. Events: {len(events)}. Event types: {event_types or 'n/a'}.",
    ]
    if actions:
        lines.append(f"Observed actions: {actions}.")
    if top_titles:
        lines.append(f"Window focus: {' | '.join(top_titles[:3])}.")
    if top_urls:
        lines.append(f"URLs: {' | '.join(top_urls[:3])}.")
    if summary_hint:
        lines.append(summary_hint.strip())
    return " ".join(lines)


def _summarize_structured_payload(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True)[:500]


def _extract_dom_evidence(
    *,
    html: str,
    frame_ref: str,
    encoder: DeterministicFrameEncoder,
    limit: int = 32,
) -> List[Dict[str, Any]]:
    if not html:
        fallback = _extract_frame_keywords(frame_ref, fallback_text="", limit=12)
        if not fallback:
            return []
        return [
            {
                "chunk_type": "fallback",
                "role": "page",
                "label": Path(frame_ref).stem or "page",
                "text": fallback,
                "selector_hint": Path(frame_ref).name,
                "position": 0,
                "embedding": encoder.encode_text(fallback),
                "metadata": {"source": "fallback"},
            }
        ]
    chunks: List[Dict[str, Any]] = []
    if BeautifulSoup is None:
        text = _keywords_from_text(html, limit=48)
        if text:
            chunks.append(
                {
                    "chunk_type": "page_text",
                    "role": "page",
                    "label": Path(frame_ref).stem or "page",
                    "text": text,
                    "selector_hint": Path(frame_ref).name,
                    "position": 0,
                    "embedding": encoder.encode_text(text),
                    "metadata": {"source": "raw_html"},
                }
            )
        return chunks
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    title = (soup.title.get_text(" ", strip=True) if soup.title else "").strip()
    if title:
        chunks.append(_make_chunk(encoder, 0, "page_title", "page", title, title, "title", {}))
    selectors = [
        ("heading", "h1, h2, h3"),
        ("control", "button, a, input, select, textarea, option"),
        ("landmark", "[role], [aria-label], [data-testid]"),
    ]
    position = len(chunks)
    seen = set()
    for chunk_type, selector in selectors:
        for element in soup.select(selector):
            descriptor = _element_descriptor(element)
            fingerprint = (
                descriptor["role"],
                descriptor["label"],
                descriptor["selector_hint"],
                descriptor["text"],
            )
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            if not descriptor["label"] and not descriptor["text"]:
                continue
            chunks.append(
                _make_chunk(
                    encoder,
                    position,
                    chunk_type,
                    descriptor["role"],
                    descriptor["label"],
                    descriptor["text"],
                    descriptor["selector_hint"],
                    descriptor["metadata"],
                )
            )
            position += 1
            if len(chunks) >= limit:
                return chunks[:limit]
    body_text = soup.get_text(" ", strip=True)
    skim = _keywords_from_text(body_text, limit=36)
    if skim and len(chunks) < limit:
        chunks.append(_make_chunk(encoder, position, "page_skim", "page", "visible_text", skim, "body", {"source": "visible_text"}))
    return chunks[:limit]


def _make_chunk(
    encoder: DeterministicFrameEncoder,
    position: int,
    chunk_type: str,
    role: str,
    label: str,
    text: str,
    selector_hint: str,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    packed = " | ".join(part for part in [role, label, text, selector_hint] if part)
    return {
        "chunk_type": chunk_type,
        "role": role,
        "label": label,
        "text": text,
        "selector_hint": selector_hint,
        "position": position,
        "embedding": encoder.encode_text(packed),
        "metadata": metadata,
    }


def _element_descriptor(element: Any) -> Dict[str, Any]:
    tag_name = getattr(element, "name", "") or ""
    attrs = getattr(element, "attrs", {}) or {}
    role = str(attrs.get("role") or _default_role(tag_name)).strip()
    text = element.get_text(" ", strip=True)
    label = str(
        attrs.get("aria-label")
        or attrs.get("placeholder")
        or attrs.get("name")
        or attrs.get("value")
        or text
        or tag_name
    ).strip()
    selector_bits = [tag_name]
    if attrs.get("id"):
        selector_bits.append(f"#{attrs['id']}")
    if attrs.get("name"):
        selector_bits.append(f"[name={attrs['name']}]")
    if attrs.get("data-testid"):
        selector_bits.append(f"[data-testid={attrs['data-testid']}]")
    if attrs.get("aria-label"):
        selector_bits.append(f"[aria-label={attrs['aria-label']}]")
    selector_hint = "".join(selector_bits)[:140]
    metadata = {
        "tag": tag_name,
        "id": str(attrs.get("id", "")),
        "name": str(attrs.get("name", "")),
        "data-testid": str(attrs.get("data-testid", "")),
        "aria-label": str(attrs.get("aria-label", "")),
    }
    return {
        "role": role,
        "label": label[:160],
        "text": text[:280],
        "selector_hint": selector_hint,
        "metadata": metadata,
    }


def _default_role(tag_name: str) -> str:
    return {
        "button": "button",
        "a": "link",
        "input": "input",
        "select": "select",
        "textarea": "textbox",
        "option": "option",
        "h1": "heading",
        "h2": "heading",
        "h3": "heading",
    }.get(tag_name, tag_name or "node")


def _extract_focus_keywords(chunks: List[Dict[str, Any]], frame_ref: str, fallback_text: str) -> str:
    if chunks:
        text = " ".join(
            " ".join(
                part for part in [chunk.get("label", ""), chunk.get("text", ""), chunk.get("selector_hint", "")]
                if part
            )
            for chunk in chunks[:8]
        )
        keywords = _keywords_from_text(text, limit=18)
        if keywords:
            return keywords
    return _extract_frame_keywords(frame_ref, fallback_text=fallback_text, limit=18)


def _extract_frame_keywords(frame_ref: str, *, fallback_text: str = "", limit: int = 18) -> str:
    path = Path(frame_ref)
    if not path.exists():
        source_text = fallback_text or path.name
        return _keywords_from_text(source_text, limit=limit)
    lower = path.name.lower()
    if lower.endswith((".html", ".htm", ".txt", ".md", ".json")):
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            source_text = fallback_text or path.name
            return _keywords_from_text(source_text, limit=limit)
        text = raw
        if BeautifulSoup is not None and lower.endswith((".html", ".htm")):
            soup = BeautifulSoup(raw, "html.parser")
            for tag in soup(["script", "style"]):
                tag.decompose()
            text = soup.get_text(" ", strip=True)
        return _keywords_from_text(text, limit=limit)
    source_text = fallback_text or path.stem
    return _keywords_from_text(source_text, limit=limit)


def _keywords_from_text(text: str, *, limit: int) -> str:
    tokens = []
    seen = set()
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        if len(token) <= 1 or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
        if len(tokens) >= limit:
            break
    return " ".join(tokens)


def _collect_focus_targets(matches: List[Any], limit: int = 6) -> List[Dict[str, Any]]:
    targets: List[Dict[str, Any]] = []
    for match in matches:
        ordered_chunks = sorted(
            match.evidence_chunks,
            key=lambda chunk: (
                1 if chunk.chunk_type in {"page_skim", "page_title", "fallback"} else 0,
                0 if chunk.state_id == match.target_state.id else 1,
                chunk.position,
            ),
        )
        for chunk in ordered_chunks[:2]:
            targets.append(
                {
                    "ptr": match.transition.ptr,
                    "role": chunk.role,
                    "label": chunk.label,
                    "selector_hint": chunk.selector_hint,
                    "position": chunk.position,
                    "score": match.score,
                }
            )
            if len(targets) >= limit:
                return targets
    return targets


def _build_current_page_skim(chunks: List[Dict[str, Any]], limit: int = 6) -> List[Dict[str, Any]]:
    skim: List[Dict[str, Any]] = []
    for chunk in chunks:
        if chunk.get("chunk_type") in {"page_skim", "fallback"}:
            continue
        skim.append(
            {
                "role": str(chunk.get("role", "")),
                "label": str(chunk.get("label", "")),
                "selector_hint": str(chunk.get("selector_hint", "")),
                "position": int(chunk.get("position", 0)),
            }
        )
        if len(skim) >= limit:
            break
    return skim


def _slim_focus(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for chunk in chunks[:4]:
        items.append(
            {
                "role": str(chunk.get("role", "")),
                "label": str(chunk.get("label", "")),
                "selector_hint": str(chunk.get("selector_hint", "")),
                "position": int(chunk.get("position", 0)),
            }
        )
    return items


def _build_structured_memory_card(
    *,
    ptr: str,
    action_type: str,
    task_instruction: str,
    surprise: float,
    source_state_id: str,
    target_state_id: str,
    source_focus: List[Dict[str, Any]],
    target_focus: List[Dict[str, Any]],
    recent_actions: List[str],
) -> str:
    card = {
        "type": "world_memory_card",
        "ptr": ptr,
        "task": task_instruction or "n/a",
        "action": action_type,
        "surprise": round(surprise, 3),
        "source_state_id": source_state_id,
        "target_state_id": target_state_id,
        "recent_actions": list(recent_actions[:6]),
        "source_focus": _slim_focus(source_focus),
        "target_focus": _slim_focus(target_focus),
    }
    return json.dumps(card, ensure_ascii=True, sort_keys=True)


def _build_text_recall_query(*, task_instruction: str, frame_keywords: str, recent_actions: List[str]) -> str:
    recent = ", ".join(recent_actions[:4]) if recent_actions else "none"
    return "\n".join(
        [
            f"task: {task_instruction}",
            f"current_screen: {frame_keywords or 'n/a'}",
            f"recent_actions: {recent}",
            "retrieve the closest world-memory screen transition",
        ]
    )


def _match_to_dict(match: Any) -> Dict[str, Any]:
    return {
        "ptr": match.transition.ptr,
        "score": match.score,
        "latentScore": match.latent_score,
        "instructionScore": match.instruction_score,
        "actionScore": match.action_score,
        "surpriseScore": match.surprise_score,
        "evidenceScore": match.evidence_score,
        "actionType": match.transition.action_type,
        "instructionContext": match.transition.instruction_context,
        "surprise": match.transition.surprise,
        "sourceState": asdict(match.source_state),
        "targetState": asdict(match.target_state),
        "evidenceChunks": [asdict_chunk(chunk) for chunk in match.evidence_chunks],
    }
