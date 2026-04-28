"""Tests for dhee.repo_link — personal vs repo context."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Pin ~/.dhee/ + the cwd-anchored search to a tmp dir."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    yield home


@pytest.fixture
def git_repo(tmp_path):
    """Create a real git repo on disk so hook installation can run."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test"], check=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "test"], check=True
    )
    return repo


@pytest.fixture
def two_git_repos(tmp_path):
    repos = []
    for name in ("svc-a", "svc-b"):
        repo = tmp_path / name
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "test@test"], check=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "test"], check=True
        )
        repos.append(repo)
    return repos


# ---------------------------------------------------------------------------
# Linking
# ---------------------------------------------------------------------------


class TestLink:
    def test_link_creates_skeleton_and_hooks(self, isolated_home, git_repo):
        from dhee import repo_link

        info = repo_link.link(git_repo)

        assert (git_repo / ".dhee").is_dir()
        assert (git_repo / ".dhee" / "config.json").is_file()
        assert (git_repo / ".dhee" / "context" / "manifest.json").is_file()
        assert (git_repo / ".dhee" / "context" / "entries.jsonl").is_file()
        assert (git_repo / ".dhee" / ".gitattributes").is_file()
        for name in ("post-merge", "post-checkout", "post-rewrite", "pre-push"):
            hook = git_repo / ".git" / "hooks" / name
            assert hook.is_file(), name
            assert hook.stat().st_mode & 0o111, f"{name} not executable"
        assert "dhee context refresh" in (git_repo / ".git" / "hooks" / "post-merge").read_text()
        assert "dhee context check" in (git_repo / ".git" / "hooks" / "pre-push").read_text()
        assert info["repo_id"]
        assert info["hooks"] == ["post-merge", "post-checkout", "post-rewrite", "pre-push"]

    def test_link_idempotent(self, isolated_home, git_repo):
        from dhee import repo_link

        first = repo_link.link(git_repo)
        second = repo_link.link(git_repo)
        assert first["repo_id"] == second["repo_id"]
        # Skeleton and hooks should still be present
        assert (git_repo / ".dhee" / "config.json").is_file()

    def test_link_outside_git_repo_raises(self, isolated_home, tmp_path):
        from dhee import repo_link

        non_repo = tmp_path / "loose"
        non_repo.mkdir()
        with pytest.raises(ValueError):
            repo_link.link(non_repo)

    def test_link_registers_in_links_json(self, isolated_home, git_repo):
        from dhee import repo_link

        repo_link.link(git_repo)
        links = repo_link.list_links()
        assert str(git_repo.resolve()) in links

    def test_link_mirrors_into_workspace_store(self, isolated_home, git_repo):
        from dhee import repo_link

        repo_link.link(git_repo)
        store = isolated_home / ".dhee" / "local_context_folders.json"
        assert store.is_file()
        data = json.loads(store.read_text())
        folders = data["folders"]
        assert str(git_repo.resolve()) in folders
        assert folders[str(git_repo.resolve())]["shared"] is True
        assert folders[str(git_repo.resolve())]["linked"] is True

    def test_link_preserves_existing_user_hook(self, isolated_home, git_repo):
        from dhee import repo_link

        existing = git_repo / ".git" / "hooks" / "post-merge"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_text("#!/bin/sh\necho user-hook\n")
        existing.chmod(0o755)

        repo_link.link(git_repo)

        hook = (git_repo / ".git" / "hooks" / "post-merge").read_text()
        user_copy = (git_repo / ".git" / "hooks" / "post-merge.user").read_text()
        assert "dhee context refresh" in hook
        assert "post-merge.user" in hook
        assert "echo user-hook" in user_copy

    def test_link_preserves_user_pre_push_failure(self, isolated_home, git_repo):
        from dhee import repo_link

        existing = git_repo / ".git" / "hooks" / "pre-push"
        existing.write_text("#!/bin/sh\nexit 42\n")
        existing.chmod(0o755)

        repo_link.link(git_repo)

        result = subprocess.run([str(existing)], cwd=git_repo)

        assert result.returncode == 42

    def test_unlink_removes_hooks_and_registry(self, isolated_home, git_repo):
        from dhee import repo_link

        repo_link.link(git_repo)
        repo_link.unlink(git_repo)

        assert str(git_repo.resolve()) not in repo_link.list_links()
        for name in ("post-merge", "post-checkout", "post-rewrite", "pre-push"):
            assert not (git_repo / ".git" / "hooks" / name).exists()
        # .dhee/ stays — it's git-tracked content
        assert (git_repo / ".dhee" / "config.json").is_file()


