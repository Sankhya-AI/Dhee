"""HippoCamp benchmark runner for Dhee.

This runner targets the official HippoCamp release artifacts on Hugging Face.
It supports:

* ``gold``: the released ``HippoCamp_Gold`` parsed-text setting. Useful for
  ablations, but easier than the paper's default raw-file benchmark.
* ``raw``: raw file-tree ingestion using Dhee's own deterministic parsers and
  metadata extraction. This follows the raw-file exposure boundary, but remains
  only partially multimodal unless augmented with OCR / ASR / vision.

Usage:
    python -m dhee.benchmarks.hippocamp \
        --config adam_subset \
        --mode raw \
        --embedder-provider simple \
        --llm-provider mock \
        --answer-strategy extractive
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from dhee.benchmarks.longmemeval import build_memory
from dhee.benchmarks.raw_extractors import raw_file_to_items
from dhee.memory.utils import strip_code_fences
from dhee.utils.factory import LLMFactory

logger = logging.getLogger("dhee.benchmarks.hippocamp")

DEFAULT_REPO_ID = "MMMem-org/HippoCamp"
NO_INFO = "No information available"

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.S)
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_WHITESPACE_RE = re.compile(r"\s+")


def _require_huggingface_hub():
    try:
        from huggingface_hub import hf_hub_download, list_repo_files
    except ImportError as exc:
        raise ImportError(
            "HippoCamp benchmark support requires 'huggingface_hub'. "
            "Install it with `pip install 'dhee[benchmarks]'` or `pip install huggingface_hub`."
        ) from exc
    return hf_hub_download, list_repo_files


@dataclass(frozen=True)
class HippoCampConfigSpec:
    name: str
    profile: str
    manifest_path: str
    environment_prefix: str


CONFIG_SPECS: Dict[str, HippoCampConfigSpec] = {
    "adam_fullset": HippoCampConfigSpec(
        name="adam_fullset",
        profile="Adam",
        manifest_path="Adam/Fullset/Adam.json",
        environment_prefix="Adam/Fullset/Adam/",
    ),
    "adam_subset": HippoCampConfigSpec(
        name="adam_subset",
        profile="Adam",
        manifest_path="Adam/Subset/Adam_Subset.json",
        environment_prefix="Adam/Subset/Adam_Subset/",
    ),
    "bei_fullset": HippoCampConfigSpec(
        name="bei_fullset",
        profile="Bei",
        manifest_path="Bei/Fullset/Bei.json",
        environment_prefix="Bei/Fullset/Bei/",
    ),
    "bei_subset": HippoCampConfigSpec(
        name="bei_subset",
        profile="Bei",
        manifest_path="Bei/Subset/Bei_Subset.json",
        environment_prefix="Bei/Subset/Bei_Subset/",
    ),
    "victoria_fullset": HippoCampConfigSpec(
        name="victoria_fullset",
        profile="Victoria",
        manifest_path="Victoria/Fullset/Victoria.json",
        environment_prefix="Victoria/Fullset/Victoria/",
    ),
    "victoria_subset": HippoCampConfigSpec(
        name="victoria_subset",
        profile="Victoria",
        manifest_path="Victoria/Subset/Victoria_Subset.json",
        environment_prefix="Victoria/Subset/Victoria_Subset/",
    ),
}


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def _persist_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _load_checkpoint(checkpoint_path: Path) -> Tuple[List[Dict[str, Any]], set]:
    """Load completed records from a checkpoint JSONL file.

    Returns (records_list, set_of_completed_question_ids).
    """
    records: List[Dict[str, Any]] = []
    completed_ids: set = set()
    if not checkpoint_path.exists():
        return records, completed_ids
    for line in checkpoint_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("id"):
            records.append(record)
            completed_ids.add(str(record["id"]))
    logger.info(
        "Resumed from checkpoint: %d completed questions loaded from %s",
        len(completed_ids),
        checkpoint_path,
    )
    return records, completed_ids


def _memory_has_data(history_db_path: Path, user_id: str) -> bool:
    """Check if the SQLite memory DB already has indexed data for the user."""
    if not history_db_path.exists():
        return False
    try:
        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(str(history_db_path))
        cursor = conn.execute(
            "SELECT count(*) FROM memories WHERE user_id = ?", (user_id,)
        )
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0
    except Exception:
        return False


def _load_env_file(env_path: Path) -> int:
    if not env_path.exists() or not env_path.is_file():
        return 0
    loaded = 0
    for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or key in os.environ:
            continue
        os.environ[key] = value
        loaded += 1
    return loaded


def _resolve_config(name: str) -> HippoCampConfigSpec:
    try:
        return CONFIG_SPECS[name]
    except KeyError as exc:
        supported = ", ".join(sorted(CONFIG_SPECS))
        raise ValueError(f"Unsupported HippoCamp config '{name}'. Supported: {supported}") from exc


def _normalize_answer(text: str) -> str:
    cleaned = strip_code_fences(str(text or "")).strip()
    cleaned = cleaned.replace("Answer:", "").replace("Final answer:", "").strip()
    cleaned = _WHITESPACE_RE.sub(" ", cleaned)
    return cleaned


def _normalize_for_match(text: str) -> str:
    lowered = _normalize_answer(text).lower()
    lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
    return _WHITESPACE_RE.sub(" ", lowered).strip()


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall(_normalize_for_match(text))


def _preview_text(text: str, *, limit: int = 160) -> str:
    cleaned = _WHITESPACE_RE.sub(" ", str(text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)].rstrip() + "..."


def token_f1(prediction: str, gold: str) -> float:
    pred_tokens = _tokenize(prediction)
    gold_tokens = _tokenize(gold)
    if not pred_tokens and not gold_tokens:
        return 1.0
    if not pred_tokens or not gold_tokens:
        return 0.0

    pred_counts: Dict[str, int] = {}
    gold_counts: Dict[str, int] = {}
    for token in pred_tokens:
        pred_counts[token] = pred_counts.get(token, 0) + 1
    for token in gold_tokens:
        gold_counts[token] = gold_counts.get(token, 0) + 1

    overlap = 0
    for token, pred_count in pred_counts.items():
        overlap += min(pred_count, gold_counts.get(token, 0))
    if overlap == 0:
        return 0.0

    precision = overlap / len(pred_tokens)
    recall = overlap / len(gold_tokens)
    return (2 * precision * recall) / (precision + recall)


def exact_match(prediction: str, gold: str) -> bool:
    return _normalize_for_match(prediction) == _normalize_for_match(gold)


def file_retrieval_metrics(predicted_paths: Sequence[str], gold_paths: Sequence[str]) -> Dict[str, float]:
    predicted = {str(path).strip() for path in predicted_paths if str(path).strip()}
    gold = {str(path).strip() for path in gold_paths if str(path).strip()}

    if not gold and not predicted:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    if not predicted:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    if not gold:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}

    true_positives = len(predicted & gold)
    precision = true_positives / len(predicted)
    recall = true_positives / len(gold)
    if precision + recall == 0:
        f1 = 0.0
    else:
        f1 = (2 * precision * recall) / (precision + recall)
    return {"precision": precision, "recall": recall, "f1": f1}


def _extract_json_block(text: str) -> Optional[Dict[str, Any]]:
    cleaned = strip_code_fences(str(text or "")).strip()
    match = _JSON_BLOCK_RE.search(cleaned)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _make_gold_repo_path(profile: str, relative_path: str) -> str:
    rel = PurePosixPath(str(relative_path))
    return f"HippoCamp_Gold/{profile}/{rel.with_suffix('.json').as_posix()}"


def _relative_path_from_environment(spec: HippoCampConfigSpec, repo_path: str) -> str:
    if not str(repo_path).startswith(spec.environment_prefix):
        raise ValueError(f"Path '{repo_path}' is outside '{spec.environment_prefix}'")
    return str(repo_path)[len(spec.environment_prefix):]


def _render_file_header(
    *,
    profile: str,
    config_name: str,
    relative_path: str,
    file_info: Dict[str, Any],
    summary: str,
) -> str:
    header_lines = [
        "[HippoCamp Gold File]",
        f"Profile: {profile}",
        f"Config: {config_name}",
        f"Relative Path: {relative_path}",
    ]
    for key, label in (
        ("file_modality", "Modality"),
        ("file_type", "File Type"),
        ("creation_date", "Created"),
        ("modification_date", "Modified"),
        ("location", "Location"),
        ("latitude", "Latitude"),
        ("longitude", "Longitude"),
    ):
        value = str(file_info.get(key) or "").strip()
        if value:
            header_lines.append(f"{label}: {value}")
    summary_text = str(summary or "").strip()
    if summary_text:
        header_lines.append(f"Summary: {summary_text[:1200]}")
    return "\n".join(header_lines)


def _segment_label(segment: Dict[str, Any], index: int) -> str:
    labels = [f"segment={index}"]
    for key in ("page", "timestamp", "frame", "sheet", "row"):
        value = segment.get(key)
        if value not in (None, "", []):
            labels.append(f"{key}={value}")
    return "[" + ", ".join(labels) + "]"


def gold_document_to_items(
    *,
    doc: Dict[str, Any],
    profile: str,
    config_name: str,
    relative_path: str,
    chunk_chars: int,
) -> List[Dict[str, Any]]:
    file_info = dict(doc.get("file_info") or {})
    summary = str(doc.get("summary") or "").strip()
    header = _render_file_header(
        profile=profile,
        config_name=config_name,
        relative_path=relative_path,
        file_info=file_info,
        summary=summary,
    )

    raw_segments = list(doc.get("segments") or [])
    segment_blocks: List[str] = []
    for index, segment in enumerate(raw_segments, start=1):
        content = str(segment.get("content") or "").strip()
        if not content:
            continue
        segment_blocks.append(f"{_segment_label(segment, index)}\n{content}")

    if not segment_blocks:
        if summary:
            segment_blocks = [f"[segment=1]\n{summary}"]
        else:
            segment_blocks = [f"[segment=1]\n{relative_path}"]

    items: List[Dict[str, Any]] = []
    current_blocks: List[str] = []
    current_len = 0
    chunk_index = 1
    target_chars = max(400, int(chunk_chars))

    def flush_current() -> None:
        nonlocal current_blocks, current_len, chunk_index
        if not current_blocks:
            return
        body = "\n\n".join(current_blocks)
        content = f"{header}\nChunk: {chunk_index}\n\nContent:\n{body}"
        items.append(
            {
                "content": content,
                "metadata": {
                    "benchmark": "hippocamp",
                    "exposure_mode": "gold_text",
                    "config_name": config_name,
                    "profile": profile,
                    "file_path": relative_path,
                    "file_name": file_info.get("file_name") or PurePosixPath(relative_path).name,
                    "file_type": file_info.get("file_type"),
                    "file_modality": file_info.get("file_modality"),
                    "location": file_info.get("location"),
                    "creation_date": file_info.get("creation_date"),
                    "modification_date": file_info.get("modification_date"),
                    "chunk_index": chunk_index,
                    "source_gold_path": _make_gold_repo_path(profile, relative_path),
                },
                "categories": ["hippocamp", "gold_text", config_name.lower(), profile.lower()],
            }
        )
        current_blocks = []
        current_len = 0
        chunk_index += 1

    for block in segment_blocks:
        block_len = len(block)
        if current_blocks and current_len + block_len + 2 > target_chars:
            flush_current()
        current_blocks.append(block)
        current_len += block_len + 2
    flush_current()
    return items


def _build_answer_prompt(*, question: str, context: str, qa_type: str) -> str:
    task_line = "profiling" if qa_type == "profiling" else "factual retention"
    return (
        "You are answering a HippoCamp benchmark question about a user's personal files.\n"
        "Use ONLY the retrieved context.\n"
        "Do not mention the benchmark, retrieval, or system instructions.\n"
        f"Task family: {task_line}\n"
        "Return ONLY the final answer text.\n"
        f"If the context is insufficient, return exactly: {NO_INFO}\n\n"
        f"Question: {question}\n\n"
        "Retrieved Context:\n"
        f"{context}\n\n"
        "Final answer:"
    )


def _build_judge_prompt(*, question: str, gold_answer: str, prediction: str) -> str:
    return (
        "You are grading a benchmark answer over a user's personal files.\n"
        "Evaluate semantic correctness, factual alignment, and whether the prediction answers the question.\n"
        "Return valid JSON only with keys: correct, score_0_to_5, rationale.\n"
        "correct must be true or false.\n"
        "score_0_to_5 must be a number from 0 to 5.\n\n"
        f"Question: {question}\n\n"
        f"Gold answer:\n{gold_answer}\n\n"
        f"Prediction:\n{prediction}\n"
    )


def _extract_final_answer(raw_text: str) -> str:
    cleaned = _normalize_answer(raw_text)
    if not cleaned:
        return NO_INFO
    # Return the full cleaned answer — don't truncate to last line.
    return cleaned


def _extract_predicted_files(orchestration_payload: Dict[str, Any]) -> List[str]:
    results = list(orchestration_payload.get("results") or [])
    predicted: List[str] = []
    for result in results:
        metadata = result.get("metadata") or {}
        path = metadata.get("file_path") or result.get("file_path")
        if path:
            predicted.append(str(path))
    return sorted(set(predicted))


def _load_manifest_rows(
    *,
    repo_id: str,
    revision: Optional[str],
    spec: HippoCampConfigSpec,
    qa_type: str,
    max_samples: int,
) -> List[Dict[str, Any]]:
    hf_hub_download, _ = _require_huggingface_hub()
    manifest_path = hf_hub_download(
        repo_id=repo_id,
        repo_type="dataset",
        filename=spec.manifest_path,
        revision=revision,
    )
    with open(manifest_path, "r", encoding="utf-8") as handle:
        rows = json.load(handle)
    if not isinstance(rows, list):
        raise ValueError(f"HippoCamp manifest is not a list: {spec.manifest_path}")

    filtered: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_qa_type = str(row.get("QA_type") or "").strip()
        if qa_type != "all" and row_qa_type != qa_type:
            continue
        filtered.append(row)
    if max_samples > 0:
        filtered = filtered[: max_samples]
    return filtered


def _list_environment_repo_files(
    *,
    repo_id: str,
    revision: Optional[str],
    spec: HippoCampConfigSpec,
) -> List[str]:
    _, hf_list_repo_files = _require_huggingface_hub()
    files = hf_list_repo_files(repo_id=repo_id, repo_type="dataset", revision=revision)
    return sorted(path for path in files if str(path).startswith(spec.environment_prefix))


def _build_environment_items(
    *,
    repo_id: str,
    revision: Optional[str],
    spec: HippoCampConfigSpec,
    max_environment_files: int,
    chunk_chars: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    hf_hub_download, _ = _require_huggingface_hub()
    repo_files = _list_environment_repo_files(repo_id=repo_id, revision=revision, spec=spec)
    if max_environment_files > 0:
        repo_files = repo_files[: max_environment_files]

    items: List[Dict[str, Any]] = []
    missing_gold: List[str] = []

    for repo_path in repo_files:
        relative_path = _relative_path_from_environment(spec, repo_path)
        gold_repo_path = _make_gold_repo_path(spec.profile, relative_path)
        try:
            local_path = hf_hub_download(
                repo_id=repo_id,
                repo_type="dataset",
                filename=gold_repo_path,
                revision=revision,
            )
        except Exception:
            missing_gold.append(relative_path)
            continue

        with open(local_path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
        if not isinstance(document, dict):
            logger.warning("Skipping malformed gold file: %s", gold_repo_path)
            continue
        items.extend(
            gold_document_to_items(
                doc=document,
                profile=spec.profile,
                config_name=spec.name,
                relative_path=relative_path,
                chunk_chars=chunk_chars,
            )
        )

    metadata = {
        "environment_repo_files": len(repo_files),
        "environment_index_items": len(items),
        "missing_gold_files": missing_gold,
    }
    return items, metadata


def _build_raw_environment_items(
    *,
    repo_id: str,
    revision: Optional[str],
    spec: HippoCampConfigSpec,
    max_environment_files: int,
    chunk_chars: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    hf_hub_download, _ = _require_huggingface_hub()
    repo_files = _list_environment_repo_files(repo_id=repo_id, revision=revision, spec=spec)
    if max_environment_files > 0:
        repo_files = repo_files[: max_environment_files]

    items: List[Dict[str, Any]] = []
    mode_counts: Dict[str, int] = {}
    metadata_only_files: List[str] = []

    for repo_path in repo_files:
        relative_path = _relative_path_from_environment(spec, repo_path)
        local_path = Path(
            hf_hub_download(
                repo_id=repo_id,
                repo_type="dataset",
                filename=repo_path,
                revision=revision,
            )
        )
        result = raw_file_to_items(
            local_path=local_path,
            relative_path=relative_path,
            profile=spec.profile,
            config_name=spec.name,
            chunk_chars=chunk_chars,
        )
        mode_counts[result.mode] = mode_counts.get(result.mode, 0) + 1
        if result.mode == "metadata_only":
            metadata_only_files.append(relative_path)
        items.extend(result.items)

    metadata = {
        "environment_repo_files": len(repo_files),
        "environment_index_items": len(items),
        "raw_extraction_modes": mode_counts,
        "metadata_only_files": metadata_only_files,
    }
    return items, metadata


def _make_llm(
    *,
    provider: str,
    model: Optional[str],
    max_tokens: int,
    timeout: int,
    temperature: float,
    top_p: float,
    enable_thinking: bool = False,
) -> Any:
    config: Dict[str, Any] = {
        "max_tokens": max(32, int(max_tokens)),
        "timeout": max(1, int(timeout)),
        "temperature": temperature,
        "top_p": top_p,
        "enable_thinking": bool(enable_thinking),
    }
    if model:
        config["model"] = model
    return LLMFactory.create(provider, config)


def _maybe_judge(
    *,
    judge_llm: Optional[Any],
    question: str,
    gold_answer: str,
    prediction: str,
) -> Optional[Dict[str, Any]]:
    if judge_llm is None:
        return None
    prompt = _build_judge_prompt(question=question, gold_answer=gold_answer, prediction=prediction)
    try:
        raw = str(judge_llm.generate(prompt)).strip()
    except Exception as exc:
        logger.warning("Judge generation failed: %s", exc)
        return None
    payload = _extract_json_block(raw)
    if not payload:
        return None
    score = payload.get("score_0_to_5")
    try:
        score_value = max(0.0, min(5.0, float(score)))
    except (TypeError, ValueError):
        score_value = None
    return {
        "correct": bool(payload.get("correct")),
        "score_0_to_5": score_value,
        "rationale": str(payload.get("rationale") or "").strip(),
        "raw": raw,
    }


def _generate_prediction(
    *,
    answer_llm: Optional[Any],
    answer_strategy: str,
    question: str,
    qa_type: str,
    context: str,
    reduced_answer: str,
) -> str:
    strategy = answer_strategy
    if strategy == "auto":
        strategy = "llm" if answer_llm is not None else "extractive"

    if strategy == "extractive":
        return _normalize_answer(reduced_answer) or NO_INFO

    if answer_llm is None:
        return _normalize_answer(reduced_answer) or NO_INFO

    prompt = _build_answer_prompt(question=question, context=context, qa_type=qa_type)
    try:
        raw = str(answer_llm.generate(prompt)).strip()
    except Exception as exc:
        logger.warning("Answer generation failed: %s", exc)
        return _normalize_answer(reduced_answer) or NO_INFO

    prediction = _extract_final_answer(raw)
    if prediction == NO_INFO and reduced_answer:
        fallback = _normalize_answer(reduced_answer)
        if fallback:
            return fallback
    return prediction


def _score_record(
    *,
    row: Dict[str, Any],
    prediction: str,
    predicted_files: Sequence[str],
    judge_payload: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    gold_answer = str(row.get("answer") or "").strip()
    gold_files = [str(path) for path in row.get("file_path") or []]
    retrieval = file_retrieval_metrics(predicted_files, gold_files)
    record: Dict[str, Any] = {
        "id": str(row.get("id") or ""),
        "qa_type": str(row.get("QA_type") or "").strip(),
        "profiling_type": str(row.get("profiling_type") or "").strip(),
        "question": str(row.get("question") or "").strip(),
        "gold_answer": gold_answer,
        "prediction": prediction,
        "exact_match": exact_match(prediction, gold_answer),
        "token_f1": round(token_f1(prediction, gold_answer), 4),
        "gold_files": gold_files,
        "predicted_files": list(predicted_files),
        "file_precision": round(retrieval["precision"], 4),
        "file_recall": round(retrieval["recall"], 4),
        "file_f1": round(retrieval["f1"], 4),
        "agent_cap": row.get("agent_cap") or {},
    }
    if judge_payload is not None:
        record["judge_correct"] = bool(judge_payload.get("correct"))
        record["judge_score_0_to_5"] = judge_payload.get("score_0_to_5")
        record["judge_rationale"] = judge_payload.get("rationale")
    return record


def _family_accuracy(records: Sequence[Dict[str, Any]], family: str) -> Optional[float]:
    by_subcategory: Dict[str, List[bool]] = {}
    for record in records:
        if "judge_correct" not in record:
            continue
        labels = ((record.get("agent_cap") or {}).get(family) or [])
        for label in labels:
            by_subcategory.setdefault(str(label), []).append(bool(record["judge_correct"]))
    if not by_subcategory:
        return None
    sub_scores = [sum(values) / len(values) for values in by_subcategory.values() if values]
    return mean(sub_scores) if sub_scores else None


def _summarize_records(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    if not records:
        return {"count": 0}

    summary: Dict[str, Any] = {
        "count": len(records),
        "exact_match": round(mean(1.0 if record["exact_match"] else 0.0 for record in records), 4),
        "token_f1": round(mean(float(record["token_f1"]) for record in records), 4),
        "file_precision": round(mean(float(record["file_precision"]) for record in records), 4),
        "file_recall": round(mean(float(record["file_recall"]) for record in records), 4),
        "file_f1": round(mean(float(record["file_f1"]) for record in records), 4),
    }
    if any("judge_correct" in record for record in records):
        judged = [record for record in records if "judge_correct" in record]
        summary["judge_accuracy"] = round(
            mean(1.0 if record["judge_correct"] else 0.0 for record in judged),
            4,
        )
        judge_scores = [
            float(record["judge_score_0_to_5"])
            for record in judged
            if record.get("judge_score_0_to_5") is not None
        ]
        if judge_scores:
            summary["judge_avg_score_0_to_5"] = round(mean(judge_scores), 4)
            summary["judge_avg_score_0_to_10"] = round(mean(score * 2.0 for score in judge_scores), 4)

        capability_breakdown = {}
        for family in ("search", "evidence_perception", "reasoning"):
            family_score = _family_accuracy(judged, family)
            if family_score is not None:
                capability_breakdown[family] = round(family_score, 4)
        if capability_breakdown:
            summary["capability_accuracy"] = capability_breakdown
    return summary


def _emit_progress(progress_path: Optional[Path], event: str, **payload: Any) -> None:
    if progress_path is None:
        return
    row = {
        "ts": round(time.time(), 3),
        "event": event,
        **payload,
    }
    try:
        _append_jsonl(progress_path, row)
    except Exception as exc:
        logger.warning("Failed to append progress event '%s' to %s: %s", event, progress_path, exc)


def _run_config(args: argparse.Namespace, spec: HippoCampConfigSpec) -> Dict[str, Any]:
    output_root = Path(args.output_dir)
    history_db_path = output_root / f"{spec.name}.sqlite"
    progress_path = output_root / f"{spec.name}.progress.jsonl"
    checkpoint_path = output_root / f"{spec.name}.checkpoint.jsonl"
    user_id = f"hippocamp_{spec.name}"
    # Don't nuke progress — append to it for resumed runs.
    # Load checkpoint of previously completed questions.
    resumed_records, completed_ids = _load_checkpoint(checkpoint_path)

    memory = build_memory(
        llm_provider=args.llm_provider,
        embedder_provider=args.embedder_provider,
        vector_store_provider=args.vector_store_provider,
        embedding_dims=args.embedding_dims,
        history_db_path=str(history_db_path),
        llm_model=args.llm_model,
        llm_timeout=args.llm_timeout,
        embedder_model=args.embedder_model,
        full_potential=not args.minimal,
        defer_enrichment=args.defer_enrichment,
        enable_rerank=args.enable_rerank,
        rerank_model=args.rerank_model,
        rerank_config=None,
        enable_episodic_index=not args.disable_episodic_index,
        enable_hierarchical_retrieval=not args.disable_hierarchical_retrieval,
        enable_orchestrated_search=not args.disable_orchestrated_search,
        cost_guardrail_strict=not args.no_cost_guardrail_strict,
    )

    answer_llm = None
    if args.answer_provider not in {"", "none", "mock"}:
        answer_llm = _make_llm(
            provider=args.answer_provider,
            model=args.answer_model,
            max_tokens=args.answer_max_tokens,
            timeout=args.answer_timeout,
            temperature=args.answer_temperature,
            top_p=args.answer_top_p,
            enable_thinking=args.answer_enable_thinking,
        )

    judge_llm = None
    if args.judge_provider:
        judge_llm = _make_llm(
            provider=args.judge_provider,
            model=args.judge_model,
            max_tokens=args.judge_max_tokens,
            timeout=args.judge_timeout,
            temperature=args.judge_temperature,
            top_p=args.judge_top_p,
            enable_thinking=args.judge_enable_thinking,
        )

    rows = _load_manifest_rows(
        repo_id=args.repo_id,
        revision=args.revision,
        spec=spec,
        qa_type=args.qa_type,
        max_samples=args.max_samples,
    )

    # When resuming with an existing index, skip the expensive environment download/parse.
    can_skip_env = bool(completed_ids) and _memory_has_data(history_db_path, user_id)
    if can_skip_env:
        environment_items: List[Dict[str, Any]] = []
        environment_meta: Dict[str, Any] = {"environment_repo_files": 0, "environment_index_items": 0, "resumed": True}
        logger.info("RESUME: Skipping environment download — memory DB already populated for %s", spec.name)
    elif args.mode == "gold":
        environment_items, environment_meta = _build_environment_items(
            repo_id=args.repo_id,
            revision=args.revision,
            spec=spec,
            max_environment_files=args.max_environment_files,
            chunk_chars=args.chunk_chars,
        )
    elif args.mode == "raw":
        environment_items, environment_meta = _build_raw_environment_items(
            repo_id=args.repo_id,
            revision=args.revision,
            spec=spec,
            max_environment_files=args.max_environment_files,
            chunk_chars=args.chunk_chars,
        )
    else:
        raise ValueError(f"Unsupported mode: {args.mode}")

    logger.info("Live progress %s -> %s", spec.name, progress_path)
    _emit_progress(
        progress_path,
        "config_start",
        config=spec.name,
        profile=spec.profile,
        mode=args.mode,
        qa_type=args.qa_type,
        question_count=len(rows),
        environment_repo_files=environment_meta["environment_repo_files"],
        environment_index_items=environment_meta["environment_index_items"],
    )
    # Skip re-indexing if the memory DB already has data for this user (resume mode).
    skip_indexing = bool(completed_ids) and _memory_has_data(history_db_path, user_id)
    if skip_indexing:
        logger.info(
            "RESUME: Skipping indexing for %s — memory DB exists with data, %d questions already completed",
            spec.name,
            len(completed_ids),
        )
        index_seconds = 0.0
    else:
        logger.info(
            "Indexing %s: %d files -> %d memory items",
            spec.name,
            environment_meta["environment_repo_files"],
            environment_meta["environment_index_items"],
        )
        _emit_progress(
            progress_path,
            "index_start",
            config=spec.name,
            environment_repo_files=environment_meta["environment_repo_files"],
            environment_index_items=environment_meta["environment_index_items"],
        )
        t0 = time.time()
        memory.delete_all(user_id=user_id)
        memory.add_batch(items=environment_items, user_id=user_id)
        index_seconds = time.time() - t0
    _emit_progress(
        progress_path,
        "index_done",
        config=spec.name,
        index_seconds=round(index_seconds, 4),
        environment=environment_meta,
    )

    if args.enrich_after_ingest:
        try:
            memory.enrich_pending(user_id=user_id, batch_size=args.enrich_batch_size, max_batches=args.enrich_max_batches)
            _emit_progress(
                progress_path,
                "enrichment_done",
                config=spec.name,
                batch_size=args.enrich_batch_size,
                max_batches=args.enrich_max_batches,
            )
        except Exception as exc:
            logger.warning("Post-ingest enrichment failed for %s: %s", spec.name, exc)
            _emit_progress(
                progress_path,
                "enrichment_failed",
                config=spec.name,
                error=str(exc),
            )

    records: List[Dict[str, Any]] = list(resumed_records)
    skipped_count = 0
    total_rows = len(rows)
    for index, row in enumerate(rows, start=1):
        row_id = str(row.get("id") or "")
        if row_id and row_id in completed_ids:
            skipped_count += 1
            continue
        question = str(row.get("question") or "").strip()
        qa_type = str(row.get("QA_type") or "").strip() or "factual_retention"
        _emit_progress(
            progress_path,
            "question_start",
            config=spec.name,
            question_index=index,
            question_count=total_rows,
            qa_type=qa_type,
            question=_preview_text(question, limit=240),
        )
        payload = memory.search_orchestrated(
            query=question,
            user_id=user_id,
            question_type=f"hippocamp-{qa_type}",
            question_date="",
            limit=args.top_k,
            orchestration_mode=args.answer_orchestration_mode,
            base_search_limit=args.top_k,
            base_context_limit=args.answer_context_top_k,
            search_cap=args.search_cap,
            context_cap=args.context_cap,
            map_max_candidates=args.map_max_candidates,
            map_max_chars=args.map_max_chars,
            keyword_search=True,
            hybrid_alpha=0.7,
            include_evidence=True,
            evidence_strategy=args.evidence_strategy,
            evidence_max_chars=args.evidence_max_chars,
            evidence_context_lines=args.evidence_context_lines,
            max_context_chars=args.max_context_chars,
            rerank=args.enable_rerank,
            orchestrator_llm=memory.llm if args.answer_orchestration_mode != "off" else None,
            reflection_max_hops=1,
        )
        context = str(payload.get("context") or "").strip()
        reduced_answer = str(payload.get("reduced_answer") or "").strip()
        predicted_files = _extract_predicted_files(payload)
        _emit_progress(
            progress_path,
            "search_done",
            config=spec.name,
            question_index=index,
            question_count=total_rows,
            search_result_count=len(payload.get("results") or []),
            predicted_files=predicted_files,
            reduced_answer_preview=_preview_text(reduced_answer),
        )
        _emit_progress(
            progress_path,
            "answer_start",
            config=spec.name,
            question_index=index,
            question_count=total_rows,
            strategy=args.answer_strategy,
            provider=args.answer_provider or None,
            model=args.answer_model or None,
        )
        prediction = _generate_prediction(
            answer_llm=answer_llm,
            answer_strategy=args.answer_strategy,
            question=question,
            qa_type=qa_type,
            context=context,
            reduced_answer=reduced_answer,
        )
        _emit_progress(
            progress_path,
            "answer_done",
            config=spec.name,
            question_index=index,
            question_count=total_rows,
            prediction_preview=_preview_text(prediction),
        )
        if judge_llm is not None:
            _emit_progress(
                progress_path,
                "judge_start",
                config=spec.name,
                question_index=index,
                question_count=total_rows,
                provider=args.judge_provider or None,
                model=args.judge_model or None,
            )
        judge_payload = _maybe_judge(
            judge_llm=judge_llm,
            question=question,
            gold_answer=str(row.get("answer") or ""),
            prediction=prediction,
        )
        if judge_llm is not None:
            _emit_progress(
                progress_path,
                "judge_done",
                config=spec.name,
                question_index=index,
                question_count=total_rows,
                judge_correct=None if judge_payload is None else bool(judge_payload.get("correct")),
                judge_score_0_to_5=None if judge_payload is None else judge_payload.get("score_0_to_5"),
                judge_rationale_preview="" if judge_payload is None else _preview_text(judge_payload.get("rationale") or ""),
            )
        record = _score_record(
            row=row,
            prediction=prediction,
            predicted_files=predicted_files,
            judge_payload=judge_payload,
        )
        record["search_result_count"] = len(payload.get("results") or [])
        record["question_index"] = index
        records.append(record)
        # Checkpoint: persist each completed record so we can resume.
        _append_jsonl(checkpoint_path, record)
        _emit_progress(
            progress_path,
            "question_done",
            config=spec.name,
            question_index=index,
            question_count=total_rows,
            exact_match=bool(record["exact_match"]),
            token_f1=float(record["token_f1"]),
            file_f1=float(record["file_f1"]),
            judge_correct=record.get("judge_correct"),
            judge_score_0_to_5=record.get("judge_score_0_to_5"),
        )

        if args.print_every > 0 and index % args.print_every == 0:
            logger.info("Progress %s: %d/%d", spec.name, index, len(rows))

    if skipped_count > 0:
        logger.info(
            "RESUME: Skipped %d already-completed questions, processed %d new for %s",
            skipped_count,
            len(records) - len(resumed_records),
            spec.name,
        )
    overall = _summarize_records(records)
    by_type: Dict[str, Dict[str, Any]] = {}
    for qa_type in ("profiling", "factual_retention"):
        subset = [record for record in records if record.get("qa_type") == qa_type]
        if subset:
            by_type[qa_type] = _summarize_records(subset)

    _emit_progress(
        progress_path,
        "config_done",
        config=spec.name,
        overall=overall,
        by_type=by_type,
        progress_jsonl=str(progress_path),
    )

    return {
        "config": spec.name,
        "profile": spec.profile,
        "mode": args.mode,
        "repo_id": args.repo_id,
        "revision": args.revision,
        "qa_type_filter": args.qa_type,
        "answer_strategy": args.answer_strategy,
        "llm_provider": args.llm_provider,
        "llm_model": args.llm_model,
        "answer_provider": args.answer_provider or None,
        "answer_model": args.answer_model or None,
        "judge_provider": args.judge_provider or None,
        "judge_model": args.judge_model or None,
        "index_seconds": round(index_seconds, 4),
        "environment": environment_meta,
        "progress_jsonl": str(progress_path),
        "overall": overall,
        "by_type": by_type,
        "records": records,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Dhee on the HippoCamp benchmark release.")
    parser.add_argument("--repo-id", default=DEFAULT_REPO_ID)
    parser.add_argument("--revision", default=None)
    parser.add_argument(
        "--config",
        action="append",
        dest="configs",
        choices=sorted(CONFIG_SPECS),
        help="HippoCamp config(s) to run. May be passed multiple times.",
    )
    parser.add_argument("--mode", choices=["gold", "raw"], default="raw")
    parser.add_argument("--qa-type", choices=["all", "profiling", "factual_retention"], default="all")
    parser.add_argument("--max-samples", type=int, default=-1)
    parser.add_argument("--max-environment-files", type=int, default=-1)
    parser.add_argument("--chunk-chars", type=int, default=2600)
    parser.add_argument("--output-dir", default="runs/hippocamp")
    parser.add_argument("--print-every", type=int, default=10)

    # --- Full-power Dhee defaults: NVIDIA embedder + reranker, all features ON ---
    parser.add_argument("--llm-provider", default="nvidia")
    parser.add_argument("--llm-model", default="meta/llama-3.3-70b-instruct")
    parser.add_argument("--llm-timeout", type=int, default=240)
    parser.add_argument("--embedder-provider", default="nvidia")
    parser.add_argument("--embedder-model", default="nvidia/llama-nemotron-embed-vl-1b-v2")
    parser.add_argument("--embedding-dims", type=int, default=2048)
    parser.add_argument("--vector-store-provider", choices=["memory", "sqlite_vec"], default="memory")

    parser.add_argument("--minimal", action="store_true", default=False)
    # defer_enrichment=True: fast 0-LLM ingestion; batch enrichment runs after ingest via enrich_after_ingest
    parser.add_argument("--defer-enrichment", dest="defer_enrichment", action="store_true", default=True)
    parser.add_argument("--no-defer-enrichment", dest="defer_enrichment", action="store_false")
    parser.add_argument("--enrich-after-ingest", dest="enrich_after_ingest", action="store_true", default=True)
    parser.add_argument("--no-enrich-after-ingest", dest="enrich_after_ingest", action="store_false")
    parser.add_argument("--enrich-batch-size", type=int, default=10)
    parser.add_argument("--enrich-max-batches", type=int, default=200)
    parser.add_argument("--enable-rerank", dest="enable_rerank", action="store_true", default=True)
    parser.add_argument("--disable-rerank", dest="enable_rerank", action="store_false")
    parser.add_argument("--rerank-model", default="nvidia/llama-3.2-nv-rerankqa-1b-v2")
    parser.add_argument("--disable-episodic-index", action="store_true", default=False)
    parser.add_argument("--disable-hierarchical-retrieval", action="store_true", default=False)
    parser.add_argument("--disable-orchestrated-search", action="store_true", default=False)
    parser.add_argument("--no-cost-guardrail-strict", action="store_true", default=False)

    parser.add_argument("--top-k", type=int, default=12)
    parser.add_argument("--answer-context-top-k", type=int, default=8)
    parser.add_argument("--search-cap", type=int, default=30)
    parser.add_argument("--context-cap", type=int, default=20)
    parser.add_argument("--map-max-candidates", type=int, default=8)
    parser.add_argument("--map-max-chars", type=int, default=1400)
    parser.add_argument("--max-context-chars", type=int, default=28000)
    parser.add_argument("--evidence-strategy", choices=["full", "vector_text", "snippet"], default="snippet")
    parser.add_argument("--evidence-max-chars", type=int, default=3500)
    parser.add_argument("--evidence-context-lines", type=int, default=1)
    parser.add_argument("--answer-orchestration-mode", choices=["off", "hybrid", "strict"], default="hybrid")

    parser.add_argument("--answer-strategy", choices=["auto", "llm", "extractive"], default="auto")
    parser.add_argument("--answer-provider", default="nvidia")
    parser.add_argument("--answer-model", default="meta/llama-3.3-70b-instruct")
    parser.add_argument("--answer-timeout", type=int, default=240)
    parser.add_argument("--answer-max-tokens", type=int, default=1024)
    parser.add_argument("--answer-temperature", type=float, default=0.2)
    parser.add_argument("--answer-top-p", type=float, default=0.7)
    parser.add_argument("--answer-enable-thinking", dest="answer_enable_thinking", action="store_true", default=False)
    parser.add_argument("--answer-disable-thinking", dest="answer_enable_thinking", action="store_false")

    parser.add_argument("--judge-provider", default="nvidia")
    parser.add_argument("--judge-model", default="deepseek-ai/deepseek-v3.1-terminus")
    parser.add_argument("--judge-timeout", type=int, default=60)
    parser.add_argument("--judge-max-tokens", type=int, default=2048)
    parser.add_argument("--judge-temperature", type=float, default=0.2)
    parser.add_argument("--judge-top-p", type=float, default=0.7)
    parser.add_argument("--judge-enable-thinking", dest="judge_enable_thinking", action="store_true", default=False)
    parser.add_argument("--judge-disable-thinking", dest="judge_enable_thinking", action="store_false")

    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()
    if not args.configs:
        args.configs = ["adam_subset"]
    return args


def main() -> None:
    args = parse_args()
    _configure_logging(args.log_level)
    _load_env_file(Path.cwd() / ".env")
    _load_env_file(Path(__file__).resolve().parents[2] / ".env")

    output_root = Path(args.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    started = time.time()
    config_results = []
    for config_name in args.configs:
        spec = _resolve_config(config_name)
        logger.info("Running HippoCamp config=%s mode=%s qa_type=%s", spec.name, args.mode, args.qa_type)
        config_results.append(_run_config(args, spec))

    summary = {
        "runner": "dhee.benchmarks.hippocamp",
        "repo_id": args.repo_id,
        "revision": args.revision,
        "configs": args.configs,
        "mode": args.mode,
        "qa_type": args.qa_type,
        "elapsed_seconds": round(time.time() - started, 4),
        "config_results": config_results,
    }

    summary_path = output_root / "summary.json"
    predictions_path = output_root / "predictions.json"
    flat_records = []
    for config_result in config_results:
        for record in config_result.get("records", []):
            flat_records.append(
                {
                    "config": config_result["config"],
                    "profile": config_result["profile"],
                    **record,
                }
            )

    _persist_json(summary_path, summary)
    _persist_json(predictions_path, flat_records)

    print(
        json.dumps(
            {
                "summary_json": str(summary_path),
                "predictions_json": str(predictions_path),
                "configs": args.configs,
                "mode": args.mode,
                "qa_type": args.qa_type,
                "records": len(flat_records),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
