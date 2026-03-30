"""Dhee — Python SDK. 4 methods, zero config.

    from dhee import Dhee

    d = Dhee()                                          # auto-detects API key from env
    d.remember("User prefers dark mode")                # store (0 LLM, 1 embed)
    results = d.recall("what theme?")                   # search (0 LLM, 1 embed)
    ctx = d.context("fixing auth bug")                  # HyperAgent bootstrap
    d.checkpoint("Fixed auth bug", what_worked="...")    # save + enrich + reflect

Environment Variables:
    OPENAI_API_KEY    — OpenAI (recommended, cheapest embeddings)
    GEMINI_API_KEY    — Google Gemini

No env vars? Falls back to in-memory mock (for testing).
For local/free: pip install dhee[ollama] and run Ollama.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class Dhee:
    """Cognition as a Service. 4 methods that mirror the 4 MCP tools.

    Args:
        user_id: Default user identifier. Default: "default".

    Everything else is auto-configured from environment variables.
    """

    def __init__(self, user_id: str = "default"):
        self.user_id = user_id
        self._memory = None
        self._buddhi = None

    @property
    def _mem(self):
        """Lazy-init memory with deferred enrichment (0 LLM on hot path)."""
        if self._memory is None:
            from engram.mcp_server import get_memory_instance
            self._memory = get_memory_instance()
            # Enable deferred enrichment: store fast, enrich at checkpoint
            if hasattr(self._memory, "config") and hasattr(self._memory.config, "enrichment"):
                self._memory.config.enrichment.defer_enrichment = True
                self._memory.config.enrichment.enable_unified = True
        return self._memory

    @property
    def _bud(self):
        """Lazy-init Buddhi (proactive cognition layer)."""
        if self._buddhi is None:
            from engram.core.buddhi import Buddhi
            self._buddhi = Buddhi()
        return self._buddhi

    # ------------------------------------------------------------------
    # 1. remember — store a fact (0 LLM, 1 embed)
    # ------------------------------------------------------------------

    def remember(self, content: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Store a fact, preference, or observation.

        Fast: 0 LLM calls, 1 embedding. Echo enrichment deferred to checkpoint().

        Args:
            content: What to remember.
            user_id: Override default user_id.

        Returns:
            {"stored": True, "id": "memory_id"}

        Example:
            d.remember("User prefers Python over JavaScript")
            d.remember("Project uses FastAPI + PostgreSQL")
        """
        uid = user_id or self.user_id
        result = self._mem.add(
            messages=content,
            user_id=uid,
            agent_id="dhee-sdk",
            source_app="dhee-sdk",
            infer=False,
        )

        # Buddhi: auto-detect intentions
        self._bud.on_memory_stored(content=content, user_id=uid)

        response: Dict[str, Any] = {"stored": True}
        if isinstance(result, dict):
            results = result.get("results", [])
            if results:
                response["id"] = results[0].get("id")
        return response

    # ------------------------------------------------------------------
    # 2. recall — search memory (0 LLM, 1 embed)
    # ------------------------------------------------------------------

    def recall(
        self, query: str, limit: int = 5, user_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Search memory for relevant facts. Returns top-K results.

        Fast: 0 LLM calls, 1 embedding.

        Args:
            query: What you're looking for.
            limit: Max results (default 5, max 20).
            user_id: Override default user_id.

        Returns:
            List of {"id", "memory", "score"} dicts.

        Example:
            results = d.recall("what programming language?")
            for r in results:
                print(r["memory"], r["score"])
        """
        uid = user_id or self.user_id
        limit = min(max(1, limit), 20)

        result = self._mem.search(query=query, user_id=uid, limit=limit)
        raw = result.get("results", [])

        return [
            {
                "id": r.get("id"),
                "memory": r.get("memory", ""),
                "score": round(r.get("composite_score", r.get("score", 0)), 3),
            }
            for r in raw
        ]

    # ------------------------------------------------------------------
    # 3. context — HyperAgent bootstrap
    # ------------------------------------------------------------------

    def context(
        self, task_description: Optional[str] = None, user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """HyperAgent session bootstrap. Call once at start.

        Returns everything: performance trends, synthesized insights,
        relevant skills, pending intentions, proactive warnings, top memories.

        Args:
            task_description: What you're about to work on (for relevance filtering).
            user_id: Override default user_id.

        Returns:
            HyperContext dict with keys: performance, insights, intentions,
            warnings, memories, last_session, meta.

        Example:
            ctx = d.context("fixing the auth bug in login.py")
            if ctx["warnings"]:
                print("Watch out:", ctx["warnings"])
            if ctx["insights"]:
                print("From past runs:", ctx["insights"][0]["content"])
        """
        uid = user_id or self.user_id
        hyper = self._bud.get_hyper_context(
            user_id=uid,
            task_description=task_description,
            memory=self._mem,
        )
        return hyper.to_dict()

    # ------------------------------------------------------------------
    # 4. checkpoint — save session + enrich + reflect
    # ------------------------------------------------------------------

    def checkpoint(
        self,
        summary: str,
        *,
        status: str = "paused",
        task_type: Optional[str] = None,
        outcome_score: Optional[float] = None,
        what_worked: Optional[str] = None,
        what_failed: Optional[str] = None,
        key_decision: Optional[str] = None,
        remember_to: Optional[str] = None,
        trigger_keywords: Optional[List[str]] = None,
        decisions: Optional[List[str]] = None,
        todos: Optional[List[str]] = None,
        files_touched: Optional[List[str]] = None,
        repo: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Save session state + batch-enrich + record outcome + reflect.

        Call before ending a session. This is where the magic happens:
        - Saves session digest for cross-agent handoff
        - Batch-enriches stored memories (1 LLM call per ~10 memories)
        - Records task outcome for performance tracking
        - Synthesizes insights from reflection
        - Stores future intentions (prospective memory)

        Args:
            summary: What you were working on.
            status: "active", "paused", or "completed".
            task_type: Category for performance tracking (e.g., "bug_fix").
            outcome_score: 0.0-1.0 score for this task.
            what_worked: Strategy that worked (becomes insight).
            what_failed: Strategy that failed (becomes warning).
            key_decision: Important decision and rationale.
            remember_to: Future intention ("remember to X when Y").
            trigger_keywords: Keywords that trigger the intention.
            decisions: Key decisions made.
            todos: Remaining work items.
            files_touched: Files modified.
            repo: Repository/project path.
            user_id: Override default user_id.

        Returns:
            Dict with keys: session_saved, memories_enriched, outcome_recorded,
            insights_created, intention_stored.

        Example:
            d.checkpoint(
                "Fixed auth bug in login.py",
                task_type="bug_fix",
                outcome_score=1.0,
                what_worked="git blame → found the commit that broke it",
                remember_to="run auth tests after any login.py changes",
                trigger_keywords=["login", "auth"],
            )
        """
        uid = user_id or self.user_id
        result: Dict[str, Any] = {}

        # 1. Session digest
        try:
            from engram.core.kernel import save_session_digest
            digest = save_session_digest(
                task_summary=summary,
                agent_id="dhee-sdk",
                repo=repo,
                status=status,
                decisions_made=decisions,
                files_touched=files_touched,
                todos_remaining=todos,
            )
            result["session_saved"] = True
            if isinstance(digest, dict):
                result["session_id"] = digest.get("session_id")
        except Exception as e:
            logger.debug("Session save skipped: %s", e)
            result["session_saved"] = False

        # 2. Batch-enrich deferred memories
        if hasattr(self._mem, "enrich_pending"):
            try:
                enrich = self._mem.enrich_pending(
                    user_id=uid, batch_size=10, max_batches=5,
                )
                enriched = enrich.get("enriched_count", 0)
                if enriched > 0:
                    result["memories_enriched"] = enriched
            except Exception as e:
                logger.debug("Batch enrichment skipped: %s", e)

        # 3. Record outcome
        if task_type and outcome_score is not None:
            score = max(0.0, min(1.0, float(outcome_score)))
            insight = self._bud.record_outcome(
                user_id=uid, task_type=task_type, score=score,
            )
            result["outcome_recorded"] = True
            if insight:
                result["auto_insight"] = insight.to_dict()

        # 4. Reflect
        if any([what_worked, what_failed, key_decision]):
            reflections = self._bud.reflect(
                user_id=uid,
                task_type=task_type or "general",
                what_worked=what_worked,
                what_failed=what_failed,
                key_decision=key_decision,
            )
            result["insights_created"] = len(reflections)

        # 5. Store intention
        if remember_to:
            intention = self._bud.store_intention(
                user_id=uid,
                description=remember_to,
                trigger_keywords=trigger_keywords,
            )
            result["intention_stored"] = intention.to_dict()

        return result

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Flush Buddhi state and clean up."""
        if self._buddhi:
            self._buddhi.flush()
        if self._memory and hasattr(self._memory, "close"):
            self._memory.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """CLI entry point: dhee remember/recall/context/checkpoint."""
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="dhee",
        description="Dhee — Cognition as a Service",
    )
    sub = parser.add_subparsers(dest="command")

    # remember
    p = sub.add_parser("remember", help="Store a fact or preference")
    p.add_argument("content", help="What to remember")
    p.add_argument("--user", default="default")

    # recall
    p = sub.add_parser("recall", help="Search memory")
    p.add_argument("query", help="What to search for")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--user", default="default")

    # context
    p = sub.add_parser("context", help="HyperAgent session bootstrap")
    p.add_argument("--task", default=None, help="Task description")
    p.add_argument("--user", default="default")

    # checkpoint
    p = sub.add_parser("checkpoint", help="Save session + enrich + reflect")
    p.add_argument("summary", help="What you were working on")
    p.add_argument("--status", default="paused", choices=["active", "paused", "completed"])
    p.add_argument("--task-type", default=None)
    p.add_argument("--score", type=float, default=None)
    p.add_argument("--what-worked", default=None)
    p.add_argument("--what-failed", default=None)
    p.add_argument("--user", default="default")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    d = Dhee(user_id=args.user)
    try:
        if args.command == "remember":
            result = d.remember(args.content)
        elif args.command == "recall":
            result = d.recall(args.query, limit=args.limit)
        elif args.command == "context":
            result = d.context(task_description=args.task)
        elif args.command == "checkpoint":
            result = d.checkpoint(
                args.summary,
                status=args.status,
                task_type=args.task_type,
                outcome_score=args.score,
                what_worked=args.what_worked,
                what_failed=args.what_failed,
            )
        else:
            parser.print_help()
            return
        print(json.dumps(result, indent=2, default=str))
    finally:
        d.close()


if __name__ == "__main__":
    main()
