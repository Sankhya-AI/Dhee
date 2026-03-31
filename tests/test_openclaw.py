"""Tests for OpenClaw v1 primitives (checkpoint + handoff + trace persistence)."""

import os
import tempfile

import pytest

from dhee.configs.base import MemoryConfig
from dhee.memory.main import FullMemory as Memory
from dhee.memory.projects import ProjectManager
from dhee.memory.tasks import TaskManager
from engram_bus import Bus
from engram_router import RouterConfig, TaskRouter

from engram_bridge.bridge import Bridge
from engram_bridge.config import AgentConfig, BridgeConfig


def _make_memory(tmpdir: str) -> Memory:
    config = MemoryConfig(
        vector_store={"provider": "memory", "config": {}},
        llm={"provider": "mock", "config": {}},
        embedder={"provider": "simple", "config": {}},
        history_db_path=os.path.join(tmpdir, "test.db"),
        graph={"enable_graph": False},
        scene={"enable_scenes": False},
        profile={"enable_profiles": False},
        handoff={"enable_handoff": False},
        echo={"enable_echo": False},
        category={"enable_categories": False},
    )
    return Memory(config)


@pytest.fixture
def tmpdir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def mem(tmpdir):
    return _make_memory(tmpdir)


@pytest.fixture
def tm(mem):
    return TaskManager(mem)


@pytest.fixture
def pm(mem):
    return ProjectManager(mem)


@pytest.fixture
def bridge(tmpdir, mem, monkeypatch):
    # Keep all "~/.engram" artifacts inside the test temp dir.
    monkeypatch.setenv("HOME", tmpdir)

    cfg = BridgeConfig(
        telegram_token="",
        allowed_users=[],
        default_agent="claude-code",
        agents={
            "claude-code": AgentConfig(type="claude", model=""),
            "codex": AgentConfig(type="codex", model=""),
        },
        memory_provider="simple",
        auto_store=True,
        channel="web",
    )
    b = Bridge(cfg)
    # Inject test memory + isolated bus
    b._memory = mem
    b._memory_init = True
    b.bus = Bus(db_path=os.path.join(tmpdir, "handoff.db"))
    return b


@pytest.mark.asyncio
async def test_checkpoint_checksum_matches(bridge, pm, tm):
    proj = pm.create_project("P", repo_path="/tmp/repo")
    task = tm.create_task(
        "Fix bug",
        description="Repro: ...",
        project_id=proj["id"],
        extra_metadata={
            "openclaw": {
                "schema_version": 1,
                "runtime_status": "idle",
                "repo_path": "",
                "sessions": {},
                "lanes": [],
                "checkpoints": [],
                "last_routing_event_id": None,
            },
        },
    )

    # Add some conversation for tail capture
    tm.add_conversation_entry(task["id"], {"type": "user", "content": "please fix"})
    tm.add_conversation_entry(task["id"], {"type": "assistant", "content": "working..."})

    cp = await bridge.create_checkpoint(task["id"], "claude-code", "manual")
    assert "error" not in cp
    assert cp["bus_session_id"]
    assert cp["checkpoint_id"]

    snapshot = cp["snapshot"]
    assert snapshot["task_id"] == task["id"]
    assert snapshot["project_id"] == proj["id"]
    assert snapshot["repo_path"] == "/tmp/repo"
    assert snapshot["from_agent"] == "claude-code"
    assert snapshot["reason"] == "manual"
    assert snapshot["checksum_sha256"]

    # Check checksum integrity
    snap_no = dict(snapshot)
    checksum = snap_no.pop("checksum_sha256")
    assert checksum == Bridge._sha256_canonical_json(snap_no)

    # Check bus store persistence
    cps = bridge.bus.list_checkpoints(session_id=cp["bus_session_id"])
    ids = {c["id"] for c in cps}
    assert cp["checkpoint_id"] in ids

    # OpenClaw metadata updated
    updated = tm.get_task(task["id"])
    oc = updated["custom"]["openclaw"]
    assert oc["repo_path"] == "/tmp/repo"
    assert cp["checkpoint_id"] in oc["checkpoints"]


@pytest.mark.asyncio
async def test_handoff_updates_openclaw(bridge, pm, tm, monkeypatch):
    proj = pm.create_project("P", repo_path="/tmp/repo")
    task = tm.create_task("Implement feature", project_id=proj["id"])

    async def _noop_handle_message(_msg):  # avoid launching external CLIs
        return None

    monkeypatch.setattr(bridge, "handle_message", _noop_handle_message)

    result = await bridge.handoff_task(
        task["id"],
        from_agent="claude-code",
        to_agent="codex",
        reason="manual",
    )
    assert "error" not in result
    assert result["lane_id"]
    assert result["checkpoint_id"]

    updated = tm.get_task(task["id"])
    oc = updated["custom"]["openclaw"]
    assert oc["runtime_status"] == "handoff_pending"
    assert result["lane_id"] in oc["lanes"]
    assert result["checkpoint_id"] in oc["checkpoints"]
    assert "claude-code" in oc["sessions"]
    assert "codex" in oc["sessions"]
    assert oc["sessions"]["claude-code"]["bus_session_id"] == result["bus_session_id"]


@pytest.mark.asyncio
async def test_task_edit_persistence_updates_entry(mem, tm):
    from engram_bridge.channels.web import WebChannel, EngramTaskStore

    task = tm.create_task("Streamed output task")

    wc = WebChannel()
    wc.tasks = EngramTaskStore(mem, user_id="bridge")

    await wc.send_task_update(0, task["id"], "task_text", {
        "content": "hello",
        "agent": "claude-code",
        "message_id": 123,
        "streaming": True,
    })
    await wc.send_task_update(0, task["id"], "task_edit", {
        "content": "hello world",
        "message_id": 123,
        "streaming": False,
    })

    updated = wc.tasks.get_detail(task["id"])
    conv = updated.get("conversation", [])
    match = [e for e in conv if str(e.get("message_id", "")) == "123"]
    assert match
    assert match[-1]["content"] == "hello world"
    assert match[-1].get("streaming") is False


def test_router_logs_routing_evidence(mem, tm, tmpdir):
    class StubRegistry:
        def find_capable(self, _query: str, limit: int = 5):
            return [
                {
                    "name": "claude-code",
                    "similarity": 0.91,
                    "status": "available",
                    "active_tasks": [],
                    "max_concurrent": 1,
                },
                {
                    "name": "codex",
                    "similarity": 0.80,
                    "status": "available",
                    "active_tasks": [],
                    "max_concurrent": 2,
                },
            ][:limit]

        def add_active_task(self, _agent_name: str, _task_id: str) -> None:
            return None

        def remove_active_task(self, _agent_name: str, _task_id: str) -> None:
            return None

    bus = Bus(db_path=os.path.join(tmpdir, "router_handoff.db"))
    cfg = RouterConfig(log_events=True)
    router = TaskRouter(StubRegistry(), tm, config=cfg, memory=mem)
    router.connect_bus(bus)

    t = tm.create_task("Fix CSS padding", description="Button misaligned")
    routed = router.route(t["id"], force=True)
    assert routed and routed.get("assigned_agent")

    sigs = bus.signals(topic="bridge.task.routed", limit=5)
    assert sigs
    last = sigs[-1]["data"]
    assert last["task_id"] == t["id"]
    assert "query" in last
    assert isinstance(last.get("candidates"), list)
    assert "event_id" in last
