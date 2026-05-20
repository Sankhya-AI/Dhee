"""Shared MCP tool registry for Dhee compiler/runtime surfaces."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Sequence


TASK_CONTRACT_TOOL_NAMES = (
    "dhee_task_contract_compile",
    "dhee_task_contract_create",
    "dhee_task_contract_list",
    "dhee_task_contract_get",
    "dhee_task_contract_import",
    "dhee_task_contract_interpret",
)

CONTRACT_RUNTIME_TOOL_NAMES = (
    "dhee_contract_supervise_action",
    "dhee_contract_record_observation",
    "dhee_contract_run_verification",
    "dhee_contract_proof_bundle",
    "dhee_contract_runtime_activate",
    "dhee_contract_runtime_status",
    "dhee_contract_runtime_deactivate",
    "dhee_contract_enforcement_set",
    "dhee_contract_enforcement_status",
    "dhee_contract_runtime_doctor",
)

UPDATE_CAPSULE_TOOL_NAMES = (
    "dhee_update_capsule_create",
    "dhee_update_capsule_list",
    "dhee_update_capsule_get",
    "dhee_update_capsule_import",
    "dhee_update_capsule_interpret",
)

REPO_INTELLIGENCE_TOOL_NAMES = (
    "dhee_repo_brain_index",
    "dhee_repo_brain_get",
    "dhee_repo_brain_localize",
    "dhee_repo_graph_export",
    "dhee_context_graph_query",
)

TEMPORAL_FACT_TOOL_NAMES = (
    "dhee_temporal_fact_assert",
    "dhee_temporal_fact_search",
    "dhee_temporal_fact_get",
    "dhee_temporal_fact_invalidate",
    "dhee_temporal_fact_stats",
)

CONTEXT_COMPILER_TOOL_NAMES = (
    *REPO_INTELLIGENCE_TOOL_NAMES,
    *TEMPORAL_FACT_TOOL_NAMES,
    *TASK_CONTRACT_TOOL_NAMES,
    *CONTRACT_RUNTIME_TOOL_NAMES,
    *UPDATE_CAPSULE_TOOL_NAMES,
)


_TASK_COMPILE_PROPERTIES = {
    "goal": {"type": "string"},
    "task": {"type": "string"},
    "query": {"type": "string"},
    "repo": {"type": "string"},
    "mode": {"type": "string"},
    "risk": {"type": "string"},
    "allowed_write_paths": {"type": "array", "items": {"type": "string"}},
    "forbidden_paths": {"type": "array", "items": {"type": "string"}},
    "must_run": {"type": "array", "items": {"type": "string"}},
    "success_criteria": {"type": "array", "items": {"type": "string"}},
    "context_budget": {"type": "object"},
    "memory_pointers": {"type": "array", "items": {"type": "object"}},
    "recent_failures": {"type": "array", "items": {"type": "object"}},
}

_CONTRACT_REF_PROPERTIES = {
    "repo": {"type": "string"},
    "task_id": {"type": "string"},
    "id": {"type": "string"},
    "path": {"type": "string"},
    "contract": {"type": "object"},
}

_CAPSULE_REF_PROPERTIES = {
    "repo": {"type": "string"},
    "capsule_id": {"type": "string"},
    "path": {"type": "string"},
    "capsule": {"type": "object"},
}

_REPO_BRAIN_PROPERTIES = {
    "repo": {"type": "string"},
    "goal": {"type": "string"},
    "query": {"type": "string"},
    "ref": {"type": "string"},
    "relevant_files": {"type": "array", "items": {"type": "string"}},
    "must_run": {"type": "array", "items": {"type": "string"}},
    "file_limit": {"type": "integer"},
    "persist": {"type": "boolean"},
    "include_brain": {"type": "boolean"},
    "include_graph": {"type": "boolean"},
    "quarantine": {"type": "boolean"},
    "limit": {"type": "integer"},
    "node_limit": {"type": "integer"},
    "edge_limit": {"type": "integer"},
}

_TEMPORAL_FACT_PROPERTIES = {
    "db_path": {"type": "string"},
    "user_id": {"type": "string"},
    "namespace": {"type": "string"},
    "fact_id": {"type": "string"},
    "id": {"type": "string"},
    "fact_text": {"type": "string"},
    "query": {"type": "string"},
    "subject": {"type": "string"},
    "predicate": {"type": "string"},
    "object": {"type": "string"},
    "valid_from": {"type": "string"},
    "valid_to": {"type": "string"},
    "observed_at": {"type": "string"},
    "confidence": {"type": "number"},
    "source_scene": {"type": "string"},
    "source_event_ids": {"type": "array", "items": {"type": "string"}},
    "source_memory_ids": {"type": "array", "items": {"type": "string"}},
    "evidence": {"type": "array", "items": {"type": "object"}},
    "privacy_scope": {"type": "string"},
    "metadata": {"type": "object"},
    "contradicts_fact_ids": {"type": "array", "items": {"type": "string"}},
    "invalidate_conflicts": {"type": "boolean"},
    "actor_id": {"type": "string"},
    "reason": {"type": "string"},
    "contradicted_by": {"type": "string"},
    "invalidated_at": {"type": "string"},
    "active_only": {"type": "boolean"},
    "as_of": {"type": "string"},
    "include_invalidated": {"type": "boolean"},
    "include_events": {"type": "boolean"},
    "limit": {"type": "integer"},
}


TOOL_SPECS: Dict[str, Dict[str, Any]] = {
    "dhee_repo_brain_index": {
        "name": "dhee_repo_brain_index",
        "description": "Build and persist a git-SHA scoped SWE repo brain with files, symbols, imports, calls, tests, dependencies, and failure signatures.",
        "inputSchema": {"type": "object", "properties": deepcopy(_REPO_BRAIN_PROPERTIES)},
    },
    "dhee_repo_brain_get": {
        "name": "dhee_repo_brain_get",
        "description": "Load the latest or referenced repo brain. Returns a compact summary unless include_brain=true.",
        "inputSchema": {"type": "object", "properties": deepcopy(_REPO_BRAIN_PROPERTIES)},
    },
    "dhee_repo_brain_localize": {
        "name": "dhee_repo_brain_localize",
        "description": "Run deterministic multi-signal localization against the latest or referenced repo brain.",
        "inputSchema": {"type": "object", "properties": deepcopy(_REPO_BRAIN_PROPERTIES)},
    },
    "dhee_repo_graph_export": {
        "name": "dhee_repo_graph_export",
        "description": "Export the latest repo brain as a durable provenance-rich repo graph artifact: files, symbols, tests, configs, errors, owners, imports, calls, tested_by, failed_with.",
        "inputSchema": {"type": "object", "properties": deepcopy(_REPO_BRAIN_PROPERTIES)},
    },
    "dhee_context_graph_query": {
        "name": "dhee_context_graph_query",
        "description": "Return a rich multi-hop context graph query proving why files, tests, symbols, failures, and ownership evidence matter for a task.",
        "inputSchema": {
            "type": "object",
            "properties": {**deepcopy(_REPO_BRAIN_PROPERTIES), "max_hops": {"type": "integer"}},
            "required": ["query"],
        },
    },
    "dhee_temporal_fact_assert": {
        "name": "dhee_temporal_fact_assert",
        "description": "Assert a temporal fact with validity windows, provenance, confidence, and optional contradiction/invalidation of prior facts.",
        "inputSchema": {
            "type": "object",
            "properties": deepcopy(_TEMPORAL_FACT_PROPERTIES),
            "required": ["fact_text"],
        },
    },
    "dhee_temporal_fact_search": {
        "name": "dhee_temporal_fact_search",
        "description": "Search temporal facts with active-at-time semantics. Invalidated facts can still answer historical as_of queries.",
        "inputSchema": {"type": "object", "properties": deepcopy(_TEMPORAL_FACT_PROPERTIES)},
    },
    "dhee_temporal_fact_get": {
        "name": "dhee_temporal_fact_get",
        "description": "Get a temporal fact by id, optionally including its assertion/invalidation event trail.",
        "inputSchema": {"type": "object", "properties": deepcopy(_TEMPORAL_FACT_PROPERTIES)},
    },
    "dhee_temporal_fact_invalidate": {
        "name": "dhee_temporal_fact_invalidate",
        "description": "Invalidate a temporal fact without deleting it, setting valid_to, contradicted_by, and an audit event.",
        "inputSchema": {
            "type": "object",
            "properties": deepcopy(_TEMPORAL_FACT_PROPERTIES),
            "required": ["fact_id"],
        },
    },
    "dhee_temporal_fact_stats": {
        "name": "dhee_temporal_fact_stats",
        "description": "Return temporal fact ledger counts by status and active flag for a user/namespace.",
        "inputSchema": {"type": "object", "properties": deepcopy(_TEMPORAL_FACT_PROPERTIES)},
    },
    "dhee_task_contract_compile": {
        "name": "dhee_task_contract_compile",
        "description": "Compile a messy user task plus repo state into a deterministic TaskContract and typed ChotuAction plan.",
        "inputSchema": {"type": "object", "properties": deepcopy(_TASK_COMPILE_PROPERTIES)},
    },
    "dhee_task_contract_create": {
        "name": "dhee_task_contract_create",
        "description": "Compile and store a portable TaskContract under .dhee/context/task_contracts.",
        "inputSchema": {
            "type": "object",
            "properties": {"out": {"type": "string"}, **deepcopy(_TASK_COMPILE_PROPERTIES)},
        },
    },
    "dhee_task_contract_list": {
        "name": "dhee_task_contract_list",
        "description": "List portable task contracts in a repo.",
        "inputSchema": {"type": "object", "properties": {"repo": {"type": "string"}}},
    },
    "dhee_task_contract_get": {
        "name": "dhee_task_contract_get",
        "description": "Get one task contract's markdown and machine JSON.",
        "inputSchema": {
            "type": "object",
            "properties": {"repo": {"type": "string"}, "task_id": {"type": "string"}, "id": {"type": "string"}},
        },
    },
    "dhee_task_contract_import": {
        "name": "dhee_task_contract_import",
        "description": "Import a portable task contract into a repo and index it.",
        "inputSchema": {
            "type": "object",
            "properties": {"repo": {"type": "string"}, "path": {"type": "string"}},
            "required": ["path"],
        },
    },
    "dhee_task_contract_interpret": {
        "name": "dhee_task_contract_interpret",
        "description": "Interpret a portable TaskContract on this repo and return executable ChotuAction readiness without running tools.",
        "inputSchema": {
            "type": "object",
            "properties": {**deepcopy(_CONTRACT_REF_PROPERTIES), "strict": {"type": "boolean"}},
        },
    },
    "dhee_contract_supervise_action": {
        "name": "dhee_contract_supervise_action",
        "description": "Runtime gate: allow or deny a proposed ChotuAction against an interpreted task contract.",
        "inputSchema": {
            "type": "object",
            "properties": {
                **deepcopy(_CONTRACT_REF_PROPERTIES),
                "action": {"type": "object"},
                "proposed_action": {"type": "object"},
                "strict": {"type": "boolean"},
            },
        },
    },
    "dhee_contract_record_observation": {
        "name": "dhee_contract_record_observation",
        "description": "Record a compact observation-to-next-action transition for a supervised task contract.",
        "inputSchema": {
            "type": "object",
            "properties": {
                **deepcopy(_CONTRACT_REF_PROPERTIES),
                "action": {"type": "object"},
                "observation": {},
                "outcome": {"type": "string"},
                "next_action": {"type": "object"},
                "strict": {"type": "boolean"},
            },
        },
    },
    "dhee_contract_proof_bundle": {
        "name": "dhee_contract_proof_bundle",
        "description": "Build the proof bundle for a task contract run: action trace, changed files, tests, verifier result, context pointers, memory pointers, skills, and contamination status.",
        "inputSchema": {
            "type": "object",
            "properties": {**deepcopy(_CONTRACT_REF_PROPERTIES), "strict": {"type": "boolean"}, "persist": {"type": "boolean"}},
        },
    },
    "dhee_contract_run_verification": {
        "name": "dhee_contract_run_verification",
        "description": "Execute a task contract verification card with safe bounded commands, record supervised test observations, and persist an auditable verification run.",
        "inputSchema": {
            "type": "object",
            "properties": {
                **deepcopy(_CONTRACT_REF_PROPERTIES),
                "timeout_sec": {"type": "integer"},
                "max_commands": {"type": "integer"},
                "include_pass_to_pass": {"type": "boolean"},
                "include_static": {"type": "boolean"},
                "include_security": {"type": "boolean"},
                "strict": {"type": "boolean"},
                "persist": {"type": "boolean"},
            },
        },
    },
    "dhee_contract_runtime_activate": {
        "name": "dhee_contract_runtime_activate",
        "description": "Bind a TaskContract as the active repo runtime so router/native actions are supervised.",
        "inputSchema": {
            "type": "object",
            "properties": {
                **deepcopy(_CONTRACT_REF_PROPERTIES),
                "strict": {"type": "boolean"},
                "force": {"type": "boolean"},
                "agent_id": {"type": "string"},
                "harness": {"type": "string"},
            },
        },
    },
    "dhee_contract_runtime_status": {
        "name": "dhee_contract_runtime_status",
        "description": "Show the active task-contract runtime bound to a repo, including readiness, enforcement, diagnostics, and event paths.",
        "inputSchema": {"type": "object", "properties": {"repo": {"type": "string"}}},
    },
    "dhee_contract_runtime_deactivate": {
        "name": "dhee_contract_runtime_deactivate",
        "description": "Deactivate the active task-contract runtime for a repo without deleting observation history.",
        "inputSchema": {
            "type": "object",
            "properties": {"repo": {"type": "string"}, "agent_id": {"type": "string"}, "reason": {"type": "string"}},
        },
    },
    "dhee_contract_enforcement_set": {
        "name": "dhee_contract_enforcement_set",
        "description": "Set repo contract enforcement policy to off, warn, or deny.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "mode": {"type": "string", "enum": ["off", "warn", "deny"]},
                "agent_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["mode"],
        },
    },
    "dhee_contract_enforcement_status": {
        "name": "dhee_contract_enforcement_status",
        "description": "Show the effective repo contract enforcement policy, including env-forced deny and diagnostics.",
        "inputSchema": {"type": "object", "properties": {"repo": {"type": "string"}}},
    },
    "dhee_contract_runtime_doctor": {
        "name": "dhee_contract_runtime_doctor",
        "description": "Doctor the contract runtime and report protected, partially_protected, or unprotected with bypass risks.",
        "inputSchema": {"type": "object", "properties": {"repo": {"type": "string"}}},
    },
    "dhee_update_capsule_create": {
        "name": "dhee_update_capsule_create",
        "description": "Create a sanitized repo-shareable UpdateCapsule under .dhee/context/capsules and index it as kind=update_capsule.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "since": {"type": "string"},
                "task_id": {"type": "string"},
                "out": {"type": "string"},
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "commands": {"type": "array", "items": {"type": "string"}},
                "evidence": {"type": "array", "items": {"type": "object"}},
            },
        },
    },
    "dhee_update_capsule_list": {
        "name": "dhee_update_capsule_list",
        "description": "List update capsules in a repo.",
        "inputSchema": {"type": "object", "properties": {"repo": {"type": "string"}}},
    },
    "dhee_update_capsule_get": {
        "name": "dhee_update_capsule_get",
        "description": "Get one update capsule's markdown and machine JSON.",
        "inputSchema": {
            "type": "object",
            "properties": {"repo": {"type": "string"}, "capsule_id": {"type": "string"}},
            "required": ["capsule_id"],
        },
    },
    "dhee_update_capsule_import": {
        "name": "dhee_update_capsule_import",
        "description": "Import a sanitized update capsule into a repo and index it as kind=update_capsule.",
        "inputSchema": {
            "type": "object",
            "properties": {"repo": {"type": "string"}, "path": {"type": "string"}, "allow_private": {"type": "boolean"}},
            "required": ["path"],
        },
    },
    "dhee_update_capsule_interpret": {
        "name": "dhee_update_capsule_interpret",
        "description": "Interpret a compiled update capsule on this repo and return a reproduction plan without auto-applying edits.",
        "inputSchema": {
            "type": "object",
            "properties": {**deepcopy(_CAPSULE_REF_PROPERTIES), "strict": {"type": "boolean"}},
        },
    },
}


def tool_specs(names: Iterable[str] = CONTEXT_COMPILER_TOOL_NAMES) -> List[Dict[str, Any]]:
    return [deepcopy(TOOL_SPECS[name]) for name in names]


def make_tools(tool_cls: Any, names: Sequence[str] = CONTEXT_COMPILER_TOOL_NAMES) -> List[Any]:
    return [tool_cls(**spec) for spec in tool_specs(names)]
