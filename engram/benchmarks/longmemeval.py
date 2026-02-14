"""LongMemEval runner for Engram (Colab-friendly).

Usage:
    python -m engram.benchmarks.longmemeval --dataset-path ... --output-jsonl ...
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from engram import Memory
from engram.configs.base import (
    CategoryMemConfig,
    EchoMemConfig,
    EmbedderConfig,
    KnowledgeGraphConfig,
    LLMConfig,
    MemoryConfig,
    ProfileConfig,
    SceneConfig,
    VectorStoreConfig,
)


SESSION_ID_PATTERN = re.compile(r"^Session ID:\s*(?P<session_id>\S+)\s*$", re.MULTILINE)
HISTORY_HEADER = "User Transcript:"


def extract_user_only_text(session_turns: Sequence[Dict[str, Any]]) -> str:
    """Convert one LongMemEval session into newline-separated user text."""
    lines = [str(turn.get("content", "")).strip() for turn in session_turns if turn.get("role") == "user"]
    return "\n".join([line for line in lines if line])


def format_session_memory(session_id: str, session_date: str, session_turns: Sequence[Dict[str, Any]]) -> str:
    """Create a memory payload that preserves session metadata in plain text."""
    user_text = extract_user_only_text(session_turns)
    return (
        f"Session ID: {session_id}\n"
        f"Session Date: {session_date}\n"
        f"{HISTORY_HEADER}\n"
        f"{user_text}"
    )


def parse_session_id_from_result(result: Dict[str, Any]) -> Optional[str]:
    """Extract session_id from memory metadata or fallback text header."""
    metadata = result.get("metadata") or {}
    sid = metadata.get("session_id")
    if sid:
        return str(sid)
    memory_text = str(result.get("memory", "") or "")
    match = SESSION_ID_PATTERN.search(memory_text)
    if match:
        return match.group("session_id")
    return None


def dedupe_preserve_order(items: Iterable[str]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def compute_session_metrics(retrieved_session_ids: Sequence[str], answer_session_ids: Sequence[str]) -> Dict[str, float]:
    """Compute simple retrieval metrics over session IDs."""
    retrieved = dedupe_preserve_order([str(x) for x in retrieved_session_ids if str(x).strip()])
    gold = {str(x) for x in answer_session_ids if str(x).strip()}

    metrics: Dict[str, float] = {}
    for k in (1, 3, 5, 10):
        top_k = set(retrieved[:k])
        metrics[f"recall_any@{k}"] = 1.0 if gold and bool(top_k & gold) else 0.0
        metrics[f"recall_all@{k}"] = 1.0 if gold and gold.issubset(top_k) else 0.0
    return metrics


def build_answer_prompt(question: str, retrieved_context: str) -> str:
    return (
        "You are answering a LongMemEval memory question.\n"
        "Use only the retrieved history context. If the answer is missing, say: "
        "\"I don't have enough information in memory.\"\n\n"
        "Retrieved history:\n"
        f"{retrieved_context}\n\n"
        f"Question: {question}\n"
        "Answer concisely:"
    )


@dataclass
class HFResponder:
    model_name: str
    max_new_tokens: int = 128

    def __post_init__(self) -> None:
        # Lazy heavy import so module-level import stays lightweight.
        try:
            from transformers import pipeline
        except ImportError as exc:
            raise ImportError(
                "HF backend requires transformers (and torch). "
                "Install with: pip install transformers accelerate"
            ) from exc

        self._pipeline = pipeline(
            "text-generation",
            model=self.model_name,
            tokenizer=self.model_name,
            device_map="auto",
            model_kwargs={"torch_dtype": "auto"},
        )

    def generate(self, prompt: str) -> str:
        outputs = self._pipeline(
            prompt,
            max_new_tokens=self.max_new_tokens,
            do_sample=False,
            return_full_text=False,
        )
        if not outputs:
            return ""
        text = outputs[0].get("generated_text", "")
        return str(text).strip()


def build_memory(
    *,
    llm_provider: str,
    embedder_provider: str,
    vector_store_provider: str,
    embedding_dims: int,
    history_db_path: str,
    llm_model: Optional[str] = None,
    embedder_model: Optional[str] = None,
    full_potential: bool = True,
) -> Memory:
    """Build Engram Memory for LongMemEval. By default uses full potential (echo, categories, graph, scenes, profiles)."""
    vector_cfg: Dict[str, Any] = {
        "collection_name": "engram_longmemeval",
        "embedding_model_dims": embedding_dims,
    }

    llm_cfg: Dict[str, Any] = {}
    if llm_model:
        llm_cfg["model"] = llm_model
    embedder_cfg: Dict[str, Any] = {"embedding_dims": embedding_dims}
    if embedder_model:
        embedder_cfg["model"] = embedder_model

    config = MemoryConfig(
        vector_store=VectorStoreConfig(provider=vector_store_provider, config=vector_cfg),
        llm=LLMConfig(provider=llm_provider, config=llm_cfg),
        embedder=EmbedderConfig(provider=embedder_provider, config=embedder_cfg),
        history_db_path=history_db_path,
        embedding_model_dims=embedding_dims,
        echo=EchoMemConfig(enable_echo=full_potential),
        category=CategoryMemConfig(use_llm_categorization=full_potential, enable_categories=full_potential),
        graph=KnowledgeGraphConfig(enable_graph=full_potential),
        scene=SceneConfig(use_llm_summarization=full_potential, enable_scenes=full_potential),
        profile=ProfileConfig(use_llm_extraction=full_potential, enable_profiles=full_potential),
    )
    return Memory(config)


def build_context_text(results: Sequence[Dict[str, Any]], max_chars: int) -> str:
    chunks: List[str] = []
    total = 0
    for result in results:
        if result.get("masked"):
            continue
        text = str(result.get("memory") or result.get("details") or "").strip()
        if not text:
            continue
        if total + len(text) > max_chars and chunks:
            break
        chunks.append(text)
        total += len(text)
    if not chunks:
        return "No relevant retrieved history."
    return "\n\n".join(chunks)


def build_output_row(
    *,
    question_id: str,
    hypothesis: str,
    retrieved_session_ids: Sequence[str],
    retrieval_metrics: Dict[str, float],
    include_debug_fields: bool,
) -> Dict[str, Any]:
    """Build evaluator-compatible output row with optional debug fields."""
    row: Dict[str, Any] = {
        "question_id": question_id,
        "hypothesis": hypothesis,
    }
    if include_debug_fields:
        row["retrieved_session_ids"] = list(retrieved_session_ids)
        row["retrieval_metrics"] = dict(retrieval_metrics)
    return row


def run_longmemeval(args: argparse.Namespace) -> Dict[str, Any]:
    with open(args.dataset_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)
    if not isinstance(dataset, list):
        raise ValueError("Dataset file must be a JSON list of instances.")

    selected = dataset[args.start_index : args.end_index if args.end_index > 0 else None]
    if args.max_questions > 0:
        selected = selected[: args.max_questions]
    if args.skip_abstention:
        selected = [entry for entry in selected if "_abs" not in str(entry.get("question_id", ""))]

    memory = build_memory(
        llm_provider=args.llm_provider,
        embedder_provider=args.embedder_provider,
        vector_store_provider=args.vector_store_provider,
        embedding_dims=args.embedding_dims,
        history_db_path=args.history_db_path,
        llm_model=args.llm_model,
        embedder_model=args.embedder_model,
        full_potential=args.full_potential,
    )

    hf_responder: Optional[HFResponder] = None
    if args.answer_backend == "hf":
        hf_responder = HFResponder(model_name=args.hf_model, max_new_tokens=args.hf_max_new_tokens)

    output_path = Path(args.output_jsonl)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    retrieval_path = Path(args.retrieval_jsonl) if args.retrieval_jsonl else None
    if retrieval_path:
        retrieval_path.parent.mkdir(parents=True, exist_ok=True)

    per_question_metrics: List[Dict[str, float]] = []
    processed = 0

    with output_path.open("w", encoding="utf-8") as out_f:
        retrieval_f = retrieval_path.open("w", encoding="utf-8") if retrieval_path else None
        try:
            for entry in selected:
                question_id = str(entry.get("question_id", ""))
                if not question_id:
                    continue

                # Keep each question isolated.
                memory.delete_all(user_id=args.user_id)

                session_ids = entry.get("haystack_session_ids") or []
                session_dates = entry.get("haystack_dates") or []
                sessions = entry.get("haystack_sessions") or []
                for sess_id, sess_date, sess_turns in zip(session_ids, session_dates, sessions):
                    payload = format_session_memory(str(sess_id), str(sess_date), sess_turns or [])
                    memory.add(
                        messages=payload,
                        user_id=args.user_id,
                        metadata={
                            "session_id": str(sess_id),
                            "session_date": str(sess_date),
                            "question_id": question_id,
                        },
                        categories=["longmemeval", "session"],
                        infer=False,
                    )

                query = str(entry.get("question", "")).strip()
                search_payload = memory.search_with_context(
                    query=query,
                    user_id=args.user_id,
                    limit=args.top_k,
                )
                results = search_payload.get("results", [])

                retrieved_session_ids = dedupe_preserve_order(
                    [
                        sid
                        for sid in [parse_session_id_from_result(result) for result in results]
                        if sid is not None
                    ]
                )
                metrics = compute_session_metrics(
                    retrieved_session_ids=retrieved_session_ids,
                    answer_session_ids=entry.get("answer_session_ids", []),
                )
                per_question_metrics.append(metrics)

                context = build_context_text(results, max_chars=args.max_context_chars)
                prompt = build_answer_prompt(question=query, retrieved_context=context)

                if args.answer_backend == "hf":
                    assert hf_responder is not None
                    hypothesis = hf_responder.generate(prompt)
                else:
                    hypothesis = str(memory.llm.generate(prompt)).strip()

                output_row = build_output_row(
                    question_id=question_id,
                    hypothesis=hypothesis,
                    retrieved_session_ids=retrieved_session_ids[: args.top_k],
                    retrieval_metrics=metrics,
                    include_debug_fields=args.include_debug_fields,
                )
                out_f.write(json.dumps(output_row, ensure_ascii=False) + "\n")

                if retrieval_f is not None:
                    retrieval_row = {
                        "question_id": question_id,
                        "answer_session_ids": entry.get("answer_session_ids", []),
                        "retrieved_session_ids": retrieved_session_ids[: args.top_k],
                        "metrics": metrics,
                    }
                    retrieval_f.write(json.dumps(retrieval_row, ensure_ascii=False) + "\n")

                processed += 1
                if args.print_every > 0 and processed % args.print_every == 0:
                    print(f"[LongMemEval] processed={processed} question_id={question_id}")
        finally:
            if retrieval_f is not None:
                retrieval_f.close()

    aggregate: Dict[str, float] = {}
    if per_question_metrics:
        for key in sorted(per_question_metrics[0].keys()):
            aggregate[key] = round(mean(metric[key] for metric in per_question_metrics), 4)

    summary = {
        "processed": processed,
        "output_jsonl": str(output_path),
        "retrieval_jsonl": str(retrieval_path) if retrieval_path else None,
        "aggregate_retrieval_metrics": aggregate,
        "answer_backend": args.answer_backend,
        "hf_model": args.hf_model if args.answer_backend == "hf" else None,
    }
    print(json.dumps(summary, indent=2))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Engram on LongMemEval in Colab or local environments.")
    parser.add_argument("--dataset-path", required=True, help="Path to LongMemEval json file.")
    parser.add_argument("--output-jsonl", required=True, help="Path to write question_id/hypothesis jsonl.")
    parser.add_argument("--retrieval-jsonl", default=None, help="Optional path for retrieval-only log jsonl.")
    parser.add_argument(
        "--include-debug-fields",
        action="store_true",
        help="Include retrieval debug fields in output jsonl rows (official evaluator only needs question_id/hypothesis).",
    )

    parser.add_argument(
        "--minimal",
        action="store_true",
        help="Disable echo, categories, graph, scenes, profiles (faster but lower retrieval quality). Default is full potential.",
    )
    parser.add_argument("--user-id", default="longmemeval", help="User scope used for temporary ingestion.")
    parser.add_argument("--start-index", type=int, default=0, help="Start offset for dataset slicing.")
    parser.add_argument("--end-index", type=int, default=-1, help="End offset for dataset slicing (exclusive).")
    parser.add_argument("--max-questions", type=int, default=-1, help="Cap number of evaluated questions.")
    parser.add_argument("--skip-abstention", action="store_true", help="Skip *_abs questions.")

    parser.add_argument("--top-k", type=int, default=8, help="Number of retrieved memories for context.")
    parser.add_argument("--max-context-chars", type=int, default=12000, help="Maximum context size passed to reader.")
    parser.add_argument("--print-every", type=int, default=25, help="Progress print interval.")

    parser.add_argument(
        "--answer-backend",
        choices=["hf", "engram-llm"],
        default="hf",
        help="Reader backend for hypothesis generation.",
    )
    parser.add_argument("--hf-model", default="Qwen/Qwen2.5-1.5B-Instruct", help="HF model when --answer-backend hf.")
    parser.add_argument("--hf-max-new-tokens", type=int, default=128, help="Generation cap for HF backend.")

    parser.add_argument(
        "--llm-provider",
        choices=["mock", "gemini", "openai", "ollama", "nvidia"],
        default="mock",
        help="Engram LLM provider (used for --answer-backend engram-llm).",
    )
    parser.add_argument("--llm-model", default=None, help="Optional LLM model override.")
    parser.add_argument(
        "--embedder-provider",
        choices=["simple", "gemini", "openai", "ollama", "nvidia"],
        default="simple",
        help="Engram embedder provider for retrieval.",
    )
    parser.add_argument("--embedder-model", default=None, help="Optional embedder model override.")
    parser.add_argument("--embedding-dims", type=int, default=1536, help="Embedding dimensions for simple/memory configs.")
    parser.add_argument("--vector-store-provider", choices=["memory", "sqlite_vec"], default="memory")
    parser.add_argument("--history-db-path", default="/content/engram-longmemeval.db", help="SQLite db path.")
    args = parser.parse_args()
    args.full_potential = not args.minimal
    return args


def main() -> None:
    args = parse_args()
    run_longmemeval(args)


if __name__ == "__main__":
    main()
