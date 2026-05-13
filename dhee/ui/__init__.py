"""Sankhya — Dhee's web UI.

`dhee ui` starts a FastAPI server that serves the built React SPA and
exposes the Dhee substrate (memories, router stats, policy, evolution,
conflicts, tasks) as JSON endpoints.
"""

try:
    from dhee.ui.server import app, create_app  # noqa: F401
except ModuleNotFoundError:
    # FastAPI is an optional dep (`pip install dhee[api]`). The package
    # still imports so `dhee ui` can print a useful message.
    app = None  # type: ignore[assignment]

    def create_app(*args, **kwargs):  # type: ignore[no-redef]
        raise ModuleNotFoundError(
            "dhee.ui requires fastapi + uvicorn. Install with `pip install 'dhee[api]'`."
        )

__all__ = ["app", "create_app"]
