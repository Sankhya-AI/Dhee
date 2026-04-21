"""dhee CLI — cognition layer for AI agents.

Usage:
    dhee setup              Interactive setup wizard
    dhee add "text"         Add a memory
    dhee search "query"     Search memories
    dhee list               List all memories
    dhee stats              Memory statistics
    dhee decay              Apply forgetting
    dhee categories         List categories
    dhee export             Export to JSON or .dheemem
    dhee import <file>      Import from JSON or .dheemem
    dhee why <id>           Explain why a memory or artifact exists
    dhee handoff            Emit structured resume JSON for a new harness/agent
    dhee harness status     Show native Claude Code / Codex integration state
    dhee benchmark          Run performance benchmarks
    dhee status             Version, config, DB info
"""

import argparse
import json
import os
import shutil
import sys
from typing import Any, Dict, Optional


def _json_out(data: Any) -> None:
    """Print data as formatted JSON."""
    print(json.dumps(data, indent=2, default=str))


def _get_memory(config: Optional[Dict] = None):
    """Lazy-load a Memory instance from CLI config."""
    from dhee.cli_config import get_memory_instance
    return get_memory_instance(config)


def _get_db():
    """Direct DB handle for commands that should not require model credentials."""
    from dhee.cli_config import CONFIG_DIR
    from dhee.db.sqlite import SQLiteManager

    return SQLiteManager(os.path.join(CONFIG_DIR, "history.db"))


def _get_vector_store():
    """Direct vector-store handle for commands that should not require model credentials."""
    from dhee.cli_config import CONFIG_DIR, PROVIDER_DEFAULTS, load_config
    from dhee.vector_stores.sqlite_vec import SqliteVecStore

    config = load_config()
    provider = config.get("provider", "gemini")
    defaults = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["gemini"])
    embedding_dims = config.get("embedding_dims", defaults["embedding_dims"])
    return SqliteVecStore(
        {
            "path": os.path.join(CONFIG_DIR, "sqlite_vec.db"),
            "collection_name": "dhee_memories",
            "embedding_model_dims": embedding_dims,
        }
    )


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def cmd_setup(args: argparse.Namespace) -> None:
    """Run interactive setup wizard."""
    from dhee.cli_setup import run_setup
    run_setup()


def cmd_add(args: argparse.Namespace) -> None:
    """Add a memory."""
    memory = _get_memory()
    result = memory.add(
        messages=args.text,
        user_id=args.user_id,
        infer=False,
    )
    if args.json:
        _json_out(result)
    else:
        mem_id = None
        if isinstance(result, dict):
            results = result.get("results", [])
            if results:
                mem_id = results[0].get("id")
        if mem_id:
            print(f"Added memory: {mem_id}")
        else:
            print("Memory added.")


def cmd_search(args: argparse.Namespace) -> None:
    """Search memories."""
    memory = _get_memory()
    result = memory.search(
        query=args.query,
        user_id=args.user_id,
        limit=args.limit,
    )
    if args.json:
        _json_out(result)
    else:
        results = result.get("results", [])
        if not results:
            print("No results found.")
            return
        for r in results:
            score = r.get("composite_score", r.get("score", 0))
            layer = r.get("layer", "sml")
            mem = r.get("memory", r.get("details", ""))
            mid = r.get("id", "")[:8]
            print(f"  [{mid}] ({layer}, {score:.3f}) {mem}")
        print(f"\n  {len(results)} result(s)")


def cmd_list(args: argparse.Namespace) -> None:
    """List all memories."""
    memory = _get_memory()
    result = memory.get_all(
        user_id=args.user_id,
        limit=args.limit,
        layer=args.layer,
    )
    if args.json:
        _json_out(result)
    else:
        results = result.get("results", [])
        if not results:
            print("No memories found.")
            return
        for r in results:
            strength = r.get("strength", 1.0)
            layer = r.get("layer", "sml")
            mem = r.get("memory", "")
            mid = r.get("id", "")[:8]
            cats = r.get("categories", [])
            cat_str = f" [{', '.join(cats)}]" if cats else ""
            print(f"  [{mid}] ({layer}, s={strength:.3f}){cat_str} {mem}")
        print(f"\n  {len(results)} memor{'y' if len(results) == 1 else 'ies'}")


def cmd_stats(args: argparse.Namespace) -> None:
    """Show memory statistics."""
    memory = _get_memory()
    result = memory.get_stats(user_id=args.user_id)
    if args.json:
        _json_out(result)
    else:
        for key, value in result.items():
            print(f"  {key}: {value}")


def cmd_decay(args: argparse.Namespace) -> None:
    """Apply forgetting/decay."""
    memory = _get_memory()
    scope = None
    if args.user_id != "default":
        scope = {"user_id": args.user_id}
    result = memory.apply_decay(scope=scope)
    if args.json:
        _json_out(result)
    else:
        forgotten = result.get("forgotten", 0)
        promoted = result.get("promoted", 0)
        print(f"  Decay applied. Forgotten: {forgotten}, Promoted: {promoted}")


def cmd_categories(args: argparse.Namespace) -> None:
    """List categories."""
    memory = _get_memory()
    cats = memory.get_categories()
    if args.json:
        _json_out(cats)
    else:
        if not cats:
            print("No categories found.")
            return
        for c in cats:
            name = c.get("name", c.get("category", ""))
            count = c.get("memory_count", c.get("count", 0))
            strength = c.get("strength", 1.0)
            print(f"  {name} ({count} memories, s={strength:.3f})")


def cmd_export(args: argparse.Namespace) -> None:
    """Export all memories to JSON or `.dheemem`."""
    from dhee.cli_config import CONFIG_DIR
    from dhee.protocol import PACK_EXTENSION, export_pack

    output = args.output
    pack_mode = args.format == "dheemem" or (output and str(output).endswith(PACK_EXTENSION))
    db = _get_db()
    if pack_mode:
        if not output:
            raise RuntimeError("Pack export requires --output ending in .dheemem")
        vector_store = _get_vector_store()
        try:
            result = export_pack(
                db=db,
                vector_store=vector_store,
                output_path=output,
                user_id=getattr(args, "user_id", "default"),
                key_dir=CONFIG_DIR,
            )
        finally:
            vector_store.close()
        if args.json:
            _json_out(result)
        else:
            counts = result.get("counts", {})
            print(
                f"Exported {counts.get('memories', 0)} memories, "
                f"{counts.get('vectors', 0)} vector nodes, "
                f"and {counts.get('artifacts_manifest', 0)} artifacts to {output}"
            )
        return

    from dhee.core.artifacts import ArtifactManager

    memories = db.get_all_memories(user_id=getattr(args, "user_id", "default"), limit=10000)
    artifacts = ArtifactManager(db).export_payload(user_id=getattr(args, "user_id", "default"))
    data = {"version": "2", "memories": memories, **artifacts}
    if output:
        with open(output, "w") as f:
            json.dump(data, f, indent=2, default=str)
            f.write("\n")
        print(
            f"Exported {len(memories)} memories and "
            f"{len(artifacts.get('artifacts_manifest', []))} artifacts to {output}"
        )
        return
    _json_out(data)


