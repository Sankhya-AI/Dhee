"""Self-evolution integration — wires samskara, viveka, alaya into the memory pipeline.

This module provides the EvolutionLayer that sits alongside the existing
memory pipeline without modifying its core logic. It's initialized lazily
by FullMemory and receives signals from key pipeline stages:

  WRITE path:
    _process_single_memory → [extraction complete] → viveka.assess_extraction
                                                   → samskara.on_extraction

  READ path:
    search → [results returned] → alaya.on_retrieval
                                → viveka.assess_retrieval
    answer → [answer generated] → alaya.on_activation
                                → viveka.assess_answer
                                → samskara.on_answer_accepted/corrected

  BACKGROUND:
    nididhyasana.should_evolve() checked periodically
    → auto-retraining when threshold crossed

Minimal coupling: FullMemory holds one EvolutionLayer instance.
All self-evolution logic lives here, not scattered across main.py.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class EvolutionLayer:
    """Single entry point for all self-evolution components.

    Initialized lazily by FullMemory. All methods are safe to call
    even if individual components fail to initialize (graceful degradation).
    """

    def __init__(
        self,
        data_dir: Optional[str] = None,
        enable_samskara: bool = True,
        enable_viveka: bool = True,
        enable_alaya: bool = True,
        enable_nididhyasana: bool = True,
    ):
        self._data_dir = data_dir or os.path.join(
            os.path.expanduser("~"), ".dhee"
        )

        self._samskara = None
        self._viveka = None
        self._alaya = None
        self._nididhyasana = None

        if enable_samskara:
            try:
                from dhee.core.samskara import SamskaraCollector
                self._samskara = SamskaraCollector(
                    log_dir=os.path.join(self._data_dir, "samskaras"),
                )
            except Exception as e:
                logger.debug("Samskara init skipped: %s", e)

        if enable_viveka:
            try:
                from dhee.core.viveka import Viveka
                self._viveka = Viveka(
                    samskara_collector=self._samskara,
                )
            except Exception as e:
                logger.debug("Viveka init skipped: %s", e)

        if enable_alaya:
            try:
                from dhee.core.alaya import AlayaStore
                self._alaya = AlayaStore(
                    samskara_collector=self._samskara,
                )
                self._load_alaya_state()
            except Exception as e:
                logger.debug("Alaya init skipped: %s", e)

        if enable_nididhyasana and self._samskara:
            try:
                # Canonical path after M7.1 relocation. The full evolve() cycle
                # still depends on ``dhee.mini.progressive_trainer``, which
                # is not yet restored — that is the remaining M7 training work.
                from dhee.training.nididhyasana import NididhyasanaLoop
                self._nididhyasana = NididhyasanaLoop(
                    samskara=self._samskara,
                    viveka=self._viveka,
                    alaya=self._alaya,
                    dhee_dir=self._data_dir,
                )
            except Exception as e:
                logger.debug("Nididhyasana init skipped: %s", e)

        # Phase 2: MetaBuddhi (self-referential cognition)
        self._meta_buddhi = None
        try:
            from dhee.core.meta_buddhi import MetaBuddhi
            self._meta_buddhi = MetaBuddhi(
                data_dir=os.path.join(self._data_dir, "meta_buddhi"),
            )
        except Exception as e:
            logger.debug("MetaBuddhi init skipped: %s", e)

        # M4.2: substrate handle for downstream-success tier bumps + last_verified
        # stamping. Optional — the evolution layer works without it; caller
        # passes the engram DB via ``attach_substrate(db)``.
        self._substrate_db = None

    # ------------------------------------------------------------------
    # WRITE path hooks
    # ------------------------------------------------------------------

    def on_memory_stored(
        self,
        memory_id: str,
        content: str,
        facts: Optional[List[Dict]] = None,
        context: Optional[Dict] = None,
        user_id: str = "default",
    ) -> None:
        """Called after a memory is successfully stored and enriched.

        Hook point: end of _process_single_memory, after engram extraction.
        """
        # Viveka: assess extraction quality
        if self._viveka and facts is not None:
            try:
                self._viveka.assess_extraction(
                    content=content,
                    facts=facts,
                    context=context,
                    memory_id=memory_id,
                    user_id=user_id,
                )
            except Exception as e:
                logger.debug("Viveka extraction assessment failed: %s", e)

        # Samskara: record extraction impression (if viveka didn't already)
        if self._samskara and facts is not None and not self._viveka:
            try:
                self._samskara.on_extraction(
                    memory_id=memory_id,
                    input_text=content[:500],
                    extracted_output=json.dumps(facts[:3]) if facts else "[]",
                    fact_count=len(facts),
                    user_id=user_id,
                )
            except Exception as e:
                logger.debug("Samskara extraction recording failed: %s", e)

    def on_conflict_resolved(
        self,
        memory_id: str,
        old_value: str,
        new_value: str,
        resolved_to: str,
        user_id: str = "default",
    ) -> None:
        """Called when a contradiction between memories is resolved."""
        if self._samskara:
            try:
                self._samskara.on_conflict_detected(
                    memory_id=memory_id,
                    old_value=old_value,
                    new_value=new_value,
                    resolved_to=resolved_to,
                    user_id=user_id,
                )
            except Exception as e:
                logger.debug("Samskara conflict recording failed: %s", e)

    # ------------------------------------------------------------------
    # READ path hooks
    # ------------------------------------------------------------------

    def on_search_results(
        self,
        query: str,
        results: List[Dict],
        user_id: str = "default",
    ) -> None:
        """Called after search returns results (before answer synthesis).

        Hook point: end of search(), after reranking.
        """
        result_ids = [r.get("id", "") for r in results if r.get("id")]

        # Alaya: record which seeds were surfaced
        if self._alaya and result_ids:
            try:
                self._alaya.on_retrieval(
                    query=query,
                    retrieved_ids=result_ids,
                    user_id=user_id,
                )
            except Exception as e:
                logger.debug("Alaya retrieval recording failed: %s", e)

        # Viveka: assess retrieval quality
        if self._viveka:
            try:
                self._viveka.assess_retrieval(
                    query=query,
                    results=results,
                    user_id=user_id,
                )
            except Exception as e:
                logger.debug("Viveka retrieval assessment failed: %s", e)

    def on_answer_generated(
        self,
        query: str,
        answer: str,
        source_memory_ids: List[str],
        source_texts: Optional[List[str]] = None,
        user_id: str = "default",
        task_type: Optional[str] = None,
    ) -> None:
        """Called after an answer is synthesized from memories.

        Hook point: search_orchestrated / answer orchestration output.
        """
        # Alaya: record which seeds actually contributed (ripening)
        if self._alaya and source_memory_ids:
            try:
                self._alaya.on_activation(
                    memory_ids=source_memory_ids,
                    query=query,
                    user_id=user_id,
                )
            except Exception as e:
                logger.debug("Alaya activation recording failed: %s", e)

        # Viveka: assess answer quality
        if self._viveka:
            try:
                self._viveka.assess_answer(
                    query=query,
                    answer=answer,
                    source_memories=source_texts,
                    user_id=user_id,
                )
            except Exception as e:
                logger.debug("Viveka answer assessment failed: %s", e)

        # Samskara: record answer acceptance (implicit positive signal)
        if self._samskara:
            try:
                self._samskara.on_answer_accepted(
                    query=query,
                    answer=answer,
                    memory_ids=source_memory_ids,
                    user_id=user_id,
                )
            except Exception as e:
                logger.debug("Samskara answer recording failed: %s", e)

        # M4.2: close the MetaBuddhi propose → assess → commit/rollback loop.
        # An accepted answer is a positive evaluation of the active strategy
        # (score=1.0). Once _MIN_EVAL_COUNT samples accumulate MetaBuddhi
        # auto-resolves the pending attempt.
        # M4.2b: tag the sample with task_type (if the caller supplied one)
        # so resolution can use group-relative deltas.
        self._feed_meta_buddhi_signal(1.0, task_type=task_type)

        # M3↔M4 bridge: "world didn't push back" signal. For each cited memory,
        # mark its active engram_facts as verified and bump their tier (the
        # downstream-success hook referenced in engram_tiering.py).
        if source_memory_ids:
            self._on_facts_grounded(source_memory_ids)

    def on_answer_corrected(
        self,
        query: str,
        wrong_answer: str,
        correct_answer: str,
        memory_ids: List[str],
        user_id: str = "default",
        task_type: Optional[str] = None,
    ) -> None:
        """Called when a user explicitly corrects an answer.

        This is the highest-value signal for self-evolution.
        """
        if self._samskara:
            try:
                self._samskara.on_answer_corrected(
                    query=query,
                    wrong_answer=wrong_answer,
                    correct_answer=correct_answer,
                    memory_ids=memory_ids,
                    user_id=user_id,
                )
            except Exception as e:
                logger.debug("Samskara correction recording failed: %s", e)

        # M4.2: correction = negative evaluation of the active strategy.
        # Don't bump tiers or mark facts verified on a correction path.
        self._feed_meta_buddhi_signal(0.0, task_type=task_type)

    # ------------------------------------------------------------------
    # M4.2 helpers: close the propose → assess → commit/rollback loop.
    # ------------------------------------------------------------------

    def attach_substrate(self, db) -> None:
        """Wire the engram DB so tier promotion + verification can run.

        Optional. Without it, answer acceptance still feeds MetaBuddhi but
        can't stamp ``last_verified_at`` or bump engram_facts tiers.
        """
        self._substrate_db = db

    def _feed_meta_buddhi_signal(
        self, score: float, *, task_type: Optional[str] = None
    ) -> None:
        if not self._meta_buddhi:
            return
        try:
            self._meta_buddhi.record_evaluation(score, task_type=task_type)
        except Exception as exc:
            logger.debug("MetaBuddhi record_evaluation failed: %s", exc)

    def _on_facts_grounded(self, memory_ids: List[str]) -> None:
        """Mark active engram_facts verified + bump tier on downstream success.

        "Grounded" means the agent cited these memories *and* the user
        accepted the resulting answer. That's the strongest real-world
        signal we have — stronger than raw reaffirmation count — so it
        flows straight to ``promote_on_downstream_success``.
        """
        if not self._substrate_db or not memory_ids:
            return
        try:
            from dhee.core.engram_tiering import promote_on_downstream_success
            from dhee.core.engram_verification import mark_verified

            placeholders = ",".join(["?"] * len(memory_ids))
            with self._substrate_db._get_connection() as conn:
                rows = conn.execute(
                    f"SELECT id FROM engram_facts "
                    f"WHERE memory_id IN ({placeholders}) "
                    f"  AND superseded_by_id IS NULL "
                    f"  AND COALESCE(tier, 'medium') != 'avoid'",
                    tuple(memory_ids),
                ).fetchall()
            for row in rows:
                fact_id = row["id"] if hasattr(row, "keys") else row[0]
                mark_verified(self._substrate_db, fact_id=fact_id)
                promote_on_downstream_success(
                    self._substrate_db, fact_id=fact_id
                )
        except Exception as exc:
            logger.debug("on_facts_grounded failed: %s", exc)

    # ------------------------------------------------------------------
    # M4.3 — session-boundary scheduler for Nididhyasana
    # ------------------------------------------------------------------

    def on_session_end(
        self,
        *,
        reason: str = "session_end",
        force_evolve: bool = False,
    ) -> Dict[str, Any]:
        """Fire the Nididhyasana readiness gate on a real session boundary.

        Called from harness SessionEnd hooks. Runs ``should_evolve()``
        (cheap — just samskara counters + cooldown), persists a gate
        record to ``~/.dhee/nididhyasana/session_gates.jsonl`` with
        (timestamp, verdict, reason), and returns the decision.

        By design, ``evolve()`` is NOT invoked unless ``force_evolve=True``.
        The heavy training cycle is gated on a separate operator action
        until the training-infrastructure relocation (``dhee.training.*``,
        ``dhee.mini.progressive_trainer``) is reunified in M7. Firing the
        gate today still gives operators and ``dhee doctor`` an honest
        signal of when retraining *would* have triggered.
        """
        record: Dict[str, Any] = {
            "ts": __import__("time").time(),
            "reason": reason,
            "gate_fired": False,
            "gate_reason": "nididhyasana not initialized",
            "evolved": False,
        }
        if not self._nididhyasana:
            self._persist_session_gate(record)
            return record
        try:
            should, why = self._nididhyasana.should_evolve()
            record["gate_fired"] = bool(should)
            record["gate_reason"] = why
            if should and force_evolve:
                cycle = self._nididhyasana.evolve()
                if cycle:
                    record["evolved"] = True
                    record["cycle_id"] = cycle.cycle_id
                    record["verdict"] = cycle.verdict
                    record["error"] = cycle.error
        except Exception as exc:
            record["gate_reason"] = f"should_evolve failed: {exc}"
            logger.debug("on_session_end failed: %s", exc)
        self._persist_session_gate(record)
        return record

    def _persist_session_gate(self, record: Dict[str, Any]) -> None:
        path = os.path.join(self._data_dir, "nididhyasana", "session_gates.jsonl")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.debug("session gate persist failed: %s", exc)

    def read_session_gates(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Tail the session-gate log. Used by ``dhee doctor``."""
        path = os.path.join(self._data_dir, "nididhyasana", "session_gates.jsonl")
        if not os.path.exists(path):
            return []
        out: List[Dict[str, Any]] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return []
        return out[-max(1, int(limit)):]

    # ------------------------------------------------------------------
    # Background: evolution check
    # ------------------------------------------------------------------

    def check_evolution(self) -> Optional[Dict[str, Any]]:
        """Check if auto-evolution should trigger. Call periodically.

        Runs two loops:
          1. Nididhyasana: model weight updates (SFT/DPO/RL)
          2. MetaBuddhi: retrieval strategy mutations (propose/evaluate/promote)

        Returns evolution cycle result if triggered, None otherwise.
        """
        result: Optional[Dict[str, Any]] = None

        # 1. Nididhyasana: model training
        if self._nididhyasana:
            try:
                should, reason = self._nididhyasana.should_evolve()
                if should:
                    logger.info("Auto-evolution triggered: %s", reason)
                    cycle = self._nididhyasana.evolve()
                    if cycle:
                        result = {
                            "cycle_id": cycle.cycle_id,
                            "verdict": cycle.verdict,
                            "karma_net": cycle.karma_net,
                            "hot_swapped": cycle.hot_swapped,
                            "error": cycle.error,
                        }
            except Exception as e:
                logger.debug("Nididhyasana check failed: %s", e)

        # 2. MetaBuddhi: strategy improvement proposals
        if self._meta_buddhi and self._samskara:
            try:
                signals = self._samskara.get_training_signals()
                vasana_report = signals.get("vasana_report")
                degrading = signals.get("degrading_dimensions", [])
                if degrading:
                    attempt = self._meta_buddhi.propose_improvement(
                        vasana_report=vasana_report,
                    )
                    if attempt:
                        logger.info("MetaBuddhi proposed: %s", attempt.rationale)
            except Exception as e:
                logger.debug("MetaBuddhi check failed: %s", e)

        return result

    # ------------------------------------------------------------------
    # Status and persistence
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive self-evolution status."""
        status: Dict[str, Any] = {"enabled": True}

        if self._samskara:
            status["samskara"] = self._samskara.get_training_signals()

        if self._viveka:
            status["viveka"] = self._viveka.get_stats()

        if self._alaya:
            status["alaya"] = self._alaya.get_activation_stats()

        if self._nididhyasana:
            status["nididhyasana"] = self._nididhyasana.get_status()

        if self._meta_buddhi:
            status["meta_buddhi"] = self._meta_buddhi.get_stats()

        return status

    def flush(self) -> None:
        """Persist all state. Call on shutdown."""
        if self._samskara:
            try:
                self._samskara.flush()
            except Exception:
                pass

        if self._alaya:
            self._save_alaya_state()

    def _load_alaya_state(self) -> None:
        """Load alaya seed state from disk."""
        path = os.path.join(self._data_dir, "alaya_state.json")
        if not os.path.exists(path) or not self._alaya:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._alaya.load_dict(data)
        except (OSError, json.JSONDecodeError):
            pass

    def _save_alaya_state(self) -> None:
        """Persist alaya seed state to disk."""
        if not self._alaya:
            return
        path = os.path.join(self._data_dir, "alaya_state.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._alaya.to_dict(), f)
        except OSError:
            pass
