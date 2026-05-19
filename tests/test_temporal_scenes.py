from dhee.temporal_scenes import (
    PromotionGate,
    build_context_pack,
    collect_live_scene_sources,
    collect_scene_evidence,
    compile_scene,
    compile_scene_from_sources,
    search_scenes,
)


def test_scene_compile_preserves_multi_agent_provenance_and_cards_are_pointer_safe(tmp_path):
    evidence = [
        {
            "id": "mem-chotu-1",
            "memory": "Watched a Kimi CLI agent walkthrough about compact agent adapters and replayable update recipes.",
            "user_id": "u1",
            "agent_id": "chotu",
            "source_app": "chotu-browser",
            "source_event_id": "video-42",
            "memory_type": "world_memory",
            "confidentiality_scope": "personal",
            "metadata": {"url": "https://example.test/kimi"},
            "categories": ["kimi", "agent-adapter"],
        },
        {
            "id": "codex-run-7",
            "content": "Codex found that repo updates should travel as capsules with before and after behavior, hashes, and tests.",
            "user_id": "u1",
            "agent_id": "codex",
            "source_app": "codex",
            "source_event_id": "run-7",
            "run_id": "run-7",
            "memory_type": "session_digest",
            "confidentiality_scope": "personal",
        },
    ]

    scene = compile_scene(
        evidence,
        user_id="u1",
        repo="/tmp/repo",
        task="Implement Kimi CLI adapter update capsules",
        store_dir=tmp_path,
    )

    assert scene.provenance["agent_ids"] == ["chotu", "codex"]
    assert scene.provenance["source_apps"] == ["chotu-browser", "codex"]
    assert "text" in scene.modalities
    assert scene.tier in {"hot", "warm", "cold"}

    card = scene.to_card()
    assert "evidence_refs" in card
    assert "snippet" not in card["evidence_refs"][0]
    assert "url" not in card["evidence_refs"][0]

    hits = search_scenes("Kimi adapter capsules", user_id="u1", repo="/tmp/repo", store_dir=tmp_path)
    assert hits and hits[0].id == scene.id


def test_context_pack_obeys_budget_and_never_includes_raw_evidence_fields(tmp_path):
    compile_scene(
        [
            {
                "id": "mem-1",
                "memory": "A long transcript derivative about token budgets, scene cards, and pointer-only media expansion.",
                "agent_id": "chotu",
                "source_app": "wearable-transcript",
                "source_event_id": "audio-1",
                "memory_type": "transcript_chunk",
                "modality": "audio",
                "confidentiality_scope": "personal",
            }
        ],
        user_id="u1",
        task="Use wearable transcript memory for repo task context",
        store_dir=tmp_path,
    )

    pack = build_context_pack(
        "token budgets transcript scene cards",
        user_id="u1",
        token_budget=300,
        store_dir=tmp_path,
    )

    assert pack["estimated_tokens"] <= 300
    assert pack["raw_media_included"] is False
    assert pack["full_diffs_included"] is False
    serialized = str(pack)
    assert "snippet" not in serialized
    assert "transcript_chunk" in serialized


def test_promotion_gate_redacts_personal_scene_evidence(tmp_path):
    scene = compile_scene(
        [
            {
                "id": "private-1",
                "memory": "Personal browsing note from /Users/alice/private/topic.txt about adapter design.",
                "agent_id": "chotu",
                "source_app": "browser",
                "source_event_id": "evt-1",
                "confidentiality_scope": "personal",
                "uri": "/Users/alice/private/topic.txt",
            }
        ],
        user_id="u1",
        task="Adapter design",
        store_dir=tmp_path,
    )

    safe = PromotionGate().sanitize_scene(scene)
    assert safe["personal_context_used"] is True
    assert "<local-path>" in safe["summary"]
    assert "snippet" not in str(safe["evidence_refs"])
    assert "/Users/alice" not in str(safe)


def test_mcp_slim_scene_handlers_compile_search_and_pack(tmp_path):
    from dhee import mcp_slim

    compile_result = mcp_slim.HANDLERS["dhee_scene_compile"](
        {
            "evidence": [
                {
                    "id": "mem-mcp",
                    "memory": "MCP scene handler captures a compact adapter lesson for future coding agents.",
                    "agent_id": "codex",
                    "source_app": "codex",
                    "confidentiality_scope": "personal",
                }
            ],
            "task": "adapter lesson",
            "user_id": "u1",
            "store_dir": str(tmp_path),
        }
    )
    assert compile_result["format"] == "dhee_scene_compile.v1"

    search_result = mcp_slim.HANDLERS["dhee_scene_search"](
        {"query": "adapter lesson", "user_id": "u1", "store_dir": str(tmp_path)}
    )
    assert search_result["results"]

    pack = mcp_slim.HANDLERS["dhee_context_pack"](
        {"query": "adapter lesson", "user_id": "u1", "store_dir": str(tmp_path), "token_budget": 300}
    )
    assert pack["format"] == "dhee_context_pack.v1"
    assert pack["estimated_tokens"] <= 300


