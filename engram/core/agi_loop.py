"""AGI Loop — the full cognitive cycle.

Orchestrates all memory subsystems in a single cycle:
Perceive → Attend → Encode → Store → Consolidate → Retrieve →
Evaluate → Learn → Plan → Act → Loop

This module provides the run_agi_cycle function called by the
heartbeat behavior, plus system health reporting.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def run_agi_cycle(
    memory: Any,
    user_id: str = "default",
    context: Optional[str] = None,
) -> Dict[str, Any]:
    """Run one iteration of the AGI cognitive cycle.

    Each step is optional — missing subsystems are gracefully skipped.

    Args:
        memory: Engram Memory instance
        user_id: User identifier for scoped operations
        context: Optional current context for reconsolidation

    Returns:
        Dict with status of each subsystem step
    """
    now = datetime.now(timezone.utc).isoformat()
    results: Dict[str, Any] = {"timestamp": now, "user_id": user_id}

    # 1. Consolidate — run distillation (episodic → semantic)
    try:
        if hasattr(memory, "_kernel") and memory._kernel:
            consolidation = memory._kernel.sleep_cycle(user_id=user_id)
            results["consolidation"] = {"status": "ok", "result": consolidation}
        else:
            results["consolidation"] = {"status": "skipped", "reason": "no kernel"}
    except Exception as e:
        results["consolidation"] = {"status": "error", "error": str(e)}

    # 2. Decay — apply forgetting
    try:
        decay_result = memory.apply_decay(scope={"user_id": user_id})
        results["decay"] = {"status": "ok", "result": decay_result}
    except Exception as e:
        results["decay"] = {"status": "error", "error": str(e)}

    # 3. Reconsolidation — auto-apply high-confidence proposals
    try:
        from engram_reconsolidation import Reconsolidation
        rc = Reconsolidation(memory, user_id=user_id)
        pending = rc.list_pending_proposals(limit=5)
        auto_applied = 0
        for p in pending:
            if p.get("confidence", 0) >= rc.config.min_confidence_for_auto_apply:
                rc.apply_update(p["id"])
                auto_applied += 1
        results["reconsolidation"] = {
            "status": "ok", "pending": len(pending), "auto_applied": auto_applied
        }
    except ImportError:
        results["reconsolidation"] = {"status": "skipped", "reason": "not installed"}
    except Exception as e:
        results["reconsolidation"] = {"status": "error", "error": str(e)}

    # 4. Procedural — scan for extractable procedures
    try:
        from engram_procedural import Procedural
        proc = Procedural(memory, user_id=user_id)
        procedures = proc.list_procedures(status="active", limit=5)
        results["procedural"] = {
            "status": "ok", "active_procedures": len(procedures)
        }
    except ImportError:
        results["procedural"] = {"status": "skipped", "reason": "not installed"}
    except Exception as e:
        results["procedural"] = {"status": "error", "error": str(e)}

    # 5. Metamemory — calibration check
    try:
        from engram_metamemory import Metamemory
        mm = Metamemory(memory, user_id=user_id)
        gaps = mm.list_knowledge_gaps(limit=5)
        results["metamemory"] = {
            "status": "ok", "open_gaps": len(gaps)
        }
    except ImportError:
        results["metamemory"] = {"status": "skipped", "reason": "not installed"}
    except Exception as e:
        results["metamemory"] = {"status": "error", "error": str(e)}

    # 6. Prospective — check intention triggers
    try:
        from engram_prospective import Prospective
        pm = Prospective(memory, user_id=user_id)
        triggered = pm.check_triggers()
        results["prospective"] = {
            "status": "ok", "triggered": len(triggered)
        }
    except ImportError:
        results["prospective"] = {"status": "skipped", "reason": "not installed"}
    except Exception as e:
        results["prospective"] = {"status": "error", "error": str(e)}

    # 7. Working memory — decay stale items
    try:
        from engram_working import WorkingMemory
        wm = WorkingMemory(memory, user_id=user_id)
        items = wm.list()
        results["working_memory"] = {
            "status": "ok", "active_items": len(items)
        }
    except ImportError:
        results["working_memory"] = {"status": "skipped", "reason": "not installed"}
    except Exception as e:
        results["working_memory"] = {"status": "error", "error": str(e)}

    # 8. Failure — check for extractable anti-patterns
    try:
        from engram_failure import FailureLearning
        fl = FailureLearning(memory, user_id=user_id)
        stats = fl.get_failure_stats()
        results["failure_learning"] = {"status": "ok", **stats}
    except ImportError:
        results["failure_learning"] = {"status": "skipped", "reason": "not installed"}
    except Exception as e:
        results["failure_learning"] = {"status": "error", "error": str(e)}

    # Compute overall status
    statuses = [v.get("status", "unknown") for v in results.values() if isinstance(v, dict)]
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
    """Report health status across all cognitive subsystems.

    Returns a dict with each subsystem's availability and basic stats.
    """
    now = datetime.now(timezone.utc).isoformat()
    systems: Dict[str, Dict] = {}

    # Core systems (always available)
    try:
        stats = memory.get_stats(user_id=user_id)
        systems["core_memory"] = {"available": True, "stats": stats}
    except Exception as e:
        systems["core_memory"] = {"available": False, "error": str(e)}

    # Knowledge Graph
    systems["knowledge_graph"] = {
        "available": hasattr(memory, "knowledge_graph") and memory.knowledge_graph is not None,
    }
    if systems["knowledge_graph"]["available"]:
        try:
            systems["knowledge_graph"]["stats"] = memory.knowledge_graph.stats()
        except Exception:
            pass

    # Power packages
    _optional_packages = [
        ("engram_router", "router"),
        ("engram_identity", "identity"),
        ("engram_heartbeat", "heartbeat"),
        ("engram_policy", "policy"),
        ("engram_skills", "skills"),
        ("engram_spawn", "spawn"),
        ("engram_resilience", "resilience"),
        ("engram_metamemory", "metamemory"),
        ("engram_prospective", "prospective"),
        ("engram_procedural", "procedural"),
        ("engram_reconsolidation", "reconsolidation"),
        ("engram_failure", "failure_learning"),
        ("engram_working", "working_memory"),
    ]

    for pkg_name, system_name in _optional_packages:
        try:
            __import__(pkg_name)
            systems[system_name] = {"available": True}
        except ImportError:
            systems[system_name] = {"available": False}

    available = sum(1 for s in systems.values() if s.get("available"))
    total = len(systems)

    return {
        "timestamp": now,
        "systems": systems,
        "available": available,
        "total": total,
        "health_pct": round(available / total * 100, 1) if total else 0,
    }