# ---------------------------------------------------------------------------
# Entry management — promote / demote / tombstone
# ---------------------------------------------------------------------------


class _FakeMemoryDB:
    """Tiny in-memory stand-in for memory.db.update_memory."""

    def __init__(self) -> None:
        self.updates: List[Dict[str, Any]] = []

    def update_memory(self, mem_id: str, payload: Dict[str, Any]) -> None:
        self.updates.append({"id": mem_id, "payload": payload})


class _FakeMemory:
    """Stand-in for the Memory facade used by promote/demote.

    Stores records in a dict; ``add`` returns the standard
    ``{"results": [{"id": ..}]}`` shape; ``get`` returns the record.
    """

    def __init__(self, records: Dict[str, Dict[str, Any]] | None = None) -> None:
        self.records = dict(records or {})
        self.added: List[Dict[str, Any]] = []
        self.db = _FakeMemoryDB()

    def get(self, memory_id: str) -> Dict[str, Any] | None:
        return self.records.get(memory_id)

    def add(
        self,
        *,
        messages,
        user_id: str = "default",
        metadata=None,
        infer: bool = True,
        **_: Any,
    ) -> Dict[str, Any]:
        new_id = f"mem-{len(self.records) + 1:04d}"
        rec = {"id": new_id, "memory": messages, "metadata": dict(metadata or {})}
        self.records[new_id] = rec
        self.added.append(rec)
        return {"results": [{"id": new_id}]}


