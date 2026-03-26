"""Engram core API server runner.

Starts the lightweight FastAPI app from ``engram.api.app`` on the configured
host/port.  This is the standalone server — the enterprise version adds auth,
governance, and more endpoints on top.
"""

from __future__ import annotations


def run():
    """Run the Engram core API server."""
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Engram Core API Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8100, help="Port to listen on")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    args = parser.parse_args()

    print(f"Starting Engram Core API on http://{args.host}:{args.port}")
    print(f"Docs at http://{args.host}:{args.port}/docs")

    uvicorn.run(
        "engram.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    run()
