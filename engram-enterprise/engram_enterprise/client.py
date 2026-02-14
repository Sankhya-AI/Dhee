import os
from typing import Any, Dict, List, Optional

from engram_enterprise.policy import DEFAULT_CAPABILITIES


class MemoryClient:
    """Thin HTTP client for remote engram server."""

    def __init__(
        self,
        api_key: str = None,
        host: str = "https://api.engram.ai",
        org_id: str = None,
        project_id: str = None,
        admin_key: str = None,
    ):
        try:
            import requests  # noqa: F401
        except Exception as exc:
            raise ImportError("requests package is required for MemoryClient") from exc

        self.api_key = api_key
        self.host = host.rstrip("/")
        self.org_id = org_id
        self.project_id = project_id
        self.admin_key = admin_key
        self.session_token: Optional[str] = None

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.session_token:
            headers["Authorization"] = f"Bearer {self.session_token}"
        elif self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.org_id:
            headers["X-Org-Id"] = self.org_id
        if self.project_id:
            headers["X-Project-Id"] = self.project_id
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Dict[str, Any] = None,
        json_body: Dict[str, Any] = None,
        extra_headers: Dict[str, Any] = None,
    ):
        import requests

        url = f"{self.host}{path}"
        headers = self._headers()
        if extra_headers:
            headers.update({str(k): str(v) for k, v in extra_headers.items() if v is not None})
        response = requests.request(method, url, headers=headers, params=params, json=json_body, timeout=60)
        response.raise_for_status()
        return response.json()

    def add(self, messages, **kwargs) -> Dict[str, Any]:
        payload = {"messages": messages}
        payload.update(kwargs)
        return self._request("POST", "/v1/memories/", json_body=payload)

    def create_session(
        self,
        *,
        user_id: str,
        agent_id: Optional[str] = None,
        allowed_confidentiality_scopes: Optional[List[str]] = None,
        capabilities: Optional[List[str]] = None,
        namespaces: Optional[List[str]] = None,
        ttl_minutes: int = 24 * 60,
    ) -> Dict[str, Any]:
        payload = {
            "user_id": user_id,
            "agent_id": agent_id,
            "allowed_confidentiality_scopes": allowed_confidentiality_scopes or ["work"],
            "capabilities": capabilities or list(DEFAULT_CAPABILITIES),
            "namespaces": namespaces or ["default"],
            "ttl_minutes": ttl_minutes,
        }
        key = self.admin_key if self.admin_key is not None else os.environ.get("ENGRAM_ADMIN_KEY")
        headers = {"X-Engram-Admin-Key": key} if key else None
        session = self._request("POST", "/v1/sessions", json_body=payload, extra_headers=headers)
        token = session.get("token")
        if token:
            self.session_token = token
        return session

    def propose_write(self, content: str, **kwargs) -> Dict[str, Any]:
        payload = {"content": content}
        payload.update(kwargs)
        payload.setdefault("mode", "staging")
        payload.setdefault("namespace", "default")
        return self._request("POST", "/v1/memories/", json_body=payload)

    def search(self, query: str, **kwargs) -> Dict[str, Any]:
        payload = {"query": query}
        payload.update(kwargs)
        return self._request("POST", "/v1/memories/search/", json_body=payload)

    def search_scenes(self, query: str, **kwargs) -> Dict[str, Any]:
        payload = {"query": query}
        payload.update(kwargs)
        return self._request("POST", "/v1/scenes/search", json_body=payload)

    def get(self, memory_id: str, **kwargs) -> Dict[str, Any]:
        return self._request("GET", f"/v1/memories/{memory_id}/", params=kwargs)

    def get_all(self, **kwargs) -> Dict[str, Any]:
        return self._request("GET", "/v1/memories/", params=kwargs)

    def update(self, memory_id: str, data: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if data is not None:
            payload["data"] = data
        if metadata is not None:
            payload["metadata"] = metadata
        payload.update(kwargs)
        return self._request("PUT", f"/v1/memories/{memory_id}/", json_body=payload)

    def delete(self, memory_id: str, **kwargs) -> Dict[str, Any]:
        return self._request("DELETE", f"/v1/memories/{memory_id}/", params=kwargs)

    def delete_all(self, **kwargs) -> Dict[str, Any]:
        return self._request("DELETE", "/v1/memories/", params=kwargs)

    def history(self, memory_id: str, **kwargs) -> List[Dict[str, Any]]:
        return self._request("GET", f"/v1/memories/{memory_id}/history/", params=kwargs)

    def list_pending_commits(self, **kwargs) -> Dict[str, Any]:
        return self._request("GET", "/v1/staging/commits", params=kwargs)

    def approve_commit(self, commit_id: str) -> Dict[str, Any]:
        return self._request("POST", f"/v1/staging/commits/{commit_id}/approve", json_body={})

    def reject_commit(self, commit_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
        return self._request("POST", f"/v1/staging/commits/{commit_id}/reject", json_body={"reason": reason})

    def resolve_conflict(self, stash_id: str, resolution: str) -> Dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/conflicts/{stash_id}/resolve",
            json_body={"resolution": resolution},
        )

    def daily_digest(self, *, user_id: str, date: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"user_id": user_id}
        if date:
            params["date"] = date
        return self._request("GET", "/v1/digest/daily", params=params)

    def run_sleep_cycle(
        self,
        *,
        user_id: Optional[str] = None,
        date: Optional[str] = None,
        apply_decay: bool = True,
        cleanup_stale_refs: bool = True,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "user_id": user_id,
            "date": date,
            "apply_decay": apply_decay,
            "cleanup_stale_refs": cleanup_stale_refs,
        }
        return self._request("POST", "/v1/sleep/run", json_body=payload)

    def handoff_resume(
        self,
        *,
        user_id: str,
        agent_id: Optional[str] = None,
        repo_path: Optional[str] = None,
        branch: Optional[str] = None,
        lane_type: str = "general",
        objective: Optional[str] = None,
        agent_role: Optional[str] = None,
        namespace: str = "default",
        statuses: Optional[List[str]] = None,
        auto_create: bool = True,
        requester_agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "user_id": user_id,
            "agent_id": agent_id,
            "repo_path": repo_path,
            "branch": branch,
            "lane_type": lane_type,
            "objective": objective,
            "agent_role": agent_role,
            "namespace": namespace,
            "statuses": statuses,
            "auto_create": auto_create,
            "requester_agent_id": requester_agent_id,
        }
        return self._request("POST", "/v1/handoff/resume", json_body=payload)

    def handoff_checkpoint(
        self,
        *,
        user_id: str,
        agent_id: str,
        task_summary: Optional[str] = None,
        status: str = "active",
        repo_path: Optional[str] = None,
        branch: Optional[str] = None,
        lane_id: Optional[str] = None,
        lane_type: str = "general",
        objective: Optional[str] = None,
        agent_role: Optional[str] = None,
        namespace: str = "default",
        confidentiality_scope: str = "work",
        event_type: str = "tool_complete",
        decisions_made: Optional[List[str]] = None,
        files_touched: Optional[List[str]] = None,
        todos_remaining: Optional[List[str]] = None,
        blockers: Optional[List[str]] = None,
        key_commands: Optional[List[str]] = None,
        test_results: Optional[List[str]] = None,
        context_snapshot: Optional[str] = None,
        expected_version: Optional[int] = None,
        requester_agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "user_id": user_id,
            "agent_id": agent_id,
            "task_summary": task_summary,
            "status": status,
            "repo_path": repo_path,
            "branch": branch,
            "lane_id": lane_id,
            "lane_type": lane_type,
            "objective": objective,
            "agent_role": agent_role,
            "namespace": namespace,
            "confidentiality_scope": confidentiality_scope,
            "event_type": event_type,
            "decisions_made": decisions_made or [],
            "files_touched": files_touched or [],
            "todos_remaining": todos_remaining or [],
            "blockers": blockers or [],
            "key_commands": key_commands or [],
            "test_results": test_results or [],
            "context_snapshot": context_snapshot,
            "expected_version": expected_version,
            "requester_agent_id": requester_agent_id,
        }
        return self._request("POST", "/v1/handoff/checkpoint", json_body=payload)

    def list_handoff_lanes(
        self,
        *,
        user_id: str,
        repo_path: Optional[str] = None,
        status: Optional[str] = None,
        statuses: Optional[List[str]] = None,
        limit: int = 20,
        requester_agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "user_id": user_id,
            "repo_path": repo_path,
            "status": status,
            "limit": limit,
            "requester_agent_id": requester_agent_id,
        }
        if statuses:
            params["statuses"] = statuses
        return self._request("GET", "/v1/handoff/lanes", params=params)

    def save_session_digest(self, **kwargs) -> Dict[str, Any]:
        payload = dict(kwargs)
        return self._request("POST", "/v1/handoff/sessions/digest", json_body=payload)

    def get_last_session(
        self,
        *,
        user_id: str,
        agent_id: Optional[str] = None,
        requester_agent_id: Optional[str] = None,
        repo: Optional[str] = None,
        statuses: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "user_id": user_id,
            "agent_id": agent_id,
            "requester_agent_id": requester_agent_id,
            "repo": repo,
        }
        if statuses:
            params["statuses"] = statuses
        return self._request("GET", "/v1/handoff/sessions/last", params=params)

    def list_sessions(
        self,
        *,
        user_id: str,
        agent_id: Optional[str] = None,
        requester_agent_id: Optional[str] = None,
        repo: Optional[str] = None,
        status: Optional[str] = None,
        statuses: Optional[List[str]] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "user_id": user_id,
            "agent_id": agent_id,
            "requester_agent_id": requester_agent_id,
            "repo": repo,
            "status": status,
            "limit": limit,
        }
        if statuses:
            params["statuses"] = statuses
        return self._request("GET", "/v1/handoff/sessions", params=params)

    def get_agent_trust(self, *, user_id: str, agent_id: str) -> Dict[str, Any]:
        return self._request("GET", "/v1/trust", params={"user_id": user_id, "agent_id": agent_id})

    def list_namespaces(self, *, user_id: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if user_id:
            params["user_id"] = user_id
        return self._request("GET", "/v1/namespaces", params=params)

    def declare_namespace(
        self,
        *,
        user_id: str,
        namespace: str,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/v1/namespaces",
            json_body={"user_id": user_id, "namespace": namespace, "description": description},
        )

    def grant_namespace_permission(
        self,
        *,
        user_id: str,
        namespace: str,
        agent_id: str,
        capability: str = "read",
        expires_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/v1/namespaces/permissions",
            json_body={
                "user_id": user_id,
                "namespace": namespace,
                "agent_id": agent_id,
                "capability": capability,
                "expires_at": expires_at,
            },
        )

    def upsert_agent_policy(
        self,
        *,
        user_id: str,
        agent_id: str,
        allowed_confidentiality_scopes: Optional[List[str]] = None,
        allowed_capabilities: Optional[List[str]] = None,
        allowed_namespaces: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "user_id": user_id,
            "agent_id": agent_id,
        }
        if allowed_confidentiality_scopes is not None:
            payload["allowed_confidentiality_scopes"] = allowed_confidentiality_scopes
        if allowed_capabilities is not None:
            payload["allowed_capabilities"] = allowed_capabilities
        if allowed_namespaces is not None:
            payload["allowed_namespaces"] = allowed_namespaces
        return self._request("POST", "/v1/agent-policies", json_body=payload)

    def list_agent_policies(
        self,
        *,
        user_id: str,
    ) -> Dict[str, Any]:
        return self._request("GET", "/v1/agent-policies", params={"user_id": user_id})

    def get_agent_policy(
        self,
        *,
        user_id: str,
        agent_id: str,
        include_wildcard: bool = True,
    ) -> Dict[str, Any]:
        return self._request(
            "GET",
            "/v1/agent-policies",
            params={
                "user_id": user_id,
                "agent_id": agent_id,
                "include_wildcard": str(bool(include_wildcard)).lower(),
            },
        )

    def delete_agent_policy(
        self,
        *,
        user_id: str,
        agent_id: str,
    ) -> Dict[str, Any]:
        return self._request(
            "DELETE",
            "/v1/agent-policies",
            params={"user_id": user_id, "agent_id": agent_id},
        )
