from __future__ import annotations

import pytest

from dhee.core.learnings import LearningExchange
from dhee.fs import ContextWorkspace
from dhee.fs.types import DheeFSEntry, DheeMount


@pytest.fixture(autouse=True)
def _isolated_dhee_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))


def make_workspace(tmp_path, **kwargs):
    exchange = kwargs.pop("learning_exchange", None) or LearningExchange(data_dir=tmp_path / "learnings")
    return ContextWorkspace(
        repo=str(tmp_path),
        user_id="test-user",
        agent_id="test-agent",
        learning_exchange=exchange,
        **kwargs,
    )


def test_shell_lists_learning_status_dirs(tmp_path):
    ws = make_workspace(tmp_path)

    result = ws.execute("ls /learnings")

    assert result.ok
    assert "candidates/" in result.stdout
    assert "promoted/" in result.stdout
    assert result.data["entries"][0]["kind"] == "dir"


def test_shell_exposes_compiled_state_and_context_debt(tmp_path):
    ws = make_workspace(tmp_path)
    store = ws.context_state_store()
    store.observe_prompt("Fix parser bug without rereading stale transcript")
    store.add_fact("Parser failure is in token stream handling", source="pytest")

    state = ws.execute("cat /state/card.xml")
    current = ws.execute("cat /state/current.md")
    history = ws.execute("cat /state/history.md")
    status = ws.execute("cat /context/status.json")
    debt = ws.execute("cat /context/debt.json")

    assert state.ok
    assert "<dhee_state" in state.stdout
    assert "Fix parser bug" in state.stdout
    assert current.ok
    assert "Parser failure" in current.stdout
    assert history.ok
    assert "Task Epoch History" in history.stdout
    assert status.ok
    assert "dhee_context_status" in status.stdout
    assert debt.ok
    assert "projected_cache_read_tokens" in debt.stdout


def test_dheefs_accepts_canonical_dhee_uri_aliases(tmp_path):
    ws = make_workspace(tmp_path)
    store = ws.context_state_store()
    store.observe_prompt("Win the developer brain workflow")
    store.add_fact("Dhee URI aliases resolve onto DheeFS paths", source="test")

    current = ws.execute("cat dhee://state/current")
    handoff = ws.execute("cat dhee://handoff/latest")
    shared = ws.execute("cat dhee://shared/task-results")
    agent = ws.execute("cat dhee://agents/codex/memory")

    assert current.ok
    assert "Dhee URI aliases" in current.stdout
    assert handoff.ok
    assert "# Latest Handoff" in handoff.stdout
    assert shared.ok
    assert '"results"' in shared.stdout
    assert agent.ok
    assert "# codex" in agent.stdout


def test_context_kernel_reads_dhee_uri_aliases(tmp_path):
    from dhee.context_kernel import DheeContextKernel, KernelScope

    kernel = DheeContextKernel(KernelScope(repo=str(tmp_path), user_id="test-user", agent_id="test-agent"))
    kernel.workspace().context_state_store().add_fact("Kernel path boundary is stable", source="test")

    assert kernel.normalize("dhee://state/current") == "/state/current.md"
    assert "Kernel path boundary" in kernel.read("dhee://state/current")


def test_context_checkpoint_visible_in_dheefs(tmp_path):
    ws = make_workspace(tmp_path)
    checkpoint = ws.context_checkpoint(reason="test")

    listing = ws.execute("ls /context/checkpoints")
    content = ws.execute(f"cat /context/checkpoints/{checkpoint['id']}.json")

    assert listing.ok
    assert checkpoint["id"] in listing.stdout
    assert content.ok
    assert "dhee_context_checkpoint" in content.stdout


def test_shell_cat_grep_and_why_learning(tmp_path):
    exchange = LearningExchange(data_dir=tmp_path / "learnings")
    candidate = exchange.submit(
        title="Parser debugging",
        body="When parser errors look impossible, inspect the generated token stream first.",
        kind="workflow",
        source_agent_id="hermes",
        source_harness="test",
        confidence=0.8,
    )
    ws = make_workspace(tmp_path, learning_exchange=exchange)

    cat_result = ws.execute(f"cat /learnings/candidates/{candidate.id}.md")
    grep_result = ws.execute("grep token /learnings/candidates")
    why_result = ws.execute(f"why /learnings/candidates/{candidate.id}.md")

    assert cat_result.ok
    assert "# Parser debugging" in cat_result.stdout
    assert grep_result.ok
    assert candidate.id in grep_result.stdout
    assert why_result.ok
    assert "source_agent_id: hermes" in why_result.stdout


def test_promote_moves_candidate_to_promoted(tmp_path):
    exchange = LearningExchange(data_dir=tmp_path / "learnings")
    candidate = exchange.submit(
        title="Use focused tests",
        body="Run the narrow test first before broad regression suites.",
        confidence=0.2,
    )
    ws = make_workspace(tmp_path, learning_exchange=exchange)

    result = ws.execute(f"promote /learnings/candidates/{candidate.id}.md")

    assert result.ok
    assert exchange.get(candidate.id).status == "promoted"
    assert candidate.id in ws.execute("ls /learnings/promoted").stdout