def cmd_import(args: argparse.Namespace) -> None:
    """Import memories from JSON or `.dheemem`."""
    from dhee.protocol import PACK_EXTENSION, import_pack

    if args.format == "dheemem" or str(args.file).endswith(PACK_EXTENSION):
        db = _get_db()
        vector_store = _get_vector_store()
        try:
            result = import_pack(
                db=db,
                vector_store=vector_store,
                input_path=args.file,
                user_id=args.user_id,
                strategy=args.strategy,
            )
        finally:
            vector_store.close()
        if args.json:
            _json_out(result)
        else:
            if args.strategy == "dry-run":
                print(
                    f"Pack preview: {result.get('memories', 0)} memories, "
                    f"{result.get('vectors', 0)} vectors, "
                    f"{result.get('artifacts', 0)} artifacts "
                    f"({result.get('existing_ids', 0)} existing IDs, "
                    f"{result.get('existing_hashes', 0)} existing hashes)."
                )
            else:
                mem_stats = result.get("memory_import", {})
                art_stats = result.get("artifact_import", {})
                print(
                    f"Imported {mem_stats.get('imported', 0)} memories, "
                    f"{result.get('vectors_imported', 0)} vector nodes, "
                    f"and {art_stats.get('artifacts', 0)} artifacts."
                )
        return

    from dhee.core.artifacts import ArtifactManager

    with open(args.file, "r") as f:
        data = json.load(f)
    memories = data.get("memories", [])
    memory = _get_memory()
    count = 0
    for m in memories:
        content = m.get("memory", m.get("content", ""))
        if content:
            memory.add(
                messages=content,
                user_id=args.user_id,
                categories=m.get("categories"),
                infer=False,
            )
            count += 1
    artifact_counts = ArtifactManager(memory.db, engram=memory).import_payload(
        data,
        user_id=args.user_id,
    )
    if args.json:
        _json_out({"imported": count, "artifacts": artifact_counts})
    else:
        if not memories and not any(artifact_counts.values()):
            print("No memories or artifacts found in file.")
            return
        print(
            f"Imported {count} memories, "
            f"{artifact_counts.get('artifacts', 0)} artifacts, "
            f"{artifact_counts.get('chunks', 0)} chunks."
        )


def cmd_why(args: argparse.Namespace) -> None:
    """Explain provenance for a memory or artifact."""
    from dhee.core.provenance import explain_identifier

    db = _get_db()
    result = explain_identifier(
        db,
        args.identifier,
        history_limit=args.history_limit,
        include_extraction_text=args.include_extraction_text,
        include_chunks=args.include_chunks,
        chunk_limit=args.chunk_limit,
        max_text_chars=args.max_text_chars,
    )
    if args.json:
        _json_out(result)
        return
    if result.get("error"):
        raise ValueError(result["error"])

    if result.get("kind") == "artifact":
        print(f"Artifact: {result.get('artifact_id')}")
        print(f"  File: {result.get('filename')} ({result.get('mime_type')})")
        print(f"  State: {result.get('lifecycle_state')}")
        print(f"  Bindings: {len(result.get('bindings', []))}")
        print(f"  Extractions: {len(result.get('extractions', []))}")
        print(f"  Chunks: {result.get('chunk_count', 0)}")
        for binding in result.get("bindings", [])[:3]:
            print(
                f"  Bound: {binding.get('source_path')} "
                f"[workspace={binding.get('workspace_id')}, folder={binding.get('folder_path')}]"
            )
        return

    print(f"Memory: {result.get('memory_id')}")
    print(f"  Content: {result.get('memory')}")
    print(
        f"  Source: {result.get('source_type')} / {result.get('source_app')} "
        f"[event={result.get('source_event_id')}]"
    )
    print(
        f"  Layer: {result.get('layer')}  Strength: {result.get('strength')}  "
        f"Type: {result.get('memory_type')}"
    )
    if result.get("artifact"):
        artifact = result["artifact"]
        print(
            f"  Artifact: {artifact.get('filename')} "
            f"[id={artifact.get('artifact_id')}, state={artifact.get('lifecycle_state')}]"
        )
    distillation = result.get("distillation", {})
    if distillation.get("source_count"):
        print(f"  Distilled from: {distillation.get('source_count')} episodic memories")
    history = result.get("history", [])
    if history:
        print("  Recent history:")
        for row in history[: args.history_limit]:
            print(
                f"    - {row.get('timestamp')} {row.get('event')} "
                f"{(row.get('new_value') or row.get('old_value') or '')[:80]}"
            )
    warnings = result.get("warnings", [])
    for warning in warnings:
        print(f"  Warning: {warning}")


def cmd_handoff(args: argparse.Namespace) -> None:
    """Emit a structured handoff snapshot."""
    from dhee.core.handoff_snapshot import build_handoff_snapshot

    db = _get_db()
    repo = os.path.abspath(os.path.expanduser(args.repo)) if getattr(args, "repo", None) else os.getcwd()
    snapshot = build_handoff_snapshot(
        db,
        user_id=args.user_id,
        repo=repo,
        workspace_id=repo,
        thread_id=getattr(args, "thread_id", None),
        memory_limit=args.memory_limit,
        artifact_limit=args.artifact_limit,
        task_limit=args.task_limit,
        intention_limit=args.intention_limit,
    )
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, indent=2, default=str)
            handle.write("\n")
        if not args.json:
            print(f"Wrote handoff snapshot to {args.output}")
            return
    _json_out(snapshot)


def cmd_thread_state(args: argparse.Namespace) -> None:
    """Read, update, or clear thread-native continuity state."""
    db = _get_db()
    repo = (
        os.path.abspath(os.path.expanduser(args.repo))
        if getattr(args, "repo", None)
        else None
    )

    if args.clear:
        removed = db.delete_thread_state(user_id=args.user_id, thread_id=args.thread_id)
        result = {"thread_id": args.thread_id, "deleted": bool(removed)}
        if args.json:
            _json_out(result)
        else:
            print("Deleted thread state." if removed else "No thread state found.")
        return

    update_fields = {
        "repo": repo,
        "workspace_id": args.workspace_id,
        "folder_path": args.folder_path,
        "status": args.status,
        "summary": args.summary,
        "current_goal": args.goal,
        "current_step": args.step,
        "session_id": args.session_id,
        "handoff_session_id": args.handoff_session_id,
    }
    metadata = {}
    if args.metadata:
        metadata = json.loads(args.metadata)
    should_update = any(value is not None for value in update_fields.values()) or bool(metadata)

    if should_update:
        state = db.upsert_thread_state(
            {
                "user_id": args.user_id,
                "thread_id": args.thread_id,
                "repo": repo,
                "workspace_id": args.workspace_id or repo,
                "folder_path": args.folder_path,
                "status": args.status or "active",
                "summary": args.summary,
                "current_goal": args.goal,
                "current_step": args.step,
                "session_id": args.session_id,
                "handoff_session_id": args.handoff_session_id,
                "metadata": metadata,
            }
        )
    else:
        state = db.get_thread_state(user_id=args.user_id, thread_id=args.thread_id)

    if args.json or state is None:
        _json_out(state or {"thread_id": args.thread_id, "status": "not_found"})
    else:
        print(f"Thread: {state.get('thread_id')}")
        print(f"  Status: {state.get('status')}")
        print(f"  Summary: {state.get('summary') or '(none)'}")
        print(f"  Goal: {state.get('current_goal') or '(none)'}")
        print(f"  Step: {state.get('current_step') or '(none)'}")
        print(f"  Updated: {state.get('updated_at')}")


def cmd_shared_task(args: argparse.Namespace) -> None:
    """Manage repo-scoped shared collaboration tasks."""
    from dhee.core.shared_tasks import resolve_active_shared_task

    db = _get_db()
    repo = (
        os.path.abspath(os.path.expanduser(args.repo))
        if getattr(args, "repo", None)
        else os.getcwd()
    )
    metadata = {}
    if getattr(args, "metadata", None):
        metadata = json.loads(args.metadata)

    if args.shared_action == "start":
        if not args.title:
            raise ValueError("shared-task start requires --title")
        task = db.upsert_shared_task(
            {
                "id": getattr(args, "shared_task_id", None),
                "user_id": args.user_id,
                "repo": repo,
                "workspace_id": args.workspace_id or repo,
                "folder_path": args.folder_path,
                "title": args.title,
                "status": "active",
                "created_by": "dhee-cli",
                "metadata": metadata,
            }
        )
        if args.json:
            _json_out(task)
        else:
            print(f"Shared task active: {task.get('title')} [{task.get('id')}]")
        return

    if args.shared_action == "list":
        rows = db.list_shared_tasks(user_id=args.user_id, repo=repo if args.repo else None, limit=args.limit)
        if args.json:
            _json_out({"count": len(rows), "results": rows})
        else:
            for row in rows:
                print(
                    f"[{row.get('id')}] {row.get('title')} "
                    f"(status={row.get('status')}, repo={row.get('repo')})"
                )
        return

    task = resolve_active_shared_task(
        db,
        user_id=args.user_id,
        shared_task_id=getattr(args, "shared_task_id", None),
        repo=repo if args.repo or getattr(args, "shared_task_id", None) else os.getcwd(),
        cwd=repo,
    )
    if not task:
        if args.json:
            _json_out({"status": "not_found"})
        else:
            print("No active shared task found.")
        return

    if args.shared_action == "close":
        closed = db.close_shared_task(
            str(task["id"]),
            user_id=args.user_id,
            status="completed",
            prune_results=not args.keep_results,
        )
        result = {
            "shared_task_id": task["id"],
            "closed": bool(closed),
            "kept_results": bool(args.keep_results),
        }
        if args.json:
            _json_out(result)
        else:
            print(f"Closed shared task {task.get('title')} [{task.get('id')}]")
        return

    if args.shared_action == "results":
        rows = db.list_shared_task_results(
            shared_task_id=str(task["id"]),
            limit=args.limit,
            result_status=args.result_status,
            packet_kind=args.packet_kind,
        )
        if args.json:
            _json_out({"shared_task": task, "count": len(rows), "results": rows})
        else:
            print(f"Shared task: {task.get('title')} [{task.get('id')}]")
            for row in rows:
                print(
                    f"  [{row.get('result_status')}] {row.get('tool_name')} "
                    f"{row.get('source_path') or ''} :: {(row.get('digest') or '')[:140]}"
                )
        return

    if args.json:
        _json_out(task)
    else:
        print(f"Shared task: {task.get('title')} [{task.get('id')}]")
        print(f"  Status: {task.get('status')}")
        print(f"  Repo: {task.get('repo')}")
        print(f"  Updated: {task.get('updated_at')}")


