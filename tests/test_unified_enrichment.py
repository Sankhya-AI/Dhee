"""Tests for unified enrichment processor."""

import json
import pytest
from unittest.mock import MagicMock, patch

from engram.core.category import CategoryMatch, CategoryProcessor
from engram.core.echo import EchoDepth, EchoProcessor, EchoResult
from engram.core.enrichment import (
    EnrichmentResult,
    UnifiedCategoryOutput,
    UnifiedEchoOutput,
    UnifiedEnrichmentOutput,
    UnifiedEnrichmentProcessor,
    UnifiedEntityOutput,
    UnifiedProfileOutput,
    _extract_json_blob,
    _normalize_unified_dict,
    _robust_json_load,
)
from engram.core.graph import Entity, EntityType, KnowledgeGraph
from engram.core.profile import ProfileProcessor, ProfileUpdate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_UNIFIED_RESPONSE = json.dumps({
    "echo": {
        "paraphrases": ["I enjoy coding in Python", "Python is my preferred language"],
        "keywords": ["Python", "programming", "preference"],
        "implications": ["User is likely a developer"],
        "questions": ["What programming language does the user prefer?"],
        "question_form": "What is the user's preferred programming language?",
        "category": "preference",
        "importance": 0.8,
    },
    "category": {
        "action": "use_existing",
        "category_id": "preferences",
        "new_category": None,
        "confidence": 0.9,
    },
    "entities": [
        {"name": "Python", "type": "technology"},
    ],
    "profiles": [
        {"name": "self", "type": "self", "facts": [], "preferences": ["Python programming"]},
    ],
})

VALID_BATCH_RESPONSE = json.dumps({
    "results": [
        {
            "index": 0,
            "echo": {
                "paraphrases": ["User likes Python"],
                "keywords": ["Python"],
                "implications": [],
                "questions": [],
                "question_form": None,
                "category": "preference",
                "importance": 0.7,
            },
            "category": {"action": "use_existing", "category_id": "preferences", "confidence": 0.8},
            "entities": [{"name": "Python", "type": "technology"}],
            "profiles": [],
        },
        {
            "index": 1,
            "echo": {
                "paraphrases": ["User works at Acme"],
                "keywords": ["Acme", "work"],
                "implications": [],
                "questions": [],
                "question_form": None,
                "category": "fact",
                "importance": 0.6,
            },
            "category": {"action": "use_existing", "category_id": "facts", "confidence": 0.7},
            "entities": [{"name": "Acme", "type": "organization"}],
            "profiles": [{"name": "self", "type": "self", "facts": ["Works at Acme"], "preferences": []}],
        },
    ]
})


def _make_mock_llm(response=VALID_UNIFIED_RESPONSE):
    llm = MagicMock()
    llm.generate.return_value = response
    return llm


def _make_processor(llm=None, echo=True, category=True, graph=True, profile=False):
    llm = llm or _make_mock_llm()
    echo_proc = EchoProcessor(llm) if echo else None
    cat_proc = None
    if category:
        embedder = MagicMock()
        embedder.embed.return_value = [0.1] * 384
        cat_proc = CategoryProcessor(llm=llm, embedder=embedder)
    kg = KnowledgeGraph() if graph else None
    pp = None
    if profile:
        db = MagicMock()
        pp = ProfileProcessor(db=db, llm=llm)
    return UnifiedEnrichmentProcessor(
        llm=llm,
        echo_processor=echo_proc,
        category_processor=cat_proc,
        knowledge_graph=kg,
        profile_processor=pp,
    )


# ---------------------------------------------------------------------------
# Pydantic model tests
# ---------------------------------------------------------------------------

class TestPydanticModels:
    def test_echo_output_defaults(self):
        echo = UnifiedEchoOutput()
        assert echo.paraphrases == []
        assert echo.keywords == []
        assert echo.importance == 0.5

    def test_echo_output_coerces_lists(self):
        echo = UnifiedEchoOutput(paraphrases=None, keywords="single")
        assert echo.paraphrases == []
        assert echo.keywords == ["single"]

    def test_echo_output_coerces_importance(self):
        echo = UnifiedEchoOutput(importance="0.7")
        assert echo.importance == 0.7

    def test_category_output_defaults(self):
        cat = UnifiedCategoryOutput()
        assert cat.action == "use_existing"
        assert cat.confidence == 0.5

    def test_entity_output(self):
        ent = UnifiedEntityOutput(name="Python", type="technology")
        assert ent.name == "Python"
        assert ent.type == "technology"

    def test_profile_output_coerces_lists(self):
        prof = UnifiedProfileOutput(name="self", facts=None, preferences="coding")
        assert prof.facts == []
        assert prof.preferences == ["coding"]

    def test_full_output_parsing(self):
        data = json.loads(VALID_UNIFIED_RESPONSE)
        output = UnifiedEnrichmentOutput.model_validate(data)
        assert len(output.echo.paraphrases) == 2
        assert output.category.category_id == "preferences"
        assert len(output.entities) == 1
        assert output.entities[0].name == "Python"
        assert len(output.profiles) == 1

    def test_output_extra_fields_ignored(self):
        data = {"echo": {"extra_field": True, "paraphrases": ["a"], "keywords": ["b"], "importance": 0.5},
                "category": {}, "entities": [], "profiles": []}
        output = UnifiedEnrichmentOutput.model_validate(data)
        assert output.echo.paraphrases == ["a"]


