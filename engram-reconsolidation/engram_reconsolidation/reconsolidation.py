"""Reconsolidation — propose, review, and apply updates to existing memories."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from engram_reconsolidation.config import ReconsolidationConfig
from engram_reconsolidation.window import (
    propose_update as _propose_update_llm,
    should_reconsolidate,
)

logger = logging.getLogger(__name__)


class Reconsolidation:
    """Memory reconsolidation — The Updater.

    Provides:
    - Propose updates to memories based on new context
    - Apply or reject proposals with conflict checking
    - Full version history via memory_history table
    - Auto-apply high-confidence proposals
    """

    def __init__(
        self,
        memory: Any,
        user_id: str = "default",
        config: Optional[ReconsolidationConfig] = None,
    ) -> None:
        self.memory = memory
        self.user_id = user_id
        self.config = config or ReconsolidationConfig()

    # ── Helpers ──

    def _find_proposals(self, target_id: str = "", status: str = "") -> List[Dict]:
        """Find reconsolidation proposals."""
        filters: Dict[str, Any] = {"memory_type": "reconsolidation_proposal"}
        if target_id:
            filters["rc_target_memory_id"] = target_id
        if status:
            filters["rc_status"] = status
        results = self.memory.get_all(
            user_id=self.user_id,
            filters=filters,
            limit=100,
        )
        items = results.get("results", []) if isinstance(results, dict) else results
        return items

    def _format_proposal(self, mem: Dict) -> Dict:
        """Format a raw memory into a proposal dict."""
        md = mem.get("metadata", {}) or {}
        return {
            "id": mem.get("id", ""),
            "target_memory_id": md.get("rc_target_memory_id", ""),
            "old_content": md.get("rc_old_content", ""),
            "proposed_content": md.get("rc_proposed_content", ""),
            "context": md.get("rc_context", ""),
            "confidence": md.get("rc_confidence", 0.0),
            "change_type": md.get("rc_change_type", ""),
            "reasoning": md.get("rc_reasoning", ""),
            "status": md.get("rc_status", "pending"),
            "agent_id": md.get("rc_agent_id", ""),
            "created_at": md.get("rc_created_at", ""),
            "applied_at": md.get("rc_applied_at", ""),
        }

    # ── Public API ──

    def propose_update(
        self,
        memory_id: str,
        new_context: str,
        agent_id: str = "",
    ) -> Dict:
        """Propose an update to a memory based on new context.

        LLM evaluates whether new_context refines this memory.
        Stores as memory_type='reconsolidation_proposal'.
        """
        target = self.memory.get(memory_id)
        if not target:
            return {"error": f"Memory '{memory_id}' not found"}

        old_content = target.get("memory", "")

        # Use LLM to generate proposal
        llm = getattr(self.memory, "llm", None)
        if llm:
            result = _propose_update_llm(old_content, new_context, llm)
        else:
            result = {
                "proposed_content": f"{old_content}\n\nUpdate: {new_context}",
                "confidence": 0.6,
                "reasoning": "Direct append (no LLM available)",
                "change_type": "elaborate",
            }

        # Skip low-confidence proposals
        if result["confidence"] < self.config.min_confidence_for_proposal:
            return {
                "status": "skipped",
                "reason": f"Confidence {result['confidence']:.2f} below threshold {self.config.min_confidence_for_proposal}",
            }

        # Skip no_change proposals
        if result["change_type"] == "no_change":
            return {"status": "no_change", "reason": result.get("reasoning", "")}

        now = datetime.now(timezone.utc).isoformat()
        metadata = {
            "memory_type": "reconsolidation_proposal",
            "explicit_remember": True,
            "rc_target_memory_id": memory_id,
            "rc_old_content": old_content,
            "rc_proposed_content": result["proposed_content"],
            "rc_context": new_context,
            "rc_confidence": result["confidence"],
            "rc_change_type": result["change_type"],
            "rc_reasoning": result["reasoning"],
            "rc_status": "pending",
            "rc_agent_id": agent_id,
            "rc_created_at": now,
            "rc_applied_at": "",
        }

        content = f"Reconsolidation proposal for {memory_id}: {result['change_type']}"
        store_result = self.memory.add(
            content,
            user_id=self.user_id,
            metadata=metadata,
            categories=["reconsolidation"],
            infer=False,
        )
        items = store_result.get("results", [])
        if items and items[0].get("id"):
            # add() returns slim result without metadata; fetch full memory
            full = self.memory.get(items[0]["id"])
            if full:
                return self._format_proposal(full)
        return {
            "status": "pending",
            "target_memory_id": memory_id,
            "confidence": result["confidence"],
            "change_type": result["change_type"],
        }

    def apply_update(self, proposal_id: str) -> Dict:
        """Apply a proposed update to the target memory.

        Calls Memory.update() which auto-logs to memory_history.
        """
        proposal_mem = self.memory.get(proposal_id)
        if not proposal_mem:
            return {"error": f"Proposal '{proposal_id}' not found"}

        md = proposal_mem.get("metadata", {}) or {}
        if md.get("rc_status") != "pending":
            return {"error": f"Proposal status is '{md.get('rc_status')}', expected 'pending'"}

        target_id = md.get("rc_target_memory_id", "")
        proposed_content = md.get("rc_proposed_content", "")

        if not target_id or not proposed_content:
            return {"error": "Invalid proposal: missing target or content"}

        # Apply the update to the target memory
        now = datetime.now(timezone.utc).isoformat()
        target = self.memory.get(target_id)
        if not target:
            return {"error": f"Target memory '{target_id}' not found"}

        target_md = target.get("metadata", {}) or {}
        rc_count = target_md.get("rc_reconsolidation_count", 0) + 1
        rc_version = target_md.get("rc_version", 0) + 1

        # Update target memory
        update_metadata = {
            **target_md,
            "rc_last_reconsolidated_at": now,
            "rc_reconsolidation_count": rc_count,
            "rc_version": rc_version,
        }
        self.memory.update(target_id, {
            "content": proposed_content,
            "metadata": update_metadata,
        })

        # Mark proposal as applied
        proposal_md = {**md, "rc_status": "applied", "rc_applied_at": now}
        self.memory.update(proposal_id, {"metadata": proposal_md})

        return {
            "status": "applied",
            "target_memory_id": target_id,
            "proposal_id": proposal_id,
            "version": rc_version,
            "applied_at": now,
        }

    def reject_update(self, proposal_id: str, reason: str = "") -> Dict:
        """Reject a pending proposal."""
        proposal_mem = self.memory.get(proposal_id)
        if not proposal_mem:
            return {"error": f"Proposal '{proposal_id}' not found"}

        md = proposal_mem.get("metadata", {}) or {}
        if md.get("rc_status") != "pending":
            return {"error": f"Proposal status is '{md.get('rc_status')}', expected 'pending'"}

        now = datetime.now(timezone.utc).isoformat()
        update_md = {**md, "rc_status": "rejected", "rc_reject_reason": reason}
        self.memory.update(proposal_id, {"metadata": update_md})

        return {
            "status": "rejected",
            "proposal_id": proposal_id,
            "reason": reason,
            "rejected_at": now,
        }

    def get_version_history(self, memory_id: str) -> List[Dict]:
        """Get full reconsolidation history for a memory.

        Reads from memory_history table (already populated by db.update_memory).
        """
        try:
            if hasattr(self.memory, "db") and hasattr(self.memory.db, "get_memory_history"):
                history = self.memory.db.get_memory_history(memory_id)
                return history if isinstance(history, list) else []
        except Exception as e:
            logger.warning("Failed to get version history: %s", e)

        # Fallback: find proposals for this memory
        proposals = self._find_proposals(target_id=memory_id)
        return [self._format_proposal(p) for p in proposals]

    def list_pending_proposals(self, limit: int = 20) -> List[Dict]:
        """List proposals awaiting approval."""
        proposals = self._find_proposals(status="pending")
        return [self._format_proposal(p) for p in proposals[:limit]]

    def get_stats(self) -> Dict:
        """Stats on reconsolidation activity."""
        all_proposals = self._find_proposals()
        applied = sum(1 for p in all_proposals if (p.get("metadata", {}) or {}).get("rc_status") == "applied")
        rejected = sum(1 for p in all_proposals if (p.get("metadata", {}) or {}).get("rc_status") == "rejected")
        pending = sum(1 for p in all_proposals if (p.get("metadata", {}) or {}).get("rc_status") == "pending")

        confidences = [
            (p.get("metadata", {}) or {}).get("rc_confidence", 0)
            for p in all_proposals
        ]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        return {
            "total_proposals": len(all_proposals),
            "applied": applied,
            "rejected": rejected,
            "pending": pending,
            "avg_confidence": round(avg_confidence, 3),
        }