def cmd_checkpoint(args: argparse.Namespace) -> None:
    """Save session state and learnings (checkpoint)."""
    from dhee.core.buddhi import Buddhi

    buddhi = Buddhi()

    result: dict = {}

    # Save session digest if engram-bus is available
    try:
        from dhee.core.kernel import save_session_digest
        digest = save_session_digest(
            task_summary=args.summary,
            agent_id="dhee-cli",
            status="paused",
        )
        result["session_saved"] = True
        result["session_id"] = digest.get("session_id")
    except Exception:
        result["session_saved"] = False

    # Record outcome if provided
    if args.task_type and args.outcome_score is not None:
        buddhi.record_outcome(
            user_id=args.user_id,
            task_type=args.task_type,
            score=max(0.0, min(1.0, args.outcome_score)),
        )
        result["outcome_recorded"] = True

    # Reflect
    if args.what_worked or args.what_failed:
        insights = buddhi.reflect(
            user_id=args.user_id,
            task_type=args.task_type or "general",
            what_worked=args.what_worked,
            what_failed=args.what_failed,
        )
        result["insights_created"] = len(insights)

    # Store intention
    if args.remember_to:
        intention = buddhi.store_intention(
            user_id=args.user_id,
            description=args.remember_to,
        )
        result["intention_stored"] = intention.description

    if args.json:
        _json_out(result)
    else:
        print(f"  Checkpoint saved: {args.summary[:60]}")
        if result.get("session_saved"):
            print(f"  Session ID: {result.get('session_id', '')[:12]}...")
        if result.get("insights_created"):
            print(f"  Insights created: {result['insights_created']}")
        if result.get("intention_stored"):
            print(f"  Intention stored: {result['intention_stored'][:60]}")


def cmd_status(args: argparse.Namespace) -> None:
    """Show version, config, DB size, detected agents."""
    from dhee import __version__
    from dhee.cli_config import CONFIG_DIR, CONFIG_PATH, load_config
    from dhee.cli_mcp import detect_agents
    from dhee.harness.install import harness_status

    config = load_config()
    provider = config.get("provider", "not configured")
    packages = config.get("packages", [])
    native_harnesses = harness_status(harness="all")
    vec_db = os.path.join(CONFIG_DIR, "sqlite_vec.db")
    hist_db = os.path.join(CONFIG_DIR, "history.db")
    agents = detect_agents()

    db_sizes: Dict[str, Any] = {}
    for label, path in [("vector_db", vec_db), ("history_db", hist_db)]:
        if os.path.exists(path):
            db_sizes[label] = {
                "path": path,
                "bytes": os.path.getsize(path),
            }
        else:
            db_sizes[label] = None

    if args.json:
        _json_out({
            "version": __version__,
            "provider": provider,
            "packages": packages,
            "config_path": CONFIG_PATH,
            "agents": agents,
            "native_harnesses": native_harnesses,
            "db_sizes": db_sizes,
        })
        return

    print(f"  dhee v{__version__}")
    print(f"  Provider: {provider}")
    print(f"  Packages: {', '.join(packages) if packages else 'none'}")
    print(f"  Config:   {CONFIG_PATH}")

    # DB sizes
    for label, path in [("Vector DB", vec_db), ("History DB", hist_db)]:
        if os.path.exists(path):
            size = os.path.getsize(path)
            if size > 1_000_000:
                print(f"  {label}:  {path} ({size / 1_000_000:.1f} MB)")
            else:
                print(f"  {label}:  {path} ({size / 1_000:.1f} KB)")
        else:
            print(f"  {label}:  not created yet")

    # Detected agents
    if agents:
        print(f"  Agents:   {', '.join(agents)}")
    else:
        print("  Agents:   none detected")

    print("  Native harnesses:")
    for name, state in native_harnesses.items():
        label = "Claude Code" if name == "claude_code" else "Codex"
        enabled = "on" if state.get("enabled_in_config") else "off"
        bound = "ready" if state.get("mcp_registered") else "not configured"
        print(f"    {label}: {enabled} ({bound})")


def cmd_doctor(args: argparse.Namespace) -> None:
    """Composite observability report. No controls, just truth."""
    from dhee.doctor import run

    sys.stdout.write(run(as_json=bool(getattr(args, "json", False))))


def cmd_uninstall(args: argparse.Namespace) -> None:
    """Remove ~/.dhee data directory."""
    from dhee.cli_config import CONFIG_DIR
    if not os.path.exists(CONFIG_DIR):
        print("Nothing to remove.")
        return
    confirm = input(f"Remove {CONFIG_DIR}? [y/N]: ").strip().lower()
    if confirm in ("y", "yes"):
        shutil.rmtree(CONFIG_DIR)
        print(f"Removed {CONFIG_DIR}")
    else:
        print("Cancelled.")


def cmd_task(args: argparse.Namespace) -> None:
    """Start Claude Code with Dhee cognition hooks."""
    from dhee.hooks.claude_code.install import ensure_installed

    result = ensure_installed()
    if result.already_installed:
        pass  # hooks already in place
    elif result.created or result.updated:
        print(f"  Dhee hooks installed → {result.settings_path}")

    # Find claude executable
    claude_bin = shutil.which("claude")
    if not claude_bin:
        print("Error: 'claude' not found in PATH. Install Claude Code first.", file=sys.stderr)
        sys.exit(1)

    # Build command
    cmd = [claude_bin]
    if args.print_mode:
        cmd.append("--print")
    if args.description:
        cmd.append(args.description)

    # Replace current process with claude
    os.execvp(claude_bin, cmd)


def cmd_install_hooks(args: argparse.Namespace) -> None:
    """Native Dhee install for Claude Code, Codex, and/or gstack."""
    from dhee.harness.install import install_harnesses

    positional = (getattr(args, "target", None) or "").strip().lower()
    flag_value = getattr(args, "harness", None)
    if positional:
        harness = positional
    elif flag_value:
        harness = flag_value
    else:
        harness = "all"
    enable_router = not getattr(args, "no_router", False)
    results = install_harnesses(
        harness=harness,
        enable_router=enable_router,
    )

    labels = {"claude_code": "Claude Code", "codex": "Codex", "gstack": "gstack"}
    for name, result in results.items():
        label = labels.get(name, name)
        print(f"  {label}: {result.action}")
        if result.path:
            print(f"    path: {result.path}")
        for key, value in result.details.items():
            print(f"    {key}: {value}")
    print("")
    print("  Dhee is now configured as the native memory/router layer for the selected harnesses.")
    print("  Inspect status:       dhee harness status")
    print("  Disable a harness:    dhee harness disable --harness codex")