class TestPromoteDemote:
    def test_promote_writes_repo_entry(self, isolated_home, git_repo, monkeypatch):
        from dhee import repo_link

        repo_link.link(git_repo)
        memory = _FakeMemory(
            {"m1": {"id": "m1", "memory": "always run pytest -q before push", "metadata": {}}}
        )

        # Simulate cwd inside the linked repo so promote() picks it.
        monkeypatch.chdir(git_repo)
        entry, repo_root = repo_link.promote("m1", memory=memory)

        assert repo_root == git_repo.resolve()
        assert entry.kind == "learning"
        assert "pytest -q" in entry.content
        assert entry.source_memory_id == "m1"

        # Persisted to entries.jsonl
        jsonl = (git_repo / ".dhee" / "context" / "entries.jsonl").read_text()
        assert entry.id in jsonl
        # Manifest updated
        manifest = json.loads(
            (git_repo / ".dhee" / "context" / "manifest.json").read_text()
        )
        assert manifest["entry_count"] == 1
        # Personal memory annotated with promotion record
        assert memory.db.updates, "personal memory should be annotated"
        update = memory.db.updates[-1]["payload"]["metadata"]
        promoted = update["promoted_to"]
        assert promoted and promoted[-1]["entry_id"] == entry.id

    def test_promote_explicit_repo_argument(self, isolated_home, two_git_repos):
        from dhee import repo_link

        repo_a, repo_b = two_git_repos
        repo_link.link(repo_a)
        repo_link.link(repo_b)
        memory = _FakeMemory(
            {"m1": {"id": "m1", "memory": "shared learning", "metadata": {}}}
        )

        entry, root = repo_link.promote("m1", memory=memory, repo=repo_b)
        assert root == repo_b.resolve()
        b_entries = repo_link.list_entries(repo_b)
        a_entries = repo_link.list_entries(repo_a)
        assert [e.id for e in b_entries] == [entry.id]
        assert a_entries == []

    def test_demote_creates_personal_memory(self, isolated_home, git_repo, monkeypatch):
        from dhee import repo_link

        repo_link.link(git_repo)
        memory = _FakeMemory()
        entry = repo_link.add_entry(
            git_repo,
            kind="decision",
            title="prefer postgres for tenant store",
            content="we tried mysql; postgres won on JSONB ergonomics",
        )

        monkeypatch.chdir(git_repo)
        new_id, demoted = repo_link.demote(entry.id, memory=memory)

        assert new_id, "demote should return new personal memory id"
        assert demoted.id == entry.id
        assert memory.added
        added_meta = memory.added[-1]["metadata"]
        assert added_meta["demoted_from_repo"] == str(git_repo.resolve())
        assert added_meta["demoted_from_entry"] == entry.id
        # The entry remains in the repo
        assert repo_link.get_entry(git_repo, entry.id) is not None

    def test_promote_unknown_memory_raises(self, isolated_home, git_repo, monkeypatch):
        from dhee import repo_link

        repo_link.link(git_repo)
        memory = _FakeMemory()
        monkeypatch.chdir(git_repo)
        with pytest.raises(ValueError):
            repo_link.promote("nope", memory=memory)

    def test_promote_without_link_raises(self, isolated_home, tmp_path, monkeypatch):
        from dhee import repo_link

        # Not under a linked repo and no --repo passed.
        monkeypatch.chdir(tmp_path)
        memory = _FakeMemory({"m1": {"id": "m1", "memory": "x"}})
        with pytest.raises(ValueError):
            repo_link.promote("m1", memory=memory)

    def test_tombstone_hides_entry_but_keeps_history(self, isolated_home, git_repo):
        from dhee import repo_link

        repo_link.link(git_repo)
        e = repo_link.add_entry(
            git_repo, kind="learning", title="t", content="body"
        )
        assert [x.id for x in repo_link.list_entries(git_repo)] == [e.id]

        repo_link.tombstone_entry(git_repo, e.id)
        assert repo_link.list_entries(git_repo) == []
        # Tombstone is preserved when explicitly requested
        all_entries = repo_link.list_entries(git_repo, include_deleted=True)
        assert [x.id for x in all_entries] == [e.id]
        assert all_entries[0].deleted

    def test_concurrent_same_entry_updates_surface_conflict(self, isolated_home, git_repo):
        from dhee import repo_link

        repo_link.link(git_repo)
        base = repo_link.add_entry(
            git_repo, kind="decision", title="deploy policy", content="ship on green"
        )
        base_hash = base.to_json()["content_hash"]
        path = git_repo / ".dhee" / "context" / "entries.jsonl"
        variants = [
            repo_link.Entry(
                id=base.id,
                kind="decision",
                title="deploy policy",
                content="ship after canary",
                created_at=base.created_at,
                updated_at="2026-04-27T10:00:00+00:00",
                created_by="dev-a",
                parent_hash=base_hash,
            ),
            repo_link.Entry(
                id=base.id,
                kind="decision",
                title="deploy policy",
                content="ship after manual approval",
                created_at=base.created_at,
                updated_at="2026-04-27T10:00:01+00:00",
                created_by="dev-b",
                parent_hash=base_hash,
            ),
        ]
        with path.open("a", encoding="utf-8") as fh:
            for entry in variants:
                fh.write(json.dumps(entry.to_json()) + "\n")

        manifest = repo_link.refresh(repo=git_repo)[0]["manifest"]
        conflicts = repo_link.detect_conflicts(git_repo)
        entries = repo_link.list_entries(git_repo)

        assert manifest["conflicts"] == 1
        assert conflicts and conflicts[0]["entry_id"] == base.id
        assert entries[0].meta["dhee_conflict"]["head_count"] == 2
        assert repo_link.check(repo=git_repo)["ok"] is False


# ---------------------------------------------------------------------------
# Refresh — simulates teammate pushing new entries that we git-pull
# ---------------------------------------------------------------------------


class TestRefresh:
    def test_refresh_picks_up_appended_entries(self, isolated_home, git_repo):
        from dhee import repo_link

        repo_link.link(git_repo)
        # Simulate teammate pushing two new entries that we just pulled:
        # they're appended to entries.jsonl by an external git merge.
        path = git_repo / ".dhee" / "context" / "entries.jsonl"
        external_entries = [
            {
                "id": "ext-1",
                "kind": "decision",
                "title": "use redis streams",
                "content": "team voted Tuesday",
                "created_at": "2026-04-27T10:00:00+00:00",
                "created_by": "teammate-a",
                "meta": {},
                "source_memory_id": None,
                "deleted": False,
            },
            {
                "id": "ext-2",
                "kind": "learning",
                "title": "rate limit fix",
                "content": "headers must be lowercase on outbound",
                "created_at": "2026-04-27T11:00:00+00:00",
                "created_by": "teammate-b",
                "meta": {},
                "source_memory_id": None,
                "deleted": False,
            },
        ]
        with path.open("a", encoding="utf-8") as fh:
            for raw in external_entries:
                fh.write(json.dumps(raw) + "\n")

        results = repo_link.refresh(repo=git_repo)
        assert results
        manifest = results[0]["manifest"]
        assert manifest["entry_count"] == 2

        ids = {e.id for e in repo_link.list_entries(git_repo)}
        assert ids == {"ext-1", "ext-2"}

    def test_refresh_all_walks_every_link(self, isolated_home, two_git_repos):
        from dhee import repo_link

        for repo in two_git_repos:
            repo_link.link(repo)
            repo_link.add_entry(
                repo, kind="learning", title=f"hello {repo.name}", content="x"
            )

        all_results = repo_link.refresh()
        roots = {r["repo_root"] for r in all_results}
        assert roots == {str(p.resolve()) for p in two_git_repos}


