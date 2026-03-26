"""Tests for ProfileProcessor — extraction, updates, narrative, self-profile."""

import os
import tempfile
import uuid

import pytest

from dhee.core.profile import ProfileProcessor, ProfileUpdate, _SELF_PATTERNS, _PERSON_PATTERN
from dhee.db.sqlite import SQLiteManager


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    mgr = SQLiteManager(path)
    yield mgr
    os.unlink(path)


@pytest.fixture
def processor(db):
    return ProfileProcessor(
        db=db,
        embedder=None,
        llm=None,
        config={
            "auto_detect_profiles": True,
            "use_llm_extraction": False,  # No LLM in tests
            "narrative_regenerate_threshold": 10,
            "self_profile_auto_create": True,
            "max_facts_per_profile": 100,
        },
    )


class TestSelfPatterns:
    def test_i_prefer(self):
        assert any(p.search("I prefer dark mode") for p in _SELF_PATTERNS)

    def test_my_name(self):
        assert any(p.search("my name is John") for p in _SELF_PATTERNS)

    def test_im_a(self):
        assert any(p.search("I'm a software engineer") for p in _SELF_PATTERNS)

    def test_no_match(self):
        assert not any(p.search("The sky is blue") for p in _SELF_PATTERNS)


class TestPersonPattern:
    def test_full_name(self):
        matches = _PERSON_PATTERN.findall("John Smith works here")
        assert "John Smith" in matches

    def test_no_match(self):
        matches = _PERSON_PATTERN.findall("hello world")
        assert len(matches) == 0


class TestExtraction:
    def test_self_preference(self, processor):
        updates = processor.extract_profile_mentions(
            "I prefer using Python for data analysis",
            user_id="u1",
        )
        assert len(updates) >= 1
        self_update = next((u for u in updates if u.profile_type == "self"), None)
        assert self_update is not None
        assert len(self_update.new_preferences) > 0

    def test_person_mention(self, processor):
        updates = processor.extract_profile_mentions(
            "Had a meeting with John Smith about the project",
            user_id="u1",
        )
        person_updates = [u for u in updates if u.profile_type == "contact"]
        assert len(person_updates) >= 1
        assert any("John Smith" in u.profile_name for u in person_updates)

    def test_no_mentions(self, processor):
        updates = processor.extract_profile_mentions(
            "the sky is blue today",
            user_id="u1",
        )
        assert len(updates) == 0


class TestSelfProfile:
    def test_ensure_self_profile(self, processor, db):
        profile = processor.ensure_self_profile("u1")
        assert profile["name"] == "self"
        assert profile["profile_type"] == "self"

        # Second call returns same profile
        profile2 = processor.ensure_self_profile("u1")
        assert profile2["id"] == profile["id"]

    def test_auto_create_on_self_ref(self, processor, db):
        mem_id = str(uuid.uuid4())
        db.add_memory({"id": mem_id, "memory": "I prefer dark mode", "user_id": "u1"})

        updates = processor.extract_profile_mentions("I prefer dark mode", user_id="u1")
        for u in updates:
            processor.apply_update(u, mem_id, "u1")

        self_profile = db.get_profile_by_name("self", user_id="u1")
        assert self_profile is not None
        assert len(self_profile["preferences"]) > 0


class TestProfileLifecycle:
    def test_create_contact(self, processor, db):
        mem_id = str(uuid.uuid4())
        db.add_memory({"id": mem_id, "memory": "Met with Alice Johnson", "user_id": "u1"})

        update = ProfileUpdate(
            profile_name="Alice Johnson",
            profile_type="contact",
            new_facts=["Met for lunch"],
        )
        profile_id = processor.apply_update(update, mem_id, "u1")
        assert profile_id

        profile = db.get_profile(profile_id)
        assert profile["name"] == "Alice Johnson"
        assert "Met for lunch" in profile["facts"]

    def test_merge_facts(self, processor, db):
        mem1 = str(uuid.uuid4())
        mem2 = str(uuid.uuid4())
        db.add_memory({"id": mem1, "memory": "fact 1", "user_id": "u1"})
        db.add_memory({"id": mem2, "memory": "fact 2", "user_id": "u1"})

        update1 = ProfileUpdate(
            profile_name="Bob Wilson",
            profile_type="contact",
            new_facts=["Works at Google"],
        )
        pid = processor.apply_update(update1, mem1, "u1")

        update2 = ProfileUpdate(
            profile_name="Bob Wilson",
            profile_type="contact",
            new_facts=["Likes Python", "Works at Google"],  # duplicate
        )
        pid2 = processor.apply_update(update2, mem2, "u1")
        assert pid == pid2

        profile = db.get_profile(pid)
        assert "Works at Google" in profile["facts"]
        assert "Likes Python" in profile["facts"]
        # No duplicate
        assert profile["facts"].count("Works at Google") == 1

    def test_max_facts(self, processor, db):
        processor.max_facts = 3
        mem_id = str(uuid.uuid4())
        db.add_memory({"id": mem_id, "memory": "test", "user_id": "u1"})

        update = ProfileUpdate(
            profile_name="Test Person",
            profile_type="contact",
            new_facts=["fact1", "fact2", "fact3", "fact4", "fact5"],
        )
        pid = processor.apply_update(update, mem_id, "u1")
        profile = db.get_profile(pid)
        assert len(profile["facts"]) <= 3


class TestProfileSearch:
    def test_keyword_search(self, processor, db):
        db.add_profile({
            "id": str(uuid.uuid4()),
            "user_id": "u1",
            "name": "Alice Johnson",
            "profile_type": "contact",
            "facts": ["Software engineer", "Works at Google"],
        })

        results = processor.search_profiles("Alice", user_id="u1")
        assert len(results) >= 1
        assert any("Alice" in r["name"] for r in results)

    def test_no_results(self, processor, db):
        results = processor.search_profiles("nonexistent", user_id="u1")
        assert len(results) == 0


class TestProfileMemories:
    def test_link_memory(self, processor, db):
        mem_id = str(uuid.uuid4())
        db.add_memory({"id": mem_id, "memory": "About Alice", "user_id": "u1"})

        update = ProfileUpdate(
            profile_name="Alice Test",
            profile_type="contact",
            new_facts=["Test fact"],
        )
        pid = processor.apply_update(update, mem_id, "u1")

        linked = db.get_profile_memories(pid)
        assert len(linked) == 1
        assert linked[0]["id"] == mem_id