def cmd_uninstall_hooks(args: argparse.Namespace) -> None:
    """Backward-compatible alias: disable Claude Code integration."""
    from dhee.harness.install import disable_harnesses

    result = disable_harnesses(harness="claude_code")["claude_code"]
    if result.changed:
        print("  Claude Code integration removed.")
    else:
        print("  Claude Code integration was already disabled.")


def cmd_harness(args: argparse.Namespace) -> None:
    """Inspect or change native harness integration state."""
    from dhee.harness.install import disable_harnesses, harness_status, install_harnesses

    action = str(args.harness_action or "status")
    harness = getattr(args, "harness", "all")
    if action == "status":
        _json_out(harness_status(harness=harness)) if args.json else print(
            json.dumps(harness_status(harness=harness), indent=2)
        )
        return

    if action == "enable":
        results = install_harnesses(
            harness=harness,
            enable_router=not getattr(args, "no_router", False),
        )
    else:
        results = disable_harnesses(harness=harness)

    if args.json:
        _json_out(
            {
                name: {
                    "action": result.action,
                    "changed": result.changed,
                    "path": result.path,
                    "details": result.details,
                }
                for name, result in results.items()
            }
        )
        return
    for name, result in results.items():
        print(f"  {name}: {result.action} ({'changed' if result.changed else 'no-op'})")
        if result.path:
            print(f"    path: {result.path}")


def cmd_adapters(args: argparse.Namespace) -> None:
    """Inspect or refresh third-party memory adapters (currently: gstack)."""
    adapter = str(getattr(args, "adapter", "") or "").strip().lower()
    action = str(getattr(args, "adapter_action", None) or "status")

    if adapter != "gstack":
        print(f"Unsupported adapter: {adapter}", file=sys.stderr)
        sys.exit(2)

    from dhee.adapters import gstack as gstack_adapter

    if action == "status":
        info = gstack_adapter.status()
        if args.json:
            _json_out(info)
            return
        detected = info["detected"]
        print(f"  gstack installed: {detected['installed']}")
        if detected.get("version"):
            print(f"    version: {detected['version']}")
        print(f"    gstack_home: {detected['gstack_home']}")
        print(f"    projects_detected: {len(detected.get('projects') or [])}")
        print(f"    projects_tracked:  {len(info.get('projects_tracked') or [])}")
        print(f"    last_ingest_ts:    {info.get('last_ingest_ts') or '—'}")
        print(f"    manifest:          {info['manifest_path']}")
        return

    if action == "reingest":
        report = gstack_adapter.backfill(reset=bool(getattr(args, "reset", False)))
        if args.json:
            _json_out(report)
            return
        print(f"  atoms ingested:    {report.get('atoms_total', 0)}")
        print(f"    learnings:       {report.get('learnings_total', 0)}")
        print(f"    timeline:        {report.get('timeline_total', 0)}")
        print(f"    reviews:         {report.get('reviews_total', 0)}")
        print(f"    checkpoints:     {report.get('checkpoint_sections_total', 0)}")
        errors = report.get("errors") or []
        if errors:
            print(f"    errors:          {len(errors)}")
            for err in errors[:5]:
                print(f"      - {err}")
        return

    if action == "clear":
        removed = gstack_adapter.clear_manifest()
        if args.json:
            _json_out({"manifest_cleared": removed})
            return
        print("  gstack manifest cleared." if removed else "  gstack manifest already absent.")
        return


def cmd_purge_legacy_noise(args: argparse.Namespace) -> None:
    """Clean v3.3.0 hook noise from the Dhee vector store.

    v3.3.0 stored every successful Bash invocation verbatim as a memory.
    v3.3.1 no longer does. This command removes the legacy entries so
    recall stops surfacing shell-echo noise as "ground truth".
    """
    from dhee.hooks.claude_code.migrate import purge_legacy_noise

    result = purge_legacy_noise(dry_run=args.dry_run)
    if args.json:
        _json_out({
            "scanned": result.scanned,
            "removed": result.removed,
            "db_path": str(result.db_path) if result.db_path else None,
            "dry_run": args.dry_run,
            "skipped_reason": result.skipped_reason,
        })
        return
    if result.skipped_reason:
        print(f"  Skipped: {result.skipped_reason}")
        return
    verb = "Would remove" if args.dry_run else "Removed"
    print(f"  Scanned: {result.scanned} memories")
    print(f"  {verb}: {result.removed} legacy-noise entries")
    if result.db_path:
        print(f"  DB: {result.db_path}")


def cmd_ingest(args: argparse.Namespace) -> None:
    """Ingest markdown files into Dhee's vector store for selective retrieval."""
    from dhee import Dhee
    from dhee.hooks.claude_code.ingest import auto_ingest_project, ingest_file

    dhee = Dhee(auto_context=False, auto_checkpoint=False)

    if args.paths:
        for path in args.paths:
            result = ingest_file(dhee, path, force=args.force)
            if args.json:
                _json_out({
                    "path": result.source_path,
                    "stored": result.chunks_stored,
                    "deleted": result.chunks_deleted,
                    "skipped": result.skipped,
                    "reason": result.reason,
                })
            elif result.skipped:
                print(f"  {path}: unchanged (skip)")
            elif result.reason:
                print(f"  {path}: {result.reason}")
            else:
                print(f"  {path}: {result.chunks_stored} chunks stored" +
                      (f", {result.chunks_deleted} old removed" if result.chunks_deleted else ""))
    else:
        results = auto_ingest_project(dhee, args.root)
        if not results:
            print("  No standard docs found (CLAUDE.md, AGENTS.md, SKILL.md).")
            return
        for r in results:
            name = r.source_path.rsplit("/", 1)[-1]
            if r.skipped:
                print(f"  {name}: unchanged")
            elif r.reason:
                print(f"  {name}: {r.reason}")
            else:
                print(f"  {name}: {r.chunks_stored} chunks")


def cmd_portability_eval(args: argparse.Namespace) -> None:
    """Round-trip your Dhee state through a `.dheemem` pack and score it.

    The scorecard quantifies what `dhee export | dhee import` actually
    preserves: memories, history, provenance, artifacts, vectors, and
    the portable handoff snapshot. A substrate retention below the
    threshold (default 0.95) flags a portability regression.
    """
    from dhee.benchmarks.portability import run_portability_eval
    from dhee.cli_config import CONFIG_DIR

    db = _get_db()
    vector_store = _get_vector_store()
    try:
        scorecard = run_portability_eval(
            source_db=db,
            source_vector_store=vector_store,
            user_id=getattr(args, "user_id", "default"),
            key_dir=CONFIG_DIR,
            output_dir=getattr(args, "out_dir", None),
            retention_threshold=getattr(args, "threshold", 0.95),
        )
    finally:
        try:
            vector_store.close()
        except Exception:
            pass

    if args.json:
        _json_out(scorecard.to_dict())
        return

    print(f"Portability scorecard (user={scorecard.user_id})")
    print(f"  pack : {scorecard.pack_path}")
    for sub in scorecard.substrates:
        marker = "OK " if sub.retention >= 0.95 else "!! "
        print(
            f"  {marker}{sub.name:<26} "
            f"{sub.imported_count:>6} / {sub.source_count:<6} "
            f"retention={sub.retention:.3f}"
        )
    print(f"  handoff_survived = {scorecard.handoff_survived}")
    print(f"  passed           = {scorecard.passed}")
    if scorecard.notes:
        for note in scorecard.notes:
            print(f"  note: {note}")


