"""Invariant validation for staged writes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_NAME_RE = re.compile(r"\b(?:my\s+name\s+is|name:)\s*([A-Za-z][A-Za-z\s'-]{1,80})", re.IGNORECASE)
_LOCATION_RE = re.compile(r"\b(?:i\s+live\s+in|based\s+in|location:)\s*([A-Za-z][A-Za-z\s'-]{1,80})", re.IGNORECASE)
_SECRET_RE = re.compile(r"\b(password|api[_\s-]?key|secret|access token|private key)\b", re.IGNORECASE)


@dataclass
class InvariantConflict:
    key: str
    existing: str
    proposed: str


class InvariantEngine:
    def __init__(self, db):
        self.db = db

    def evaluate_add(self, *, user_id: str, content: str) -> Dict[str, Any]:
        checks: Dict[str, Any] = {
            "invariants_ok": True,
            "conflicts": [],
            "risk_score": 0.0,
            "duplicate_of": None,
            "pii_risk": False,
        }

        existing = self.db.get_all_memories(user_id=user_id, include_tombstoned=False)
        normalized_content = (content or "").strip().lower()
        for mem in existing:
            existing_text = (mem.get("memory") or "").strip().lower()
            if existing_text and existing_text == normalized_content:
                checks["duplicate_of"] = mem.get("id")
                checks["risk_score"] = max(checks["risk_score"], 0.35)
                break

        proposed_pairs = self.extract_invariant_pairs(content)
        conflicts: List[InvariantConflict] = []
        for key, proposed in proposed_pairs:
            current = self.db.get_invariant(user_id, key)
            if not current:
                continue
            current_value = str(current.get("invariant_value", "")).strip()
            if current_value and current_value.lower() != str(proposed).strip().lower():
                conflicts.append(
                    InvariantConflict(
                        key=key,
                        existing=current_value,
                        proposed=str(proposed).strip(),
                    )
                )

        if conflicts:
            checks["invariants_ok"] = False
            checks["conflicts"] = [
                {
                    "key": c.key,
                    "existing": c.existing,
                    "proposed": c.proposed,
                }
                for c in conflicts
            ]
            checks["risk_score"] = max(checks["risk_score"], 0.72)

        pii_risk = bool(_SECRET_RE.search(content or ""))
        if pii_risk:
            checks["pii_risk"] = True
            checks["risk_score"] = max(checks["risk_score"], 0.85)

        if not conflicts and not pii_risk and checks["duplicate_of"] is None:
            checks["risk_score"] = max(checks["risk_score"], 0.15)

        return checks

    def extract_invariant_pairs(self, content: str) -> List[Tuple[str, str]]:
        text = content or ""
        pairs: List[Tuple[str, str]] = []

        name_match = _NAME_RE.search(text)
        if name_match:
            pairs.append(("identity.name", name_match.group(1).strip()))

        email_match = _EMAIL_RE.search(text)
        if email_match:
            pairs.append(("identity.primary_email", email_match.group(0).strip()))

        location_match = _LOCATION_RE.search(text)
        if location_match:
            pairs.append(("identity.location", location_match.group(1).strip()))

        return pairs

    def upsert_invariants_from_content(self, *, user_id: str, content: str, source_memory_id: Optional[str]) -> None:
        for key, value in self.extract_invariant_pairs(content):
            self.db.upsert_invariant(
                user_id=user_id,
                invariant_key=key,
                invariant_value=value,
                category="identity",
                confidence=0.9,
                source_memory_id=source_memory_id,
            )
