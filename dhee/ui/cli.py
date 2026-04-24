"""`dhee ui` — start Sankhya.

Starts the FastAPI bridge (which also serves the built SPA) on a local
port. If the SPA hasn't been built, prints the build instructions and
exits cleanly so users know exactly what to do.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _schedule_browser_open(url: str, delay: float = 1.2) -> None:
    """Open the UI in the default browser shortly after startup.

    Runs in a thread so we don't block uvicorn. Silent on failure —
    headless boxes (CI, servers, SSH sessions without $DISPLAY) fall
    back cleanly to the printed URL.
    """

    def _run() -> None:
        time.sleep(delay)
        try:
            webbrowser.open_new_tab(url)
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def cmd_ui(args: argparse.Namespace) -> None:
    import uvicorn
    from dhee.ui.server import create_app

    web_dir = Path(__file__).parent / "web"
    dist = web_dir / "dist"

    # Auto-fallback to dev mode if dist is missing and we're in a source tree
    if not dist.exists() and not args.dev:
        if (web_dir / "package.json").exists():
            print("Sankhya SPA not built. Falling back to dev mode (hot-reloading)...")
            args.dev = True
        else:
            print(f"Sankhya SPA not built yet.")
            print(f"  cd {web_dir}")
            print(f"  npm install && npm run build")
            sys.exit(1)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    app = create_app(serve_static=not args.dev, dev_mode=args.dev)
    host_display = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    ui_url = f"http://{host_display}:{args.port}/"
    print(f"Sankhya — Dhee UI")
    print(f"  API   http://{args.host}:{args.port}/api")

    frontend_proc = None
    if args.dev:
        print(f"  Dev   Starting Vite frontend (http://127.0.0.1:5173)...")
        print(f"  Dashboard  http://{args.host}:{args.port}/  (Proxied to Vite)")
        try:
            frontend_proc = subprocess.Popen(
                ["npm", "run", "dev"],
                cwd=str(web_dir),
                stdout=subprocess.DEVNULL if not args.verbose else None,
                stderr=subprocess.STDOUT if not args.verbose else None,
            )
        except Exception as e:
            print(f"Warning: Could not start frontend: {e}")
    else:
        print(f"  Dashboard  http://{args.host}:{args.port}/")
        if args.host == "127.0.0.1" and args.port == 8080:
            print(f"  Tip: Add '127.0.0.1 dhee.ui' to /etc/hosts to use http://dhee.ui:8080/")

    should_open = not args.no_open and os.environ.get("DHEE_UI_NO_OPEN") != "1"
    # Skip auto-open on headless servers (no DISPLAY on X11).
    if should_open and sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        should_open = False
    if should_open:
        _schedule_browser_open(ui_url, delay=1.5 if args.dev else 1.0)

    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    finally:
        if frontend_proc:
            frontend_proc.terminate()
            try:
                frontend_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                frontend_proc.kill()


def cmd_ui_build(args: argparse.Namespace) -> None:
    web = Path(__file__).parent / "web"
    if not (web / "package.json").exists():
        print(f"No package.json at {web}", file=sys.stderr)
        sys.exit(1)
    if args.install or not (web / "node_modules").exists():
        print("→ npm install")
        subprocess.check_call(["npm", "install"], cwd=str(web))
    print("→ npm run build")
    subprocess.check_call(["npm", "run", "build"], cwd=str(web))
    print("✓ Built at", web / "dist")


def register(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    p = sub.add_parser("ui", help="Start Sankhya (Dhee web UI)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument(
        "--dev",
        action="store_true",
        help="Start both API bridge and Vite frontend with hot-reloading.",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Show frontend (Vite) logs in dev mode.",
    )
    p.add_argument(
        "--no-open",
        action="store_true",
        help="Don't auto-open the UI in the default browser.",
    )
    p.set_defaults(func=cmd_ui)

    pb = sub.add_parser("ui-build", help="Build the Sankhya SPA (npm install + npm run build)")
    pb.add_argument("--install", action="store_true", help="Force `npm install` even if node_modules exists")
    pb.set_defaults(func=cmd_ui_build)
