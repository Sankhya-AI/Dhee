"""Dhee Hive — multi-agent shared cognition layer.

Built on top of engram-bus for real-time agent-to-agent communication,
with CRDT-based sync for offline/edge scenarios.
"""

from dhee.hive.hive_memory import HiveMemory

__all__ = ["HiveMemory"]
