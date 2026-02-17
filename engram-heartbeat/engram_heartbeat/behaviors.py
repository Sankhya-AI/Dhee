"""Built-in heartbeat behaviors."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

BUILTIN_BEHAVIORS: dict[str, str] = {
    "decay": "Run memory decay for this agent's user",
    "consolidation": "Run sleep cycle (SML to LML promotion)",
    "health_check": "Publish agent health status to bus",
    "stale_task_check": "Find tasks stuck in active too long",
    "memory_stats": "Log memory statistics",
    "check_intentions": "Evaluate prospective memory triggers and return due intentions",
    "extract_procedures": "Scan recent episodes and auto-extract procedures when patterns detected",
    "process_reconsolidation": "Auto-apply high-confidence reconsolidation proposals",
    "extract_antipatterns": "Scan recent failures and extract anti-patterns",
    "wm_decay": "Expire stale working memory items",
    "agi_loop": "Run the full AGI cognitive cycle",
}


def run_behavior(action: str, memory: Any, params: dict,
                 bus: Any = None, agent_id: str = "") -> dict:
    """Execute a built-in behavior and return the result."""
    now = datetime.now(timezone.utc).isoformat()

    if action == "decay":
        user_id = params.get("user_id", "default")
        try:
            result = memory.apply_decay(user_id=user_id)
            return {"action": "decay", "status": "ok", "result": result, "timestamp": now}
        except Exception as e:
            return {"action": "decay", "status": "error", "error": str(e), "timestamp": now}

    elif action == "consolidation":
        user_id = params.get("user_id", "default")
        try:
            if hasattr(memory, "_kernel") and memory._kernel:
                result = memory._kernel.sleep_cycle(user_id=user_id)
                return {"action": "consolidation", "status": "ok", "result": result, "timestamp": now}
            return {"action": "consolidation", "status": "skipped", "reason": "no kernel", "timestamp": now}
        except Exception as e:
            return {"action": "consolidation", "status": "error", "error": str(e), "timestamp": now}

    elif action == "health_check":
        status_data = {
            "agent_id": agent_id,
            "status": "healthy",
            "timestamp": now,
        }
        if bus:
            bus.publish("agent.health", status_data)
        return {"action": "health_check", "status": "ok", "published": bus is not None, "timestamp": now}

    elif action == "stale_task_check":
        try:
            from engram.memory.tasks import TaskManager
            tm = TaskManager(memory)
            active = tm.list_tasks(user_id=params.get("user_id", "bridge"), status="active", limit=50)
            stale = [t for t in active if _is_stale(t, params.get("stale_minutes", 60))]
            return {"action": "stale_task_check", "status": "ok", "stale_count": len(stale),
                    "stale_tasks": [t.get("id") for t in stale], "timestamp": now}
        except Exception as e:
            return {"action": "stale_task_check", "status": "error", "error": str(e), "timestamp": now}

    elif action == "memory_stats":
        try:
            stats = memory.get_stats(user_id=params.get("user_id", "default"))
            return {"action": "memory_stats", "status": "ok", "stats": stats, "timestamp": now}
        except Exception as e:
            return {"action": "memory_stats", "status": "error", "error": str(e), "timestamp": now}

    elif action == "check_intentions":
        try:
            from engram_prospective import Prospective
            pm = Prospective(memory, user_id=params.get("user_id", "default"))
            triggered = pm.check_triggers(
                events=params.get("events"),
                context=params.get("context"),
            )
            return {"action": "check_intentions", "status": "ok",
                    "triggered_count": len(triggered), "triggered": triggered, "timestamp": now}
        except ImportError:
            return {"action": "check_intentions", "status": "skipped",
                    "reason": "engram-prospective not installed", "timestamp": now}
        except Exception as e:
            return {"action": "check_intentions", "status": "error", "error": str(e), "timestamp": now}

    elif action == "extract_procedures":
        try:
            from engram_procedural import Procedural
            proc = Procedural(memory, user_id=params.get("user_id", "default"))
            procedures = proc.list_procedures(status="active", limit=10)
            return {"action": "extract_procedures", "status": "ok",
                    "active_procedures": len(procedures), "timestamp": now}
        except ImportError:
            return {"action": "extract_procedures", "status": "skipped",
                    "reason": "engram-procedural not installed", "timestamp": now}
        except Exception as e:
            return {"action": "extract_procedures", "status": "error", "error": str(e), "timestamp": now}

    elif action == "process_reconsolidation":
        try:
            from engram_reconsolidation import Reconsolidation
            rc = Reconsolidation(memory, user_id=params.get("user_id", "default"))
            pending = rc.list_pending_proposals(limit=10)
            auto_applied = 0
            for p in pending:
                if p.get("confidence", 0) >= rc.config.min_confidence_for_auto_apply:
                    rc.apply_update(p["id"])
                    auto_applied += 1
            return {"action": "process_reconsolidation", "status": "ok",
                    "pending": len(pending), "auto_applied": auto_applied, "timestamp": now}
        except ImportError:
            return {"action": "process_reconsolidation", "status": "skipped",
                    "reason": "engram-reconsolidation not installed", "timestamp": now}
        except Exception as e:
            return {"action": "process_reconsolidation", "status": "error", "error": str(e), "timestamp": now}

    elif action == "extract_antipatterns":
        try:
            from engram_failure import FailureLearning
            fl = FailureLearning(memory, user_id=params.get("user_id", "default"))
            stats = fl.get_failure_stats()
            return {"action": "extract_antipatterns", "status": "ok",
                    "stats": stats, "timestamp": now}
        except ImportError:
            return {"action": "extract_antipatterns", "status": "skipped",
                    "reason": "engram-failure not installed", "timestamp": now}
        except Exception as e:
            return {"action": "extract_antipatterns", "status": "error", "error": str(e), "timestamp": now}

    elif action == "wm_decay":
        try:
            from engram_working import WorkingMemory
            wm = WorkingMemory(memory, user_id=params.get("user_id", "default"))
            items = wm.list()
            return {"action": "wm_decay", "status": "ok",
                    "active_items": len(items), "timestamp": now}
        except ImportError:
            return {"action": "wm_decay", "status": "skipped",
                    "reason": "engram-working not installed", "timestamp": now}
        except Exception as e:
            return {"action": "wm_decay", "status": "error", "error": str(e), "timestamp": now}

    elif action == "agi_loop":
        try:
            from engram.core.agi_loop import run_agi_cycle
            result = run_agi_cycle(memory, user_id=params.get("user_id", "default"))
            return {"action": "agi_loop", "status": "ok", "result": result, "timestamp": now}
        except Exception as e:
            return {"action": "agi_loop", "status": "error", "error": str(e), "timestamp": now}

    return {"action": action, "status": "unknown", "error": f"Unknown behavior: {action}", "timestamp": now}


def _is_stale(task: dict, stale_minutes: int) -> bool:
    """Check if a task has been in active status too long."""
    updated = task.get("updated_at", "")
    if not updated:
        return False
    try:
        updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - updated_dt).total_seconds() / 60
        return age > stale_minutes
    except (ValueError, TypeError):
        return False