def cmd_decades_eval(args: argparse.Namespace) -> None:
    """Run the Movement-3 longevity scorecard on a synthetic corpus.

    Writes N time-skewed facts (default 10000) with ~20% supersede
    rate across a simulated 3-year span, runs the consolidator, and
    scores three substrate invariants: canonical-tier rows never
    evicted, supersede chains stay explorable (live or archived), and
    recall-latency degradation at N vs 1k rows stays under 2×. This
    is the only place where synthetic data is honest — there is no
    live "3 years of usage" to measure against yet.
    """
    from dhee.benchmarks.decades import DecadesConfig, run_decades_eval

    cfg = DecadesConfig(
        total_events=getattr(args, "events", 10000),
        supersede_fraction=getattr(args, "supersede_fraction", 0.20),
        canonical_fraction=getattr(args, "canonical_fraction", 0.10),
        span_days=getattr(args, "span_days", 365 * 3),
        latency_samples=getattr(args, "latency_samples", 200),
        seed=getattr(args, "seed", 42),
    )
    scorecard = run_decades_eval(
        data_dir=getattr(args, "data_dir", None),
        config=cfg,
    )

    if args.json:
        _json_out(scorecard.to_dict())
        return

    print(
        f"Decades scorecard (events={scorecard.total_facts_written}, "
        f"span={cfg.span_days}d, seed={cfg.seed})"
    )
    print(
        f"  canonical_retention       = "
        f"{scorecard.canonical_retention:.3f} "
        f"({scorecard.canonical_retained_after_sweep}/"
        f"{scorecard.canonical_rows})"
    )
    print(
        f"  supersede_chain_integrity = "
        f"{scorecard.supersede_chain_integrity:.3f} "
        f"({scorecard.supersede_chains} chains, "
        f"{scorecard.archived_old_rows} old rows archived)"
    )
    print(
        f"  latency_1k_p50_ms         = {scorecard.latency_1k_p50_ms:.4f}"
    )
    print(
        f"  latency_10k_p50_ms        = {scorecard.latency_10k_p50_ms:.4f}"
    )
    print(
        f"  latency_degradation       = "
        f"{scorecard.latency_degradation:.2f}x"
    )
    print(f"  passed                    = {scorecard.passed}")
    for note in scorecard.notes:
        print(f"  note: {note}")


def cmd_replay_corpus(args: argparse.Namespace) -> None:
    """Export a replay-gate corpus from the durable samskara log.

    The corpus is what ``dhee.mini.replay_gate.ReplayGate`` scores
    candidate models against. Today each entry comes from a real
    ANSWER_ACCEPTED or ANSWER_CORRECTED signal — no synthetic data,
    no fake records. Empty log → empty corpus → the gate correctly
    reports ``insufficient_samples`` downstream.
    """
    from dhee.core.samskara import SamskaraCollector

    action = getattr(args, "replay_action", None) or "export"
    log_dir = getattr(args, "log_dir", None)
    collector = SamskaraCollector(log_dir=log_dir)

    if action == "export":
        out_dir = getattr(args, "out_dir", None) or os.path.join(
            os.path.expanduser("~"), ".dhee", "replay_corpus"
        )
        max_records = getattr(args, "max_records", None)
        summary = collector.export_replay_corpus(
            out_dir,
            max_records=max_records,
        )
        if args.json:
            _json_out(summary)
            return
        if summary["record_count"] == 0:
            print(
                "No replay corpus exported "
                f"(source log: {summary['source_log']})"
            )
            print("  Tip: corpus grows as users accept/correct answers.")
            return
        print(f"Exported {summary['record_count']} replay records:")
        print(f"  accepted : {summary['accepted_count']}")
        print(f"  corrected: {summary['corrected_count']}")
        print(f"  path     : {summary['path']}")
        return

    print(f"Unknown replay-corpus action: {action}")


def cmd_docs(args: argparse.Namespace) -> None:
    """Show what docs are ingested and available for selective retrieval."""
    from dhee.hooks.claude_code.ingest import get_manifest_summary

    summary = get_manifest_summary()
    if args.json:
        _json_out(summary)
        return
    if not summary["files"]:
        print("  No docs ingested. Run: dhee ingest")
        return
    print(f"  {summary['files']} file(s), {summary['total_chunks']} total chunks\n")
    for path, info in summary["entries"].items():
        name = path.rsplit("/", 1)[-1]
        print(f"  {name}: {info['chunks']} chunks (sha:{info['sha']})")


def cmd_assets(args: argparse.Namespace) -> None:
    """Inspect or sync host-parsed artifacts."""
    from dhee import Dhee
    from dhee.core.artifacts import ArtifactManager
    from dhee.core.codex_stream import find_latest_codex_log, sync_latest_codex_stream

    action = getattr(args, "assets_action", None)
    if action == "list":
        db = _get_db()
        workspace = None
        if getattr(args, "workspace", None):
            workspace = os.path.abspath(os.path.expanduser(args.workspace))
        rows = db.list_artifacts(
            user_id=args.user_id,
            workspace_id=workspace,
            limit=args.limit,
        )
        if args.json:
            _json_out(rows)
            return
        if not rows:
            print("No artifacts found.")
            return
        for row in rows:
            name = row.get("filename", "")
            state = row.get("lifecycle_state", "attached")
            source_hash = str(row.get("content_hash", ""))[:10]
            print(
                f"  [{row.get('artifact_id', '')[:8]}] {name} "
                f"({state}, extractions={row.get('extraction_count', 0)}, hash={source_hash})"
            )
        return

    if action == "show":
        if not args.artifact_id:
            raise ValueError("assets show requires an artifact_id")
        db = _get_db()
        artifact = db.get_artifact(args.artifact_id)
        if artifact is None:
            raise ValueError(f"Unknown artifact: {args.artifact_id}")
        if args.json:
            _json_out(artifact)
            return
        print(f"Artifact: {artifact.get('artifact_id')}")
        print(f"  File: {artifact.get('filename')} ({artifact.get('mime_type')})")
        print(f"  State: {artifact.get('lifecycle_state')}")
        print(f"  Bindings: {len(artifact.get('bindings', []))}")
        print(f"  Extractions: {len(artifact.get('extractions', []))}")
        print(f"  Chunks: {len(artifact.get('chunks', []))}")
        for binding in artifact.get("bindings", [])[:3]:
            print(
                f"  Bound: {binding.get('source_path')} "
                f"[workspace={binding.get('workspace_id')}, folder={binding.get('folder_path')}]"
            )
        return

    if action == "sync-codex":
        dhee = Dhee(user_id=args.user_id, auto_context=False, auto_checkpoint=False)
        manager = ArtifactManager(dhee._engram.memory.db, engram=dhee._engram)
        log_path = args.log or find_latest_codex_log()
        if not log_path:
            raise ValueError("No Codex session log found.")
        stats = sync_latest_codex_stream(
            manager,
            dhee._engram.memory.db,
            user_id=args.user_id,
            log_path=log_path,
        )
        if args.json:
            _json_out({"log": log_path, **stats})
            return
        print(
            f"Synced Codex log {log_path}\n"
            f"  claims: {stats['claims']}\n"
            f"  completed: {stats['completed']}\n"
            f"  attached: {stats['attached']}\n"
            f"  parsed: {stats['parsed']}\n"
            f"  chunks indexed: {stats['chunks_indexed']}"
        )
        return

    raise ValueError("Usage: dhee assets {list|show|sync-codex}")


