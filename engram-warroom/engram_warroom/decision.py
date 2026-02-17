"""War room decision state machine.

States: open -> discussing -> deciding -> decided -> delivering -> closed

Valid transitions enforced. Any state can force-close to closed.
deciding can go back to discussing (if more discussion needed).
"""

from __future__ import annotations

from enum import Enum


class DecisionState(str, Enum):
    OPEN = "open"
    DISCUSSING = "discussing"
    DECIDING = "deciding"
    DECIDED = "decided"
    DELIVERING = "delivering"
    CLOSED = "closed"


# Valid transitions: from_state -> set of allowed to_states
_TRANSITIONS: dict[DecisionState, set[DecisionState]] = {
    DecisionState.OPEN: {DecisionState.DISCUSSING, DecisionState.CLOSED},
    DecisionState.DISCUSSING: {DecisionState.DECIDING, DecisionState.CLOSED},
    DecisionState.DECIDING: {DecisionState.DISCUSSING, DecisionState.DECIDED, DecisionState.CLOSED},
    DecisionState.DECIDED: {DecisionState.DELIVERING, DecisionState.CLOSED},
    DecisionState.DELIVERING: {DecisionState.CLOSED},
    DecisionState.CLOSED: set(),
}


def validate_transition(from_state: str, to_state: str) -> bool:
    """Check whether a state transition is valid."""
    try:
        src = DecisionState(from_state)
        dst = DecisionState(to_state)
    except ValueError:
        return False
    return dst in _TRANSITIONS.get(src, set())


def all_states() -> list[str]:
    """Return all valid state names."""
    return [s.value for s in DecisionState]