# ---------------------------------------------------------------------------
# JSON parsing tests
# ---------------------------------------------------------------------------

class TestJsonParsing:
    def test_extract_json_blob_clean(self):
        blob = _extract_json_blob('{"key": "value"}')
        data = json.loads(blob)
        assert data["key"] == "value"

    def test_extract_json_blob_code_fence(self):
        response = '```json\n{"key": "value"}\n```'
        blob = _extract_json_blob(response)
        data = json.loads(blob)
        assert data["key"] == "value"

    def test_extract_json_blob_with_preamble(self):
        response = 'Here is the result:\n{"key": "value"}'
        blob = _extract_json_blob(response)
        data = json.loads(blob)
        assert data["key"] == "value"

    def test_robust_json_load_trailing_comma(self):
        text = '{"key": "value", "list": [1, 2, 3,],}'
        data = _robust_json_load(text)
        assert data["key"] == "value"

    def test_robust_json_load_comments(self):
        text = '{"key": "value" // this is a comment\n}'
        data = _robust_json_load(text)
        assert data["key"] == "value"

    def test_normalize_echo_at_top_level(self):
        data = {"paraphrases": ["a"], "keywords": ["b"], "importance": 0.5, "category": {}}
        normalized = _normalize_unified_dict(data)
        assert "echo" in normalized
        assert normalized["echo"]["paraphrases"] == ["a"]


# ---------------------------------------------------------------------------
# Converter tests
# ---------------------------------------------------------------------------

class TestConverters:
    def test_to_echo_result_medium(self):
        proc = _make_processor()
        echo_out = UnifiedEchoOutput(
            paraphrases=["rewrite"],
            keywords=["key"],
            implications=["impl"],
            questions=["q?"],
            question_form="What?",
            category="fact",
            importance=0.7,
        )
        result = proc._to_echo_result(echo_out, "original", EchoDepth.MEDIUM)
        assert isinstance(result, EchoResult)
        assert result.echo_depth == EchoDepth.MEDIUM
        assert result.paraphrases == ["rewrite"]
        assert result.implications == []  # medium skips implications
        assert result.questions == []  # medium skips questions
        assert result.question_form == "What?"
        assert result.strength_multiplier == EchoProcessor.STRENGTH_MULTIPLIERS[EchoDepth.MEDIUM]

    def test_to_echo_result_deep(self):
        proc = _make_processor()
        echo_out = UnifiedEchoOutput(
            paraphrases=["rewrite"],
            keywords=["key"],
            implications=["impl"],
            questions=["q?"],
            importance=0.7,
        )
        result = proc._to_echo_result(echo_out, "original", EchoDepth.DEEP)
        assert result.implications == ["impl"]
        assert result.questions == ["q?"]
        assert result.strength_multiplier == EchoProcessor.STRENGTH_MULTIPLIERS[EchoDepth.DEEP]

    def test_to_echo_result_question_form_from_questions(self):
        proc = _make_processor()
        echo_out = UnifiedEchoOutput(
            paraphrases=["rewrite"],
            keywords=["key"],
            questions=["What is X?"],
            importance=0.5,
        )
        result = proc._to_echo_result(echo_out, "original", EchoDepth.DEEP)
        assert result.question_form == "What is X?"

    def test_to_category_match_use_existing(self):
        proc = _make_processor()
        cat_out = UnifiedCategoryOutput(
            action="use_existing",
            category_id="preferences",
            confidence=0.9,
        )
        match = proc._to_category_match(cat_out)
        assert isinstance(match, CategoryMatch)
        assert match.category_id == "preferences"
        assert match.confidence == 0.9
        assert not match.is_new

    def test_to_category_match_create_new(self):
        proc = _make_processor()
        cat_out = UnifiedCategoryOutput(
            action="create_new",
            new_category={"name": "Hobbies", "description": "User hobbies", "keywords": ["hobby"]},
            confidence=0.7,
        )
        match = proc._to_category_match(cat_out)
        assert isinstance(match, CategoryMatch)
        assert match.is_new
        assert match.confidence == 0.7

    def test_to_category_match_fallback(self):
        proc = _make_processor()
        cat_out = UnifiedCategoryOutput()  # defaults
        match = proc._to_category_match(cat_out)
        assert match.category_id == "context"

    def test_to_entities(self):
        proc = _make_processor()
        entity_outs = [
            UnifiedEntityOutput(name="Python", type="technology"),
            UnifiedEntityOutput(name="Alice", type="person"),
            UnifiedEntityOutput(name="", type="unknown"),  # should be filtered
        ]
        entities = proc._to_entities(entity_outs)
        assert len(entities) == 2
        assert entities[0].name == "Python"
        assert entities[0].entity_type == EntityType.TECHNOLOGY
        assert entities[1].entity_type == EntityType.PERSON

    def test_to_entities_invalid_type(self):
        proc = _make_processor()
        entity_outs = [UnifiedEntityOutput(name="Foo", type="invalid_type")]
        entities = proc._to_entities(entity_outs)
        assert len(entities) == 1
        assert entities[0].entity_type == EntityType.UNKNOWN

    def test_to_profile_updates(self):
        proc = _make_processor()
        profile_outs = [
            UnifiedProfileOutput(name="self", type="self", facts=["Name: Alice"], preferences=["Python"]),
            UnifiedProfileOutput(name="", type="contact"),  # should be filtered
        ]
        updates = proc._to_profile_updates(profile_outs)
        assert len(updates) == 1
        assert isinstance(updates[0], ProfileUpdate)
        assert updates[0].profile_name == "self"
        assert updates[0].new_facts == ["Name: Alice"]
        assert updates[0].new_preferences == ["Python"]


