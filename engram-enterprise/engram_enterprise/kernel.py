"""Personal Memory Kernel (PMK) orchestrator for Engram v2."""

from __future__ import annotations

import hashlib
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from engram_enterprise.invariants import InvariantEngine
from engram_enterprise.policy import (
    CONFIDENTIALITY_SCOPES,
    DEFAULT_CAPABILITIES,
    HANDOFF_CAPABILITIES,
    default_allowed_scopes,
    detect_confidentiality_scope,
    enforce_scope_on_results,
    feature_enabled,
    normalize_confidentiality_scope,
    token_required_for_agent,
)
from engram_enterprise.provenance import build_provenance
from engram_enterprise.refcounts import RefCountManager
from engram_enterprise.episodic_store import EpisodicStore
from engram_enterprise.staging_store import StagingStore
from engram.observability import metrics
from engram_enterprise.dual_search import DualSearchEngine


class PersonalMemoryKernel:
    """Coordinates policy, retrieval, and staged writes for v2."""

    def __init__(self, memory):
        self.memory = memory
        self.db = memory.db
        self.staging_store = StagingStore(self.db)
        self.invariant_engine = InvariantEngine(self.db)
        self.ref_manager = RefCountManager(self.db)
        self.episodic_store = EpisodicStore(self.db, memory.embedder)
        self.dual_search = DualSearchEngine(
            memory=memory,
            episodic_store=self.episodic_store,
            ref_manager=self.ref_manager,
        )

    # ------------------------------------------------------------------
    # Sessions / auth
    # ------------------------------------------------------------------

    def create_session(
        self,
        *,
        user_id: str,
        agent_id: Optional[str],
        allowed_confidentiality_scopes: Optional[List[str]] = None,
        capabilities: Optional[List[str]] = None,
        namespaces: Optional[List[str]] = None,
        ttl_minutes: int = 24 * 60,
    ) -> Dict[str, Any]:
        scopes = allowed_confidentiality_scopes or ["work"]
        normalized_scopes = sorted(
            {
                normalize_confidentiality_scope(scope)
                for scope in scopes
                if normalize_confidentiality_scope(scope) in CONFIDENTIALITY_SCOPES
            }
        )
        if not normalized_scopes:
            normalized_scopes = ["work"]

        normalized_capabilities = sorted(
            {
                str(cap).strip().lower()
                for cap in (capabilities or DEFAULT_CAPABILITIES)
                if str(cap).strip()
            }
        )
        if not normalized_capabilities:
            normalized_capabilities = list(DEFAULT_CAPABILITIES)

        normalized_namespaces = self._resolve_session_namespaces(
            user_id=user_id,
            agent_id=agent_id,
            namespaces=namespaces,
        )
        policy = None
        if agent_id:
            policy = self.db.get_agent_policy(
                user_id=user_id,
                agent_id=agent_id,
                include_wildcard=True,
            )
            require_policy = feature_enabled("ENGRAM_V2_REQUIRE_AGENT_POLICY", default=False)
            if require_policy and not policy:
                raise PermissionError(f"No agent policy configured for user={user_id} agent={agent_id}")

        requested_caps = set(normalized_capabilities)
        handoff_caps = set(HANDOFF_CAPABILITIES)
        if requested_caps & handoff_caps:
            if agent_id and not policy:
                policy = self._bootstrap_handoff_policy_if_trusted(
                    user_id=user_id,
                    agent_id=agent_id,
                    namespaces=normalized_namespaces,
                )
            if not policy:
                raise PermissionError(
                    f"Handoff capabilities require explicit agent policy for user={user_id} agent={agent_id}"
                )

        if policy:
            normalized_scopes = self._clamp_scopes_with_policy(
                requested_scopes=normalized_scopes,
                policy_scopes=policy.get("allowed_confidentiality_scopes", []),
                user_id=user_id,
                agent_id=agent_id,
            )
            normalized_capabilities = self._clamp_capabilities_with_policy(
                requested_capabilities=normalized_capabilities,
                policy_capabilities=policy.get("allowed_capabilities", []),
                user_id=user_id,
                agent_id=agent_id,
            )
            normalized_namespaces = self._clamp_namespaces_with_policy(
                requested_namespaces=normalized_namespaces,
                policy_namespaces=policy.get("allowed_namespaces", []),
                user_id=user_id,
                agent_id=agent_id,
            )
        for namespace in normalized_namespaces:
            if namespace == "*":
                continue
            self.db.ensure_namespace(user_id=user_id, name=namespace)

        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=max(1, ttl_minutes))).isoformat()

        session_id = self.db.create_session(
            {
                "token_hash": token_hash,
                "user_id": user_id,
                "agent_id": agent_id,
                "allowed_confidentiality_scopes": normalized_scopes,
                "capabilities": normalized_capabilities,
                "namespaces": normalized_namespaces,
                "expires_at": expires_at,
            }
        )
        return {
            "session_id": session_id,
            "token": token,
            "expires_at": expires_at,
            "allowed_confidentiality_scopes": normalized_scopes,
            "capabilities": normalized_capabilities,
            "namespaces": normalized_namespaces,
        }

    def authenticate_session(
        self,
        *,
        token: Optional[str],
        user_id: Optional[str],
        agent_id: Optional[str],
        require_for_agent: bool = True,
        required_capabilities: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not token:
            if require_for_agent and token_required_for_agent(agent_id):
                raise PermissionError("Capability token required for agent access")
            return None

        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        session = self.db.get_session_by_token_hash(token_hash)
        if not session:
            raise PermissionError("Invalid capability token")

        revoked_at = session.get("revoked_at")
        if revoked_at:
            raise PermissionError("Session has been revoked")

        expires_at = session.get("expires_at")
        if expires_at:
            exp_dt = datetime.fromisoformat(expires_at)
            if datetime.now(timezone.utc) > exp_dt:
                raise PermissionError("Session expired")

        if user_id and session.get("user_id") not in {None, user_id}:
            raise PermissionError("Session user scope mismatch")

        if agent_id and session.get("agent_id") and session.get("agent_id") != agent_id:
            raise PermissionError("Session agent scope mismatch")

        required_caps = [str(cap).strip().lower() for cap in (required_capabilities or []) if str(cap).strip()]
        if required_caps:
            session_caps = {str(cap).strip().lower() for cap in (session.get("capabilities") or []) if str(cap).strip()}
            if "*" not in session_caps:
                missing = [cap for cap in required_caps if cap not in session_caps]
                if missing:
                    missing_str = ", ".join(sorted(set(missing)))
                    raise PermissionError(f"Session missing required capability: {missing_str}")

        return session

    @staticmethod
    def _normalize_namespace(value: Optional[str]) -> str:
        ns = str(value or "default").strip()
        return ns or "default"

    def _resolve_session_namespaces(
        self,
        *,
        user_id: str,
        agent_id: Optional[str],
        namespaces: Optional[List[str]],
    ) -> List[str]:
        if namespaces:
            resolved = sorted({self._normalize_namespace(ns) for ns in namespaces if str(ns).strip()})
        elif agent_id:
            resolved = self.db.get_agent_allowed_namespaces(user_id=user_id, agent_id=agent_id, capability="read")
        else:
            resolved = ["default"]
        if not resolved:
            resolved = ["default"]
        return resolved

    def _bootstrap_handoff_policy_if_trusted(
        self,
        *,
        user_id: str,
        agent_id: str,
        namespaces: Optional[List[str]],
    ) -> Optional[Dict[str, Any]]:
        handoff_cfg = getattr(self.memory, "handoff_config", None)
        if not bool(getattr(handoff_cfg, "allow_auto_trusted_bootstrap", False)):
            return None
        trusted_agents = {
            str(value).strip().lower()
            for value in getattr(handoff_cfg, "auto_trusted_agents", [])
            if str(value).strip()
        }
        if str(agent_id).strip().lower() not in trusted_agents:
            return None

        allowed_namespaces = ["default"]
        for namespace in namespaces or []:
            ns_value = self._normalize_namespace(namespace)
            if ns_value not in allowed_namespaces:
                allowed_namespaces.append(ns_value)

        allowed_capabilities = sorted(set(list(DEFAULT_CAPABILITIES) + list(HANDOFF_CAPABILITIES)))
        self.db.upsert_agent_policy(
            user_id=user_id,
            agent_id=agent_id,
            allowed_confidentiality_scopes=list(CONFIDENTIALITY_SCOPES),
            allowed_capabilities=allowed_capabilities,
            allowed_namespaces=allowed_namespaces,
        )
        return self.db.get_agent_policy(
            user_id=user_id,
            agent_id=agent_id,
            include_wildcard=True,
        )

    @staticmethod
    def _normalize_policy_namespaces(namespaces: Optional[List[str]]) -> List[str]:
        values = sorted({str(namespace).strip() for namespace in (namespaces or []) if str(namespace).strip()})
        return values

    @staticmethod
    def _normalize_policy_capabilities(capabilities: Optional[List[str]]) -> List[str]:
        values = sorted(
            {
                str(capability).strip().lower()
                for capability in (capabilities or [])
                if str(capability).strip()
            }
        )
        return values

    @staticmethod
    def _normalize_policy_scopes(scopes: Optional[List[str]]) -> List[str]:
        values = sorted(
            {
                normalize_confidentiality_scope(scope)
                for scope in (scopes or [])
                if normalize_confidentiality_scope(scope) in CONFIDENTIALITY_SCOPES
            }
        )
        return values

    @staticmethod
    def _clamp_with_policy(
        *,
        requested: List[str],
        allowed: List[str],
        label: str,
        user_id: str,
        agent_id: Optional[str],
    ) -> List[str]:
        """Generic clamping: intersect requested with allowed, respecting wildcards."""
        allowed_set = set(allowed)
        if "*" in allowed_set:
            return sorted(set(requested))
        if not allowed_set:
            raise PermissionError(f"Agent policy denies {label} for user={user_id} agent={agent_id}")
        clamped = [item for item in requested if item in allowed_set]
        if not clamped:
            raise PermissionError(
                f"Requested {label} are not allowed by policy for user={user_id} agent={agent_id}"
            )
        return sorted(set(clamped))

    def _clamp_scopes_with_policy(
        self,
        *,
        requested_scopes: List[str],
        policy_scopes: Optional[List[str]],
        user_id: str,
        agent_id: Optional[str],
    ) -> List[str]:
        # Check raw policy for wildcard before normalization strips it.
        if "*" in {str(s).strip() for s in (policy_scopes or [])}:
            return requested_scopes
        return self._clamp_with_policy(
            requested=requested_scopes,
            allowed=self._normalize_policy_scopes(policy_scopes),
            label="confidentiality scopes",
            user_id=user_id,
            agent_id=agent_id,
        )

    def _clamp_capabilities_with_policy(
        self,
        *,
        requested_capabilities: List[str],
        policy_capabilities: Optional[List[str]],
        user_id: str,
        agent_id: Optional[str],
    ) -> List[str]:
        return self._clamp_with_policy(
            requested=requested_capabilities,
            allowed=self._normalize_policy_capabilities(policy_capabilities),
            label="capabilities",
            user_id=user_id,
            agent_id=agent_id,
        )

    def _clamp_namespaces_with_policy(
        self,
        *,
        requested_namespaces: List[str],
        policy_namespaces: Optional[List[str]],
        user_id: str,
        agent_id: Optional[str],
    ) -> List[str]:
        return self._clamp_with_policy(
            requested=requested_namespaces,
            allowed=self._normalize_policy_namespaces(policy_namespaces),
            label="namespaces",
            user_id=user_id,
            agent_id=agent_id,
        )

    def _resolve_allowed_namespaces(
        self,
        *,
        session: Optional[Dict[str, Any]],
        user_id: str,
        agent_id: Optional[str],
        capability: str,
    ) -> List[str]:
        if not feature_enabled("ENGRAM_V2_POLICY_GATEWAY", default=True):
            return ["*"]
        if session and session.get("namespaces"):
            return sorted({self._normalize_namespace(ns) for ns in session.get("namespaces", [])})
        if agent_id:
            return self.db.get_agent_allowed_namespaces(user_id=user_id, agent_id=agent_id, capability=capability)
        return ["*"]

    @staticmethod
    def _is_namespace_allowed(namespace: str, allowed_namespaces: List[str]) -> bool:
        return "*" in allowed_namespaces or namespace in set(allowed_namespaces)

    def _mask_for_namespace(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": item.get("id"),
            "type": "private_event",
            "time": item.get("created_at") or item.get("timestamp") or item.get("start_time"),
            "importance": item.get("importance", item.get("scene_strength", 0.5)),
            "details": "[REDACTED]",
            "masked": True,
        }

    def _enforce_namespaces_on_results(
        self,
        items: List[Dict[str, Any]],
        allowed_namespaces: List[str],
    ) -> List[Dict[str, Any]]:
        if "*" in allowed_namespaces:
            # Fast path: no masking needed, avoid copying dicts.
            for item in items:
                item["masked"] = False
            return items
        allowed_set = set(allowed_namespaces)
        filtered: List[Dict[str, Any]] = []
        for item in items:
            namespace = self._normalize_namespace(item.get("namespace"))
            if namespace in allowed_set or "*" in allowed_set:
                item["masked"] = bool(item.get("masked", False))
                filtered.append(item)
            else:
                filtered.append(self._mask_for_namespace(item))
        return filtered

    @staticmethod
    def _parse_float_env(name: str, default: float) -> float:
        try:
            return float(os.environ.get(name, default))
        except Exception:
            return float(default)

    @staticmethod
    def _parse_int_env(name: str, default: int) -> int:
        try:
            return int(os.environ.get(name, default))
        except Exception:
            return int(default)

    def _passes_auto_merge_guardrails(self, trust_row: Dict[str, Any]) -> bool:
        total = int(trust_row.get("total_proposals", 0) or 0)
        approved = int(trust_row.get("approved_proposals", 0) or 0)
        rejected = int(trust_row.get("rejected_proposals", 0) or 0)

        min_total = self._parse_int_env("ENGRAM_V2_AUTO_MERGE_MIN_TOTAL", 10)
        min_approved = self._parse_int_env("ENGRAM_V2_AUTO_MERGE_MIN_APPROVED", 7)
        max_reject_rate = self._parse_float_env("ENGRAM_V2_AUTO_MERGE_MAX_REJECT_RATE", 0.2)

        if total < max(1, min_total):
            return False
        if approved < max(1, min_approved):
            return False

        rejection_rate = (rejected / total) if total > 0 else 1.0
        return rejection_rate <= max(0.0, max_reject_rate)

    def _enforce_write_quotas(
        self,
        *,
        user_id: str,
        agent_id: Optional[str],
    ) -> None:
        if not feature_enabled("ENGRAM_V2_POLICY_GATEWAY", default=True):
            return

        now = datetime.now(timezone.utc)
        windows: List[Dict[str, Any]] = [
            {
                "env": "ENGRAM_V2_POLICY_WRITE_QUOTA_PER_USER_PER_HOUR",
                "label": "per-user hourly",
                "user_id": user_id,
                "agent_id": None,
                "since": (now - timedelta(hours=1)).isoformat(),
            },
            {
                "env": "ENGRAM_V2_POLICY_WRITE_QUOTA_PER_USER_PER_DAY",
                "label": "per-user daily",
                "user_id": user_id,
                "agent_id": None,
                "since": (now - timedelta(days=1)).isoformat(),
            },
        ]
        if agent_id:
            windows.extend(
                [
                    {
                        "env": "ENGRAM_V2_POLICY_WRITE_QUOTA_PER_AGENT_PER_HOUR",
                        "label": "per-agent hourly",
                        "user_id": user_id,
                        "agent_id": agent_id,
                        "since": (now - timedelta(hours=1)).isoformat(),
                    },
                    {
                        "env": "ENGRAM_V2_POLICY_WRITE_QUOTA_PER_AGENT_PER_DAY",
                        "label": "per-agent daily",
                        "user_id": user_id,
                        "agent_id": agent_id,
                        "since": (now - timedelta(days=1)).isoformat(),
                    },
                ]
            )

        for window in windows:
            limit = self._parse_int_env(window["env"], 0)
            if limit <= 0:
                continue

            count = self.db.count_proposal_commits(
                user_id=window["user_id"],
                agent_id=window["agent_id"],
                since=window["since"],
            )
            if count >= limit:
                raise PermissionError(
                    f"Write quota exceeded ({window['label']}): "
                    f"{count}/{limit} proposals in active window"
                )

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def search(
        self,
        *,
        query: str,
        user_id: str,
        agent_id: Optional[str],
        token: Optional[str],
        limit: int = 10,
        categories: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        session = self.authenticate_session(
            token=token,
            user_id=user_id,
            agent_id=agent_id,
            require_for_agent=True,
            required_capabilities=["search"] if (token or agent_id) else None,
        )

        allowed_scopes = default_allowed_scopes()
        if session:
            allowed_scopes = session.get("allowed_confidentiality_scopes")
        allowed_namespaces = self._resolve_allowed_namespaces(
            session=session,
            user_id=user_id,
            agent_id=agent_id,
            capability="read",
        )

        use_dual = feature_enabled("ENGRAM_V2_DUAL_RETRIEVAL", default=True)
        if use_dual:
            result = self.dual_search.search(
                query=query,
                user_id=user_id,
                agent_id=agent_id,
                limit=limit,
                categories=categories,
                allowed_confidentiality_scopes=allowed_scopes,
                allowed_namespaces=allowed_namespaces,
            )
        else:
            fallback = self.memory.search(
                query=query,
                user_id=user_id,
                agent_id=agent_id,
                limit=limit,
                categories=categories,
            )
            fallback_results = fallback.get("results", fallback)
            masked_results = enforce_scope_on_results(fallback_results, allowed_scopes)
            namespaced_results = self._enforce_namespaces_on_results(masked_results, allowed_namespaces)
            final_results = namespaced_results[:limit]
            masked_count = sum(1 for item in final_results if item.get("masked"))
            result = {
                "results": final_results,
                "count": len(final_results),
                "context_packet": {
                    "query": query,
                    "snippets": [],
                    "token_usage": {"estimated_tokens": 0, "budget": 0},
                    "masking": {"masked_count": masked_count, "total_candidates": len(fallback_results)},
                },
                "scene_hits": [],
            }
        return result

    def search_scenes(
        self,
        *,
        query: str,
        user_id: str,
        agent_id: Optional[str],
        token: Optional[str],
        limit: int = 10,
    ) -> Dict[str, Any]:
        session = self.authenticate_session(
            token=token,
            user_id=user_id,
            agent_id=agent_id,
            require_for_agent=True,
            required_capabilities=["read_scene"] if (token or agent_id) else None,
        )
        allowed_scopes = session.get("allowed_confidentiality_scopes") if session else default_allowed_scopes()
        allowed_namespaces = self._resolve_allowed_namespaces(
            session=session,
            user_id=user_id,
            agent_id=agent_id,
            capability="read",
        )
        scenes = self.episodic_store.search_scenes(user_id=user_id, query=query, limit=limit)
        # Scene masking is coarse: mask summaries if no permitted scope.
        masked_scenes = []
        for scene in scenes:
            scene_namespace = self._normalize_namespace(scene.get("namespace"))
            if not self._is_namespace_allowed(scene_namespace, allowed_namespaces):
                masked_scenes.append(self._mask_for_namespace(scene))
                continue
            if not feature_enabled("ENGRAM_V2_POLICY_GATEWAY", default=True):
                visible_scene = dict(scene)
                visible_scene["masked"] = False
                masked_scenes.append(visible_scene)
                continue
            scope = normalize_confidentiality_scope(scene.get("confidentiality_scope") or "work")
            if scope in set(allowed_scopes or []):
                visible_scene = dict(scene)
                visible_scene["masked"] = False
                masked_scenes.append(visible_scene)
            else:
                masked_scenes.append(
                    {
                        "id": scene.get("id"),
                        "type": "private_event",
                        "time": scene.get("start_time"),
                        "importance": scene.get("scene_strength", 0.5),
                        "details": "[REDACTED]",
                        "masked": True,
                    }
                )
        return {"scenes": masked_scenes, "count": len(masked_scenes)}

    def get_scene(
        self,
        *,
        scene_id: str,
        user_id: str,
        agent_id: Optional[str],
        token: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        session = self.authenticate_session(
            token=token,
            user_id=user_id,
            agent_id=agent_id,
            require_for_agent=True,
            required_capabilities=["read_scene"] if (token or agent_id) else None,
        )

        scene = self.memory.get_scene(scene_id)
        if not scene:
            return None
        scene = dict(scene)
        scene.pop("embedding", None)

        if not feature_enabled("ENGRAM_V2_POLICY_GATEWAY", default=True):
            scene["masked"] = False
            return scene

        allowed_scopes = session.get("allowed_confidentiality_scopes") if session else default_allowed_scopes()
        allowed_namespaces = self._resolve_allowed_namespaces(
            session=session,
            user_id=user_id,
            agent_id=agent_id,
            capability="read",
        )
        scene_namespace = self._normalize_namespace(scene.get("namespace"))
        if not self._is_namespace_allowed(scene_namespace, allowed_namespaces):
            return self._mask_for_namespace(scene)
        scope = normalize_confidentiality_scope(scene.get("confidentiality_scope") or "work")
        if scope in set(allowed_scopes or []):
            scene["masked"] = False
            return scene
        return {
            "id": scene.get("id"),
            "type": "private_event",
            "time": scene.get("start_time"),
            "importance": scene.get("scene_strength", 0.5),
            "details": "[REDACTED]",
            "masked": True,
        }

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def propose_write(
        self,
        *,
        content: str,
        user_id: str,
        agent_id: Optional[str],
        token: Optional[str],
        categories: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        scope: str = "work",
        namespace: Optional[str] = None,
        mode: str = "staging",
        infer: bool = False,
        source_app: Optional[str] = None,
        trusted_direct: bool = False,
        source_type: str = "mcp",
        source_event_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        metadata = dict(metadata or {})
        confidentiality_scope = detect_confidentiality_scope(
            categories=categories,
            metadata=metadata,
            content=content,
            explicit_scope=scope,
        )
        metadata["confidentiality_scope"] = confidentiality_scope
        namespace_value = self._normalize_namespace(namespace or metadata.get("namespace"))
        metadata["namespace"] = namespace_value

        require_for_agent = (mode != "direct" or not trusted_direct)
        session = self.authenticate_session(
            token=token,
            user_id=user_id,
            agent_id=agent_id,
            require_for_agent=require_for_agent,
            required_capabilities=["propose_write"] if (token or require_for_agent) else None,
        )
        if feature_enabled("ENGRAM_V2_POLICY_GATEWAY", default=True):
            allowed_write_namespaces = self._resolve_allowed_namespaces(
                session=session,
                user_id=user_id,
                agent_id=agent_id,
                capability="write",
            )
            if not self._is_namespace_allowed(namespace_value, allowed_write_namespaces):
                raise PermissionError(f"Namespace access denied: {namespace_value}")
            self._enforce_write_quotas(user_id=user_id, agent_id=agent_id)
        self.db.ensure_namespace(user_id=user_id, name=namespace_value)

        if mode == "staging" and not feature_enabled("ENGRAM_V2_STAGING_WRITES", default=True):
            mode = "direct"

        provenance = build_provenance(
            source_type=source_type,
            source_app=source_app,
            source_event_id=source_event_id,
            agent_id=agent_id,
            tool="propose_write",
        )

        if mode == "direct":
            if not trusted_direct:
                raise PermissionError("Direct mode is allowed only for trusted local clients")
            return self._apply_direct_write(
                content=content,
                user_id=user_id,
                agent_id=agent_id,
                categories=categories,
                metadata=metadata,
                infer=infer,
                provenance=provenance,
                source_app=source_app,
            )

        checks = self.invariant_engine.evaluate_add(user_id=user_id, content=content)
        status = "PENDING"
        if checks.get("conflicts") or checks.get("pii_risk"):
            status = "AUTO_STASHED"

        changes = [
            {
                "op": "ADD",
                "target": "memory_item",
                "target_id": None,
                "patch": {
                    "content": content,
                    "categories": categories or [],
                    "metadata": metadata,
                    "infer": infer,
                    "source_app": source_app,
                    "confidentiality_scope": confidentiality_scope,
                    "namespace": namespace_value,
                },
            }
        ]
        preview = {
            "summary": content[:140],
            "scope": confidentiality_scope,
            "namespace": namespace_value,
            "category_count": len(categories or []),
        }
        commit = self.staging_store.create_commit(
            user_id=user_id,
            agent_id=agent_id,
            scope=confidentiality_scope,
            changes=changes,
            checks=checks,
            preview=preview,
            provenance=provenance,
            status=status,
        )
        self.db.record_agent_proposal(user_id=user_id, agent_id=agent_id, status=status)

        if checks.get("conflicts"):
            for conflict in checks["conflicts"]:
                self.staging_store.add_conflict(
                    user_id=user_id,
                    conflict_key=conflict["key"],
                    existing={"value": conflict["existing"]},
                    proposed={"value": conflict["proposed"], "source": commit["id"]},
                    source_commit_id=commit["id"],
                )

        if status == "AUTO_STASHED":
            self.staging_store.mark_auto_stashed(commit["id"])

        metrics.record_staged_commit(status)

        auto_merged = False
        if (
            status == "PENDING"
            and agent_id
            and feature_enabled("ENGRAM_V2_TRUST_AUTOMERGE", default=True)
            and not checks.get("duplicate_of")
            and not checks.get("conflicts")
            and not checks.get("pii_risk")
        ):
            threshold = self._parse_float_env("ENGRAM_V2_AUTO_MERGE_TRUST_THRESHOLD", 0.85)
            trust = self.db.get_agent_trust(user_id=user_id, agent_id=agent_id)
            if float(trust.get("trust_score", 0.0)) >= threshold and self._passes_auto_merge_guardrails(trust):
                auto_merged = True
                self.approve_commit(commit_id=commit["id"])
                status = "APPROVED"

        return {
            "mode": "staging",
            "commit_id": commit["id"],
            "status": status,
            "checks": checks,
            "preview": preview,
            "auto_merged": auto_merged,
        }

    def _apply_direct_write(
        self,
        *,
        content: str,
        user_id: str,
        agent_id: Optional[str],
        categories: Optional[List[str]],
        metadata: Dict[str, Any],
        infer: bool,
        provenance: Dict[str, Any],
        source_app: Optional[str],
    ) -> Dict[str, Any]:
        metadata = dict(metadata)
        metadata.update(provenance)
        metadata["allow_sensitive"] = True
        namespace_value = self._normalize_namespace(metadata.get("namespace"))
        metadata["namespace"] = namespace_value

        source_event_id = str(provenance.get("source_event_id") or "").strip()
        source_app = provenance.get("source_app") or source_app
        if source_event_id:
            existing = self.db.get_memory_by_source_event(
                user_id=user_id,
                source_event_id=source_event_id,
                namespace=namespace_value,
                source_app=source_app,
            )
            if existing:
                existing_text = str(existing.get("memory") or "").strip()
                proposed_text = str(content or "").strip()
                if existing_text != proposed_text:
                    raise ValueError(
                        f"source_event_id={source_event_id} already exists with different content"
                    )
                return {
                    "mode": "direct",
                    "result": {
                        "results": [{"id": existing.get("id"), "status": "EXISTING"}],
                        "count": 1,
                        "idempotent": True,
                    },
                    "created_ids": [],
                }

        sharing_scope = str(metadata.get("sharing_scope", "global")).lower()
        result = self.memory.add(
            messages=content,
            user_id=user_id,
            agent_id=agent_id,
            categories=categories,
            metadata=metadata,
            scope=sharing_scope,
            infer=infer,
            source_app=source_app,
        )

        # Pre-compute provenance fields to patch onto created memories in one update.
        patch_fields = {
            "confidentiality_scope": metadata.get("confidentiality_scope", "work"),
            "source_type": provenance.get("source_type"),
            "source_app": provenance.get("source_app"),
            "source_event_id": provenance.get("source_event_id"),
            "status": "active",
            "namespace": self._normalize_namespace(metadata.get("namespace")),
        }

        created_ids: List[str] = []
        result_items = result.get("results", [])
        memory_ids = [item.get("id") for item in result_items if item.get("id")]

        # Batch-fetch all created memories in one query instead of N queries.
        created_map = self.memory.db.get_memories_bulk(memory_ids) if memory_ids else {}

        for memory_id in memory_ids:
            created = created_map.get(memory_id)
            if not created:
                continue
            created_ids.append(memory_id)
            self.memory.db.update_memory(memory_id, patch_fields)
            self.episodic_store.ingest_memory_as_view(
                user_id=user_id,
                agent_id=agent_id,
                memory_id=memory_id,
                content=created.get("memory", content),
                metadata=created.get("metadata", {}),
                timestamp=created.get("created_at"),
            )
            self.invariant_engine.upsert_invariants_from_content(
                user_id=user_id,
                content=created.get("memory", content),
                source_memory_id=memory_id,
            )

        return {
            "mode": "direct",
            "result": result,
            "created_ids": created_ids,
        }

    def list_pending_commits(
        self,
        *,
        user_id: Optional[str],
        status: Optional[str] = None,
        limit: int = 100,
        token: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if token or agent_id:
            self.authenticate_session(
                token=token,
                user_id=user_id,
                agent_id=agent_id,
                require_for_agent=bool(agent_id),
                required_capabilities=["review_commits"],
            )
        commits = self.staging_store.list_commits(user_id=user_id, status=status, limit=limit)
        return {"commits": commits, "count": len(commits)}

    def approve_commit(
        self,
        *,
        commit_id: str,
        token: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        start = time.perf_counter()
        commit = self.staging_store.get_commit(commit_id)
        if not commit:
            return {"error": "Commit not found", "commit_id": commit_id}
        if token or agent_id:
            self.authenticate_session(
                token=token,
                user_id=commit.get("user_id"),
                agent_id=agent_id,
                require_for_agent=bool(agent_id),
                required_capabilities=["review_commits"],
            )

        if commit.get("status") == "APPROVED":
            return {"status": "APPROVED", "commit_id": commit_id, "applied": []}
        if commit.get("status") == "REJECTED":
            return {"error": "Commit already rejected", "commit_id": commit_id}

        moved_to_applying = self.db.transition_proposal_commit_status(
            commit_id,
            from_statuses=["PENDING", "AUTO_STASHED"],
            to_status="APPLYING",
        )
        if not moved_to_applying:
            latest = self.staging_store.get_commit(commit_id)
            if latest and latest.get("status") == "APPROVED":
                return {"status": "APPROVED", "commit_id": commit_id, "applied": []}
            status = latest.get("status") if latest else commit.get("status")
            return {"error": f"Commit not approvable from status {status}", "commit_id": commit_id}

        applied: List[Dict[str, Any]] = []
        created_memory_ids: List[str] = []
        try:
            for change in commit.get("changes", []):
                op = str(change.get("op", "ADD")).upper()
                target = change.get("target", "memory_item")
                patch = change.get("patch", {})

                if target == "memory_item" and op == "ADD":
                    outcome = self._apply_direct_write(
                        content=patch.get("content", ""),
                        user_id=commit.get("user_id", "default"),
                        agent_id=commit.get("agent_id"),
                        categories=patch.get("categories", []),
                        metadata=patch.get("metadata", {}),
                        infer=bool(patch.get("infer", False)),
                        provenance=commit.get("provenance", {}),
                        source_app=patch.get("source_app"),
                    )
                    applied.append(outcome)
                    created_ids = outcome.get("created_ids")
                    if created_ids is None:
                        created_ids = [
                            row.get("id")
                            for row in outcome.get("result", {}).get("results", [])
                            if row.get("id")
                        ]
                    for memory_id in created_ids:
                        if memory_id:
                            created_memory_ids.append(memory_id)
                elif target == "memory_item" and op == "UPDATE":
                    memory_id = change.get("target_id")
                    self.memory.update(memory_id, patch)
                    applied.append({"op": "UPDATE", "target_id": memory_id})
                elif target == "memory_item" and op == "DELETE":
                    memory_id = change.get("target_id")
                    self.memory.delete(memory_id)
                    applied.append({"op": "DELETE", "target_id": memory_id})
                else:
                    raise ValueError(f"Unsupported staged change: target={target}, op={op}")
        except Exception as exc:
            rolled_back = 0
            for memory_id in reversed(created_memory_ids):
                try:
                    self.memory.delete(memory_id)
                    rolled_back += 1
                except Exception:
                    continue

            latest = self.staging_store.get_commit(commit_id) or {}
            checks = dict(latest.get("checks", {}))
            checks["apply_error"] = str(exc)
            checks["rollback_deleted"] = rolled_back
            self.db.transition_proposal_commit_status(
                commit_id,
                from_statuses=["APPLYING"],
                to_status="PENDING",
                updates={"checks": checks},
            )
            return {
                "error": "Commit apply failed",
                "commit_id": commit_id,
                "rolled_back": rolled_back,
                "details": str(exc),
            }

        finalized = self.db.transition_proposal_commit_status(
            commit_id,
            from_statuses=["APPLYING"],
            to_status="APPROVED",
        )
        if not finalized:
            return {"error": "Commit approval finalization failed", "commit_id": commit_id}

        self.db.record_agent_commit_outcome(
            user_id=commit.get("user_id", "default"),
            agent_id=commit.get("agent_id"),
            outcome="APPROVED",
        )
        latency_ms = (time.perf_counter() - start) * 1000
        metrics.record_commit_approval(latency_ms)
        return {"status": "APPROVED", "commit_id": commit_id, "applied": applied}

    def reject_commit(
        self,
        *,
        commit_id: str,
        reason: Optional[str] = None,
        token: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        commit = self.staging_store.get_commit(commit_id)
        if not commit:
            return {"error": "Commit not found", "commit_id": commit_id}
        if token or agent_id:
            self.authenticate_session(
                token=token,
                user_id=commit.get("user_id"),
                agent_id=agent_id,
                require_for_agent=bool(agent_id),
                required_capabilities=["review_commits"],
            )
        if commit.get("status") == "REJECTED":
            return {"status": "REJECTED", "commit_id": commit_id, "reason": reason}
        if commit.get("status") == "APPROVED":
            return {"error": "Approved commits cannot be rejected", "commit_id": commit_id}

        checks = dict(commit.get("checks", {}))
        if reason:
            checks["rejection_reason"] = reason
        moved = self.db.transition_proposal_commit_status(
            commit_id,
            from_statuses=["PENDING", "AUTO_STASHED", "APPLYING"],
            to_status="REJECTED",
            updates={"checks": checks},
        )
        if not moved:
            latest = self.staging_store.get_commit(commit_id)
            latest_status = latest.get("status") if latest else commit.get("status")
            return {"error": f"Commit not rejectable from status {latest_status}", "commit_id": commit_id}

        self.db.record_agent_commit_outcome(
            user_id=commit.get("user_id", "default"),
            agent_id=commit.get("agent_id"),
            outcome="REJECTED",
        )
        metrics.record_commit_rejection()
        return {"status": "REJECTED", "commit_id": commit_id, "reason": reason}

    def resolve_conflict(
        self,
        *,
        stash_id: str,
        resolution: str,
        token: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolution = resolution.upper()
        if resolution not in {"UNRESOLVED", "KEEP_EXISTING", "ACCEPT_PROPOSED", "KEEP_BOTH"}:
            return {"error": "Invalid resolution", "stash_id": stash_id}

        stash = self.db.get_conflict_stash(stash_id)
        if not stash:
            return {"error": "Conflict stash not found", "stash_id": stash_id}
        if token or agent_id:
            self.authenticate_session(
                token=token,
                user_id=stash.get("user_id"),
                agent_id=agent_id,
                require_for_agent=bool(agent_id),
                required_capabilities=["resolve_conflicts"],
            )

        self.staging_store.resolve_conflict(stash_id, resolution)
        if resolution == "ACCEPT_PROPOSED":
            proposed = stash.get("proposed", {}) or {}
            value = proposed.get("value")
            key = stash.get("conflict_key")
            if value and key:
                self.db.upsert_invariant(
                    user_id=stash.get("user_id", "default"),
                    invariant_key=key,
                    invariant_value=str(value),
                    category="identity",
                    confidence=0.8,
                    source_memory_id=None,
                )

        updated = self.db.get_conflict_stash(stash_id)
        return {"stash": updated}

    def get_daily_digest(
        self,
        *,
        user_id: str,
        date_str: str,
        token: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if token or agent_id:
            self.authenticate_session(
                token=token,
                user_id=user_id,
                agent_id=agent_id,
                require_for_agent=bool(agent_id),
                required_capabilities=["read_digest"],
            )
        existing = self.db.get_daily_digest(user_id=user_id, digest_date=date_str)
        if existing:
            payload = existing.get("payload", {})
            return {
                "date": date_str,
                "user_id": user_id,
                "top_conflicts": payload.get("top_conflicts", []),
                "top_proposed_consolidations": payload.get("top_proposed_consolidations", []),
                "scene_highlights": payload.get("scene_highlights", []),
            }

        payload = self._build_daily_digest_payload(user_id=user_id, date_str=date_str)
        self.db.upsert_daily_digest(user_id=user_id, digest_date=date_str, payload=payload)
        return {
            "date": date_str,
            "user_id": user_id,
            "top_conflicts": payload["top_conflicts"],
            "top_proposed_consolidations": payload["top_proposed_consolidations"],
            "scene_highlights": payload.get("scene_highlights", []),
        }

    def _build_daily_digest_payload(self, *, user_id: str, date_str: str) -> Dict[str, Any]:
        conflicts = self.db.list_conflict_stash(user_id=user_id, resolution="UNRESOLVED", limit=20)
        pending = self.db.list_proposal_commits(user_id=user_id, status="PENDING", limit=20)
        day_start = f"{date_str}T00:00:00"
        day_end = f"{date_str}T23:59:59.999999"
        scenes = self.db.get_scenes(
            user_id=user_id,
            start_after=day_start,
            start_before=day_end,
            limit=20,
        )
        scene_highlights = [
            {
                "scene_id": scene.get("id"),
                "summary": scene.get("summary"),
                "topic": scene.get("topic"),
                "start_time": scene.get("start_time"),
                "memory_count": len(scene.get("memory_ids", [])),
            }
            for scene in scenes[:10]
        ]
        return {
            "top_conflicts": conflicts[:10],
            "top_proposed_consolidations": pending[:10],
            "scene_highlights": scene_highlights,
        }

    def run_sleep_cycle(
        self,
        *,
        user_id: Optional[str] = None,
        date_str: Optional[str] = None,
        apply_decay: bool = True,
        cleanup_stale_refs: bool = True,
        token: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if token or agent_id:
            self.authenticate_session(
                token=token,
                user_id=user_id,
                agent_id=agent_id,
                require_for_agent=bool(agent_id),
                required_capabilities=["run_sleep_cycle"],
            )
        target_date = date_str or (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        users = [user_id] if user_id else self.db.list_user_ids()
        if not users:
            users = ["default"]

        summary: Dict[str, Any] = {
            "date": target_date,
            "users": {},
            "stale_refs_removed": 0,
        }
        day_start = f"{target_date}T00:00:00"
        day_end = f"{target_date}T23:59:59.999999"

        for uid in users:
            user_stats = {
                "promoted": 0,
                "digests_upserted": 0,
                "scenes_considered": 0,
                "decay": {"decayed": 0, "forgotten": 0, "promoted": 0},
            }
            day_memories = self.db.get_all_memories(
                user_id=uid,
                created_after=day_start,
                created_before=day_end,
            )

            # Ensure CAST views/scenes are available for the day.
            for memory in day_memories:
                if memory.get("scene_id"):
                    continue
                try:
                    self.episodic_store.ingest_memory_as_view(
                        user_id=uid,
                        agent_id=memory.get("agent_id"),
                        memory_id=memory.get("id"),
                        content=memory.get("memory", ""),
                        metadata=memory.get("metadata", {}),
                        timestamp=memory.get("created_at"),
                    )
                except Exception:
                    # Non-fatal: keep sleep cycle robust.
                    continue

            for memory in day_memories:
                if memory.get("layer") == "lml":
                    continue
                importance = float(memory.get("importance", 0.0) or 0.0)
                strength = float(memory.get("strength", 0.0) or 0.0)
                if importance >= 0.8 or strength >= 0.85:
                    if self.db.update_memory(memory["id"], {"layer": "lml"}):
                        user_stats["promoted"] += 1

            payload = self._build_daily_digest_payload(user_id=uid, date_str=target_date)
            self.db.upsert_daily_digest(user_id=uid, digest_date=target_date, payload=payload)
            user_stats["digests_upserted"] += 1
            user_stats["scenes_considered"] = len(
                self.db.get_scenes(
                    user_id=uid,
                    start_after=day_start,
                    start_before=day_end,
                    limit=100,
                )
            )

            if apply_decay:
                user_stats["decay"] = self.memory.apply_decay(scope={"user_id": uid})

            # CLS Distillation: replay distillation + trace cascade during sleep
            distillation_config = getattr(self.memory.config, "distillation", None)
            if distillation_config:
                # Gap 2: Replay distillation
                if distillation_config.enable_distillation:
                    try:
                        from engram.core.distillation import ReplayDistiller
                        distiller = ReplayDistiller(
                            db=self.db,
                            llm=self.memory.llm,
                            config=distillation_config,
                        )
                        user_stats["distillation"] = distiller.run(
                            user_id=uid,
                            date_str=target_date,
                            memory_add_fn=self.memory.add,
                        )
                    except Exception as e:
                        user_stats["distillation"] = {"error": str(e)}

                # Gap 4: Cascade traces (deep sleep)
                if distillation_config.enable_multi_trace:
                    try:
                        from engram.core.traces import cascade_traces, compute_effective_strength
                        traced_memories = self.db.get_all_memories(
                            user_id=uid,
                        )
                        cascade_count = 0
                        for mem in traced_memories:
                            if mem.get("s_fast") is None:
                                continue
                            s_f, s_m, s_s = cascade_traces(
                                s_fast=float(mem.get("s_fast", 0.0)),
                                s_mid=float(mem.get("s_mid", 0.0)),
                                s_slow=float(mem.get("s_slow", 0.0)),
                                config=distillation_config,
                                deep_sleep=True,
                            )
                            eff = compute_effective_strength(s_f, s_m, s_s, distillation_config)
                            self.db.update_multi_trace(mem["id"], s_f, s_m, s_s, eff)
                            cascade_count += 1
                        user_stats["trace_cascades"] = cascade_count
                    except Exception as e:
                        user_stats["trace_cascades"] = {"error": str(e)}

            summary["users"][uid] = user_stats

        if cleanup_stale_refs:
            summary["stale_refs_removed"] = int(self.ref_manager.cleanup_stale_refs())
        return summary

    def get_agent_trust(
        self,
        *,
        user_id: str,
        agent_id: str,
        token: Optional[str] = None,
        requester_agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if token or requester_agent_id:
            self.authenticate_session(
                token=token,
                user_id=user_id,
                agent_id=requester_agent_id,
                require_for_agent=bool(requester_agent_id),
                required_capabilities=["read_trust"],
            )
        return self.db.get_agent_trust(user_id=user_id, agent_id=agent_id)

    def list_namespaces(
        self,
        *,
        user_id: Optional[str] = None,
        token: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if token or agent_id:
            self.authenticate_session(
                token=token,
                user_id=user_id,
                agent_id=agent_id,
                require_for_agent=bool(agent_id),
                required_capabilities=["manage_namespaces"],
            )
        return self.db.list_namespaces(user_id=user_id)

    def declare_namespace(
        self,
        *,
        user_id: str,
        namespace: str,
        description: Optional[str] = None,
        token: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if token or agent_id:
            self.authenticate_session(
                token=token,
                user_id=user_id,
                agent_id=agent_id,
                require_for_agent=bool(agent_id),
                required_capabilities=["manage_namespaces"],
            )
        namespace_id = self.db.ensure_namespace(user_id=user_id, name=namespace, description=description)
        for item in self.db.list_namespaces(user_id=user_id):
            if item.get("id") == namespace_id:
                return item
        return {"id": namespace_id, "user_id": user_id, "name": namespace}

    def grant_namespace_permission(
        self,
        *,
        user_id: str,
        namespace: str,
        agent_id: str,
        capability: str = "read",
        expires_at: Optional[str] = None,
        token: Optional[str] = None,
        requester_agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if token or requester_agent_id:
            self.authenticate_session(
                token=token,
                user_id=user_id,
                agent_id=requester_agent_id,
                require_for_agent=bool(requester_agent_id),
                required_capabilities=["manage_namespaces"],
            )
        permission_id = self.db.grant_namespace_permission(
            user_id=user_id,
            agent_id=agent_id,
            namespace=namespace,
            capability=capability,
            expires_at=expires_at,
        )
        return {
            "permission_id": permission_id,
            "user_id": user_id,
            "namespace": namespace,
            "agent_id": agent_id,
            "capability": capability,
            "expires_at": expires_at,
        }

    def upsert_agent_policy(
        self,
        *,
        user_id: str,
        agent_id: str,
        allowed_confidentiality_scopes: Optional[List[str]] = None,
        allowed_capabilities: Optional[List[str]] = None,
        allowed_namespaces: Optional[List[str]] = None,
        token: Optional[str] = None,
        requester_agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if token or requester_agent_id:
            self.authenticate_session(
                token=token,
                user_id=user_id,
                agent_id=requester_agent_id,
                require_for_agent=bool(requester_agent_id),
                required_capabilities=["manage_namespaces"],
            )
        normalized_scopes = self._normalize_policy_scopes(
            allowed_confidentiality_scopes if allowed_confidentiality_scopes is not None else list(CONFIDENTIALITY_SCOPES)
        )
        normalized_capabilities = self._normalize_policy_capabilities(
            allowed_capabilities if allowed_capabilities is not None else list(DEFAULT_CAPABILITIES)
        )
        normalized_namespaces = self._normalize_policy_namespaces(
            allowed_namespaces if allowed_namespaces is not None else ["default"]
        )
        for namespace in normalized_namespaces:
            if namespace == "*":
                continue
            self.db.ensure_namespace(user_id=user_id, name=namespace)
        return self.db.upsert_agent_policy(
            user_id=user_id,
            agent_id=agent_id,
            allowed_confidentiality_scopes=normalized_scopes,
            allowed_capabilities=normalized_capabilities,
            allowed_namespaces=normalized_namespaces,
        )

    def get_agent_policy(
        self,
        *,
        user_id: str,
        agent_id: str,
        include_wildcard: bool = True,
        token: Optional[str] = None,
        requester_agent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if token or requester_agent_id:
            self.authenticate_session(
                token=token,
                user_id=user_id,
                agent_id=requester_agent_id,
                require_for_agent=bool(requester_agent_id),
                required_capabilities=["manage_namespaces"],
            )
        return self.db.get_agent_policy(user_id=user_id, agent_id=agent_id, include_wildcard=include_wildcard)

    def list_agent_policies(
        self,
        *,
        user_id: Optional[str] = None,
        token: Optional[str] = None,
        requester_agent_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if token or requester_agent_id:
            self.authenticate_session(
                token=token,
                user_id=user_id,
                agent_id=requester_agent_id,
                require_for_agent=bool(requester_agent_id),
                required_capabilities=["manage_namespaces"],
            )
        return self.db.list_agent_policies(user_id=user_id)

    def delete_agent_policy(
        self,
        *,
        user_id: str,
        agent_id: str,
        token: Optional[str] = None,
        requester_agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if token or requester_agent_id:
            self.authenticate_session(
                token=token,
                user_id=user_id,
                agent_id=requester_agent_id,
                require_for_agent=bool(requester_agent_id),
                required_capabilities=["manage_namespaces"],
            )
        deleted = self.db.delete_agent_policy(user_id=user_id, agent_id=agent_id)
        return {"deleted": bool(deleted), "user_id": user_id, "agent_id": agent_id}

    # ------------------------------------------------------------------
    # Handoff session bus methods
    # ------------------------------------------------------------------

    def _require_handoff_processor(self):
        processor = getattr(self.memory, "handoff_processor", None)
        if processor is None:
            raise RuntimeError("Handoff is disabled")
        return processor

    def save_session_digest(
        self,
        *,
        user_id: str,
        agent_id: str,
        digest: Dict[str, Any],
        token: Optional[str] = None,
        requester_agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        caller_agent = requester_agent_id or agent_id
        if token or caller_agent:
            self.authenticate_session(
                token=token,
                user_id=user_id,
                agent_id=caller_agent,
                require_for_agent=bool(caller_agent),
                required_capabilities=["write_handoff"],
            )
        processor = self._require_handoff_processor()
        return processor.save_digest(user_id=user_id, agent_id=agent_id, digest=digest)

    def get_last_session(
        self,
        *,
        user_id: str,
        agent_id: Optional[str] = None,
        repo: Optional[str] = None,
        statuses: Optional[List[str]] = None,
        token: Optional[str] = None,
        requester_agent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        caller_agent = requester_agent_id or agent_id
        if token or caller_agent:
            self.authenticate_session(
                token=token,
                user_id=user_id,
                agent_id=caller_agent,
                require_for_agent=bool(caller_agent),
                required_capabilities=["read_handoff"],
            )
        processor = self._require_handoff_processor()
        return processor.get_last_session(
            user_id=user_id,
            agent_id=agent_id,
            repo=repo,
            statuses=statuses,
        )

    def list_sessions(
        self,
        *,
        user_id: str,
        agent_id: Optional[str] = None,
        repo: Optional[str] = None,
        status: Optional[str] = None,
        statuses: Optional[List[str]] = None,
        limit: int = 20,
        token: Optional[str] = None,
        requester_agent_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        caller_agent = requester_agent_id or agent_id
        if token or caller_agent:
            self.authenticate_session(
                token=token,
                user_id=user_id,
                agent_id=caller_agent,
                require_for_agent=bool(caller_agent),
                required_capabilities=["read_handoff"],
            )
        processor = self._require_handoff_processor()
        return processor.list_sessions(
            user_id=user_id,
            agent_id=agent_id,
            repo=repo,
            status=status,
            statuses=statuses,
            limit=limit,
        )

    def auto_resume_context(
        self,
        *,
        user_id: str,
        agent_id: Optional[str],
        repo_path: Optional[str] = None,
        branch: Optional[str] = None,
        lane_type: str = "general",
        objective: Optional[str] = None,
        agent_role: Optional[str] = None,
        namespace: str = "default",
        statuses: Optional[List[str]] = None,
        auto_create: bool = True,
        token: Optional[str] = None,
        requester_agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        caller_agent = requester_agent_id or agent_id
        if token or caller_agent:
            self.authenticate_session(
                token=token,
                user_id=user_id,
                agent_id=caller_agent,
                require_for_agent=bool(caller_agent),
                required_capabilities=["read_handoff"],
            )
        processor = self._require_handoff_processor()
        return processor.auto_resume_context(
            user_id=user_id,
            agent_id=agent_id,
            repo_path=repo_path,
            branch=branch,
            lane_type=lane_type,
            objective=objective,
            agent_role=agent_role,
            namespace=namespace,
            statuses=statuses,
            auto_create=auto_create,
        )

    def auto_checkpoint(
        self,
        *,
        user_id: str,
        agent_id: str,
        payload: Dict[str, Any],
        event_type: str = "tool_complete",
        repo_path: Optional[str] = None,
        branch: Optional[str] = None,
        lane_id: Optional[str] = None,
        lane_type: str = "general",
        objective: Optional[str] = None,
        agent_role: Optional[str] = None,
        namespace: str = "default",
        confidentiality_scope: str = "work",
        expected_version: Optional[int] = None,
        token: Optional[str] = None,
        requester_agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        caller_agent = requester_agent_id or agent_id
        if token or caller_agent:
            self.authenticate_session(
                token=token,
                user_id=user_id,
                agent_id=caller_agent,
                require_for_agent=bool(caller_agent),
                required_capabilities=["write_handoff"],
            )
        processor = self._require_handoff_processor()
        return processor.auto_checkpoint(
            user_id=user_id,
            agent_id=agent_id,
            payload=payload,
            event_type=event_type,
            repo_path=repo_path,
            branch=branch,
            lane_id=lane_id,
            lane_type=lane_type,
            objective=objective,
            agent_role=agent_role,
            namespace=namespace,
            confidentiality_scope=confidentiality_scope,
            expected_version=expected_version,
        )

    def finalize_lane(
        self,
        *,
        user_id: str,
        agent_id: str,
        lane_id: str,
        status: str = "paused",
        payload: Optional[Dict[str, Any]] = None,
        repo_path: Optional[str] = None,
        branch: Optional[str] = None,
        agent_role: Optional[str] = None,
        namespace: str = "default",
        token: Optional[str] = None,
        requester_agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        caller_agent = requester_agent_id or agent_id
        if token or caller_agent:
            self.authenticate_session(
                token=token,
                user_id=user_id,
                agent_id=caller_agent,
                require_for_agent=bool(caller_agent),
                required_capabilities=["write_handoff"],
            )
        processor = self._require_handoff_processor()
        return processor.finalize_lane(
            user_id=user_id,
            agent_id=agent_id,
            lane_id=lane_id,
            status=status,
            payload=payload,
            repo_path=repo_path,
            branch=branch,
            agent_role=agent_role,
            namespace=namespace,
        )

    def list_handoff_lanes(
        self,
        *,
        user_id: str,
        repo_path: Optional[str] = None,
        status: Optional[str] = None,
        statuses: Optional[List[str]] = None,
        limit: int = 20,
        token: Optional[str] = None,
        requester_agent_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        caller_agent = requester_agent_id
        if token or caller_agent:
            self.authenticate_session(
                token=token,
                user_id=user_id,
                agent_id=caller_agent,
                require_for_agent=bool(caller_agent),
                required_capabilities=["read_handoff"],
            )
        processor = self._require_handoff_processor()
        return processor.list_lanes(
            user_id=user_id,
            repo_path=repo_path,
            status=status,
            statuses=statuses,
            limit=limit,
        )