def cmd_router(args: argparse.Namespace) -> None:
    """Enable/disable/inspect the Dhee context router for Claude Code."""
    from dhee.router import install as router_install
    from dhee.router import stats as router_stats

    action = getattr(args, "router_action", None)

    if action == "enable":
        result = router_install.enable()
        if args.json:
            _json_out({
                "action": result.action,
                "settings_path": str(result.settings_path),
                "added_allows": result.added_allows,
                "env_flag_set": result.env_flag_set,
                "backed_up": str(result.backed_up) if result.backed_up else None,
                "hooks_installed": result.hooks_installed,
                "enforce_turned_on": result.enforce_turned_on,
            })
            return
        if result.action == "already_enabled":
            print("  Router already enabled.")
        else:
            print(f"  Router enabled → {result.settings_path}")
            if result.added_allows:
                print(f"  Allowed: {', '.join(result.added_allows)}")
            if result.env_flag_set:
                print("  DHEE_ROUTER=1 on dhee MCP server")
            if result.hooks_installed:
                print("  Hooks installed (SessionStart/PreToolUse/PostToolUse/PreCompact)")
            if result.enforce_turned_on:
                print("  Enforcement ON — native Read on >20KB files / heavy Bash will be denied")
            if result.backed_up:
                print(f"  Backup: {result.backed_up}")
            print("  Restart Claude Code for changes to take effect.")
        return

    if action == "disable":
        result = router_install.disable()
        if args.json:
            _json_out({
                "action": result.action,
                "settings_path": str(result.settings_path),
                "removed_allows": result.removed_allows,
                "env_flag_cleared": result.env_flag_cleared,
                "backed_up": str(result.backed_up) if result.backed_up else None,
                "enforce_turned_off": result.enforce_turned_off,
            })
            return
        if result.action == "already_disabled":
            print("  Router was not enabled.")
        else:
            print(f"  Router disabled → {result.settings_path}")
            if result.removed_allows:
                print(f"  Removed: {', '.join(result.removed_allows)}")
            if result.enforce_turned_off:
                print("  Enforcement OFF (flag file removed)")
            if result.backed_up:
                print(f"  Backup: {result.backed_up}")
        return

    if action == "status":
        s = router_install.status()
        if args.json:
            _json_out({
                "enabled": s.enabled,
                "settings_path": str(s.settings_path),
                "allowed_tools": s.allowed_tools,
                "env_flag": s.env_flag,
                "managed": s.managed,
            })
            return
        print(f"  Enabled:   {s.enabled}")
        print(f"  Settings:  {s.settings_path}")
        print(f"  Allowed:   {', '.join(s.allowed_tools) or '(none)'}")
        print(f"  DHEE_ROUTER: {s.env_flag or '(unset)'}")
        return

    if action == "enforce":
        sub = getattr(args, "enforce_action", None)
        from dhee.router.pre_tool_gate import _flag_file
        flag = _flag_file()
        if sub == "on":
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.write_text("1\n", encoding="utf-8")
            if args.json:
                _json_out({"enforce": True, "flag_file": str(flag)})
            else:
                print(f"  Enforcement ON → {flag}")
                print("  Native Read/Bash on large files/heavy commands will be denied.")
                print("  Restart Claude Code for changes to take effect.")
                print("  Turn off: dhee router enforce off")
            return
        if sub == "off":
            existed = flag.exists()
            if existed:
                flag.unlink()
            if args.json:
                _json_out({"enforce": False, "was_on": existed})
            else:
                print(f"  Enforcement OFF{' (was on)' if existed else ' (was already off)'}")
            return
        # status (no subcommand)
        on = flag.exists()
        if args.json:
            _json_out({"enforce": on, "flag_file": str(flag)})
        else:
            print(f"  Enforcement: {'ON' if on else 'off'} ({flag})")
        return

    if action == "stats":
        computed = router_stats.compute_stats()
        if args.json:
            _json_out(computed.to_dict())
            return
        print(router_stats.format_human(computed))
        return

    if action == "tune":
        from dhee.router import policy as _policy
        from dhee.router import tune as _tune

        sub = getattr(args, "enforce_action", None)  # reuses the 2nd positional slot
        report = _tune.build_report()
        if sub == "apply":
            n = _tune.apply(report)
            if args.json:
                _json_out({"applied": n, "report": report.to_dict()})
                return
            print(_tune.format_human(report))
            print("")
            print(f"  applied {n} suggestion(s) → {_policy._policy_path()}")
            return
        if sub == "clear":
            n = _policy.clear()
            if args.json:
                _json_out({"cleared": n})
                return
            print(f"  cleared {n} policy entries")
            return
        # default: suggest
        if args.json:
            _json_out(report.to_dict())
            return
        print(_tune.format_human(report))
        return

    if action == "report":
        from dhee.router import quality_report

        report = quality_report.build_report(
            limit=getattr(args, "limit", 0) or 0,
        )
        out_path = quality_report.save_report(report)
        if args.json:
            _json_out(report.to_dict())
            return
        if getattr(args, "share", False):
            from pathlib import Path as _Path

            share_md = quality_report.format_share(report)
            share_path = _Path.home() / ".dhee" / "session_quality_report.md"
            share_path.parent.mkdir(parents=True, exist_ok=True)
            share_path.write_text(share_md + "\n", encoding="utf-8")
            print(share_md)
            print("")
            print(f"  share-ready markdown → {share_path}")
            return
        print(quality_report.format_human(report))
        print("")
        print(f"  report saved → {out_path}")
        return

    # default: print subcommand help
    print("Usage: dhee router {enable|disable|status|stats|enforce|report}")


