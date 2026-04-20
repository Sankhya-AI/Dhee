"""Karma-based replay-gate evaluator for HuggingFace causal LMs.

The evaluator answers: "how well does this model fit the ground-truth
expected answers in the replay corpus?" Metric is mean per-token
log-likelihood of ``expected`` conditioned on ``prompt``. Higher is
better, monotone with real improvement.

Why log-likelihood and not generation quality?

1. Deterministic. No sampling temperature / top-p to calibrate.
2. Single forward pass per record — cheap enough to run on every
   progressive cycle.
3. Directly sensitive to the karma axes that matter for extraction:
   FACT_PRECISION and FACT_RECALL both improve the probability the
   model assigns to the correct tokens.

Scope limits (intentional, brutally honest):

* Only works with HF-compatible model directories (``config.json`` +
  tokenizer). GGUF paths raise with a clear message; callers with a
  llama.cpp runtime pass their own ``replay_evaluator``.
* Requires ``torch`` + ``transformers`` installed. When either is
  missing ``build_karma_evaluator`` returns ``None`` so ReplayGate
  reports ``no_evaluator`` — the gate stays honest rather than
  fabricating a plausible score.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# Return signature mirrors ReplayGate's EvaluatorFn:
# ``Callable[[model_path, corpus], float]``.
EvaluatorFn = Callable[[str, List[Dict[str, Any]]], float]


def build_karma_evaluator(
    *,
    device: str = "cpu",
    max_records: Optional[int] = None,
) -> Optional[EvaluatorFn]:
    """Return a karma-based evaluator, or ``None`` if deps are missing.

    Args:
      device:  torch device for the forward pass (``"cpu"`` / ``"cuda"``).
      max_records:  cap per-call corpus size; useful when the corpus is
          larger than a single cycle needs.

    The returned callable caches loaded (tokenizer, model) pairs per
    ``model_path`` so that scoring candidate + incumbent on the same
    corpus doesn't re-instantiate each model.
    """
    try:
        import torch  # noqa: F401
        from transformers import (  # noqa: F401
            AutoModelForCausalLM, AutoTokenizer,
        )
    except Exception as exc:
        logger.debug("karma evaluator unavailable: %s", exc)
        return None

    cache: Dict[str, Any] = {}

    def _evaluator(
        model_path: str, corpus: List[Dict[str, Any]]
    ) -> float:
        if not model_path:
            raise RuntimeError("karma_evaluator: empty model_path")
        if not os.path.isdir(model_path) or not os.path.exists(
            os.path.join(model_path, "config.json")
        ):
            raise RuntimeError(
                f"karma_evaluator requires an HF model directory "
                f"with config.json; got {model_path!r}. For GGUF "
                f"models pass a custom replay_evaluator backed by "
                f"a llama.cpp runtime."
            )

        from transformers import AutoModelForCausalLM, AutoTokenizer

        if model_path not in cache:
            tok = AutoTokenizer.from_pretrained(model_path)
            model = AutoModelForCausalLM.from_pretrained(model_path)
            model.to(device)
            model.eval()
            cache[model_path] = (tok, model)

        tok, model = cache[model_path]
        records = corpus
        if max_records is not None and len(corpus) > max_records:
            records = corpus[-int(max_records):]
        return _score_corpus_mean_loglik(
            tok, model, records, device=device,
        )

    return _evaluator


def _score_corpus_mean_loglik(
    tokenizer: Any,
    model: Any,
    corpus: List[Dict[str, Any]],
    *,
    device: str = "cpu",
) -> float:
    """Mean per-token log-likelihood of ``expected`` given ``prompt``.

    Pure scoring primitive: no HF-specific logic lives here, so the
    same math works with any tokenizer that returns an ``input_ids``
    tensor and any model whose forward pass returns an object with a
    ``.logits`` tensor of shape ``[batch, seq, vocab]``. That also
    makes the function unit-testable with lightweight fakes.
    """
    import torch  # caller ensured availability

    total_ll = 0.0
    total_tokens = 0
    with torch.no_grad():
        for rec in corpus:
            prompt = str(rec.get("prompt") or "")
            expected = str(rec.get("expected") or "")
            if not prompt or not expected:
                continue

            prompt_enc = tokenizer(prompt, return_tensors="pt")
            full_enc = tokenizer(prompt + " " + expected, return_tensors="pt")
            prompt_ids = prompt_enc.input_ids.to(device)
            full_ids = full_enc.input_ids.to(device)

            prompt_len = int(prompt_ids.shape[1])
            if full_ids.shape[1] <= prompt_len:
                continue

            out = model(full_ids)
            # Position i's logits predict the token at position i+1.
            # The expected region starts at position `prompt_len`,
            # so its predictions live at logits[prompt_len - 1 : -1].
            logits = out.logits[0, prompt_len - 1: -1, :]
            expected_tokens = full_ids[0, prompt_len:]
            if expected_tokens.shape[0] == 0:
                continue

            log_probs = torch.log_softmax(logits, dim=-1)
            tok_ll = log_probs.gather(
                -1, expected_tokens.unsqueeze(-1),
            ).squeeze(-1)
            total_ll += float(tok_ll.sum().item())
            total_tokens += int(tok_ll.shape[0])

    if total_tokens == 0:
        return float("-inf")
    return total_ll / total_tokens