def test_collect_scene_evidence_from_repo_context_session_shared_task_and_artifacts(tmp_path):
    from dhee import repo_link

    repo = tmp_path / "repo"
    repo.mkdir()
    repo_link._ensure_repo_skeleton(repo)
    repo_link.add_entry(
        repo,
        kind="decision",
        title="Use update capsules",
        content="Share adapter updates as sanitized capsule recipes with hashes and tests.",
    )
    session = {
        "id": "sess-1",
        "agent_id": "codex",
        "task_summary": "Implemented temporal scenes",
        "decisions_made": ["Use pointer-backed evidence cards"],
        "files_touched": ["dhee/temporal_scenes.py"],
    }
    shared_results = {
        "results": [
            {
                "id": "packet-1",
                "packet_kind": "native_bash",
                "tool_name": "pytest",
                "digest": "pytest passed for scene packs",
                "agent_id": "codex",
                "harness": "codex",
            }
        ]
    }
    artifacts = [
        {
            "artifact_id": "artifact-1",
            "filename": "README.md",
            "summary": "Documentation explains capsule import workflow.",
        }
    ]

    evidence = collect_scene_evidence(
        repo=repo,
        session=session,
        shared_task_results=shared_results,
        artifacts=artifacts,
        sources=["repo_context", "session", "shared_task_results", "artifacts"],
        limit=10,
    )

    assert {row["kind"] for row in evidence} >= {"repo_context:decision", "session_digest", "native_bash", "artifact"}
    scene = compile_scene_from_sources(
        repo=repo,
        session=session,
        shared_task_results=shared_results,
        artifacts=artifacts,
        sources=["repo_context", "session", "shared_task_results", "artifacts"],
        user_id="u1",
        query="capsule adapter scenes",
        store_dir=tmp_path / "scenes",
    )
    assert "dhee-repo-context" in scene.provenance["source_apps"]
    assert "codex" in scene.provenance["agent_ids"]


def test_mcp_slim_scene_compile_can_collect_repo_context(tmp_path):
    from dhee import mcp_slim, repo_link

    repo = tmp_path / "repo"
    repo.mkdir()
    repo_link._ensure_repo_skeleton(repo)
    repo_link.add_entry(
        repo,
        kind="learning",
        title="Personal memory bridge",
        content="Coding agents should receive compact scene cards derived from relevant personal observations.",
    )

    result = mcp_slim.HANDLERS["dhee_scene_compile"](
        {
            "repo": str(repo),
            "query": "personal observations coding agents",
            "include_repo_context": True,
            "user_id": "u1",
            "store_dir": str(tmp_path / "scenes"),
        }
    )

    assert result["format"] == "dhee_scene_compile.v1"
    assert result["scene"]["provenance"]["source_apps"] == ["dhee-repo-context"]


class _FakeLiveSceneDB:
    def __init__(self, repo):
        self.repo = str(repo)

    def list_shared_tasks(self, user_id="default", status="active", repo=None, limit=50):
        return [
            {
                "id": "task-1",
                "repo": self.repo,
                "workspace_id": self.repo,
                "folder_path": ".",
                "title": "live task",
                "status": "active",
                "metadata": {},
            }
        ]

    def list_shared_task_results(self, shared_task_id, limit=5, **_kwargs):
        return [
            {
                "id": "packet-1",
                "packet_kind": "native_bash",
                "tool_name": "pytest",
                "digest": "Live shared task result says scene packs passed.",
                "harness": "codex",
                "agent_id": "codex",
                "metadata": {"command": "pytest"},
            }
        ][:limit]

    def list_artifacts(self, **_kwargs):
        return [
            {
                "artifact_id": "artifact-1",
                "filename": "capsule-notes.md",
                "source_path": f"{self.repo}/capsule-notes.md",
                "lifecycle_state": "attached",
            }
        ]


def test_collect_live_scene_sources_reads_bounded_shared_task_and_artifacts(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    live = collect_live_scene_sources(
        db=_FakeLiveSceneDB(repo),
        repo=repo,
        user_id="u1",
        include_session=False,
        include_shared_task_results=True,
        include_artifacts=True,
        limit=5,
    )

    evidence = collect_scene_evidence(
        repo=repo,
        shared_task_results=live["shared_task_results"],
        artifacts=live["artifacts"],
        sources=["shared_task_results", "artifacts"],
    )
    assert {row["kind"] for row in evidence} == {"native_bash", "artifact"}


def test_mcp_slim_scene_compile_can_collect_live_sources(tmp_path, monkeypatch):
    from dhee import mcp_slim

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(mcp_slim, "_get_db", lambda: _FakeLiveSceneDB(repo))

    result = mcp_slim.HANDLERS["dhee_scene_compile"](
        {
            "repo": str(repo),
            "query": "live shared task artifact scene",
            "include_live_sources": True,
            "user_id": "u1",
            "store_dir": str(tmp_path / "scenes"),
        }
    )

    assert result["format"] == "dhee_scene_compile.v1"
    assert {"codex", "dhee-artifact"}.issubset(set(result["scene"]["provenance"]["source_apps"]))