# ---------------------------------------------------------------------------
# Read-time fusion
# ---------------------------------------------------------------------------


class TestFusion:
    def test_search_entries_keyword_rank(self, isolated_home, git_repo, monkeypatch):
        from dhee import repo_link

        repo_link.link(git_repo)
        repo_link.add_entry(
            git_repo,
            kind="learning",
            title="rate limit headers",
            content="Outbound rate limit headers must be lowercase",
        )
        repo_link.add_entry(
            git_repo,
            kind="learning",
            title="postgres jsonb",
            content="Use postgres JSONB indexes for tenant config",
        )
        monkeypatch.chdir(git_repo)

        hits = repo_link.search_entries("rate limit", cwd=git_repo)
        assert hits, "should find rate-limit entry"
        assert "rate" in (hits[0]["title"] or hits[0]["memory"]).lower()
        assert hits[0]["source"] == "repo"

    def test_search_entries_outside_linked_repo_returns_empty(
        self, isolated_home, tmp_path
    ):
        from dhee import repo_link

        loose = tmp_path / "elsewhere"
        loose.mkdir()
        assert repo_link.search_entries("anything", cwd=loose) == []

    def test_fuse_personal_and_repo_results(self, isolated_home, git_repo, monkeypatch):
        from dhee import repo_link

        repo_link.link(git_repo)
        repo_link.add_entry(
            git_repo,
            kind="learning",
            title="redis streams",
            content="prefer redis streams for fanout",
        )
        monkeypatch.chdir(git_repo)

        personal = [
            {"id": "p1", "memory": "redis stream consumer config", "score": 0.4}
        ]
        merged = repo_link.fuse_search_results("redis streams", personal, cwd=git_repo)
        sources = [m["source"] for m in merged]
        assert "personal" in sources and "repo" in sources

    def test_fuse_tiebreak_prefers_personal(self, isolated_home, git_repo, monkeypatch):
        from dhee import repo_link

        repo_link.link(git_repo)
        # One match for "alpha" — score is 1 + 0.05 = 1.05.
        repo_link.add_entry(
            git_repo, kind="learning", title="alpha", content="x"
        )
        monkeypatch.chdir(git_repo)

        # Personal hit with the same composite_score → tiebreak should
        # put personal first.
        personal = [{"id": "p1", "memory": "alpha note", "composite_score": 1.05}]
        merged = repo_link.fuse_search_results("alpha", personal, cwd=git_repo)
        assert merged[0]["source"] == "personal"


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


class TestCLI:
    def test_cmd_link_creates_skeleton(self, isolated_home, git_repo):
        import argparse

        from dhee import cli

        ns = argparse.Namespace(path=str(git_repo), json=True)
        cli.cmd_link(ns)
        assert (git_repo / ".dhee" / "config.json").is_file()

    def test_cmd_context_refresh_quiet(self, isolated_home, git_repo, capsys):
        import argparse

        from dhee import cli, repo_link

        repo_link.link(git_repo)
        ns = argparse.Namespace(
            context_action="refresh",
            entry_id=None,
            repo=str(git_repo),
            quiet=True,
            json=False,
        )
        cli.cmd_context(ns)
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_cmd_promote_then_context_list(
        self, isolated_home, git_repo, capsys, monkeypatch
    ):
        import argparse

        from dhee import cli, repo_link

        repo_link.link(git_repo)
        # Add a deterministic entry directly to dodge the real Memory facade.
        repo_link.add_entry(
            git_repo, kind="learning", title="alpha", content="alpha"
        )
        monkeypatch.chdir(git_repo)

        ns = argparse.Namespace(
            context_action="list",
            entry_id=None,
            repo=str(git_repo),
            quiet=False,
            json=False,
        )
        cli.cmd_context(ns)
        captured = capsys.readouterr()
        assert "alpha" in captured.out
