"""Dhee v3 — Cognitive Maintenance Cycle.

Replaces the phantom AGI loop with honest, real maintenance operations.

v2.2 had 8 steps, 6 of which imported non-existent engram_* packages.
v3 runs only what actually exists:
    1. Consolidation (active → passive, via safe consolidation engine)
    2. Decay (forgetting curves)

Planned but not yet implemented (will be added as real Job classes):
    - Anchor candidate resolution
    - Distillation promotion
    - Conflict scanning
    - Stale intention cleanup

The old API surface (run_agi_cycle, get_system_health) is preserved
for backward compatibility with existing callers.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def run_agi_cycle(
    memory: Any,
    user_id: str = "default",
    context: Optional[str] = None,
) -> Dict[str, Any]:
    """Run one maintenance cycle. Only executes real subsystems.

    Args:
        memory: Dhee Memory instance
        user_id: User identifier for scoped operations
        context: Optional current context (reserved for future use)

    Returns:
        Dict with status of each step
    """
    now = datetime.now(timezone.utc).isoformat()
    results: Dict[str, Any] = {"timestamp": now, "user_id": user_id}

    # Step 1: Consolidation — run distillation (episodic → semantic)
    try:
        if hasattr(memory, "_kernel") and memory._kernel:
            consolidation = memory._kernel.sleep_cycle(user_id=user_id)
            results["consolidation"] = {"status": "ok", "result": consolidation}
        else:
            results["consolidation"] = {"status": "skipped", "reason": "no kernel"}
    except Exception as e:
        results["consolidation"] = {"status": "error", "error": str(e)}

    # Step 2: Decay — apply forgetting curves
    try:
        decay_result = memory.apply_decay(scope={"user_id": user_id})
        results["decay"] = {"status": "ok", "result": decay_result}
    except Exception as e:
        results["decay"] = {"status": "error", "error": str(e)}

    # Compute summary
    statuses = [
        v.get("status", "unknown")
        for v in results.values()
        if isinstance(v, dict) and "status" in v
    ]
    ok_count = statuses.count("ok")
    error_count = statuses.count("error")
    skipped_count = statuses.count("skipped")

    results["summary"] = {
        "ok": ok_count,
        "errors": error_count,
        "skipped": skipped_count,
        "total_subsystems": len(statuses),
    }

    return results


def get_system_health(memory: Any, user_id: str = "default") -> Dict[str, Any]:
    """Report health status across real cognitive subsystems.

    Only reports subsystems that actually exist — no phantom package checks.
    """
    now = datetime.now(timezone.utc).isoformat()
    systems: Dict[str, Dict] = {}

    # Core memory
    try:
        stats = memory.get_stats(user_id=user_id)
        systems["core_memory"] = {"available": True, "stats": stats}
    except Exception as e:
        systems["core_memory"] = {"available": False, "error": str(e)}

    # Knowledge graph
    systems["knowledge_graph"] = {
        "available": (
            hasattr(memory, "knowledge_graph")
            and memory.knowledge_graph is not None
        ),
    }
    if systems["knowledge_graph"]["available"]:
        try:
            systems["knowledge_graph"]["stats"] = memory.knowledge_graph.stats()
        except Exception:
            pass

    # Cognition kernel
    has_kernel = hasattr(memory, "_kernel") and memory._kernel is not None
    systems["cognition_kernel"] = {"available": has_kernel}
    if has_kernel:
        try:
            systems["cognition_kernel"]["stats"] = memory._kernel.cognition_health(
                user_id=user_id
            )
        except Exception:
            pass

    # Active memory / consolidation
    systems["consolidation"] = {
        "available": (
            hasattr(memory, "_consolidation_engine")
            and memory._consolidation_engine is not None
        ),
    }

    # v3 stores (if wired)
    systems["v3_event_store"] = {
        "available": hasattr(memory, "_event_store") and memory._event_store is not None,
    }

    available = sum(1 for s in systems.values() if s.get("available"))
    total = len(systems)

    return {
        "timestamp": now,
        "systems": systems,
        "available": available,
        "total": total,
        "health_pct": round(available / total * 100, 1) if total else 0,
    }
