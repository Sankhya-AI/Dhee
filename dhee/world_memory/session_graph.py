from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .schema import (
    CaptureAction,
    CapturedArtifact,
    CapturedObservation,
    CapturedSurface,
    CaptureLink,
    CaptureSession,
)


class SessionGraphStore:
    """Append-only JSONL session graph for pointer-capture flows."""

    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def session_dir(self, session_id: str) -> Path:
        return self.root_dir / session_id

    def init_session(self, session: CaptureSession, *, mode: str = "pointer-capture") -> Dict[str, Any]:
        session_dir = self.session_dir(session.id)
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        manifest = {
            "session_id": session.id,
            "user_id": session.user_id,
            "source_app": session.source_app,
            "namespace": session.namespace,
            "started_at": session.started_at,
            "ended_at": session.ended_at,
            "mode": mode,
            "status": session.status,
            "page_count": 0,
            "action_count": 0,
            "observation_count": 0,
            "artifact_count": 0,
            "artifact_bytes": 0,
            "active_surface_id": None,
            "last_activity_at": session.started_at,
            "metadata": dict(session.metadata or {}),
        }
        self.write_manifest(session.id, manifest)
        for name in ["actions", "surfaces", "observations", "artifacts", "links"]:
            path = self._jsonl_path(session.id, name)
            if not path.exists():
                path.write_text("", encoding="utf-8")
        return manifest

    def write_manifest(self, session_id: str, manifest: Dict[str, Any]) -> Dict[str, Any]:
        path = self.session_dir(session_id) / "session_manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2), encoding="utf-8")
        return manifest

    def read_manifest(self, session_id: str) -> Dict[str, Any]:
        path = self.session_dir(session_id) / "session_manifest.json"
        if not path.exists():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def patch_manifest(self, session_id: str, **updates: Any) -> Dict[str, Any]:
        manifest = self.read_manifest(session_id)
        manifest.update({key: value for key, value in updates.items() if value is not None})
        return self.write_manifest(session_id, manifest)

    def bump_manifest(self, session_id: str, **deltas: int) -> Dict[str, Any]:
        manifest = self.read_manifest(session_id)
        for key, value in deltas.items():
            manifest[key] = int(manifest.get(key, 0)) + int(value)
        return self.write_manifest(session_id, manifest)

    def append_surface(self, surface: CapturedSurface) -> Dict[str, Any]:
        return self._append_dataclass(surface.session_id, "surfaces", surface)

    def append_action(self, action: CaptureAction) -> Dict[str, Any]:
        return self._append_dataclass(action.session_id, "actions", action)

    def append_observation(self, observation: CapturedObservation) -> Dict[str, Any]:
        return self._append_dataclass(observation.session_id, "observations", observation)

    def append_artifact(self, artifact: CapturedArtifact) -> Dict[str, Any]:
        return self._append_dataclass(artifact.session_id, "artifacts", artifact)

    def append_link(self, link: CaptureLink) -> Dict[str, Any]:
        return self._append_dataclass(link.session_id, "links", link)

    def load_graph(self, session_id: str) -> Dict[str, Any]:
        manifest = self.read_manifest(session_id)
        actions = self._read_jsonl(session_id, "actions")
        observations = self._read_jsonl(session_id, "observations")
        links = self._read_jsonl(session_id, "links")
        surfaces = list(self._latest_by_id(self._read_jsonl(session_id, "surfaces")).values())
        artifacts = list(self._latest_by_id(self._read_jsonl(session_id, "artifacts")).values())
        return {
            "manifest": manifest,
            "actions": actions,
            "surfaces": surfaces,
            "observations": observations,
            "artifacts": artifacts,
            "links": links,
        }

    def load_surface(self, session_id: str, surface_id: str) -> Optional[Dict[str, Any]]:
        return self._latest_by_id(self._read_jsonl(session_id, "surfaces")).get(surface_id)

    def find_artifact_by_hash(self, session_id: str, sha256: str) -> Optional[Dict[str, Any]]:
        for artifact in self._latest_by_id(self._read_jsonl(session_id, "artifacts")).values():
            if str(artifact.get("sha256") or "") == sha256:
                return artifact
        return None

    def save_artifact_bytes(self, session_id: str, filename: str, data: bytes) -> str:
        target = self.session_dir(session_id) / "artifacts" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return str(target)

    def remove_artifact_path(self, path: str) -> bool:
        try:
            target = Path(path)
            if target.exists():
                target.unlink()
            return True
        except OSError:
            return False

    def list_session_ids(self) -> List[str]:
        ids: List[str] = []
        for child in sorted(self.root_dir.iterdir()) if self.root_dir.exists() else []:
            if child.is_dir():
                ids.append(child.name)
        return ids

    def artifact_bytes(self, session_id: str) -> int:
        total = 0
        artifacts_dir = self.session_dir(session_id) / "artifacts"
        if not artifacts_dir.exists():
            return 0
        for path in artifacts_dir.iterdir():
            if path.is_file():
                total += path.stat().st_size
        return total

    def _append_dataclass(self, session_id: str, name: str, item: Any) -> Dict[str, Any]:
        payload = asdict(item)
        self.append_record(session_id, name, payload)
        return payload

    def append_record(self, session_id: str, name: str, payload: Dict[str, Any]) -> None:
        path = self._jsonl_path(session_id, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True))
            handle.write("\n")

    def _jsonl_path(self, session_id: str, name: str) -> Path:
        return self.session_dir(session_id) / f"{name}.jsonl"

    def _read_jsonl(self, session_id: str, name: str) -> List[Dict[str, Any]]:
        path = self._jsonl_path(session_id, name)
        if not path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
        return rows

    @staticmethod
    def _latest_by_id(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        latest: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            key = str(row.get("id") or "")
            if key:
                latest[key] = row
        return latest
