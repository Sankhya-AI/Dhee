"""HTTP sidecar for Dhee's universal agent runtime."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from dhee.agent_runtime import Client, Run
from dhee.agent_runtime.auth import require_bearer_token
from dhee.webhooks.elevenlabs import (
    extract_user_and_run,
    summary_from_analysis,
    transcript_to_events,
)


class StartRunRequest(BaseModel):
    user_id: str = "default"
    app_id: str = "default-agent"
    task: Optional[str] = None
    channel: str = "generic"
    metadata: dict[str, Any] = Field(default_factory=dict)


class EventRequest(BaseModel):
    user_id: str = "default"
    app_id: str = "default-agent"
    type: str
    content: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolRequest(BaseModel):
    user_id: str = "default"
    app_id: str = "default-agent"
    run_id: Optional[str] = None
    action: str
    query: Optional[str] = None
    content: Optional[str] = None
    summary: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class FinishRequest(BaseModel):
    user_id: str = "default"
    app_id: str = "default-agent"
    outcome: str = "completed"
    summary: Optional[str] = None
    outcome_score: Optional[float] = None
    what_worked: Optional[str] = None
    what_failed: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass
class RuntimeSettings:
    data_dir: Optional[Union[str, Path]] = None
    provider: Optional[str] = None
    in_memory: bool = False
    offline: bool = False
    profile: str = "generic"
    allow_unsigned_webhooks: bool = False
    run_ttl_seconds: float = 6 * 60 * 60
    max_active_runs: int = 1000


class RunRegistry:
    """Process-local run cache for a sidecar process."""

    def __init__(self, settings: RuntimeSettings):
        self.settings = settings
        self._runs: dict[str, Run] = {}
        self._last_accessed: dict[str, float] = {}

    def client(self, user_id: str, app_id: str) -> Client:
        return Client(
            user_id=user_id,
            app_id=app_id,
            data_dir=self.settings.data_dir,
            provider=self.settings.provider,
            in_memory=self.settings.in_memory,
            offline=self.settings.offline,
        )

    def start(self, req: StartRunRequest) -> Run:
        self.prune()
        run = self.client(req.user_id, req.app_id).run(
            task=req.task,
            metadata=req.metadata,
        )
        self._runs[run.id] = run
        self._last_accessed[run.id] = time.time()
        self.prune()
        return run

    def get(
        self,
        run_id: Optional[str],
        user_id: str,
        app_id: str,
        task: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Run:
        self.prune()
        if run_id and run_id in self._runs:
            self._last_accessed[run_id] = time.time()
            return self._runs[run_id]
        run = self.client(user_id, app_id).run(
            task=task,
            run_id=run_id,
            metadata=metadata or {},
        )
        return run

    def finish(self, run_id: Optional[str]) -> None:
        if not run_id:
            return
        self._runs.pop(run_id, None)
        self._last_accessed.pop(run_id, None)

    def prune(self) -> None:
        now = time.time()
        ttl = max(1.0, float(self.settings.run_ttl_seconds or 1.0))
        expired = [
            run_id
            for run_id, last_accessed in self._last_accessed.items()
            if now - last_accessed > ttl
        ]
        for run_id in expired:
            self.finish(run_id)

        max_runs = max(1, int(self.settings.max_active_runs or 1))
        overflow = len(self._runs) - max_runs
        if overflow <= 0:
            return
        oldest = sorted(
            self._last_accessed.items(),
            key=lambda item: item[1],
        )[:overflow]
        for run_id, _ in oldest:
            self.finish(run_id)


def create_app(
    *,
    data_dir: Optional[Union[str, Path]] = None,
    provider: Optional[str] = None,
    in_memory: bool = False,
    offline: bool = False,
    profile: str = "generic",
    allow_unsigned_webhooks: bool = False,
    run_ttl_seconds: float = 6 * 60 * 60,
    max_active_runs: int = 1000,
) -> FastAPI:
    settings = RuntimeSettings(
        data_dir=data_dir,
        provider=provider,
        in_memory=in_memory,
        offline=offline,
        profile=profile,
        allow_unsigned_webhooks=allow_unsigned_webhooks,
        run_ttl_seconds=run_ttl_seconds,
        max_active_runs=max_active_runs,
    )
    registry = RunRegistry(settings)
    app = FastAPI(title="Dhee Agent Runtime")
    app.state.dhee_runtime_settings = settings
    app.state.dhee_run_registry = registry

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {
            "ok": True,
            "service": "dhee-agent-runtime",
            "profile": settings.profile,
        }

    @app.post("/v1/runs/start")
    def start_run(
        req: StartRunRequest,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        require_bearer_token(authorization)
        run = registry.start(req)
        patch = run.before(channel=req.channel)
        return patch.model_dump()

    @app.post("/v1/runs/{run_id}/event")
    def record_event(
        run_id: str,
        req: EventRequest,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        require_bearer_token(authorization)
        run = registry.get(run_id, req.user_id, req.app_id)
        return run.event(req.type, content=req.content, metadata=req.metadata)

    @app.post("/v1/runs/{run_id}/finish")
    def finish_run(
        run_id: str,
        req: FinishRequest,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        require_bearer_token(authorization)
        run = registry.get(run_id, req.user_id, req.app_id)
        result = run.finish(
            outcome=req.outcome,
            summary=req.summary,
            outcome_score=req.outcome_score,
            what_worked=req.what_worked,
            what_failed=req.what_failed,
            metadata=req.metadata,
        )
        registry.finish(run_id)
        return result

    @app.post("/v1/tools/dhee_memory")
    def dhee_memory_tool(
        req: ToolRequest,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        require_bearer_token(authorization)
        run = registry.get(req.run_id, req.user_id, req.app_id)
        result = run.tool(
            action=req.action,
            query=req.query,
            content=req.content,
            summary=req.summary,
            metadata=req.metadata,
        )
        return result.model_dump()

    @app.post("/v1/webhooks/elevenlabs/post_call")
    async def elevenlabs_post_call(request: Request) -> Any:
        raw_body = await request.body()
        signature = request.headers.get("elevenlabs-signature")
        webhook_secret = os.getenv("ELEVENLABS_WEBHOOK_SECRET")

        if webhook_secret:
            try:
                from elevenlabs.client import ElevenLabs

                elevenlabs = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
                event = elevenlabs.webhooks.construct_event(
                    rawBody=raw_body.decode("utf-8"),
                    sig_header=signature,
                    secret=webhook_secret,
                )
            except Exception:
                return JSONResponse({"error": "Invalid signature"}, status_code=401)
        elif not settings.allow_unsigned_webhooks:
            return JSONResponse(
                {
                    "error": "Unsigned ElevenLabs webhook rejected",
                    "detail": "Set ELEVENLABS_WEBHOOK_SECRET or start with --allow-unsigned-webhooks for local development.",
                },
                status_code=401,
            )
        else:
            event = json.loads(raw_body.decode("utf-8"))

        if event.get("type") != "post_call_transcription":
            return {"status": "ignored"}

        data = event.get("data") or {}
        user_id, run_id = extract_user_and_run(data)
        app_id = f"elevenlabs:{data.get('agent_id') or 'agent'}"

        run = registry.get(
            run_id,
            user_id,
            app_id,
            task="voice call post-call checkpoint",
        )
        for ev in transcript_to_events(data):
            run.event(
                ev["type"],
                content=ev["content"],
                metadata=ev["metadata"],
            )

        analysis = data.get("analysis") or {}
        run.finish(
            outcome=(
                "completed"
                if data.get("status") in {None, "done", "completed"}
                else str(data.get("status"))
            ),
            summary=summary_from_analysis(data),
            outcome_score=1.0 if analysis.get("call_successful") is True else None,
            metadata={
                "conversation_id": data.get("conversation_id"),
                "agent_id": data.get("agent_id"),
                "elevenlabs_status": data.get("status"),
                "elevenlabs_analysis": analysis,
            },
        )
        registry.finish(run_id)

        return {"status": "received"}

    return app


app = create_app()


def run(
    host: str = "127.0.0.1",
    port: int = 8765,
    profile: str = "generic",
    data_dir: Optional[Union[str, Path]] = None,
    provider: Optional[str] = None,
    in_memory: bool = False,
    offline: bool = False,
    allow_unsigned_webhooks: bool = False,
) -> None:
    import uvicorn

    runtime_app = create_app(
        data_dir=data_dir,
        provider=provider,
        in_memory=in_memory,
        offline=offline,
        profile=profile,
        allow_unsigned_webhooks=allow_unsigned_webhooks,
    )
    uvicorn.run(runtime_app, host=host, port=port)