# ---------------------------------------------------------------------------
# Enrichment flow tests
# ---------------------------------------------------------------------------

class TestEnrichFlow:
    def test_enrich_single_valid(self):
        proc = _make_processor()
        result = proc.enrich("I prefer Python for backend development", EchoDepth.MEDIUM)
        assert isinstance(result, EnrichmentResult)
        assert result.echo_result is not None
        assert result.echo_result.echo_depth == EchoDepth.MEDIUM
        assert result.category_match is not None
        assert len(result.entities) >= 1
        assert result.raw_response == VALID_UNIFIED_RESPONSE

    def test_enrich_fallback_on_invalid_json(self):
        llm = _make_mock_llm("this is not json at all!!!")
        proc = _make_processor(llm=llm)
        # Should fall back to individual processors
        result = proc.enrich("I prefer Python", EchoDepth.MEDIUM)
        assert isinstance(result, EnrichmentResult)
        # Fallback calls echo_processor.process which also calls the broken LLM,
        # but EchoProcessor has its own fallback to shallow
        # The important thing is no exception is raised

    def test_enrich_with_code_fenced_response(self):
        response = f"```json\n{VALID_UNIFIED_RESPONSE}\n```"
        llm = _make_mock_llm(response)
        proc = _make_processor(llm=llm)
        result = proc.enrich("I prefer Python", EchoDepth.MEDIUM)
        assert result.echo_result is not None
        assert result.category_match is not None


class TestEnrichBatch:
    def test_batch_single_item(self):
        proc = _make_processor()
        results = proc.enrich_batch(["I prefer Python"], EchoDepth.MEDIUM)
        assert len(results) == 1
        assert results[0].echo_result is not None

    def test_batch_multiple_items(self):
        llm = _make_mock_llm(VALID_BATCH_RESPONSE)
        proc = _make_processor(llm=llm)
        results = proc.enrich_batch(
            ["I prefer Python", "I work at Acme"],
            EchoDepth.MEDIUM,
        )
        assert len(results) == 2
        assert results[0].echo_result is not None
        assert results[1].echo_result is not None
        assert results[0].entities[0].name == "Python"
        assert results[1].entities[0].name == "Acme"

    def test_batch_empty(self):
        proc = _make_processor()
        results = proc.enrich_batch([], EchoDepth.MEDIUM)
        assert results == []

    def test_batch_partial_failure(self):
        """If one item fails in batch, it falls back to individual."""
        response = json.dumps({
            "results": [
                {
                    "index": 0,
                    "echo": {"paraphrases": ["ok"], "keywords": ["ok"], "importance": 0.5},
                    "category": {"action": "use_existing", "category_id": "facts", "confidence": 0.5},
                    "entities": [],
                    "profiles": [],
                },
                # Index 1 is missing — should trigger fallback for that item
            ]
        })
        llm = MagicMock()
        # First call returns batch response, subsequent calls return single response
        llm.generate.side_effect = [response, VALID_UNIFIED_RESPONSE]
        proc = _make_processor(llm=llm)
        results = proc.enrich_batch(["Memory 1", "Memory 2"], EchoDepth.MEDIUM)
        assert len(results) == 2


