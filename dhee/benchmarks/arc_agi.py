"""ARC-AGI benchmark runner for Engram.

Tests abstract reasoning using Engram memory + LLM on Chollet's
Abstraction and Reasoning Corpus (ARC-AGI).

Two modes:
  1. Direct: LLM sees training examples and predicts test output.
  2. Memory-augmented: solved patterns stored in Engram memory;
     similar patterns retrieved as extra context for new tasks.

Usage:
    python -m engram.benchmarks.arc_agi \
        --data-dir data/arc-agi/evaluation \
        --max-tasks 50 \
        --mode memory
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Grid helpers ──────────────────────────────────────────

COLOR_NAMES = {
    0: "black", 1: "blue", 2: "red", 3: "green", 4: "yellow",
    5: "grey", 6: "magenta", 7: "orange", 8: "cyan", 9: "maroon",
}


def grid_to_text(grid: List[List[int]]) -> str:
    """Render a grid as a compact text block with row/col indices."""
    rows = len(grid)
    cols = len(grid[0]) if grid else 0
    header = "   " + " ".join(f"{c:>2}" for c in range(cols))
    lines = [f"({rows}x{cols} grid)", header]
    for r, row in enumerate(grid):
        lines.append(f"{r:>2} " + " ".join(f"{v:>2}" for v in row))
    return "\n".join(lines)


def grids_equal(a: List[List[int]], b: List[List[int]]) -> bool:
    if len(a) != len(b):
        return False
    return all(row_a == row_b for row_a, row_b in zip(a, b))


def parse_grid_from_text(text: str, expected_rows: int = 0, expected_cols: int = 0) -> Optional[List[List[int]]]:
    """Best-effort parse a grid from LLM output text."""
    # Strip thinking blocks (e.g. <think>...</think> from reasoning models)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    # Try JSON array first
    try:
        # Find the outermost [[...]] pattern
        match = re.search(r"\[\s*\[.*?\]\s*\]", text, re.DOTALL)
        if match:
            grid = json.loads(match.group())
            if isinstance(grid, list) and all(isinstance(r, list) for r in grid):
                return grid
    except (json.JSONDecodeError, ValueError):
        pass

    # Try row-by-row number extraction
    lines = text.strip().split("\n")
    grid = []
    for line in lines:
        nums = re.findall(r"\b(\d)\b", line)
        if nums and len(nums) >= 2:
            grid.append([int(n) for n in nums])
    if grid and len(grid) >= 2:
        # Normalize column counts
        max_cols = max(len(r) for r in grid)
        if all(len(r) == max_cols for r in grid):
            return grid

    return None


# ── Prompt construction ───────────────────────────────────

def format_task_prompt(
    train_pairs: List[Dict[str, Any]],
    test_input: List[List[int]],
    memory_context: str = "",
) -> str:
    """Build the LLM prompt for one ARC task."""
    parts = [
        "You are solving an ARC-AGI abstract reasoning puzzle.",
        "Each puzzle has training examples showing input→output grid transformations.",
        "Find the pattern and apply it to the test input.",
        "",
    ]

    if memory_context:
        parts.extend([
            "Here are similar patterns you solved before:",
            memory_context,
            "",
        ])

    for i, pair in enumerate(train_pairs):
        parts.append(f"=== Training Example {i+1} ===")
        parts.append("Input:")
        parts.append(grid_to_text(pair["input"]))
        parts.append("Output:")
        parts.append(grid_to_text(pair["output"]))
        parts.append("")

    parts.append("=== Test ===")
    parts.append("Input:")
    parts.append(grid_to_text(test_input))
    parts.append("")
    parts.append(
        "Analyze the pattern from the training examples. "
        "Then output ONLY the predicted output grid as a JSON 2D array (e.g. [[0,1],[2,3]]). "
        "No explanation, just the JSON array."
    )

    return "\n".join(parts)


def describe_pattern(
    train_pairs: List[Dict[str, Any]],
    test_input: List[List[int]],
    test_output: List[List[int]],
) -> str:
    """Create a textual description of a solved task for memory storage."""
    in_shapes = [f"{len(p['input'])}x{len(p['input'][0])}" for p in train_pairs]
    out_shapes = [f"{len(p['output'])}x{len(p['output'][0])}" for p in train_pairs]
    test_in_shape = f"{len(test_input)}x{len(test_input[0])}"
    test_out_shape = f"{len(test_output)}x{len(test_output[0])}"

    unique_vals = set()
    for p in train_pairs:
        for row in p["input"]:
            unique_vals.update(row)
        for row in p["output"]:
            unique_vals.update(row)

    return (
        f"ARC pattern: input shapes {in_shapes}, output shapes {out_shapes}. "
        f"Test: {test_in_shape} → {test_out_shape}. "
        f"Colors used: {sorted(unique_vals)}. "
        f"Training examples: {len(train_pairs)}."
    )


# ── Benchmark runner ──────────────────────────────────────

def load_tasks(data_dir: str) -> Dict[str, Dict[str, Any]]:
    tasks = {}
    for path in sorted(Path(data_dir).glob("*.json")):
        with open(path) as f:
            tasks[path.stem] = json.load(f)
    return tasks


def run_arc_benchmark(args: argparse.Namespace) -> Dict[str, Any]:
    # Load .env (check CWD first, then project root)
    env_path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(env_path):
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

    tasks = load_tasks(args.data_dir)
    task_ids = sorted(tasks.keys())
    if args.max_tasks > 0:
        task_ids = task_ids[: args.max_tasks]

    print(f"ARC-AGI Benchmark: {len(task_ids)} tasks from {args.data_dir}")
    print(f"Mode: {args.mode}")
    print(f"LLM: {args.llm_provider}/{args.llm_model}")
    print()

    # Build LLM
    from dhee.utils.factory import LLMFactory
    llm_config = {
        "model": args.llm_model,
        "temperature": args.temperature,
        "top_p": args.top_p,
        "max_tokens": args.max_tokens,
        "timeout": args.timeout,
        "enable_thinking": args.enable_thinking,
    }
    if args.api_key:
        llm_config["api_key"] = args.api_key
    llm = LLMFactory.create(args.llm_provider, llm_config)

    # Build memory (only in memory mode)
    memory = None
    if args.mode == "memory":
        from dhee.configs.base import MemoryConfig
        from dhee.memory.main import FullMemory

        tmpdir = tempfile.mkdtemp(prefix="arc_bench_")
        config = MemoryConfig(
            vector_store={"provider": "memory", "config": {}},
            llm={"provider": args.llm_provider, "config": {"model": args.llm_model, "temperature": args.temperature, "max_tokens": args.max_tokens, "timeout": args.timeout}},
            embedder={"provider": args.embedder_provider, "config": {"model": args.embedder_model}},
            history_db_path=os.path.join(tmpdir, "arc.db"),
            embedding_model_dims=args.embedding_dims,
            echo={"enable_echo": False},
            category={"enable_categories": False},
            graph={"enable_graph": False},
            scene={"enable_scenes": False},
            profile={"enable_profiles": False},
        )
        memory = FullMemory(config)
        print(f"Embedder: {args.embedder_provider}/{args.embedder_model}")
        print(f"Memory DB: {tmpdir}")
        print()

    solved = 0
    failed = 0
    errored = 0
    results = []
    t_start = time.time()

    for idx, task_id in enumerate(task_ids):
        task = tasks[task_id]
        train_pairs = task["train"]
        test_cases = task["test"]

        for test_idx, test_case in enumerate(test_cases):
            test_input = test_case["input"]
            expected_output = test_case["output"]

            # Memory retrieval
            memory_context = ""
            if memory and idx > 0:
                try:
                    query = describe_pattern(train_pairs, test_input, [])
                    search_results = memory.search(query=query, user_id="arc", limit=3)
                    hits = search_results.get("results", [])
                    if hits:
                        memory_context = "\n".join(
                            f"- {h.get('memory', '')[:200]}" for h in hits
                        )
                except Exception as e:
                    logger.debug("Memory search failed: %s", e)

            prompt = format_task_prompt(train_pairs, test_input, memory_context)

            try:
                response = llm.generate(prompt)
                predicted = parse_grid_from_text(response)

                if predicted and grids_equal(predicted, expected_output):
                    solved += 1
                    status = "SOLVED"

                    # Store solved pattern in memory
                    if memory:
                        try:
                            pattern_desc = describe_pattern(
                                train_pairs, test_input, expected_output
                            )
                            memory.add(
                                messages=[{"role": "user", "content": pattern_desc}],
                                user_id="arc",
                                infer=False,
                            )
                        except Exception:
                            pass
                else:
                    failed += 1
                    status = "WRONG"

                results.append({
                    "task_id": task_id,
                    "test_idx": test_idx,
                    "status": status,
                    "predicted": predicted,
                    "expected_shape": f"{len(expected_output)}x{len(expected_output[0])}",
                })

            except Exception as e:
                errored += 1
                results.append({
                    "task_id": task_id,
                    "test_idx": test_idx,
                    "status": "ERROR",
                    "error": str(e),
                })
                print(f"  [{idx+1}/{len(task_ids)}] {task_id}: ERROR — {e}")
                continue

            total_attempted = solved + failed + errored
            score_pct = (solved / total_attempted * 100) if total_attempted else 0

            if (idx + 1) % args.print_every == 0 or status == "SOLVED":
                print(
                    f"  [{idx+1}/{len(task_ids)}] {task_id}: {status} "
                    f"| Running: {solved}/{total_attempted} ({score_pct:.1f}%)"
                )

    elapsed = time.time() - t_start
    total_attempted = solved + failed + errored
    score = solved / total_attempted if total_attempted else 0

    print()
    print("=" * 60)
    print("ARC-AGI BENCHMARK RESULTS")
    print("=" * 60)
    print(f"  Tasks attempted: {total_attempted}")
    print(f"  Solved:          {solved}")
    print(f"  Wrong:           {failed}")
    print(f"  Errors:          {errored}")
    print(f"  Score:           {score:.1%} ({solved}/{total_attempted})")
    print(f"  Time:            {elapsed:.0f}s ({elapsed/max(total_attempted,1):.1f}s/task)")
    print(f"  Mode:            {args.mode}")
    print(f"  LLM:             {args.llm_provider}/{args.llm_model}")
    if memory:
        print(f"  Embedder:        {args.embedder_provider}/{args.embedder_model}")
    print()

    summary = {
        "score": round(score, 4),
        "solved": solved,
        "failed": failed,
        "errored": errored,
        "total": total_attempted,
        "elapsed_s": round(elapsed, 1),
        "mode": args.mode,
        "llm": f"{args.llm_provider}/{args.llm_model}",
    }

    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump({"summary": summary, "results": results}, f, indent=2)
        print(f"  Results saved to: {args.output_json}")

    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ARC-AGI benchmark with Engram memory + LLM.")
    parser.add_argument("--data-dir", default="data/arc-agi/evaluation", help="Directory with ARC task JSON files.")
    parser.add_argument("--max-tasks", type=int, default=50, help="Max tasks to evaluate (-1 = all).")
    parser.add_argument("--mode", choices=["direct", "memory"], default="direct", help="direct: LLM only. memory: Engram memory-augmented.")
    parser.add_argument("--output-json", default=None, help="Path to save detailed results JSON.")
    parser.add_argument("--print-every", type=int, default=5, help="Progress print interval.")
    parser.add_argument("--timeout", type=int, default=120, help="LLM call timeout in seconds.")

    parser.add_argument("--llm-provider", default="nvidia", choices=["nvidia", "openai", "gemini", "ollama"])
    parser.add_argument("--llm-model", default="deepseek-ai/deepseek-r1-distill-qwen-14b")
    parser.add_argument("--api-key", default=None, help="Override LLM API key.")
    parser.add_argument("--temperature", type=float, default=0.0, help="LLM temperature.")
    parser.add_argument("--top-p", type=float, default=0.7, help="LLM top-p.")
    parser.add_argument("--max-tokens", type=int, default=2048, help="LLM max output tokens.")
    parser.add_argument("--enable-thinking", action="store_true", help="Enable thinking/CoT mode for supported models.")
    parser.add_argument("--embedder-provider", default="nvidia", choices=["nvidia", "openai", "gemini", "simple"])
    parser.add_argument("--embedder-model", default="nvidia/nv-embed-v1")
    parser.add_argument("--embedding-dims", type=int, default=4096)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_arc_benchmark(args)


if __name__ == "__main__":
    main()
