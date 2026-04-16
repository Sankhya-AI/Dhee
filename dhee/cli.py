"""dhee CLI — cognition layer for AI agents.

Usage:
    dhee setup              Interactive setup wizard
    dhee add "text"         Add a memory
    dhee search "query"     Search memories
    dhee list               List all memories
    dhee stats              Memory statistics
    dhee decay              Apply forgetting
    dhee categories         List categories
    dhee export             Export to JSON
    dhee import <file>      Import from JSON
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
    """Export all memories to JSON."""
    memory = _get_memory()
    result = memory.get_all(user_id="default", limit=10000)
    memories = result.get("results", [])
    data = {"version": "1", "memories": memories}
    if args.output:
        with open(args.output, "w") as f:
            json.dump(data, f, indent=2, default=str)
            f.write("\n")
        print(f"Exported {len(memories)} memories to {args.output}")
    else:
        _json_out(data)


def cmd_import(args: argparse.Namespace) -> None:
    """Import memories from JSON."""
    with open(args.file, "r") as f:
        data = json.load(f)
    memories = data.get("memories", [])
    if not memories:
        print("No memories found in file.")
        return
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
    if args.json:
        _json_out({"imported": count})
    else:
        print(f"Imported {count} memories.")


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

    config = load_config()
    provider = config.get("provider", "not configured")
    packages = config.get("packages", [])

    print(f"  dhee v{__version__}")
    print(f"  Provider: {provider}")
    print(f"  Packages: {', '.join(packages) if packages else 'none'}")
    print(f"  Config:   {CONFIG_PATH}")

    # DB sizes
    vec_db = os.path.join(CONFIG_DIR, "sqlite_vec.db")
    hist_db = os.path.join(CONFIG_DIR, "history.db")
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
    agents = detect_agents()
    if agents:
        print(f"  Agents:   {', '.join(agents)}")
    else:
        print("  Agents:   none detected")

    if args.json:
        _json_out({
            "version": __version__,
            "provider": provider,
            "packages": packages,
            "config_path": CONFIG_PATH,
            "agents": agents,
        })


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
    """Install Dhee hooks into Claude Code."""
    from dhee.hooks.claude_code.install import install_hooks

    result = install_hooks(force=args.force)
    if result.already_installed and not args.force:
        print("  Dhee hooks already installed.")
    else:
        action = "Created" if result.created else "Updated"
        print(f"  {action} {result.settings_path}")
        print(f"  Hooks: {', '.join(result.events)}")
        if result.backed_up:
            print(f"  Backup: {result.backed_up}")


def cmd_uninstall_hooks(args: argparse.Namespace) -> None:
    """Remove Dhee hooks from Claude Code."""
    from dhee.hooks.claude_code.install import uninstall_hooks

    if uninstall_hooks():
        print("  Dhee hooks removed.")
    else:
        print("  No Dhee hooks found.")


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
    p_export = sub.add_parser("export", help="Export memories to JSON")
    p_export.add_argument("--output", "-o", help="Output file path")

    # import
    p_import = sub.add_parser("import", help="Import memories from JSON")
    p_import.add_argument("file", help="JSON file to import")
    p_import.add_argument("--user-id", default="default", help="User ID")
    p_import.add_argument("--json", action="store_true", help="JSON output")

    # status
    p_status = sub.add_parser("status", help="Show version, config, and agents")
    p_status.add_argument("--json", action="store_true", help="JSON output")

    # task
    p_task = sub.add_parser("task", help="Start Claude Code with Dhee cognition")
    p_task.add_argument("description", nargs="?", default="", help="Task description")
    p_task.add_argument("--user-id", default="default", help="User ID")
    p_task.add_argument("--print", dest="print_mode", action="store_true", help="One-shot mode")

    # install (hooks)
    p_install = sub.add_parser("install", help="Install Dhee hooks into Claude Code")
    p_install.add_argument("--force", action="store_true", help="Overwrite existing hooks")

    # uninstall-hooks
    sub.add_parser("uninstall-hooks", help="Remove Dhee hooks from Claude Code")

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
    "status": cmd_status,
    "task": cmd_task,
    "install": cmd_install_hooks,
    "uninstall-hooks": cmd_uninstall_hooks,
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
