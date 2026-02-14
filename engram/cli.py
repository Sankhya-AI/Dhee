"""engram CLI — memory layer for AI agents.

Usage:
    engram setup              Interactive setup wizard
    engram add "text"         Add a memory
    engram search "query"     Search memories
    engram list               List all memories
    engram stats              Memory statistics
    engram decay              Apply forgetting
    engram categories         List categories
    engram export             Export to JSON
    engram import <file>      Import from JSON
    engram status             Version, config, DB info
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
    from engram.cli_config import get_memory_instance
    return get_memory_instance(config)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def cmd_setup(args: argparse.Namespace) -> None:
    """Run interactive setup wizard."""
    from engram.cli_setup import run_setup
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


def cmd_status(args: argparse.Namespace) -> None:
    """Show version, config, DB size, detected agents."""
    from engram import __version__
    from engram.cli_config import CONFIG_DIR, CONFIG_PATH, load_config
    from engram.cli_mcp import detect_agents

    config = load_config()
    provider = config.get("provider", "not configured")
    packages = config.get("packages", [])

    print(f"  engram v{__version__}")
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
    """Remove ~/.engram directory."""
    from engram.cli_config import CONFIG_DIR
    if not os.path.exists(CONFIG_DIR):
        print("Nothing to remove.")
        return
    confirm = input(f"Remove {CONFIG_DIR}? [y/N]: ").strip().lower()
    if confirm in ("y", "yes"):
        shutil.rmtree(CONFIG_DIR)
        print(f"Removed {CONFIG_DIR}")
    else:
        print("Cancelled.")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    from engram import __version__

    parser = argparse.ArgumentParser(
        prog="engram",
        description="engram — memory layer for AI agents",
    )
    parser.add_argument(
        "--version", action="version", version=f"engram {__version__}",
    )
    sub = parser.add_subparsers(dest="command")

    # setup
    sub.add_parser("setup", help="Interactive setup wizard")

    # add
    p_add = sub.add_parser("add", help="Add a memory")
    p_add.add_argument("text", help="Memory content")
    p_add.add_argument("--user-id", default="default", help="User ID")
    p_add.add_argument("--json", action="store_true", help="JSON output")

    # search
    p_search = sub.add_parser("search", help="Search memories")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--user-id", default="default", help="User ID")
    p_search.add_argument("--limit", type=int, default=10, help="Max results")
    p_search.add_argument("--json", action="store_true", help="JSON output")

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

    # uninstall
    sub.add_parser("uninstall", help="Remove ~/.engram directory")

    return parser


COMMAND_MAP = {
    "setup": cmd_setup,
    "add": cmd_add,
    "search": cmd_search,
    "list": cmd_list,
    "stats": cmd_stats,
    "decay": cmd_decay,
    "categories": cmd_categories,
    "export": cmd_export,
    "import": cmd_import,
    "status": cmd_status,
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