def test_promote_accepts_dhee_prefixed_learning_path(tmp_path):
    exchange = LearningExchange(data_dir=tmp_path / "learnings")
    candidate = exchange.submit(title="Alias path", body="Dhee-prefixed paths should work.")
    ws = make_workspace(tmp_path, learning_exchange=exchange)

    result = ws.execute(f"promote /dhee/learnings/candidates/{candidate.id}.md")

    assert result.ok
    assert exchange.get(candidate.id).status == "promoted"


def test_learning_paths_reject_trailing_garbage(tmp_path):
    exchange = LearningExchange(data_dir=tmp_path / "learnings")
    candidate = exchange.submit(title="Strict paths", body="Trailing segments should not alias a learning.")
    ws = make_workspace(tmp_path, learning_exchange=exchange)

    result = ws.execute(f"cat /learnings/candidates/{candidate.id}.md/extra")

    assert result.ok is False
    assert result.exit_code == 2


def test_single_path_commands_reject_extra_arguments(tmp_path):
    ws = make_workspace(tmp_path)

    result = ws.execute("cat /learnings /handoff")

    assert result.ok is False
    assert "exactly one path" in result.stderr


def test_reject_accepts_quoted_reason(tmp_path):
    exchange = LearningExchange(data_dir=tmp_path / "learnings")
    candidate = exchange.submit(title="Too broad", body="This should not be promoted yet.")
    ws = make_workspace(tmp_path, learning_exchange=exchange)

    result = ws.execute(f'reject /learnings/candidates/{candidate.id}.md "needs better evidence"')

    assert result.ok
    rejected = exchange.get(candidate.id)
    assert rejected.status == "rejected"
    assert rejected.rejected_reason == "needs better evidence"


def test_longest_prefix_mount_resolution(tmp_path):
    class TestPtrMount(DheeMount):
        prefix = "/router/ptr"

        def list(self, path):
            return [DheeFSEntry(name="sentinel", path="/router/ptr/sentinel")]

    ws = make_workspace(tmp_path)
    ws.mounts.append(TestPtrMount(ws))

    assert ws.resolve_mount("/router/ptr/R-abc123def4").prefix == "/router/ptr"


def test_router_pointer_read_round_trips_raw_content(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_ROUTER_PTR_DIR", str(tmp_path / "ptrs"))
    monkeypatch.setenv("DHEE_ROUTER_SESSION_ID", "test-session")
    from dhee.router import ptr_store

    stored = ptr_store.store("raw pointer content", tool="Read", meta={"test": True})
    ws = make_workspace(tmp_path)

    result = ws.execute(f"cat /router/ptr/{stored.ptr}")

    assert result.ok
    assert result.stdout == "raw pointer content"
    records = ptr_store.iter_expansion_records()
    assert any(row["ptr"] == stored.ptr and row.get("tool") == "dhee_shell" for row in records)


class FakeArtifactDB:
    def list_artifacts(self, **_kwargs):
        return [
            {
                "artifact_id": "art_1",
                "filename": "notes.md",
                "mime_type": "text/markdown",
                "lifecycle_state": "indexed",
                "extraction_count": 1,
            }
        ]

    def get_artifact(self, artifact_id):
        if artifact_id != "art_1":
            return None
        return {
            "artifact_id": "art_1",
            "filename": "notes.md",
            "mime_type": "text/markdown",
            "lifecycle_state": "indexed",
            "extractions": [{"extraction_source": "host", "extracted_text": "Auth migration notes"}],
            "chunks": [{"chunk_index": 0, "content": "Auth migration chunk"}],
        }


def test_artifacts_mount_exposes_existing_artifacts(tmp_path):
    ws = make_workspace(tmp_path, db=FakeArtifactDB())

    listing = ws.execute("ls /artifacts")
    content = ws.execute("cat /artifacts/art_1.md")

    assert listing.ok
    assert "art_1.md" in listing.stdout
    assert content.ok
    assert "Auth migration notes" in content.stdout


def test_mcp_shell_handler_returns_cli_shape(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "data"))
    import dhee.mcp_slim as slim

    monkeypatch.setattr(slim, "_get_db", lambda: None)

    result = slim._handle_dhee_shell(
        {
            "command": "ls /learnings",
            "repo": str(tmp_path),
            "user_id": "test-user",
            "agent_id": "test-agent",
        }
    )

    assert result["ok"] is True
    assert result["exit_code"] == 0
    assert "stdout" in result
    assert "entries" in result["data"]


class FakeContextSource:
    name = "slack"

    def list(self, path):
        return [DheeFSEntry(name="engineering.md", path="/sources/slack/engineering.md")]

    def read(self, path):
        if path == "/sources/slack/engineering.md":
            return "Parser outage notes from Slack"
        return "Slack source root"

    def search(self, path, query):
        return [{"path": "/sources/slack/engineering.md", "text": "Parser outage notes from Slack"}]


def test_sources_root_delegates_to_registered_context_sources(tmp_path):
    ws = make_workspace(tmp_path, context_sources=[FakeContextSource()])

    root = ws.execute("ls /sources")
    listing = ws.execute("ls /sources/slack")
    content = ws.execute("cat /sources/slack/engineering.md")
    grep = ws.execute("grep outage /sources/slack")

    assert root.ok
    assert "slack/" in root.stdout
    assert listing.ok
    assert "engineering.md" in listing.stdout
    assert content.ok
    assert "Parser outage notes" in content.stdout
    assert grep.ok
    assert "Parser outage" in grep.stdout
