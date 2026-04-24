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

        # M4.2+: accepted answers remain positive evidence, but the signal is
        # no longer binary. We score with lightweight outcome features so
        # MetaBuddhi sees better quality gradients than {1,0}.
        score, components = self._compose_meta_buddhi_signal(
            accepted=True,
            metadata={
                "source_memory_count": len(source_memory_ids),
                "answer_chars": len(answer or ""),
            },
        )
        self._feed_meta_buddhi_signal(
            score,
            task_type=task_type,
            source="answer_accepted",
            signal_components=components,
        )

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

        # M4.2+: correction remains a negative signal, but with room for
        # richer severity-aware updates when metadata is available.
        score, components = self._compose_meta_buddhi_signal(
            corrected=True,
            metadata={
                "wrong_answer_chars": len(wrong_answer or ""),
                "correct_answer_chars": len(correct_answer or ""),
                "source_memory_count": len(memory_ids),
            },
        )
        self._feed_meta_buddhi_signal(
            score,
            task_type=task_type,
            source="answer_corrected",
            signal_components=components,
        )

    def record_task_outcome(
        self,
        *,
        task_type: Optional[str] = None,
        outcome_score: Optional[float] = None,
        what_worked: Optional[str] = None,
        what_failed: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        source: str = "task_outcome",
    ) -> Optional[float]:
        """Feed a structured session/task outcome into MetaBuddhi.

        This is the higher-quality signal path for strategy evaluation:
        it blends explicit outcome score (if present) with correction/test/
        rollback context instead of relying on binary accept/correct events.
        """
        if not self._meta_buddhi:
            return None
        meta = dict(metadata or {})
        if what_worked:
            meta["what_worked"] = what_worked
        if what_failed:
            meta["what_failed"] = what_failed
        score, components = self._compose_meta_buddhi_signal(
            explicit_outcome_score=outcome_score,
            metadata=meta,
        )
        self._feed_meta_buddhi_signal(
            score,
            task_type=task_type,
            source=source,
            signal_components=components,
        )
        return score

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
        self,
        score: float,
        *,
        task_type: Optional[str] = None,
        source: str = "unknown",
        signal_components: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self._meta_buddhi:
            return
        try:
            self._meta_buddhi.record_evaluation(
                score,
                task_type=task_type,
                source=source,
                signal_components=signal_components,
            )
        except Exception as exc:
            logger.debug("MetaBuddhi record_evaluation failed: %s", exc)

    def _compose_meta_buddhi_signal(
        self,
        *,
        accepted: bool = False,
        corrected: bool = False,
        explicit_outcome_score: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> tuple[float, Dict[str, Any]]:
        """Compute a bounded [0,1] evaluation score from outcome components."""
        meta = dict(metadata or {})
        score = 0.5
        if accepted:
            score += 0.25
        if corrected:
            score -= 0.35

        clamped_outcome: Optional[float] = None
        if explicit_outcome_score is not None:
            clamped_outcome = self._clamp01(explicit_outcome_score)
            score = 0.4 * score + 0.6 * clamped_outcome

        if meta.get("what_worked"):
            score += 0.07
        if meta.get("what_failed"):
            score -= 0.10

        tests_passed = self._coerce_int(meta.get("tests_passed"))
        tests_failed = self._coerce_int(meta.get("tests_failed"))
        if tests_passed is not None or tests_failed is not None:
            passed = max(0, tests_passed or 0)
            failed = max(0, tests_failed or 0)
            total = passed + failed
            if total > 0:
                pass_rate = passed / total
                score += (pass_rate - 0.5) * 0.30
                meta["pass_rate"] = round(pass_rate, 4)

        correction_count = self._coerce_int(meta.get("correction_count"))
        if correction_count and correction_count > 0:
            score -= min(0.20, 0.03 * correction_count)

        reverted = self._coerce_bool(meta.get("reverted"))
        if reverted:
            score -= 0.15

        final = self._clamp01(score)
        components = {
            "accepted": bool(accepted),
            "corrected": bool(corrected),
            "explicit_outcome_score": clamped_outcome,
            "tests_passed": tests_passed,
            "tests_failed": tests_failed,
            "correction_count": correction_count,
            "reverted": bool(reverted),
            "score_before_clamp": round(score, 6),
            "final_score": final,
        }
        if "pass_rate" in meta:
            components["pass_rate"] = meta["pass_rate"]
        return final, components

    @staticmethod
    def _clamp01(value: float) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.5

    @staticmethod
    def _coerce_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        if isinstance(value, (int, float)):
            return bool(value)
        return False

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