# ---------------------------------------------------------------------------
# Fallback tests
# ---------------------------------------------------------------------------

class TestFallback:
    def test_fallback_calls_individual_processors(self):
        llm = _make_mock_llm()
        proc = _make_processor(llm=llm, echo=True, category=True, graph=True)
        # Force JSON parse failure by passing garbage
        result = proc._fallback_individual("I prefer Python", EchoDepth.MEDIUM, None)
        assert isinstance(result, EnrichmentResult)
        # Echo result should come from individual processor
        # (it will call llm.generate which returns our valid response,
        # but that's the echo prompt format not unified — it may or may not parse,
        # the important thing is no exception bubbles up)


# ---------------------------------------------------------------------------
# Config toggle tests
# ---------------------------------------------------------------------------

class TestConfigToggle:
    def test_enrichment_config_defaults(self):
        from engram.configs.base import EnrichmentConfig
        config = EnrichmentConfig()
        assert config.enable_unified is False
        assert config.fallback_to_individual is True
        assert config.include_entities is True
        assert config.include_profiles is True
        assert config.max_batch_size == 10

    def test_memory_config_has_enrichment(self):
        from engram.configs.base import MemoryConfig
        config = MemoryConfig()
        assert hasattr(config, "enrichment")
        assert config.enrichment.enable_unified is False

    def test_full_preset_enables_unified(self):
        from engram.configs.base import MemoryConfig
        config = MemoryConfig.full()
        assert config.enrichment.enable_unified is True

    def test_minimal_preset_unified_disabled(self):
        from engram.configs.base import MemoryConfig
        config = MemoryConfig.minimal()
        assert config.enrichment.enable_unified is False


# ---------------------------------------------------------------------------
# Prompt generation tests
# ---------------------------------------------------------------------------

class TestPromptGeneration:
    def test_single_prompt_contains_content(self):
        proc = _make_processor()
        prompt = proc._build_prompt("I love Python", EchoDepth.MEDIUM)
        assert "I love Python" in prompt
        assert "MEMORY:" in prompt
        assert "ECHO DEPTH: medium" in prompt

    def test_single_prompt_with_categories(self):
        proc = _make_processor()
        prompt = proc._build_prompt("test", EchoDepth.MEDIUM, "- preferences: User Preferences")
        assert "preferences: User Preferences" in prompt

    def test_batch_prompt_contains_all_memories(self):
        proc = _make_processor()
        prompt = proc._build_batch_prompt(
            ["Memory A", "Memory B", "Memory C"],
            EchoDepth.DEEP,
        )
        assert "[0] Memory A" in prompt
        assert "[1] Memory B" in prompt
        assert "[2] Memory C" in prompt
        assert "ECHO DEPTH: deep" in prompt

    def test_prompt_entity_toggle(self):
        proc = _make_processor()
        prompt_yes = proc._build_prompt("test", EchoDepth.MEDIUM, include_entities=True)
        prompt_no = proc._build_prompt("test", EchoDepth.MEDIUM, include_entities=False)
        assert "INCLUDE ENTITIES: yes" in prompt_yes
        assert "INCLUDE ENTITIES: no" in prompt_no


# ---------------------------------------------------------------------------
# Integration: EnrichmentResult metadata schema
# ---------------------------------------------------------------------------

class TestEnrichmentResultSchema:
    def test_echo_result_to_metadata(self):
        """Unified echo result produces same metadata keys as EchoProcessor."""
        proc = _make_processor()
        result = proc.enrich("I prefer Python", EchoDepth.MEDIUM)
        assert result.echo_result is not None
        metadata = result.echo_result.to_metadata()
        expected_keys = {
            "echo_paraphrases", "echo_keywords", "echo_implications",
            "echo_questions", "echo_question_form", "echo_category",
            "echo_importance", "echo_depth",
        }
        assert set(metadata.keys()) == expected_keys
        assert metadata["echo_depth"] == "medium"
