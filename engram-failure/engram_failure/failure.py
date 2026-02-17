"""FailureLearning — log failures, extract anti-patterns, discover recovery strategies."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from engram_failure.config import FailureConfig
from engram_failure.patterns import (
    extract_antipattern as _extract_antipattern_llm,
    extract_recovery_strategy as _extract_recovery_llm,
)

logger = logging.getLogger(__name__)


class FailureLearning:
    """Failure learning — The Debugger.

    Provides:
    - Log failures with context and error details
    - Search past failures for similar situations
    - Extract anti-patterns (things NOT to do) from failure clusters
    - Discover recovery strategies from resolved failures
    """

    def __init__(
        self,
        memory: Any,
        user_id: str = "default",
        config: Optional[FailureConfig] = None,
    ) -> None:
        self.memory = memory
        self.user_id = user_id
        self.config = config or FailureConfig()

    # ── Helpers ──

    def _format_failure(self, mem: Dict) -> Dict:
        md = mem.get("metadata", {}) or {}
        return {
            "id": mem.get("id", ""),
            "action": md.get("fl_action", ""),
            "error": md.get("fl_error", ""),
            "context": md.get("fl_context", ""),
            "severity": md.get("fl_severity", "medium"),
            "resolution": md.get("fl_resolution", ""),
            "resolved": md.get("fl_resolved", False),
            "agent_id": md.get("fl_agent_id", ""),
            "created_at": md.get("fl_created_at", ""),
        }

    def _format_antipattern(self, mem: Dict) -> Dict:
        md = mem.get("metadata", {}) or {}
        warning_signs = md.get("fl_warning_signs", [])
        if isinstance(warning_signs, str):
            try:
                warning_signs = json.loads(warning_signs)
            except (json.JSONDecodeError, TypeError):
                warning_signs = [warning_signs]
        return {
            "id": mem.get("id", ""),
            "name": md.get("fl_antipattern_name", ""),
            "description": md.get("fl_antipattern_description", ""),
            "warning_signs": warning_signs,
            "alternative": md.get("fl_alternative", ""),
            "source_failure_ids": md.get("fl_source_failure_ids", []),
            "created_at": md.get("fl_created_at", ""),
        }

    # ── Public API ──

    def log_failure(
        self,
        action: str,
        error: str,
        context: str = "",
        severity: str = "medium",
        agent_id: str = "",
    ) -> Dict:
        """Log a failure with context and error details."""
        now = datetime.now(timezone.utc).isoformat()
        metadata = {
            "memory_type": "failure",
            "explicit_remember": True,
            "fl_action": action,
            "fl_error": error,
            "fl_context": context,
            "fl_severity": severity,
            "fl_agent_id": agent_id,
            "fl_resolved": False,
            "fl_resolution": "",
            "fl_created_at": now,
        }

        content = f"Failure: {action} — {error}"
        if context:
            content += f"\nContext: {context}"

        result = self.memory.add(
            content,
            user_id=self.user_id,
            metadata=metadata,
            categories=["failures"],
            infer=False,
        )
        items = result.get("results", [])
        if items and items[0].get("id"):
            # add() returns a slim result without metadata; fetch full memory
            full = self.memory.get(items[0]["id"])
            if full:
                return self._format_failure(full)
        return {"action": action, "error": error, "status": "logged"}

    def search_failures(self, query: str, limit: int = 10) -> List[Dict]:
        """Search past failures for similar situations."""
        results = self.memory.search(
            query,
            user_id=self.user_id,
            filters={"memory_type": "failure"},
            limit=limit,
            use_echo_rerank=False,
        )
        items = results.get("results", [])
        failures = []
        for item in items:
            f = self._format_failure(item)
            f["similarity"] = item.get("score", item.get("similarity", 0.0))
            failures.append(f)
        return failures

    def extract_antipattern(
        self,
        failure_ids: List[str],
        name: str = "",
    ) -> Dict:
        """Extract an anti-pattern from a cluster of similar failures."""
        failures_text = []
        for fid in failure_ids:
            mem = self.memory.get(fid)
            if mem:
                failures_text.append(mem.get("memory", ""))

        if len(failures_text) < self.config.min_failures_for_antipattern:
            return {
                "error": f"Need at least {self.config.min_failures_for_antipattern} failures, got {len(failures_text)}"
            }

        llm = getattr(self.memory, "llm", None)
        if llm:
            extracted = _extract_antipattern_llm(
                failures_text, llm, self.config.extraction_prompt
            )
        else:
            extracted = {
                "name": name or "unnamed_antipattern",
                "description": "Grouped failure pattern",
                "warning_signs": [],
                "alternative": "",
                "confidence": 0.5,
            }

        now = datetime.now(timezone.utc).isoformat()
        ap_name = name or extracted.get("name", "unnamed_antipattern")
        warning_signs = extracted.get("warning_signs", [])

        metadata = {
            "memory_type": "antipattern",
            "explicit_remember": True,
            "fl_antipattern_name": ap_name,
            "fl_antipattern_description": extracted.get("description", ""),
            "fl_warning_signs": json.dumps(warning_signs) if isinstance(warning_signs, list) else str(warning_signs),
            "fl_alternative": extracted.get("alternative", ""),
            "fl_source_failure_ids": json.dumps(failure_ids),
            "fl_created_at": now,
        }

        content = f"Anti-pattern: {ap_name} — {extracted.get('description', '')}"
        result = self.memory.add(
            content,
            user_id=self.user_id,
            metadata=metadata,
            categories=["antipatterns"],
            infer=False,
        )
        items = result.get("results", [])
        if items and items[0].get("id"):
            full = self.memory.get(items[0]["id"])
            if full:
                return self._format_antipattern(full)
        return {"name": ap_name, "description": extracted.get("description", "")}

    def list_antipatterns(self, limit: int = 20) -> List[Dict]:
        """List extracted anti-patterns."""
        results = self.memory.get_all(
            user_id=self.user_id,
            filters={"memory_type": "antipattern"},
            limit=limit,
        )
        items = results.get("results", []) if isinstance(results, dict) else results
        return [self._format_antipattern(m) for m in items]

    def get_failure_stats(self) -> Dict:
        """Get statistics on failures and anti-patterns."""
        failures = self.memory.get_all(
            user_id=self.user_id,
            filters={"memory_type": "failure"},
            limit=1000,
        )
        failure_items = failures.get("results", []) if isinstance(failures, dict) else failures
        resolved = sum(
            1 for f in failure_items
            if (f.get("metadata", {}) or {}).get("fl_resolved", False)
        )

        antipatterns = self.memory.get_all(
            user_id=self.user_id,
            filters={"memory_type": "antipattern"},
            limit=100,
        )
        ap_items = antipatterns.get("results", []) if isinstance(antipatterns, dict) else antipatterns

        return {
            "total_failures": len(failure_items),
            "resolved": resolved,
            "unresolved": len(failure_items) - resolved,
            "antipatterns": len(ap_items),
        }

    def search_recovery_strategies(self, query: str, limit: int = 5) -> List[Dict]:
        """Search for recovery strategies from resolved failures."""
        results = self.memory.search(
            query,
            user_id=self.user_id,
            filters={"memory_type": "recovery_strategy"},
            limit=limit,
            use_echo_rerank=False,
        )
        items = results.get("results", [])
        strategies = []
        for item in items:
            md = item.get("metadata", {}) or {}
            steps = md.get("fl_recovery_steps", [])
            if isinstance(steps, str):
                try:
                    steps = json.loads(steps)
                except (json.JSONDecodeError, TypeError):
                    steps = [steps]
            strategies.append({
                "id": item.get("id", ""),
                "name": md.get("fl_recovery_name", ""),
                "steps": steps,
                "applicable_when": md.get("fl_applicable_when", ""),
                "similarity": item.get("score", item.get("similarity", 0.0)),
            })
        return strategies
