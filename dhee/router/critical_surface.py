"""Critical-surface routing heuristics for Dhee router v1.

This layer decides whether information should stay inside the memory
substrate, become durable memory, or pass through raw. It is an
analytics/control layer only: alignment and fit affect routing
efficiency, not truth. Truth still comes from typed memory, provenance,
verification, and supersede chains.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

CHARS_PER_TOKEN = 3.5

ROUTE_REFLECT = "reflect"
ROUTE_REFRACT_MEMORY = "refract_memory"
ROUTE_ABSORB_EPISODE = "absorb_episode"
ROUTE_TRANSMIT_RAW = "transmit_raw"


def estimate_tokens(value: Any) -> int:
    if isinstance(value, int):
        chars = value
    else:
        chars = len(str(value or ""))
    return max(0, int(chars / CHARS_PER_TOKEN))


def locality_for_path(
    source_path: str,
    *,
    cwd: Optional[str] = None,
) -> Tuple[float, str]:
    """Return (structural_fit, locality_scope) for a source path."""
    raw_source = str(source_path or "").strip()
    raw_cwd = str(cwd or "").strip()
    if not raw_source or not raw_cwd:
        return 0.35, "global"

    try:
        source = Path(raw_source).resolve()
        base = Path(raw_cwd).resolve()
    except OSError:
        return 0.35, "global"

    try:
        rel = source.relative_to(base)
        parts = rel.parts
        if not parts:
            return 1.0, "folder"
        if len(parts) <= 1:
            return 0.95, "folder"
        return 0.8, "workspace"
    except Exception:
        pass

    try:
        common = os.path.commonpath([str(source.parent), str(base)])
    except ValueError:
        common = ""
    if common and common not in {"/", source.anchor, base.anchor}:
        return 0.62, "workspace"
    return 0.35, "global"


def routed_read_decision(
    *,
    source_path: str,
    intent: str,
    depth: str,
    raw_text: str,
    rendered_text: str,
    inlined: bool,
    cwd: Optional[str] = None,
    source_event_id: Optional[str] = None,
) -> Dict[str, Any]:
    structural_fit, locality_scope = locality_for_path(source_path, cwd=cwd)
    raw_tokens = estimate_tokens(raw_text)
    digest_tokens = raw_tokens if inlined else estimate_tokens(rendered_text)
    route = ROUTE_TRANSMIT_RAW if inlined else ROUTE_REFLECT
    return {
        "source_event_id": source_event_id,
        "packet_kind": "routed_read",
        "route": route,
        "depth_score": _depth_score(depth),
        "semantic_fit": _semantic_fit_for_intent(intent),
        "structural_fit": structural_fit,
        "novelty": 0.15,
        "confidence": 1.0,
        "locality_scope": locality_scope,
        "source_path": source_path,
        "token_delta": max(0, raw_tokens - digest_tokens) if route == ROUTE_REFLECT else 0,
        "metadata": {
            "intent": intent,
            "depth": depth,
            "raw_tokens": raw_tokens,
            "digest_tokens": digest_tokens,
            "inlined": bool(inlined),
        },
    }


def artifact_parse_decision(
    *,
    source_path: str,
    created: bool,
    extracted_text: str,
    extraction_source: str,
    cwd: Optional[str] = None,
    source_event_id: Optional[str] = None,
) -> Dict[str, Any]:
    structural_fit, locality_scope = locality_for_path(source_path, cwd=cwd)
    return {
        "source_event_id": source_event_id,
        "packet_kind": "artifact_parse",
        "route": ROUTE_REFRACT_MEMORY if created else ROUTE_REFLECT,
        "depth_score": 0.82,
        "semantic_fit": 0.58 if created else 1.0,
        "structural_fit": structural_fit,
        "novelty": 1.0 if created else 0.0,
        "confidence": 1.0 if extracted_text else 0.0,
        "locality_scope": locality_scope,
        "source_path": source_path,
        "token_delta": 0,
        "metadata": {
            "extraction_source": extraction_source,
            "char_count": len(extracted_text or ""),
            "created": bool(created),
        },
    }


def artifact_reuse_decision(
    *,
    source_path: str,
    total_extracted_chars: int,
    returned_chars: int,
    top_score: float,
    query_terms_count: int,
    cwd: Optional[str] = None,
    source_event_id: Optional[str] = None,
) -> Dict[str, Any]:
    structural_fit, locality_scope = locality_for_path(source_path, cwd=cwd)
    raw_tokens = estimate_tokens(total_extracted_chars)
    returned_tokens = estimate_tokens(returned_chars)
    return {
        "source_event_id": source_event_id,
        "packet_kind": "artifact_reuse",
        "route": ROUTE_REFLECT,
        "depth_score": 0.71,
        "semantic_fit": _normalize_overlap_score(top_score, query_terms_count),
        "structural_fit": structural_fit,
        "novelty": 0.0,
        "confidence": _normalize_overlap_score(top_score, query_terms_count),
        "locality_scope": locality_scope,
        "source_path": source_path,
        "token_delta": max(0, raw_tokens - returned_tokens),
        "metadata": {
            "raw_tokens": raw_tokens,
            "returned_tokens": returned_tokens,
            "query_terms": query_terms_count,
            "top_score": float(top_score),
        },
    }


def routed_bash_decision(
    *,
    command: str,
    cls: str,
    raw_output_bytes: int,
    rendered_text: str,
    inlined: bool,
    cwd: Optional[str] = None,
    source_event_id: Optional[str] = None,
    exit_code: Optional[int] = None,
    timed_out: bool = False,
) -> Dict[str, Any]:
    structural_fit, locality_scope = locality_for_path(cwd or "", cwd=cwd)
    raw_tokens = estimate_tokens(raw_output_bytes)
    digest_tokens = raw_tokens if inlined else estimate_tokens(rendered_text)
    route = ROUTE_TRANSMIT_RAW if inlined else ROUTE_REFLECT
    confidence = 0.55 if timed_out else 0.85
    if exit_code not in (None, 0):
        confidence = min(confidence, 0.7)
    return {
        "source_event_id": source_event_id,
        "packet_kind": "routed_bash",
        "route": route,
        "depth_score": _depth_score_for_bash_class(cls),
        "semantic_fit": _semantic_fit_for_bash_class(cls),
        "structural_fit": structural_fit,
        "novelty": 0.12,
        "confidence": confidence,
        "locality_scope": locality_scope,
        "source_path": cwd or "",
        "token_delta": max(0, raw_tokens - digest_tokens) if route == ROUTE_REFLECT else 0,
        "metadata": {
            "command": command,
            "class": cls,
            "raw_tokens": raw_tokens,
            "digest_tokens": digest_tokens,
            "inlined": bool(inlined),
            "exit_code": exit_code,
            "timed_out": bool(timed_out),
        },
    }


def routed_grep_decision(
    *,
    search_path: str,
    pattern: str,
    match_count: int,
    file_count: int,
    total_bytes: int,
    rendered_text: str,
    inlined: bool,
    cwd: Optional[str] = None,
    source_event_id: Optional[str] = None,
) -> Dict[str, Any]:
    structural_fit, locality_scope = locality_for_path(search_path, cwd=cwd)
    raw_tokens = estimate_tokens(total_bytes)
    digest_tokens = raw_tokens if inlined else estimate_tokens(rendered_text)
    route = ROUTE_TRANSMIT_RAW if inlined else ROUTE_REFLECT
    semantic_fit = 0.9 if match_count > 0 else 0.45
    confidence = 0.92 if match_count > 0 else 0.7
    return {
        "source_event_id": source_event_id,
        "packet_kind": "routed_grep",
        "route": route,
        "depth_score": 0.66,
        "semantic_fit": semantic_fit,
        "structural_fit": structural_fit,
        "novelty": 0.08,
        "confidence": confidence,
        "locality_scope": locality_scope,
        "source_path": search_path,
        "token_delta": max(0, raw_tokens - digest_tokens) if route == ROUTE_REFLECT else 0,
        "metadata": {
            "pattern": pattern,
            "match_count": match_count,
            "file_count": file_count,
            "raw_tokens": raw_tokens,
            "digest_tokens": digest_tokens,
            "inlined": bool(inlined),
        },
    }


def _depth_score(depth: str) -> float:
    return {
        "shallow": 0.35,
        "normal": 0.58,
        "deep": 0.82,
    }.get(str(depth or "normal"), 0.58)


def _semantic_fit_for_intent(intent: str) -> float:
    return {
        "source_code": 0.8,
        "test": 0.78,
        "doc": 0.74,
        "config": 0.72,
        "build": 0.68,
        "data": 0.56,
        "other": 0.5,
    }.get(str(intent or "other"), 0.5)


def _semantic_fit_for_bash_class(cls: str) -> float:
    return {
        "git_log": 0.78,
        "git_diff": 0.82,
        "git_status": 0.84,
        "pytest": 0.88,
        "npm_run": 0.8,
        "listing": 0.68,
        "grep": 0.86,
        "file_dump": 0.62,
        "wc": 0.64,
        "build": 0.72,
        "generic": 0.52,
    }.get(str(cls or "generic"), 0.52)


def _depth_score_for_bash_class(cls: str) -> float:
    return {
        "git_log": 0.55,
        "git_diff": 0.7,
        "git_status": 0.58,
        "pytest": 0.82,
        "npm_run": 0.74,
        "listing": 0.46,
        "grep": 0.66,
        "file_dump": 0.5,
        "wc": 0.4,
        "build": 0.72,
        "generic": 0.45,
    }.get(str(cls or "generic"), 0.45)


def _normalize_overlap_score(score: float, query_terms_count: int) -> float:
    if query_terms_count <= 0:
        return 1.0 if score > 0 else 0.0
    baseline = 0.5 + query_terms_count
    return max(0.0, min(1.0, float(score or 0.0) / baseline))
