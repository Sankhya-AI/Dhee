"""Training Data Formatter — convert teacher logs to instruction-tuning format.

Reads teacher_log.jsonl from TeacherLoggingLLM and formats for QLoRA
fine-tuning with task prefix tokens.
"""

import json
import logging
import os
import random
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_LOG_DIR = os.path.join(os.path.expanduser("~"), ".dhee", "teacher_logs")
_DEFAULT_OUTPUT_DIR = os.path.join(os.path.expanduser("~"), ".dhee", "training_data")

# Task prefix tokens for multi-task training
TASK_PREFIXES = {
    "engram": "[ENGRAM]",
    "query": "[QUERY]",
    "answer": "[ANSWER]",
    "decompose": "[DECOMPOSE]",
    "context": "[CONTEXT]",
    "scene": "[SCENE]",
    "echo": "[ENGRAM]",       # echo maps to engram task
    "category": "[ENGRAM]",   # category maps to engram task
    "entity": "[ENGRAM]",     # entity maps to engram task
}


def load_teacher_logs(log_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load teacher logs from JSONL file."""
    log_dir = log_dir or _DEFAULT_LOG_DIR
    log_file = os.path.join(log_dir, "teacher_log.jsonl")
    if not os.path.exists(log_file):
        logger.warning("No teacher log found at %s", log_file)
        return []

    entries = []
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    logger.info("Loaded %d teacher log entries from %s", len(entries), log_file)
    return entries


def format_instruction_pair(entry: Dict[str, Any]) -> Dict[str, str]:
    """Convert a teacher log entry to instruction-tuning format.

    Format:
        instruction: [TASK_PREFIX]\n{input}
        output: {teacher_response}
    """
    task_type = entry.get("task_type", "other")
    prefix = TASK_PREFIXES.get(task_type, "[ENGRAM]")
    prompt = entry.get("prompt", "")
    response = entry.get("response", "")

    # Extract the core content from the prompt (strip system instructions)
    # Keep only the user-facing content after common delimiters
    content = prompt
    for delimiter in ["TEXT:", "QUESTION:", "CONTENT:", "INPUT:"]:
        if delimiter in prompt:
            content = prompt.split(delimiter, 1)[1].strip()
            break

    return {
        "instruction": f"{prefix}\n{content}",
        "output": response,
        "task_type": task_type,
    }


def format_dataset(
    log_dir: Optional[str] = None,
    output_dir: Optional[str] = None,
    validation_split: float = 0.1,
    balance_tasks: bool = True,
    max_per_task: int = 5000,
) -> Dict[str, str]:
    """Format full dataset for training.

    Returns dict with paths to train and validation JSONL files.
    """
    output_dir = output_dir or _DEFAULT_OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)

    entries = load_teacher_logs(log_dir)
    if not entries:
        return {"error": "No teacher logs found"}

    # Convert to instruction pairs
    pairs = [format_instruction_pair(e) for e in entries]

    # Balance across task types if requested
    if balance_tasks:
        by_task = {}
        for pair in pairs:
            task = pair["task_type"]
            by_task.setdefault(task, []).append(pair)

        balanced = []
        for task, task_pairs in by_task.items():
            random.shuffle(task_pairs)
            balanced.extend(task_pairs[:max_per_task])
        pairs = balanced

    # Shuffle
    random.shuffle(pairs)

    # Split
    split_idx = max(1, int(len(pairs) * (1 - validation_split)))
    train_pairs = pairs[:split_idx]
    val_pairs = pairs[split_idx:]

    # Write JSONL
    train_path = os.path.join(output_dir, "train.jsonl")
    val_path = os.path.join(output_dir, "val.jsonl")

    for path, data in [(train_path, train_pairs), (val_path, val_pairs)]:
        with open(path, "w", encoding="utf-8") as f:
            for pair in data:
                f.write(json.dumps({
                    "instruction": pair["instruction"],
                    "output": pair["output"],
                }) + "\n")

    logger.info(
        "Dataset formatted: %d train, %d val -> %s",
        len(train_pairs), len(val_pairs), output_dir,
    )
    return {
        "train_path": train_path,
        "val_path": val_path,
        "train_count": len(train_pairs),
        "val_count": len(val_pairs),
        "task_distribution": {
            task: len([p for p in pairs if p["task_type"] == task])
            for task in set(p["task_type"] for p in pairs)
        },
    }


if __name__ == "__main__":
    import sys
    log_dir = sys.argv[1] if len(sys.argv) > 1 else None
    result = format_dataset(log_dir=log_dir)
    print(json.dumps(result, indent=2))
