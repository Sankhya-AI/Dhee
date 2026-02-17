"""engram-failure — The Debugger.

Failure learning: log failures, extract anti-patterns (things NOT to do),
and discover recovery strategies. Successful recoveries feed into
procedural memory as new procedures.

Usage::

    from engram.memory.main import Memory
    from engram_failure import FailureLearning, FailureConfig

    memory = Memory(config=...)
    fl = FailureLearning(memory, user_id="default")
    fl.log_failure(action="deploy", error="timeout", context="prod deploy at 2am")
"""

from engram_failure.config import FailureConfig
from engram_failure.failure import FailureLearning

__all__ = ["FailureLearning", "FailureConfig"]