def cmd_benchmark(args: argparse.Namespace) -> None:
    """Run performance benchmarks."""
    import time
    from dhee import Memory

    print("=" * 60)
    print(" dhee benchmark")
    print("=" * 60)

    # Cold start
    print("\n[1/4] Cold start time...")
    start = time.perf_counter()
    m = Memory()
    cold_start = time.perf_counter() - start
    print(f"  Cold start: {cold_start*1000:.1f} ms")

    # Add 100 memories
    print("\n[2/4] Add 100 memories...")
    start = time.perf_counter()
    for i in range(100):
        m.add(f"Test memory {i}: The quick brown fox jumps over the lazy dog.")
    add_time = time.perf_counter() - start
    print(f"  Added 100 memories in {add_time*1000:.1f} ms ({add_time/100*1000:.2f} ms/mem)")

    # Search (cached)
    print("\n[3/4] Search (cached embedding)...")
    start = time.perf_counter()
    for _ in range(10):
        m.search("quick fox")
    search_cached = time.perf_counter() - start
    print(f"  10 searches (cached): {search_cached*1000:.1f} ms ({search_cached/10*1000:.2f} ms/search)")

    # Decay cycle
    print("\n[4/4] Decay cycle...")
    start = time.perf_counter()
    m.apply_decay()
    decay_time = time.perf_counter() - start
    print(f"  Decay cycle: {decay_time*1000:.1f} ms")

    # Summary table
    print("\n" + "=" * 60)
    print(" Results")
    print("=" * 60)
    print(f"  Cold start:        {cold_start*1000:7.1f} ms")
    print(f"  Add (100 mems):    {add_time*1000:7.1f} ms  ({add_time/100*1000:.2f} ms/mem)")
    print(f"  Search (cached):   {search_cached/10*1000:7.2f} ms/search")
    print(f"  Decay cycle:       {decay_time*1000:7.1f} ms")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    from dhee import __version__

    parser = argparse.ArgumentParser(
        prog="dhee",
        description="dhee — cognition layer for AI agents",
    )
    parser.add_argument(
        "--version", action="version", version=f"dhee {__version__}",
    )
    sub = parser.add_subparsers(dest="command")

    # setup
    sub.add_parser("setup", help="Interactive setup wizard")

    # remember / add (aliases)
    p_remember = sub.add_parser("remember", help="Store a fact or preference")
    p_remember.add_argument("text", help="Memory content")
    p_remember.add_argument("--user-id", default="default", help="User ID")
    p_remember.add_argument("--json", action="store_true", help="JSON output")

    p_add = sub.add_parser("add", help="Store a memory (alias for remember)")
    p_add.add_argument("text", help="Memory content")
    p_add.add_argument("--user-id", default="default", help="User ID")
    p_add.add_argument("--json", action="store_true", help="JSON output")

    # recall / search (aliases)
    p_recall = sub.add_parser("recall", help="Search memory for relevant facts")
    p_recall.add_argument("query", help="What you're trying to remember")
    p_recall.add_argument("--user-id", default="default", help="User ID")
    p_recall.add_argument("--limit", type=int, default=10, help="Max results")
    p_recall.add_argument("--json", action="store_true", help="JSON output")

    p_search = sub.add_parser("search", help="Search memories (alias for recall)")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--user-id", default="default", help="User ID")
    p_search.add_argument("--limit", type=int, default=10, help="Max results")
    p_search.add_argument("--json", action="store_true", help="JSON output")

    # checkpoint
    p_cp = sub.add_parser("checkpoint", help="Save session state and learnings")
    p_cp.add_argument("summary", help="What you were working on")
    p_cp.add_argument("--what-worked", default=None, help="What approach worked well")
    p_cp.add_argument("--what-failed", default=None, help="What approach failed")
    p_cp.add_argument("--task-type", default=None, help="Task category (e.g. bug_fix)")
    p_cp.add_argument("--outcome-score", type=float, default=None, help="Outcome score 0.0-1.0")
    p_cp.add_argument("--remember-to", default=None, help="Future intention (remember to X when Y)")
    p_cp.add_argument("--user-id", default="default", help="User ID")
    p_cp.add_argument("--json", action="store_true", help="JSON output")

    # list
    p_list = sub.add_parser("list", help="List all memories")
    p_list.add_argument("--user-id", default="default", help="User ID")
    p_list.add_argument("--layer", choices=["sml", "lml"], help="Filter by layer")
    p_list.add_argument("--limit", type=int, default=50, help="Max results")
    p_list.add_argument("--json", action="store_true", help="JSON output")

    # stats
    p_stats = sub.add_parser("stats", help="Memory statistics")
    p_stats.add_argument("--user-id", default="default", help="User ID")
    p_stats.add_argument("--json", action="store_true", help="JSON output")

    # decay
    p_decay = sub.add_parser("decay", help="Apply forgetting")
    p_decay.add_argument("--user-id", default="default", help="User ID")
    p_decay.add_argument("--json", action="store_true", help="JSON output")

    # categories
    p_cats = sub.add_parser("categories", help="List categories")
    p_cats.add_argument("--json", action="store_true", help="JSON output")

    # export
    p_export = sub.add_parser("export", help="Export memories to JSON or .dheemem")
    p_export.add_argument("--output", "-o", help="Output file path")
    p_export.add_argument("--user-id", default="default", help="User ID")
    p_export.add_argument(
        "--format",
        choices=["json", "dheemem"],
        default="json",
        help="Export format (default: json; inferred from .dheemem extension when present)",
    )
    p_export.add_argument("--json", action="store_true", help="JSON output")

    # import
    p_import = sub.add_parser("import", help="Import memories from JSON or .dheemem")
    p_import.add_argument("file", help="JSON or .dheemem file to import")
    p_import.add_argument("--user-id", default="default", help="User ID")
    p_import.add_argument(
        "--format",
        choices=["json", "dheemem"],
        default="json",
        help="Import format (default: json; inferred from .dheemem extension when present)",
    )
    p_import.add_argument(
        "--strategy",
        choices=["merge", "replace", "dry-run"],
        default="merge",
        help="Pack import strategy for .dheemem archives",
    )
    p_import.add_argument("--json", action="store_true", help="JSON output")

    # why
    p_why = sub.add_parser("why", help="Explain provenance for a memory or artifact")
    p_why.add_argument("identifier", help="Memory ID or artifact ID")
    p_why.add_argument("--history-limit", type=int, default=10, help="Max memory_history rows to show")
    p_why.add_argument("--include-extraction-text", action="store_true", help="For artifact IDs, include extracted text")
    p_why.add_argument("--include-chunks", action="store_true", help="For artifact IDs, include chunk bodies")
    p_why.add_argument("--chunk-limit", type=int, default=5, help="Max chunks to include when requested")
    p_why.add_argument("--max-text-chars", type=int, default=1200, help="Per extraction/chunk text cap")
    p_why.add_argument("--json", action="store_true", help="JSON output")

    # handoff
    p_handoff = sub.add_parser("handoff", help="Emit structured handoff JSON for a new harness/agent")
    p_handoff.add_argument("--repo", help="Repository/workspace root to scope the handoff snapshot")
    p_handoff.add_argument("--thread-id", help="Optional live thread identifier to prefer thread-native continuity over session fallback")
    p_handoff.add_argument("--user-id", default="default", help="User ID")
    p_handoff.add_argument("--memory-limit", type=int, default=5, help="Recent memories to include")
    p_handoff.add_argument("--artifact-limit", type=int, default=5, help="Recent artifacts to include")
    p_handoff.add_argument("--task-limit", type=int, default=5, help="Recent tasks to include")
    p_handoff.add_argument("--intention-limit", type=int, default=5, help="Active intentions to include")
    p_handoff.add_argument("--output", "-o", help="Optional path to write the handoff JSON")
    p_handoff.add_argument("--json", action="store_true", help="JSON output")

    # thread-state
    p_thread = sub.add_parser("thread-state", help="Read or update live thread continuity state")
    p_thread.add_argument("--thread-id", required=True, help="Harness or app thread identifier")
    p_thread.add_argument("--user-id", default="default", help="User ID")
    p_thread.add_argument("--repo", help="Optional repo/workspace root")
    p_thread.add_argument("--workspace-id", help="Optional workspace scope override")
    p_thread.add_argument("--folder-path", help="Optional folder-local scope")
    p_thread.add_argument("--status", help="Thread status, e.g. active or paused")
    p_thread.add_argument("--summary", help="Compact thread summary")
    p_thread.add_argument("--goal", help="Current thread goal")
    p_thread.add_argument("--step", help="Current next step")
    p_thread.add_argument("--session-id", help="Optional harness session identifier")
    p_thread.add_argument("--handoff-session-id", help="Optional linked cross-agent session id")
    p_thread.add_argument("--metadata", help="Optional JSON metadata object")
    p_thread.add_argument("--clear", action="store_true", help="Delete the stored thread state")
    p_thread.add_argument("--json", action="store_true", help="JSON output")

    # shared-task
    p_shared = sub.add_parser("shared-task", help="Manage repo-scoped shared collaboration tasks")
    p_shared.add_argument(
        "shared_action",
        choices=["start", "show", "list", "results", "close"],
        help="Subcommand",
    )
    p_shared.add_argument("--shared-task-id", help="Explicit shared task identifier")
    p_shared.add_argument("--title", help="Task title for `start`")
    p_shared.add_argument("--repo", help="Optional repo/workspace root (defaults to cwd)")
    p_shared.add_argument("--workspace-id", help="Optional workspace scope override")
    p_shared.add_argument("--folder-path", help="Optional folder-local scope")
    p_shared.add_argument("--metadata", help="Optional JSON metadata")
    p_shared.add_argument("--keep-results", action="store_true", help="For `close`, keep transient tool results instead of pruning them")
    p_shared.add_argument("--packet-kind", help="For `results`, filter by packet kind")
    p_shared.add_argument(
        "--result-status",
        choices=["in_flight", "completed", "abandoned"],
        help="For `results`, filter by status",
    )
    p_shared.add_argument("--limit", type=int, default=20, help="Max tasks/results to show")
    p_shared.add_argument("--user-id", default="default", help="User ID")
    p_shared.add_argument("--json", action="store_true", help="JSON output")

    # status
    p_status = sub.add_parser("status", help="Show version, config, and agents")
    p_status.add_argument("--json", action="store_true", help="JSON output")

    # doctor — composite observability of router + cognition + memory
    p_doctor = sub.add_parser(
        "doctor",
        help="Composite health + honesty report (router, cognition, memory, movement plan)",
    )
    p_doctor.add_argument("--json", action="store_true", help="JSON output")

    # task
    p_task = sub.add_parser("task", help="Start Claude Code with Dhee cognition")
    p_task.add_argument("description", nargs="?", default="", help="Task description")
    p_task.add_argument("--user-id", default="default", help="User ID")
    p_task.add_argument("--print", dest="print_mode", action="store_true", help="One-shot mode")

    # install (native harness bootstrap)
    p_install = sub.add_parser(
        "install",
        help="Native Dhee install for Claude Code, Codex, and/or gstack",
    )
    p_install.add_argument(
        "target",
        nargs="?",
        default=None,
        help=(
            "Optional shortcut: 'claude_code', 'codex', 'gstack', or 'all'. "
            "Equivalent to --harness. Enables `dhee install gstack`."
        ),
    )
    p_install.add_argument(
        "--harness",
        choices=["all", "claude_code", "codex", "gstack"],
        default=None,
        help="Which harnesses to configure (default: all if no positional target given)",
    )
    p_install.add_argument(
        "--no-router",
        action="store_true",
        help="For Claude Code: skip router enforcement",
    )

    # uninstall-hooks
    sub.add_parser("uninstall-hooks", help="Remove Dhee hooks from Claude Code")

    # harness
    p_harness = sub.add_parser("harness", help="Inspect or change native harness integration")
    p_harness.add_argument(
        "harness_action",
        nargs="?",
        choices=["status", "enable", "disable"],
        default="status",
        help="Subcommand",
    )
    p_harness.add_argument(
        "--harness",
        choices=["all", "claude_code", "codex", "gstack"],
        default="all",
        help="Harness target",
    )
    p_harness.add_argument(
        "--no-router",
        action="store_true",
        help="For `harness enable`: leave Claude Code router disabled",
    )
    p_harness.add_argument("--json", action="store_true", help="JSON output")

    # adapters (third-party memory ingestors)
    p_adapters = sub.add_parser(
        "adapters",
        help="Inspect or refresh third-party memory adapters (e.g. gstack)",
    )
    p_adapters.add_argument(
        "adapter",
        choices=["gstack"],
        help="Which adapter",
    )
    p_adapters.add_argument(
        "adapter_action",
        nargs="?",
        choices=["status", "reingest", "clear"],
        default="status",
        help="Subcommand",
    )
    p_adapters.add_argument(
        "--reset",
        action="store_true",
        help="For `reingest`: clear the cursor manifest first and re-ingest everything",
    )
    p_adapters.add_argument("--json", action="store_true", help="JSON output")

    # purge-legacy-noise
    p_purge = sub.add_parser(
        "purge-legacy-noise",
        help="Remove v3.3.0 hook-noise entries from the vector store",
    )
    p_purge.add_argument("--dry-run", action="store_true", help="Report counts without deleting")
    p_purge.add_argument("--json", action="store_true", help="JSON output")

    # ingest
    p_ingest = sub.add_parser("ingest", help="Ingest markdown docs for selective retrieval")
    p_ingest.add_argument("paths", nargs="*", help="Specific files to ingest (omit for auto-scan)")
    p_ingest.add_argument("--root", default=".", help="Project root for auto-scan")
    p_ingest.add_argument("--force", action="store_true", help="Re-ingest even if unchanged")
    p_ingest.add_argument("--json", action="store_true", help="JSON output")

    # docs
    p_docs = sub.add_parser("docs", help="Show ingested doc manifest")
    p_docs.add_argument("--json", action="store_true", help="JSON output")

    # portability-eval — round-trip .dheemem scorecard
    p_port = sub.add_parser(
        "portability-eval",
        help="Score .dheemem export→import round-trip fidelity",
    )
    p_port.add_argument("--user-id", default="default", help="User ID")
    p_port.add_argument("--out-dir", dest="out_dir", help="Where to drop the tmp pack")
    p_port.add_argument("--threshold", type=float, default=0.95,
                        help="Minimum per-substrate retention to pass")
    p_port.add_argument("--json", action="store_true", help="JSON output")

    # decades-eval — Movement-3 longevity scorecard on synthetic corpus
    p_dec = sub.add_parser(
        "decades-eval",
        help="Longevity scorecard: canonical retention + supersede chains + latency",
    )
    p_dec.add_argument(
        "--events", type=int, default=10000,
        help="Total synthetic fact writes (default 10000)",
    )
    p_dec.add_argument(
        "--supersede-fraction", dest="supersede_fraction",
        type=float, default=0.20,
        help="Fraction of writes participating in supersede chains",
    )
    p_dec.add_argument(
        "--canonical-fraction", dest="canonical_fraction",
        type=float, default=0.10,
        help="Fraction of writes pre-stamped canonical tier",
    )
    p_dec.add_argument(
        "--span-days", dest="span_days", type=int, default=365 * 3,
        help="Simulated timeline span (default 3 years)",
    )
    p_dec.add_argument(
        "--latency-samples", dest="latency_samples", type=int, default=200,
        help="Queries per latency measurement",
    )
    p_dec.add_argument(
        "--seed", type=int, default=42, help="RNG seed for reproducibility",
    )
    p_dec.add_argument(
        "--data-dir", dest="data_dir",
        help="Where to place the tmp SQLite DBs (default: tempdir)",
    )
    p_dec.add_argument("--json", action="store_true", help="JSON output")

    # replay-corpus — samskara → replay-gate corpus export
    p_replay = sub.add_parser(
        "replay-corpus",
        help="Export a replay-gate corpus from the durable samskara log",
    )
    p_replay.add_argument(
        "replay_action",
        nargs="?",
        default="export",
        choices=["export"],
        help="Subcommand",
    )
    p_replay.add_argument("--out-dir", dest="out_dir", help="Destination directory")
    p_replay.add_argument("--log-dir", dest="log_dir", help="Samskara log directory")
    p_replay.add_argument(
        "--max-records", dest="max_records", type=int,
        help="Keep only the most recent N records",
    )
    p_replay.add_argument("--json", action="store_true", help="JSON output")

    # assets
    p_assets = sub.add_parser("assets", help="Inspect or sync host-parsed file artifacts")
    p_assets.add_argument(
        "assets_action",
        choices=["list", "show", "sync-codex"],
        help="Subcommand",
    )
    p_assets.add_argument("artifact_id", nargs="?", help="Artifact ID for `assets show`")
    p_assets.add_argument("--workspace", help="Filter list by workspace path")
    p_assets.add_argument("--log", help="Codex session log path for `assets sync-codex`")
    p_assets.add_argument("--limit", type=int, default=50, help="Max artifacts to list")
    p_assets.add_argument("--user-id", default="default", help="User ID")
    p_assets.add_argument("--json", action="store_true", help="JSON output")

    # router
    p_router = sub.add_parser("router", help="Context router (enable/disable/stats)")
    p_router.add_argument(
        "router_action",
        nargs="?",
        choices=["enable", "disable", "status", "stats", "enforce", "report", "tune"],
        help="Subcommand",
    )
    p_router.add_argument(
        "enforce_action",
        nargs="?",
        choices=["on", "off", "apply", "clear"],
        help="For `router enforce`: on|off  |  For `router tune`: apply|clear (omit to dry-run)",
    )
    p_router.add_argument("--limit", type=int, default=0, help="For `router report`: replay only N most-recent sessions (0 = all)")
    p_router.add_argument("--share", action="store_true", help="For `router report`: emit customer-shareable redacted Markdown")
    p_router.add_argument("--json", action="store_true", help="JSON output")

    # quality-report — top-level alias over `dhee router report`
    p_qr = sub.add_parser(
        "quality-report",
        help="Extended session-quality report: cache-read/turn, expansion rate, tool_result share, projected savings",
    )
    p_qr.add_argument("--limit", type=int, default=0, help="Replay only N most-recent sessions (0 = all)")
    p_qr.add_argument("--share", action="store_true", help="Emit customer-shareable redacted Markdown")
    p_qr.add_argument("--json", action="store_true", help="JSON output")

    # benchmark
    sub.add_parser("benchmark", help="Run performance benchmarks")

    # uninstall
    sub.add_parser("uninstall", help="Remove ~/.dhee directory")

    return parser


