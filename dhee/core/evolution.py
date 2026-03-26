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
                from dhee.training.nididhyasana import NididhyasanaLoop
                self._nididhyasana = NididhyasanaLoop(
                    samskara=self._samskara,
                    viveka=self._viveka,
                    alaya=self._alaya,
                    dhee_dir=self._data_dir,
                )
            except Exception as e:
                logger.debug("Nididhyasana init skipped: %s", e)

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

    def on_answer_corrected(
        self,
        query: str,
        wrong_answer: str,
        correct_answer: str,
        memory_ids: List[str],
        user_id: str = "default",
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

    # ------------------------------------------------------------------
    # Background: evolution check
    # ------------------------------------------------------------------

    def check_evolution(self) -> Optional[Dict[str, Any]]:
        """Check if auto-evolution should trigger. Call periodically.

        Returns evolution cycle result if triggered, None otherwise.
        """
        if not self._nididhyasana:
            return None

        try:
            should, reason = self._nididhyasana.should_evolve()
            if should:
                logger.info("Auto-evolution triggered: %s", reason)
                cycle = self._nididhyasana.evolve()
                if cycle:
                    return {
                        "cycle_id": cycle.cycle_id,
                        "verdict": cycle.verdict,
                        "karma_net": cycle.karma_net,
                        "hot_swapped": cycle.hot_swapped,
                        "error": cycle.error,
                    }
        except Exception as e:
            logger.debug("Evolution check failed: %s", e)

        return None

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
