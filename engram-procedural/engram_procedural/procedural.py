"""Procedural — learn, refine, and recall step-by-step procedures."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from engram_procedural.config import ProceduralConfig
from engram_procedural.extraction import (
    abstract_procedure,
    compute_automaticity,
    extract_procedure as _extract_procedure_llm,
)

logger = logging.getLogger(__name__)


class Procedural:
    """Procedural memory — The Craftsman.

    Provides:
    - Extract step-by-step procedures from episode memories
    - Track execution success/failure rates
    - Automatic retrieval boost for well-practiced procedures
    - Cross-domain abstraction of transferable patterns
    """

    def __init__(
        self,
        memory: Any,
        user_id: str = "default",
        config: Optional[ProceduralConfig] = None,
    ) -> None:
        self.memory = memory
        self.user_id = user_id
        self.config = config or ProceduralConfig()

    # ── Helpers ──

    def _find_procedure(self, name: str) -> Optional[Dict]:
        """Find a procedure by name."""
        results = self.memory.get_all(
            user_id=self.user_id,
            filters={"memory_type": "procedural", "proc_name": name},
            limit=1,
        )
        items = results.get("results", []) if isinstance(results, dict) else results
        return items[0] if items else None

    def _format_procedure(self, mem: Dict) -> Dict:
        """Format a raw memory into a procedure dict."""
        md = mem.get("metadata", {}) or {}
        steps = md.get("proc_steps", [])
        if isinstance(steps, str):
            try:
                steps = json.loads(steps)
            except (json.JSONDecodeError, TypeError):
                steps = [steps]
        return {
            "id": mem.get("id", ""),
            "name": md.get("proc_name", ""),
            "steps": steps,
            "domain": md.get("proc_domain", ""),
            "agent_id": md.get("proc_agent_id", ""),
            "source_episode_ids": md.get("proc_source_episode_ids", []),
            "use_count": md.get("proc_use_count", 0),
            "success_count": md.get("proc_success_count", 0),
            "success_rate": md.get("proc_success_rate", 0.0),
            "automaticity": md.get("proc_automaticity", 0.0),
            "status": md.get("proc_status", "draft"),
            "abstract": md.get("proc_abstract"),
            "last_used_at": md.get("proc_last_used_at"),
            "created_at": md.get("proc_created_at", ""),
        }

    # ── Public API ──

    def extract_procedure(
        self,
        episode_ids: List[str],
        name: str,
        domain: str = "",
        agent_id: str = "",
    ) -> Dict:
        """Extract a step-by-step procedure from multiple episode memories.

        Uses LLM to find common steps across episodes.
        Stores as memory_type='procedural'.
        """
        # Gather episode content
        episodes = []
        for eid in episode_ids:
            mem = self.memory.get(eid)
            if mem:
                episodes.append(mem.get("memory", ""))

        if len(episodes) < self.config.min_episodes_for_extraction:
            return {
                "error": f"Need at least {self.config.min_episodes_for_extraction} episodes, got {len(episodes)}"
            }

        # Check if procedure with this name already exists
        existing = self._find_procedure(name)
        if existing:
            return self._format_procedure(existing)

        # Extract via LLM
        llm = getattr(self.memory, "llm", None)
        if llm:
            extracted = _extract_procedure_llm(
                episodes, llm, self.config.extraction_prompt
            )
        else:
            extracted = {
                "name": name,
                "steps": episodes,
                "domain": domain,
                "confidence": 0.5,
            }

        now = datetime.now(timezone.utc).isoformat()
        steps = extracted.get("steps", [])
        if isinstance(steps, list):
            steps_json = json.dumps(steps)
        else:
            steps_json = str(steps)

        metadata = {
            "memory_type": "procedural",
            "explicit_remember": True,
            "proc_name": name,
            "proc_steps": steps_json,
            "proc_domain": domain or extracted.get("domain", ""),
            "proc_agent_id": agent_id,
            "proc_source_episode_ids": json.dumps(episode_ids),
            "proc_use_count": 0,
            "proc_success_count": 0,
            "proc_success_rate": 0.0,
            "proc_automaticity": 0.0,
            "proc_status": "active",
            "proc_created_at": now,
            "proc_last_used_at": "",
        }

        content = f"Procedure: {name}\nDomain: {metadata['proc_domain']}\nSteps: {steps_json}"
        result = self.memory.add(
            content,
            user_id=self.user_id,
            metadata=metadata,
            categories=["procedures"],
            infer=False,
        )
        items = result.get("results", [])
        if items and items[0].get("id"):
            # add() returns slim result without metadata; fetch full memory
            full = self.memory.get(items[0]["id"])
            if full:
                return self._format_procedure(full)
        return {"name": name, "steps": steps, "status": "active"}

    def get_procedure(self, name: str) -> Optional[Dict]:
        """Retrieve a named procedure with full stats."""
        mem = self._find_procedure(name)
        if mem:
            return self._format_procedure(mem)
        return None

    def search_procedures(self, query: str, limit: int = 10) -> List[Dict]:
        """Semantic search over procedures. Automatic procedures rank higher."""
        results = self.memory.search(
            query,
            user_id=self.user_id,
            filters={"memory_type": "procedural"},
            limit=limit,
            use_echo_rerank=False,
        )
        items = results.get("results", [])
        procedures = []
        for item in items:
            proc = self._format_procedure(item)
            proc["similarity"] = item.get("score", item.get("similarity", 0.0))
            procedures.append(proc)
        # Sort: automatic procedures first, then by similarity
        procedures.sort(
            key=lambda p: (p.get("automaticity", 0) >= 0.5, p.get("similarity", 0)),
            reverse=True,
        )
        return procedures

    def log_execution(
        self,
        procedure_id: str,
        success: bool,
        context: str = "",
        notes: str = "",
    ) -> Dict:
        """Record a procedure execution. Updates use_count, success_rate, automaticity."""
        mem = self.memory.get(procedure_id)
        if not mem:
            return {"error": f"Procedure '{procedure_id}' not found"}

        md = mem.get("metadata", {}) or {}
        use_count = md.get("proc_use_count", 0) + 1
        success_count = md.get("proc_success_count", 0) + (1 if success else 0)
        success_rate = success_count / use_count if use_count > 0 else 0.0
        automaticity = compute_automaticity(
            use_count, success_rate, self.config.automaticity_threshold
        )
        now = datetime.now(timezone.utc).isoformat()

        update_metadata = {
            **md,
            "proc_use_count": use_count,
            "proc_success_count": success_count,
            "proc_success_rate": round(success_rate, 4),
            "proc_automaticity": round(automaticity, 4),
            "proc_last_used_at": now,
        }

        self.memory.update(procedure_id, {
            "metadata": update_metadata,
        })

        return {
            "procedure_id": procedure_id,
            "use_count": use_count,
            "success_count": success_count,
            "success_rate": round(success_rate, 4),
            "automaticity": round(automaticity, 4),
            "logged_at": now,
        }

    def refine_procedure(self, procedure_id: str, correction: str) -> Dict:
        """Update steps based on new experience.

        LLM merges correction into existing steps. Routes through conflict
        resolution if contradictory. Logs version via memory_history.
        """
        mem = self.memory.get(procedure_id)
        if not mem:
            return {"error": f"Procedure '{procedure_id}' not found"}

        md = mem.get("metadata", {}) or {}
        old_steps = md.get("proc_steps", "[]")
        if isinstance(old_steps, str):
            try:
                old_steps = json.loads(old_steps)
            except (json.JSONDecodeError, TypeError):
                old_steps = [old_steps]

        llm = getattr(self.memory, "llm", None)
        if llm:
            prompt = (
                f"Current procedure steps:\n{json.dumps(old_steps, indent=2)}\n\n"
                f"Correction/refinement:\n{correction}\n\n"
                f"Merge the correction into the steps. Return updated steps as JSON list."
            )
            try:
                response = llm.generate(prompt)
                text = response if isinstance(response, str) else str(response)
                start = text.find("[")
                if start >= 0:
                    new_steps, _ = json.JSONDecoder().raw_decode(text, start)
                else:
                    new_steps = old_steps + [correction]
            except Exception:
                new_steps = old_steps + [correction]
        else:
            new_steps = old_steps + [correction]

        new_steps_json = json.dumps(new_steps)
        content = f"Procedure: {md.get('proc_name', '')}\nDomain: {md.get('proc_domain', '')}\nSteps: {new_steps_json}"

        update_metadata = {**md, "proc_steps": new_steps_json}
        self.memory.update(procedure_id, {
            "content": content,
            "metadata": update_metadata,
        })

        return {
            "procedure_id": procedure_id,
            "old_steps": old_steps,
            "new_steps": new_steps,
            "refined": True,
        }

    def list_procedures(
        self, status: str = "active", limit: int = 20
    ) -> List[Dict]:
        """List procedures by status: active, deprecated, draft."""
        filters: Dict[str, Any] = {"memory_type": "procedural"}
        if status:
            filters["proc_status"] = status
        results = self.memory.get_all(
            user_id=self.user_id,
            filters=filters,
            limit=limit,
        )
        items = results.get("results", []) if isinstance(results, dict) else results
        return [self._format_procedure(m) for m in items]