COMMAND_MAP = {
    "setup": cmd_setup,
    "remember": cmd_add,   # alias
    "add": cmd_add,
    "recall": cmd_search,  # alias
    "search": cmd_search,
    "checkpoint": cmd_checkpoint,
    "list": cmd_list,
    "stats": cmd_stats,
    "decay": cmd_decay,
    "categories": cmd_categories,
    "export": cmd_export,
    "import": cmd_import,
    "why": cmd_why,
    "handoff": cmd_handoff,
    "thread-state": cmd_thread_state,
    "shared-task": cmd_shared_task,
    "status": cmd_status,
    "doctor": cmd_doctor,
    "task": cmd_task,
    "ingest": cmd_ingest,
    "docs": cmd_docs,
    "assets": cmd_assets,
    "replay-corpus": cmd_replay_corpus,
    "portability-eval": cmd_portability_eval,
    "decades-eval": cmd_decades_eval,
    "install": cmd_install_hooks,
    "harness": cmd_harness,
    "adapters": cmd_adapters,
    "uninstall-hooks": cmd_uninstall_hooks,
    "purge-legacy-noise": cmd_purge_legacy_noise,
    "router": cmd_router,
    "quality-report": lambda args: cmd_router(
        argparse.Namespace(
            router_action="report",
            enforce_action=None,
            limit=getattr(args, "limit", 0),
            share=getattr(args, "share", False),
            json=getattr(args, "json", False),
        )
    ),
    "benchmark": cmd_benchmark,
    "uninstall": cmd_uninstall,
}


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    handler = COMMAND_MAP.get(args.command)
    if handler:
        try:
            handler(args)
        except KeyboardInterrupt:
            print("\nInterrupted.")
            sys.exit(1)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
