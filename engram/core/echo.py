"""
EchoMem - Multi-modal echo encoding for stronger memory retention.

Inspired by human cognition: when we vocalize or rehearse information,
it creates stronger memory traces than passive observation.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, TypeVar

from pydantic import BaseModel, Field, ValidationError, field_validator
try:
    from pydantic import ConfigDict
except ImportError:  # pragma: no cover - fallback for older pydantic
    ConfigDict = None

from engram.utils.prompts import BATCH_ECHO_PROCESSING_PROMPT, ECHO_PROCESSING_PROMPT

logger = logging.getLogger(__name__)

T = TypeVar("T")

def retry_parse(max_retries: int = 2, delay: float = 0.5):
    """Decorator to retry a function on parsing errors."""
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception = None
            for i in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except (ValidationError, json.JSONDecodeError) as e:
                    last_exception = e
                    if i < max_retries:
                        logger.warning(f"Parsing failed (attempt {i+1}/{max_retries+1}): {e}. Retrying...")
                        time.sleep(delay)
            
            # If we get here, all retries failed
            if last_exception:
                raise last_exception
            return func(*args, **kwargs) # Should not happen
        return wrapper
    return decorator


class EchoDepth(str, Enum):
    """Echo processing depth levels."""
    SHALLOW = "shallow"   # Keywords only - minimal processing
    MEDIUM = "medium"     # Keywords + paraphrase
    DEEP = "deep"         # Full multi-modal echo


class EchoOutput(BaseModel):
    """Structured output from LLM for echo processing."""
    if ConfigDict:
        model_config = ConfigDict(extra="ignore")

    paraphrases: List[str] = Field(description="3-5 diverse rephrasings of the memory.")
    keywords: List[str] = Field(description="Core concepts and entities.")
    implications: List[str] = Field(default_factory=list, description="Logical consequences or 'if-then' deductions.")
    questions: List[str] = Field(default_factory=list, description="Questions this memory specifically answers.")
    question_form: Optional[str] = Field(None, description="Single question-form version of the memory.")
    category: Optional[str] = Field(None, description="The semantic bucket (e.g., fact, preference, goal).")
    importance: float = Field(ge=0.0, le=1.0, description="Significance of the information.")

    @field_validator("paraphrases", "keywords", "implications", "questions", mode="before")
    def _coerce_list(cls, value):
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            return [value]
        return [str(value)]

    @field_validator("paraphrases", "keywords", "implications", "questions", mode="after")
    def _clean_list(cls, value):
        cleaned = []
        for item in value:
            if not isinstance(item, str):
                item = str(item)
            item = item.strip()
            if item:
                cleaned.append(item)
        return cleaned

    @field_validator("category", mode="before")
    def _clean_category(cls, value):
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    @field_validator("question_form", mode="before")
    def _clean_question_form(cls, value):
        if value is None:
            return None
        if isinstance(value, list):
            if not value:
                return None
            value = value[0]
        value = str(value).strip()
        return value or None

    @field_validator("importance", mode="before")
    def _coerce_importance(cls, value):
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return value
        return value


@dataclass
class EchoResult:
    """Result of echo processing."""
    raw: str
    paraphrases: List[str]
    keywords: List[str]
    implications: List[str]
    questions: List[str]
    category: Optional[str]
    importance: float  # 0.0 - 1.0
    echo_depth: EchoDepth
    strength_multiplier: float  # Based on echo depth
    question_form: Optional[str] = None

    def to_metadata(self) -> Dict[str, Any]:
        """Convert to metadata dict for storage."""
        return {
            "echo_paraphrases": self.paraphrases,
            "echo_keywords": self.keywords,
            "echo_implications": self.implications,
            "echo_questions": self.questions,
            "echo_question_form": self.question_form,
            "echo_category": self.category,
            "echo_importance": self.importance,
            "echo_depth": self.echo_depth.value,
        }


class EchoProcessor:
    """Processes memories through multi-modal echo encoding."""

    # Strength multipliers for each echo depth
    STRENGTH_MULTIPLIERS = {
        EchoDepth.SHALLOW: 1.0,
        EchoDepth.MEDIUM: 1.3,
        EchoDepth.DEEP: 1.6,
    }

    def __init__(self, llm, config: Optional[Dict[str, Any]] = None):
        self.llm = llm
        self.config = config or {}
        self.auto_depth = self.config.get("auto_depth", True)
        self.default_depth = EchoDepth(self.config.get("default_depth", "medium"))

    def process(
        self,
        content: str,
        depth: Optional[EchoDepth] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> EchoResult:
        """
        Process content through echo encoding.

        Args:
            content: The raw memory content
            depth: Override echo depth (if None, auto-detect based on importance)
            context: Additional context for importance assessment

        Returns:
            EchoResult with multi-modal representations
        """
        # Determine echo depth
        if depth is None and self.auto_depth:
            depth = self._assess_depth(content, context)
        elif depth is None:
            depth = self.default_depth

        # Process based on depth
        if depth == EchoDepth.SHALLOW:
            return self._shallow_echo(content)
        elif depth == EchoDepth.MEDIUM:
            return self._medium_echo(content)
        else:
            return self._deep_echo(content)

    def _assess_depth(
        self, content: str, context: Optional[Dict[str, Any]] = None
    ) -> EchoDepth:
        """
        Auto-detect appropriate echo depth based on content signals.

        Signals that increase importance:
        - Explicit importance markers ("important", "remember", "always")
        - Contains numbers (IDs, phone numbers, etc.)
        - Contains proper nouns (names, places)
        - Contains dates
        - Is a preference/habit statement
        - Contains credentials/secrets markers
        - Repeated in context
        """
        signals = 0
        content_lower = content.lower()

        # Explicit importance markers
        importance_patterns = [
            r'\b(important|remember|don\'t forget|always|never|must|critical)\b',
        ]
        for pattern in importance_patterns:
            if re.search(pattern, content_lower):
                signals += 2
                break

        # Contains significant numbers (3+ digits)
        if re.search(r'\d{3,}', content):
            signals += 1

        # Contains dates
        date_patterns = [
            r'\d{1,2}/\d{1,2}(/\d{2,4})?',
            r'\d{1,2}-\d{1,2}(-\d{2,4})?',
            r'\b(january|february|march|april|may|june|july|august|september|october|november|december)\b',
        ]
        for pattern in date_patterns:
            if re.search(pattern, content_lower):
                signals += 1
                break

        # Contains proper nouns (simple heuristic: capitalized words not at start)
        words = content.split()
        if len(words) > 1:
            proper_nouns = [w for w in words[1:] if w and w[0].isupper()]
            if proper_nouns:
                signals += 1

        # Is a preference statement
        preference_patterns = [
            r'\b(prefer|like|love|hate|favorite|always use|never use)\b',
        ]
        for pattern in preference_patterns:
            if re.search(pattern, content_lower):
                signals += 1
                break

        # Contains credential/secret markers
        secret_patterns = [
            r'\b(password|api[_\s]?key|token|secret|credential|auth)\b',
        ]
        for pattern in secret_patterns:
            if re.search(pattern, content_lower):
                signals += 2
                break

        # Context signals
        if context:
            # Mentioned multiple times in conversation
            if context.get("mention_count", 0) > 1:
                signals += 1
            # User explicitly marked as important
            if context.get("user_marked_important"):
                signals += 2

        # Map signals to depth
        if signals >= 3:
            return EchoDepth.DEEP
        elif signals >= 1:
            return EchoDepth.MEDIUM
        return EchoDepth.SHALLOW

    def _shallow_echo(self, content: str) -> EchoResult:
        """Shallow echo: keywords extraction only (no LLM call)."""
        keywords = self._extract_keywords_simple(content)

        return EchoResult(
            raw=content,
            paraphrases=[],
            keywords=keywords,
            implications=[],
            questions=[],
            question_form=None,
            category=None,
            importance=0.3,
            echo_depth=EchoDepth.SHALLOW,
            strength_multiplier=self.STRENGTH_MULTIPLIERS[EchoDepth.SHALLOW],
        )

    def _medium_echo(self, content: str) -> EchoResult:
        """Medium echo: keywords + paraphrase."""
        try:
            prompt = ECHO_PROCESSING_PROMPT.format(
                content=content,
                depth="medium",
                depth_instructions="Generate: paraphrases, keywords, category. Skip: implications, questions.",
            )
            response = self.llm.generate(prompt)
            parsed = self._parse_echo_response(response)
            if not parsed.paraphrases or not parsed.keywords:
                raise ValueError("Echo response missing paraphrases or keywords")

            question_form = parsed.question_form
            if not question_form and parsed.questions:
                question_form = parsed.questions[0]

            return EchoResult(
                raw=content,
                paraphrases=parsed.paraphrases,
                keywords=parsed.keywords,
                implications=[],
                questions=[],
                question_form=question_form,
                category=parsed.category,
                importance=parsed.importance,
                echo_depth=EchoDepth.MEDIUM,
                strength_multiplier=self.STRENGTH_MULTIPLIERS[EchoDepth.MEDIUM],
            )
        except (json.JSONDecodeError, ValueError, KeyError, AttributeError) as e:
            logger.warning(f"Medium echo failed, falling back to shallow: {e}")
            return self._shallow_echo(content)

    def _deep_echo(self, content: str) -> EchoResult:
        """Deep echo: full multi-modal processing."""
        try:
            prompt = ECHO_PROCESSING_PROMPT.format(
                content=content,
                depth="deep",
                depth_instructions="Generate ALL fields: paraphrases, keywords, implications, questions, category.",
            )
            response = self.llm.generate(prompt)
            parsed = self._parse_echo_response(response)
            if not parsed.paraphrases or not parsed.keywords:
                raise ValueError("Echo response missing paraphrases or keywords")

            question_form = parsed.question_form
            if not question_form and parsed.questions:
                question_form = parsed.questions[0]

            return EchoResult(
                raw=content,
                paraphrases=parsed.paraphrases,
                keywords=parsed.keywords,
                implications=parsed.implications,
                questions=parsed.questions,
                question_form=question_form,
                category=parsed.category,
                importance=parsed.importance,
                echo_depth=EchoDepth.DEEP,
                strength_multiplier=self.STRENGTH_MULTIPLIERS[EchoDepth.DEEP],
            )
        except (json.JSONDecodeError, ValueError, KeyError, AttributeError) as e:
            logger.warning(f"Deep echo failed, falling back to medium: {e}")
            return self._medium_echo(content)

    def _extract_keywords_simple(self, content: str) -> List[str]:
        """Simple keyword extraction without LLM."""
        # Remove common stop words and extract significant terms
        stop_words = {
            'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
            'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
            'should', 'may', 'might', 'must', 'shall', 'can', 'need', 'dare',
            'ought', 'used', 'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by',
            'from', 'as', 'into', 'through', 'during', 'before', 'after',
            'above', 'below', 'between', 'under', 'again', 'further', 'then',
            'once', 'here', 'there', 'when', 'where', 'why', 'how', 'all',
            'each', 'few', 'more', 'most', 'other', 'some', 'such', 'no', 'nor',
            'not', 'only', 'own', 'same', 'so', 'than', 'too', 'very', 'just',
            'and', 'but', 'if', 'or', 'because', 'until', 'while', 'this',
            'that', 'these', 'those', 'i', 'me', 'my', 'myself', 'we', 'our',
            'you', 'your', 'he', 'him', 'his', 'she', 'her', 'it', 'its',
            'they', 'them', 'their', 'what', 'which', 'who', 'whom',
        }

        # Tokenize and filter
        words = re.findall(r'\b[a-zA-Z]+\b', content.lower())
        keywords = [w for w in words if w not in stop_words and len(w) > 2]

        # Get unique keywords, preserving order
        seen = set()
        unique = []
        for kw in keywords:
            if kw not in seen:
                seen.add(kw)
                unique.append(kw)

        return unique[:10]  # Limit to 10 keywords

    @retry_parse(max_retries=2)
    def _parse_echo_response(self, response: str) -> EchoOutput:
        """Parse LLM response for echo data using Pydantic."""
        json_str = self._extract_json_blob(response)
        try:
            return EchoOutput.model_validate_json(json_str)
        except (ValidationError, json.JSONDecodeError):
            repaired = self._repair_json(json_str)
            try:
                return EchoOutput.model_validate_json(repaired)
            except (ValidationError, json.JSONDecodeError):
                data = self._load_json_dict(repaired)
                if data is None:
                    data = self._load_json_dict(json_str)
                if data is None:
                    raise
                normalized = self._normalize_echo_dict(data)
                return EchoOutput.model_validate(normalized)

    def _extract_json_blob(self, response: str) -> str:
        text = (response or "").strip()
        if not text:
            return text
        fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        if fence_match:
            return fence_match.group(1).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return text[start:end + 1].strip()
        return text

    def _repair_json(self, text: str) -> str:
        if not text:
            return text
        # Remove trailing commas before } or ]
        repaired = re.sub(r",(\s*[}\]])", r"\1", text)
        return repaired

    def _load_json_dict(self, text: str) -> Optional[Dict[str, Any]]:
        try:
            data = json.loads(text)
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def _normalize_echo_dict(self, data: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(data)
        if "paraphrases" not in normalized and "paraphrase" in normalized:
            normalized["paraphrases"] = normalized.pop("paraphrase")
        if "questions" not in normalized and "question_form" in normalized:
            normalized["questions"] = normalized.get("question_form")
        return normalized

    def process_batch(
        self,
        contents: List[str],
        depth: Optional[EchoDepth] = None,
    ) -> List[EchoResult]:
        """Batch-process multiple contents through echo encoding.

        Sends a single LLM call with all memories. Falls back to sequential
        per-item processing on parse failure.
        """
        if not contents:
            return []
        if len(contents) == 1:
            return [self.process(contents[0], depth=depth)]

        # Determine depths
        target_depth = depth or self.default_depth
        if target_depth == EchoDepth.SHALLOW:
            # Shallow is LLM-free, just do it sequentially
            return [self._shallow_echo(c) for c in contents]

        depth_instructions = (
            "Generate: paraphrases, keywords, category. Skip: implications, questions."
            if target_depth == EchoDepth.MEDIUM
            else "Generate ALL fields: paraphrases, keywords, implications, questions, category."
        )

        memories_block = "\n".join(
            f"{i+1}. {c[:500]}" for i, c in enumerate(contents)
        )

        prompt = BATCH_ECHO_PROCESSING_PROMPT.format(
            memories_block=memories_block,
            depth=target_depth.value,
            depth_instructions=depth_instructions,
            count=len(contents),
        )

        try:
            response = self.llm.generate(prompt)
            return self._parse_batch_echo_response(response, contents, target_depth)
        except Exception as e:
            logger.warning("Batch echo failed, falling back to sequential: %s", e)
            return [self.process(c, depth=target_depth) for c in contents]

    def _parse_batch_echo_response(
        self, response: str, contents: List[str], target_depth: EchoDepth
    ) -> List[EchoResult]:
        """Parse batch LLM response. Falls back per-item on partial failure."""
        json_str = self._extract_json_blob(response)
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            repaired = self._repair_json(json_str)
            data = json.loads(repaired)

        results_list = data.get("results", [])
        if not isinstance(results_list, list):
            raise ValueError("Batch response 'results' is not a list")

        # Index parsed results
        parsed_by_index = {}
        for item in results_list:
            idx = item.get("index", -1)
            if 0 <= idx < len(contents):
                parsed_by_index[idx] = item

        results: List[EchoResult] = []
        multiplier = self.STRENGTH_MULTIPLIERS.get(target_depth, 1.0)

        for i, content in enumerate(contents):
            if i in parsed_by_index:
                item = parsed_by_index[i]
                try:
                    echo_out = EchoOutput.model_validate(item)
                    question_form = echo_out.question_form
                    if not question_form and echo_out.questions:
                        question_form = echo_out.questions[0]

                    results.append(EchoResult(
                        raw=content,
                        paraphrases=echo_out.paraphrases,
                        keywords=echo_out.keywords,
                        implications=echo_out.implications if target_depth == EchoDepth.DEEP else [],
                        questions=echo_out.questions if target_depth == EchoDepth.DEEP else [],
                        question_form=question_form,
                        category=echo_out.category,
                        importance=echo_out.importance,
                        echo_depth=target_depth,
                        strength_multiplier=multiplier,
                    ))
                    continue
                except Exception:
                    pass
            # Fallback: process this item sequentially
            results.append(self.process(content, depth=target_depth))

        return results

    def reecho(self, memory: Dict[str, Any]) -> EchoResult:
        """
        Re-echo a memory on retrieval to strengthen it.

        This simulates the human process of rehearsal strengthening memory.
        """
        content = memory.get("memory", "")
        metadata = memory.get("metadata", {})

        # Get current echo depth and go one level deeper if possible
        current_depth = metadata.get("echo_depth", "shallow")

        if current_depth == "shallow":
            new_depth = EchoDepth.MEDIUM
        elif current_depth == "medium":
            new_depth = EchoDepth.DEEP
        else:
            new_depth = EchoDepth.DEEP  # Already at max

        return self.process(content, depth=new_depth)
