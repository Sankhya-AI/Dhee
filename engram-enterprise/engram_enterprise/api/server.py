"""Compatibility wrapper for Engram API server.

v2 implementation lives in ``engram.api.app``.
"""

from __future__ import annotations

from engram_enterprise.api.app import app


def run():
    """Run the Engram API server."""
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Engram REST API Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8100, help="Port to listen on")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    args = parser.parse_args()

    print(f"Starting Engram API server on http://{args.host}:{args.port}")
    print(f"API docs available at http://{args.host}:{args.port}/docs")

    uvicorn.run(
        "engram_enterprise.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    run()
