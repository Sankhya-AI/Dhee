"""Unified Enrichment — single LLM call for echo + category + entities + profiles.

Replaces 4 separate LLM calls per memory with one combined call.
Backward compatible: individual processors stay unchanged; unified is an
alternative path that falls back to individual calls on parse failure.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dhee.core.category import CategoryMatch
from dhee.core.echo import EchoDepth, EchoProcessor, EchoResult
from dhee.core.graph import Entity, EntityType
from dhee.core.profile import ProfileUpdate
from dhee.utils.prompts import (
    UNIFIED_ENRICHMENT_BATCH_PROMPT,
    UNIFIED_ENRICHMENT_PROMPT,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic output models — what the LLM returns
# ---------------------------------------------------------------------------


class UnifiedEchoOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    paraphrases: List[str] = []
    keywords: List[str] = []
    implications: List[str] = []
    questions: List[str] = []
    question_form: Optional[str] = None
    category: Optional[str] = None  # fact|preference|goal|relationship|event
    importance: float = 0.5

    @field_validator("paraphrases", "keywords", "implications", "questions", mode="before")
    @classmethod
    def _coerce_list(cls, value):
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            return [value]
        return [str(value)]

    @field_validator("importance", mode="before")
    @classmethod
    def _coerce_importance(cls, value):
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return 0.5
        if value is None:
            return 0.5
        return value

    @field_validator("question_form", mode="before")
    @classmethod
    def _clean_question_form(cls, value):
        if value is None:
            return None
        if isinstance(value, list):
            return value[0] if value else None
        value = str(value).strip()
        return value or None

    @field_validator("category", mode="before")
    @classmethod
    def _clean_category(cls, value):
        if value is None:
            return None
        value = str(value).strip()
        return value or None


class UnifiedCategoryOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: str = "use_existing"  # use_existing|create_child|create_new
    category_id: Optional[str] = None
    new_category: Optional[Dict[str, Any]] = None  # {name, description, keywords, parent_id}
    confidence: float = 0.5

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, value):
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return 0.5
        if value is None:
            return 0.5
        return value


class UnifiedEntityOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    type: str = "unknown"  # person|organization|technology|concept|location|project|tool|preference


class UnifiedProfileOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    type: str = "contact"  # self|contact|entity
    facts: List[str] = []
    preferences: List[str] = []

    @field_validator("facts", "preferences", mode="before")
    @classmethod
    def _coerce_list(cls, value):
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            return [value]
        return [str(value)]


class UnifiedEnrichmentOutput(BaseModel):
    """Full parsed output from a single unified LLM call."""
    model_config = ConfigDict(extra="ignore")

    echo: UnifiedEchoOutput = Field(default_factory=UnifiedEchoOutput)
    category: UnifiedCategoryOutput = Field(default_factory=UnifiedCategoryOutput)
    entities: List[UnifiedEntityOutput] = []
    profiles: List[UnifiedProfileOutput] = []
    facts: List[str] = []


# ---------------------------------------------------------------------------
# Bridge dataclass — unified output → existing processor types
# ---------------------------------------------------------------------------


@dataclass
class EnrichmentResult:
    """Bridges unified output to existing processor types."""
    echo_result: Optional[EchoResult] = None
    category_match: Optional[CategoryMatch] = None
    entities: List[Entity] = field(default_factory=list)
    profile_updates: List[ProfileUpdate] = field(default_factory=list)
    facts: List[str] = field(default_factory=list)
    raw_response: str = ""


# ---------------------------------------------------------------------------
# Processor
# ---------------------------------------------------------------------------

# Depth instructions for prompt construction
_DEPTH_INSTRUCTIONS = {
    EchoDepth.SHALLOW: "keywords only (skip paraphrases, implications, questions)",
    EchoDepth.MEDIUM: "paraphrases, keywords, category. Skip: implications, questions.",
    EchoDepth.DEEP: "ALL fields: paraphrases, keywords, implications, questions, question_form, category.",
}


class UnifiedEnrichmentProcessor:
    """Single LLM call for echo + category + entity + profile extraction."""

    def __init__(
        self,
        llm,
        echo_processor: Optional[EchoProcessor] = None,
        category_processor=None,
        knowledge_graph=None,
        profile_processor=None,
    ):
        self.llm = llm
        self.echo_processor = echo_processor
        self.category_processor = category_processor
        self.knowledge_graph = knowledge_graph
        self.profile_processor = profile_processor

    # ------------------------------------------------------------------
    # Single-memory enrichment
    # ------------------------------------------------------------------

    def enrich(
        self,
        content: str,
        depth: EchoDepth = EchoDepth.MEDIUM,
        existing_categories: Optional[str] = None,
        include_entities: bool = True,
        include_profiles: bool = True,
    ) -> EnrichmentResult:
        """Single LLM call for one memory. Falls back to individual on failure."""
        prompt = self._build_prompt(
            content, depth, existing_categories,
            include_entities=include_entities,
            include_profiles=include_profiles,
        )
        try:
            response = self.llm.generate(prompt)
            return self._parse_response(response, content, depth)
        except Exception as e:
            logger.warning("Unified enrichment failed, falling back to individual: %s", e)
            return self._fallback_individual(content, depth, existing_categories)

    # ------------------------------------------------------------------
    # Batch enrichment
    # ------------------------------------------------------------------

    def enrich_batch(
        self,
        contents: List[str],
        depth: EchoDepth = EchoDepth.MEDIUM,
        existing_categories: Optional[str] = None,
        include_entities: bool = True,
        include_profiles: bool = True,
    ) -> List[EnrichmentResult]:
        """Single LLM call for N memories. Falls back per-item on failure."""
        if not contents:
            return []
        if len(contents) == 1:
            return [self.enrich(
                contents[0], depth, existing_categories,
                include_entities=include_entities,
                include_profiles=include_profiles,
            )]

        prompt = self._build_batch_prompt(
            contents, depth, existing_categories,
            include_entities=include_entities,
            include_profiles=include_profiles,
        )
        try:
            response = self.llm.generate(prompt)
            return self._parse_batch_response(response, contents, depth)
        except Exception as e:
            logger.warning("Unified batch enrichment failed, falling back to sequential: %s", e)
            return [
                self.enrich(c, depth, existing_categories,
                            include_entities=include_entities,
                            include_profiles=include_profiles)
                for c in contents
            ]

    # ------------------------------------------------------------------
    # Prompt builders
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        content: str,
        depth: EchoDepth,
        existing_categories: Optional[str] = None,
        include_entities: bool = True,
        include_profiles: bool = True,
    ) -> str:
        cats = existing_categories or self._format_existing_categories()
        depth_instructions = _DEPTH_INSTRUCTIONS.get(depth, _DEPTH_INSTRUCTIONS[EchoDepth.MEDIUM])
        return UNIFIED_ENRICHMENT_PROMPT.format(
            content=content[:2000],
            depth=depth.value,
            depth_instructions=depth_instructions,
            existing_categories=cats,
            include_entities="yes" if include_entities else "no",
            include_profiles="yes" if include_profiles else "no",
        )

    def _build_batch_prompt(
        self,
        contents: List[str],
        depth: EchoDepth,
        existing_categories: Optional[str] = None,
        include_entities: bool = True,
        include_profiles: bool = True,
    ) -> str:
        cats = existing_categories or self._format_existing_categories()
        depth_instructions = _DEPTH_INSTRUCTIONS.get(depth, _DEPTH_INSTRUCTIONS[EchoDepth.MEDIUM])
        memories_block = "\n".join(
            f"[{i}] {c[:2000]}" for i, c in enumerate(contents)
        )
        return UNIFIED_ENRICHMENT_BATCH_PROMPT.format(
            memories_block=memories_block,
            count=len(contents),
            depth=depth.value,
            depth_instructions=depth_instructions,
            existing_categories=cats,
            include_entities="yes" if include_entities else "no",
            include_profiles="yes" if include_profiles else "no",
        )

    def _format_existing_categories(self) -> str:
        if not self.category_processor:
            return "(none)"
        cats = self.category_processor.get_all_categories()
        if not cats:
            return "(none)"
        return "\n".join(
            f"- {c['id']}: {c['name']} — {c.get('description', '')}"
            for c in cats[:30]
        )

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(
        self,
        response: str,
        content: str,
        depth: EchoDepth,
    ) -> EnrichmentResult:
        """Parse LLM response into EnrichmentResult."""
        json_str = _extract_json_blob(response)
        data = _robust_json_load(json_str)

        try:
            unified = UnifiedEnrichmentOutput.model_validate(data)
        except Exception:
            # Try normalizing keys
            normalized = _normalize_unified_dict(data)
            unified = UnifiedEnrichmentOutput.model_validate(normalized)

        return EnrichmentResult(
            echo_result=self._to_echo_result(unified.echo, content, depth),
            category_match=self._to_category_match(unified.category),
            entities=self._to_entities(unified.entities),
            profile_updates=self._to_profile_updates(unified.profiles),
            facts=[f for f in unified.facts if isinstance(f, str) and f.strip()],
            raw_response=response,
        )

    def _parse_batch_response(
        self,
        response: str,
        contents: List[str],
        depth: EchoDepth,
    ) -> List[EnrichmentResult]:
        """Parse batch LLM response. Falls back per-item on partial failure."""
        json_str = _extract_json_blob(response)
        data = _robust_json_load(json_str)

        results_list = data.get("results", [])
        if not isinstance(results_list, list):
            raise ValueError("Batch response 'results' is not a list")

        # Index by position
        parsed_by_index: Dict[int, Dict[str, Any]] = {}
        for item in results_list:
            idx = item.get("index", -1)
            if 0 <= idx < len(contents):
                parsed_by_index[idx] = item

        results: List[EnrichmentResult] = []
        for i, content in enumerate(contents):
            if i in parsed_by_index:
                try:
                    unified = UnifiedEnrichmentOutput.model_validate(parsed_by_index[i])
                    results.append(EnrichmentResult(
                        echo_result=self._to_echo_result(unified.echo, content, depth),
                        category_match=self._to_category_match(unified.category),
                        entities=self._to_entities(unified.entities),
                        profile_updates=self._to_profile_updates(unified.profiles),
                        facts=[f for f in unified.facts if isinstance(f, str) and f.strip()],
                    ))
                    continue
                except Exception:
                    pass
            # Fallback: enrich individually
            results.append(self.enrich(content, depth))
        return results

    # ------------------------------------------------------------------
    # Converters: unified output → existing types
    # ------------------------------------------------------------------

    def _to_echo_result(
        self,
        echo_out: UnifiedEchoOutput,
        content: str,
        depth: EchoDepth,
    ) -> EchoResult:
        multiplier = EchoProcessor.STRENGTH_MULTIPLIERS.get(depth, 1.0)

        question_form = echo_out.question_form
        if not question_form and echo_out.questions:
            question_form = echo_out.questions[0]

        return EchoResult(
            raw=content,
            paraphrases=echo_out.paraphrases,
            keywords=echo_out.keywords,
            implications=echo_out.implications if depth == EchoDepth.DEEP else [],
            questions=echo_out.questions if depth == EchoDepth.DEEP else [],
            question_form=question_form,
            category=echo_out.category,
            importance=echo_out.importance,
            echo_depth=depth,
            strength_multiplier=multiplier,
        )

    def _to_category_match(self, cat_out: UnifiedCategoryOutput) -> CategoryMatch:
        action = cat_out.action

        if action == "use_existing" and cat_out.category_id:
            # Verify it exists if we have a processor
            if self.category_processor:
                cat = self.category_processor.get_category(cat_out.category_id)
                if cat:
                    return CategoryMatch(
                        category_id=cat.id,
                        category_name=cat.name,
                        confidence=cat_out.confidence,
                    )
            # Still return what the LLM said, even without verification
            return CategoryMatch(
                category_id=cat_out.category_id,
                category_name=cat_out.category_id,
                confidence=cat_out.confidence,
            )

        if action in ("create_child", "create_new") and cat_out.new_category:
            new_cat = cat_out.new_category
            if self.category_processor:
                cat_id = self.category_processor._create_category(
                    name=new_cat.get("name", "Unnamed"),
                    description=new_cat.get("description", ""),
                    keywords=new_cat.get("keywords", []),
                    parent_id=new_cat.get("parent_id"),
                )
                return CategoryMatch(
                    category_id=cat_id,
                    category_name=new_cat.get("name", "Unnamed"),
                    confidence=cat_out.confidence,
                    is_new=True,
                    suggested_parent_id=new_cat.get("parent_id"),
                )

        # Fallback
        return CategoryMatch(
            category_id="context",
            category_name="Context & Situations",
            confidence=0.3,
        )

    def _to_entities(self, entity_outs: List[UnifiedEntityOutput]) -> List[Entity]:
        entities = []
        for eo in entity_outs:
            name = eo.name.strip()
            if not name:
                continue
            try:
                entity_type = EntityType(eo.type)
            except ValueError:
                entity_type = EntityType.UNKNOWN
            entities.append(Entity(name=name, entity_type=entity_type))
        return entities

    def _to_profile_updates(self, profile_outs: List[UnifiedProfileOutput]) -> List[ProfileUpdate]:
        updates = []
        for po in profile_outs:
            name = po.name.strip()
            if not name:
                continue
            updates.append(ProfileUpdate(
                profile_name=name,
                profile_type=po.type,
                new_facts=po.facts,
                new_preferences=po.preferences,
            ))
        return updates

    # ------------------------------------------------------------------
    # Fallback: individual processor calls
    # ------------------------------------------------------------------

    def _fallback_individual(
        self,
        content: str,
        depth: EchoDepth,
        existing_categories: Optional[str],
    ) -> EnrichmentResult:
        """If unified parsing fails, call each processor separately."""
        echo_result = None
        category_match = None
        entities: List[Entity] = []
        profile_updates: List[ProfileUpdate] = []

        # Echo
        if self.echo_processor:
            try:
                echo_result = self.echo_processor.process(content, depth=depth)
            except Exception as e:
                logger.warning("Fallback echo failed: %s", e)

        # Category
        if self.category_processor:
            try:
                category_match = self.category_processor.detect_category(content)
            except Exception as e:
                logger.warning("Fallback category failed: %s", e)

        # Entities (regex only in fallback to avoid extra LLM call)
        if self.knowledge_graph:
            try:
                entities = self.knowledge_graph._extract_entities_regex(content, "")
            except Exception as e:
                logger.warning("Fallback entity extraction failed: %s", e)

        # Profiles (regex only in fallback)
        if self.profile_processor:
            try:
                profile_updates = self.profile_processor.extract_profile_mentions(content)
            except Exception as e:
                logger.warning("Fallback profile extraction failed: %s", e)

        return EnrichmentResult(
            echo_result=echo_result,
            category_match=category_match,
            entities=entities,
            profile_updates=profile_updates,
        )


# ---------------------------------------------------------------------------
# JSON parsing helpers
# ---------------------------------------------------------------------------

def _extract_json_blob(response: str) -> str:
    """Extract JSON object from LLM response, handling code fences, thinking tags, and noise."""
    text = (response or "").strip()
    if not text:
        return "{}"

    # Strip <think>...</think> blocks (Qwen 3.x thinking models)
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()

    # Also strip an unclosed <think> block (model hit max_tokens mid-thought)
    text = re.sub(r"<think>[\s\S]*$", "", text, flags=re.IGNORECASE).strip()

    if not text:
        return "{}"

    # Strip code fences
    fence_match = re.search(r"```\w*\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1).strip()

    # Find the first JSON object
    start = text.find("{")
    if start >= 0:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text, start)
            return json.dumps(obj)
        except json.JSONDecodeError:
            pass

    return text


def _robust_json_load(text: str) -> Dict[str, Any]:
    """Load JSON with repair for common LLM output issues."""
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # Repair: remove trailing commas, comments, template placeholders
    repaired = re.sub(r",(\s*[}\]])", r"\1", text)
    repaired = re.sub(r'\s*//[^\n]*', '', repaired)
    repaired = re.sub(r'"0\.0-1\.0"', '0.5', repaired)
    repaired = re.sub(r':\s*\["str"\]', ': []', repaired)
    repaired = re.sub(r':\s*"str"', ': ""', repaired)

    try:
        data = json.loads(repaired)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # Last resort: raw_decode
    start = repaired.find("{")
    if start >= 0:
        try:
            data, _ = json.JSONDecoder().raw_decode(repaired, start)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("Could not parse unified enrichment response", text, 0)


def _normalize_unified_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize common LLM key variations."""
    normalized = dict(data)

    # Ensure top-level keys exist
    if "echo" not in normalized:
        # Check if echo keys are at top level
        echo_keys = {"paraphrases", "keywords", "importance"}
        if echo_keys & set(normalized.keys()):
            normalized["echo"] = {
                k: normalized.pop(k)
                for k in list(normalized.keys())
                if k in {"paraphrases", "keywords", "implications", "questions",
                         "question_form", "category", "importance"}
            }
        else:
            normalized["echo"] = {}

    if "category" not in normalized:
        normalized["category"] = {}
    if "entities" not in normalized:
        normalized["entities"] = []
    if "profiles" not in normalized:
        normalized["profiles"] = []

    return normalized
