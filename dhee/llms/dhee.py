"""DheeModel LLM provider — fine-tuned Qwen3.5-2B.

Two backends:
  1. GGUF via llama-cpp-python (CPU-native, no GPU required)
  2. HuggingFace PEFT via transformers (LoRA adapters, MPS/CUDA/CPU)

Auto-detects which backend to use based on available artifacts.
Zero API cost in both modes.
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from dhee.llms.base import BaseLLM

logger = logging.getLogger(__name__)

_DEFAULT_ADAPTER_DIR = Path(__file__).resolve().parents[2] / "models" / "dhee_lora_adapters"
_BASE_MODEL_ID = "Qwen/Qwen3.5-2B"


def _find_adapter_dir(config: Optional[dict] = None) -> Optional[str]:
    """Find LoRA adapter directory."""
    cfg = config or {}
    # Explicit config
    if cfg.get("adapter_dir"):
        p = Path(cfg["adapter_dir"])
        if (p / "adapter_config.json").exists():
            return str(p)
    # Environment variable
    env = os.environ.get("DHEE_ADAPTER_DIR", "").strip()
    if env and (Path(env) / "adapter_config.json").exists():
        return env
    # Default project location
    if (_DEFAULT_ADAPTER_DIR / "adapter_config.json").exists():
        return str(_DEFAULT_ADAPTER_DIR)
    # Home directory
    home_dir = Path.home() / ".dhee" / "adapters"
    if (home_dir / "adapter_config.json").exists():
        return str(home_dir)
    return None


def _find_gguf_path(config: Optional[dict] = None) -> Optional[str]:
    """Find GGUF model file."""
    try:
        from dhee_shared.model_paths import resolve_model_path
        path = resolve_model_path(
            explicit_path=(config or {}).get("model_path"),
            model_dir=(config or {}).get("model_dir"),
        )
        if os.path.exists(path):
            return path
    except ImportError:
        pass
    return None


def _detect_device() -> str:
    """Pick best available device for HF inference."""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


class DheeLLM(BaseLLM):
    """LLM provider backed by fine-tuned Dhee model.

    Auto-selects backend:
      - HuggingFace PEFT if LoRA adapters found (models/dhee_lora_adapters/)
      - GGUF via llama-cpp if .gguf file found (~/.dhee/models/)
    Task heads: [ENGRAM], [QUERY], [ANSWER], [DECOMPOSE], [CONTEXT], [SCENE]
    """

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.temperature = self.config.get("temperature", 0.1)
        self.max_tokens = self.config.get("max_tokens", 256)
        self.top_p = self.config.get("top_p", 0.9)

        # Detect backend
        self._backend = self.config.get("backend")  # "hf" or "gguf" or None (auto)
        self._adapter_dir = _find_adapter_dir(self.config)
        self._gguf_path = _find_gguf_path(self.config)
        self._model = None
        self._tokenizer = None

        if not self._backend:
            # Prefer GGUF (15-20 tok/s) over HF PEFT (0.6 tok/s)
            if self._gguf_path:
                self._backend = "gguf"
            elif self._adapter_dir:
                self._backend = "hf"
            else:
                self._backend = "gguf"  # will error at load time with clear message

        logger.info("DheeLLM backend=%s", self._backend)

    def _ensure_model(self):
        """Lazy-load the model."""
        if self._model is not None:
            return
        if self._backend == "hf":
            self._load_hf()
        else:
            self._load_gguf()

    def _load_hf(self):
        """Load base model + LoRA adapters via HuggingFace."""
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
            from peft import PeftModel
        except ImportError as e:
            raise ImportError(
                f"HuggingFace backend requires: pip install torch transformers peft. "
                f"Missing: {e.name}"
            )

        if not self._adapter_dir:
            raise FileNotFoundError(
                "No LoRA adapters found. Place adapter_config.json + "
                "adapter_model.safetensors in models/dhee_lora_adapters/ "
                "or set DHEE_ADAPTER_DIR."
            )

        base_model_id = self.config.get("base_model", _BASE_MODEL_ID)
        device = self.config.get("device", _detect_device())
        dtype = torch.float16

        logger.info("Loading base model %s on %s...", base_model_id, device)

        if device == "mps":
            device_map = "mps"
        elif device == "cuda":
            device_map = {"": 0}
        else:
            device_map = "cpu"

        self._model = AutoModelForCausalLM.from_pretrained(
            base_model_id,
            dtype=dtype,
            device_map=device_map,
            trust_remote_code=True,
        )

        logger.info("Applying LoRA adapters from %s...", self._adapter_dir)
        self._model = PeftModel.from_pretrained(self._model, self._adapter_dir)
        self._model.eval()

        # Load tokenizer from adapter dir (has the right chat template)
        self._tokenizer = AutoTokenizer.from_pretrained(
            self._adapter_dir, trust_remote_code=True
        )
        if self._tokenizer.pad_token is None:
            self._tokenizer.pad_token = self._tokenizer.eos_token

        self._device = device
        logger.info("DheeLLM (HF) ready on %s", device)

    def _load_gguf(self):
        """Load GGUF model via llama-cpp-python."""
        try:
            from llama_cpp import Llama
        except ImportError:
            raise ImportError(
                "GGUF backend requires: pip install llama-cpp-python"
            )

        if not self._gguf_path or not os.path.exists(self._gguf_path):
            raise FileNotFoundError(
                f"GGUF model not found at {self._gguf_path}. "
                "Set DHEE_MODEL_PATH or place model in ~/.dhee/models/"
            )

        n_ctx = self.config.get("n_ctx", 4096)
        n_threads = self.config.get("n_threads", 4)

        logger.info("Loading DheeModel GGUF from %s", self._gguf_path)
        self._model = Llama(
            model_path=self._gguf_path,
            n_ctx=n_ctx,
            n_threads=n_threads,
            verbose=False,
        )
        self._device = "cpu"
        logger.info("DheeLLM (GGUF) ready")

    def generate(self, prompt: str) -> str:
        """Generate text using the local model."""
        self._ensure_model()

        if self._backend == "hf":
            return self._generate_hf(prompt)
        return self._generate_gguf(prompt)

    def _generate_hf(self, prompt: str) -> str:
        """Generate via HuggingFace transformers."""
        import torch

        inputs = self._tokenizer(prompt, return_tensors="pt")
        input_ids = inputs["input_ids"].to(self._device)
        attention_mask = inputs.get("attention_mask")
        if attention_mask is not None:
            attention_mask = attention_mask.to(self._device)

        with torch.no_grad():
            outputs = self._model.generate(
                input_ids=input_ids,
                attention_mask=attention_mask,
                max_new_tokens=self.max_tokens,
                temperature=max(self.temperature, 0.01),
                top_p=self.top_p,
                do_sample=self.temperature > 0,
                pad_token_id=self._tokenizer.pad_token_id,
            )

        new_tokens = outputs[0][input_ids.shape[1]:]
        text = self._tokenizer.decode(new_tokens, skip_special_tokens=True)
        return text.strip()

    def _generate_gguf(self, prompt: str) -> str:
        """Generate via llama-cpp-python."""
        result = self._model.create_completion(
            prompt,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            top_p=self.top_p,
            stop=["</s>", "<|endoftext|>", "<|im_end|>"],
        )
        text = result["choices"][0]["text"] if result.get("choices") else ""
        return text.strip()

    def generate_with_task(self, task: str, content: str) -> str:
        """Generate with a task prefix token.

        Task heads:
        - [ENGRAM]: text -> UniversalEngram JSON
        - [QUERY]: question -> {intent, context_filters, search_terms}
        - [ANSWER]: question + facts -> natural language
        - [DECOMPOSE]: complex question -> sub-questions
        - [CONTEXT]: text -> ContextAnchor
        - [SCENE]: text -> SceneSnapshot
        - [MEMORY_OP]: context -> optimal memory operation
        - [HEURISTIC]: trajectory summary -> abstract reasoning pattern
        - [RETRIEVAL_JUDGE]: query + results -> sufficiency score 0.0-1.0
        """
        prompt = f"[{task.upper()}]\n{content}"
        return self.generate(prompt)

    def extract_engram(self, content: str, session_ctx: Optional[Dict] = None) -> str:
        """[ENGRAM] task: extract structured engram from text."""
        ctx_block = ""
        if session_ctx:
            import json
            ctx_block = f"\nSESSION: {json.dumps(session_ctx, default=str)}"
        return self.generate_with_task("ENGRAM", content + ctx_block)

    def classify_query(self, query: str) -> str:
        """[QUERY] task: classify intent and extract search params."""
        return self.generate_with_task("QUERY", query)

    def synthesize_answer(self, question: str, facts_json: str) -> str:
        """[ANSWER] task: synthesize answer from structured facts."""
        return self.generate_with_task("ANSWER", f"Q: {question}\nFACTS: {facts_json}")

    def decompose(self, question: str) -> str:
        """[DECOMPOSE] task: break into sub-questions."""
        return self.generate_with_task("DECOMPOSE", question)

    def extract_context(self, text: str) -> str:
        """[CONTEXT] task: extract context anchor."""
        return self.generate_with_task("CONTEXT", text)

    def extract_scene(self, text: str) -> str:
        """[SCENE] task: extract scene snapshot."""
        return self.generate_with_task("SCENE", text)

    # --- BuddhiMini task heads (added for self-evolution) ---

    def classify_memory_op(self, context: str) -> str:
        """[MEMORY_OP] task: predict optimal memory operation for context.

        Returns: store | retrieve | update | summarize | discard | none
        """
        return self.generate_with_task("MEMORY_OP", context)

    def generate_heuristic(self, trajectory_summary: str) -> str:
        """[HEURISTIC] task: distill abstract reasoning pattern from trajectory."""
        return self.generate_with_task("HEURISTIC", trajectory_summary)

    def judge_retrieval(self, query: str, results_text: str) -> str:
        """[RETRIEVAL_JUDGE] task: score retrieval sufficiency 0.0-1.0."""
        prompt = f"Query: {query}\nResults:\n{results_text}"
        return self.generate_with_task("RETRIEVAL_JUDGE", prompt)

    @property
    def backend(self) -> str:
        return self._backend


def is_dhee_model_available() -> bool:
    """Check if any Dhee model (LoRA adapters or GGUF) is available locally."""
    if _find_adapter_dir():
        return True
    if _find_gguf_path():
        return True
    return False
