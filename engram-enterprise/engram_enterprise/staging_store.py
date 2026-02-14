"""Staging storage for untrusted agent writes."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class StagingStore:
    def __init__(self, db):
        self.db = db

    def create_commit(
        self,
        *,
        user_id: str,
        agent_id: Optional[str],
        scope: str,
        changes: List[Dict[str, Any]],
        checks: Dict[str, Any],
        preview: Dict[str, Any],
        provenance: Dict[str, Any],
        status: str = "PENDING",
    ) -> Dict[str, Any]:
        commit_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        payload = {
            "id": commit_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "scope": scope,
            "status": status,
            "checks": checks,
            "preview": preview,
            "provenance": provenance,
            "created_at": created_at,
            "updated_at": created_at,
        }
        self.db.add_proposal_commit(payload, changes=changes)
        return {**payload, "changes": changes}

    def list_commits(self, user_id: Optional[str], status: Optional[str], limit: int = 100) -> List[Dict[str, Any]]:
        commits = self.db.list_proposal_commits(user_id=user_id, status=status, limit=limit)
        for commit in commits:
            commit["changes"] = self.db.get_proposal_changes(commit["id"])
        return commits

    def get_commit(self, commit_id: str) -> Optional[Dict[str, Any]]:
        return self.db.get_proposal_commit(commit_id)

    def mark_approved(self, commit_id: str) -> None:
        self.db.update_proposal_commit(commit_id, {"status": "APPROVED"})

    def mark_rejected(self, commit_id: str, reason: Optional[str] = None) -> None:
        updates = {"status": "REJECTED"}
        if reason:
            commit = self.get_commit(commit_id) or {}
            checks = dict(commit.get("checks", {}))
            checks["rejection_reason"] = reason
            updates["checks"] = checks
        self.db.update_proposal_commit(commit_id, updates)

    def mark_auto_stashed(self, commit_id: str) -> None:
        self.db.update_proposal_commit(commit_id, {"status": "AUTO_STASHED"})

    def add_conflict(
        self,
        *,
        user_id: str,
        conflict_key: str,
        existing: Dict[str, Any],
        proposed: Dict[str, Any],
        source_commit_id: Optional[str],
    ) -> str:
        return self.db.add_conflict_stash(
            {
                "user_id": user_id,
                "conflict_key": conflict_key,
                "existing": existing,
                "proposed": proposed,
                "source_commit_id": source_commit_id,
                "resolution": "UNRESOLVED",
            }
        )

    def resolve_conflict(self, stash_id: str, resolution: str) -> bool:
        return self.db.resolve_conflict_stash(stash_id, resolution)

    def list_conflicts(
        self,
        *,
        user_id: Optional[str],
        resolution: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        return self.db.list_conflict_stash(user_id=user_id, resolution=resolution, limit=limit)
