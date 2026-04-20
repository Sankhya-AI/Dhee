"""M4.3 regression — Nididhyasana session-boundary gate.

Plan reference: encapsulated-rolling-bengio.md, Movement 4.

These tests lock in the ``on_session_end`` scheduler behavior:

  * When Nididhyasana is unavailable, the gate still persists a record
    (so operators can see the scheduler is firing, even if the training
    loop is not wired).
  * ``should_evolve()`` is called and the verdict + reason are recorded.
  * The heavy ``evolve()`` cycle is NOT invoked unless ``force_evolve=True``.
  * ``read_session_gates`` tails the log for ``dhee doctor``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dhee.core.evolution import EvolutionLayer


class _StubNididhyasana:
    def __init__(self, should_result=(False, "quiet"), evolve_result=None):
        self._should_result = should_result
        self._evolve_result = evolve_result
        self.should_calls = 0
        self.evolve_calls = 0

    def should_evolve(self):
        self.should_calls += 1
        return self._should_result

    def evolve(self, force=False):
        self.evolve_calls += 1
        return self._evolve_result


def _fresh_evo(tmp_path) -> EvolutionLayer:
    return EvolutionLayer(
        data_dir=str(tmp_path / "dhee"),
        enable_samskara=False,
        enable_viveka=False,
        enable_alaya=False,
        enable_nididhyasana=False,
    )


def test_gate_records_when_nididhyasana_absent(tmp_path):
    evo = _fresh_evo(tmp_path)
    rec = evo.on_session_end(reason="Stop")
    assert rec["gate_fired"] is False
    assert "not initialized" in rec["gate_reason"]
    assert rec["evolved"] is False

    # persisted
    gates = evo.read_session_gates()
    assert len(gates) == 1
    assert gates[0]["reason"] == "Stop"


def test_gate_reports_should_evolve_result(tmp_path):
    evo = _fresh_evo(tmp_path)
    evo._nididhyasana = _StubNididhyasana(should_result=(True, "dpo pairs"))

    rec = evo.on_session_end(reason="SessionEnd")
    assert rec["gate_fired"] is True
    assert rec["gate_reason"] == "dpo pairs"
    # force_evolve defaults False → evolve() must not be called
    assert evo._nididhyasana.evolve_calls == 0
    assert rec["evolved"] is False


def test_force_evolve_runs_cycle_only_when_gate_fires(tmp_path):
    evo = _fresh_evo(tmp_path)

    class _Cycle:
        cycle_id = 1
        verdict = "ascend"
        error = ""

    # Gate says False → even with force_evolve, evolve must NOT run.
    stub = _StubNididhyasana(should_result=(False, "quiet"), evolve_result=_Cycle())
    evo._nididhyasana = stub
    evo.on_session_end(reason="Stop", force_evolve=True)
    assert stub.evolve_calls == 0

    # Gate fires → evolve runs and cycle info is persisted.
    stub2 = _StubNididhyasana(should_result=(True, "ready"), evolve_result=_Cycle())
    evo._nididhyasana = stub2
    rec = evo.on_session_end(reason="Stop", force_evolve=True)
    assert stub2.evolve_calls == 1
    assert rec["evolved"] is True
    assert rec["cycle_id"] == 1
    assert rec["verdict"] == "ascend"


def test_read_session_gates_returns_tail(tmp_path):
    evo = _fresh_evo(tmp_path)
    evo._nididhyasana = _StubNididhyasana(should_result=(False, "quiet"))

    for i in range(5):
        evo.on_session_end(reason=f"run-{i}")

    tail = evo.read_session_gates(limit=3)
    assert [r["reason"] for r in tail] == ["run-2", "run-3", "run-4"]


def test_session_gate_survives_should_evolve_exception(tmp_path):
    """A broken should_evolve() must not crash the session-end path."""
    evo = _fresh_evo(tmp_path)

    class _Broken:
        def should_evolve(self):
            raise RuntimeError("boom")

    evo._nididhyasana = _Broken()
    rec = evo.on_session_end(reason="Stop")
    assert rec["gate_fired"] is False
    assert "boom" in rec["gate_reason"]

    # record still persisted
    gates = evo.read_session_gates()
    assert len(gates) == 1
