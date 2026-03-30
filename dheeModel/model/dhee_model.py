"""DheeModel Runtime — fine-tuned Qwen3.5 inference via llama.cpp.

6 task heads, 1 model. CPU-native, no GPU required.

Tasks:
    [ENGRAM]     text + session_context -> UniversalEngram JSON
    [QUERY]      natural question -> {intent, context_filters, search_terms}
    [ANSWER]     question + structured_facts -> natural language answer
    [DECOMPOSE]  complex question -> list of sub-questions
    [CONTEXT]    text -> ContextAnchor
    [SCENE]      text -> SceneSnapshot + ProspectiveScene (if future intent detected)
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from dhee_shared.model_paths import resolve_model_path

logger = logging.getLogger(__name__)


@dataclass
class QueryPlan:
    """Parsed query intent and search parameters."""
    intent: str = "freeform"
    context_filters: Dict[str, Any] = None
    search_terms: List[str] = None
    subject: Optional[str] = None
    predicate: Optional[str] = None
    chain_request: bool = False

    def __post_init__(self):
        if self.context_filters is None:
            self.context_filters = {}
        if self.search_terms is None:
            self.search_terms = []


class DheeModel:
    """Fine-tuned Qwen3.5 family model for Dhee's cognitive tasks.

    Loads a GGUF model via llama-cpp-python for CPU-native inference.
    All operations are local, zero API cost.
    """

    def __init__(self, model_path: Optional[str] = None):
        self.model_path = resolve_model_path(model_path)
        self._llm = None

    def _ensure_loaded(self):
        """Lazy-load the GGUF model."""
        if self._llm is not None:
            return

        try:
            from llama_cpp import Llama
        except ImportError:
            raise ImportError(
                "llama-cpp-python required. Install: pip install llama-cpp-python"
            )

        if not os.path.exists(self.model_path):
            raise FileNotFoundError(
                f"DheeModel not found at {self.model_path}. "
                "Train the Kaggle Hugging Face pipeline first or set DHEE_MODEL_PATH."
            )

        self._llm = Llama(
            model_path=self.model_path,
            n_ctx=4096,
            n_threads=4,
            verbose=False,
        )
        logger.info("DheeModel loaded: %s", self.model_path)

    def _generate(self, prompt: str, max_tokens: int = 2048) -> str:
        self._ensure_loaded()
        result = self._llm.create_completion(
            prompt,
            max_tokens=max_tokens,
            temperature=0.1,
            top_p=0.9,
            stop=["</s>", "<|endoftext|>", "<|im_end|>"],
        )
        return result["choices"][0]["text"].strip() if result.get("choices") else ""

    def extract_engram(self, content: str, session_ctx: Optional[Dict] = None) -> Dict:
        """[ENGRAM] Extract structured engram from text."""
        ctx_part = ""
        if session_ctx:
            ctx_part = f"\nSESSION: {json.dumps(session_ctx, default=str)}"
        response = self._generate(f"[ENGRAM]\n{content}{ctx_part}")
        return self._parse_json(response) or {}

    def classify_query(self, query: str) -> QueryPlan:
        """[QUERY] Classify query intent and extract search parameters."""
        response = self._generate(f"[QUERY]\n{query}")
        parsed = self._parse_json(response)
        if not parsed:
            return QueryPlan(search_terms=query.split())
        return QueryPlan(
            intent=parsed.get("intent", "freeform"),
            context_filters=parsed.get("context_filters", {}),
            search_terms=parsed.get("search_terms", query.split()),
            subject=parsed.get("subject"),
            predicate=parsed.get("predicate"),
            chain_request=parsed.get("chain_request", False),
        )

    def synthesize_answer(
        self, question: str, facts: List[Dict[str, Any]]
    ) -> str:
        """[ANSWER] Synthesize natural language answer from structured facts."""
        facts_json = json.dumps(facts, default=str)
        return self._generate(f"[ANSWER]\nQ: {question}\nFACTS: {facts_json}")

    def decompose(
        self, question: str, known_context: Optional[List[Dict]] = None
    ) -> List[Dict]:
        """[DECOMPOSE] Break complex question into sub-questions."""
        ctx_part = ""
        if known_context:
            ctx_part = f"\nCONTEXT: {json.dumps(known_context, default=str)}"
        response = self._generate(f"[DECOMPOSE]\n{question}{ctx_part}")
        parsed = self._parse_json(response)
        if isinstance(parsed, list):
            return parsed
        return [{"question": question, "search_queries": question.split()}]

    def extract_context(self, text: str) -> Dict:
        """[CONTEXT] Extract context anchor from text."""
        response = self._generate(f"[CONTEXT]\n{text}")
        return self._parse_json(response) or {}

    def extract_scene(self, text: str) -> Dict:
        """[SCENE] Extract scene snapshot from text.

        Also detects future intent and generates ProspectiveScene data
        when plans/commitments are found.
        """
        response = self._generate(f"[SCENE]\n{text}")
        return self._parse_json(response) or {}

    def _parse_json(self, text: str) -> Any:
        if not text:
            return None
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:] if lines[0].startswith("```") else lines
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            for start_char, end_char in [("{", "}"), ("[", "]")]:
                s = text.find(start_char)
                e = text.rfind(end_char)
                if s >= 0 and e > s:
                    try:
                        return json.loads(text[s:e + 1])
                    except json.JSONDecodeError:
                        continue
        return None
