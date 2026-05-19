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

CONTEXT_COMPILER_TOOL_NAMES = (
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


TOOL_SPECS: Dict[str, Dict[str, Any]] = {
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
