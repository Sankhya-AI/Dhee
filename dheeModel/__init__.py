"""Dhee — Cognition as a Service.

The memory layer that turns any agent into a HyperAgent.
Zero config. 4 methods. ~$0.004 per session.

    from dhee import Dhee

    d = Dhee()
    d.remember("User prefers dark mode")
    results = d.recall("what theme does the user like?")
    ctx = d.context("fixing auth bug")
    d.checkpoint("Fixed the auth bug", what_worked="checked git blame first")
"""

from dhee.client import Dhee

__version__ = "1.0.0"
__all__ = ["Dhee"]
