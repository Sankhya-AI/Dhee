from __future__ import annotations

from dhee.router.agent_digest import digest_agent


def test_localization_digest_extracts_locations_and_confidence():
    text = """
- dhee/context_state.py:120-140 handles receipts
- evidence: duplicate reads should expand rollover receipts first
confidence: high
"""

    digest = digest_agent(text, kind="LocalizationDigest")
    rendered = digest.render("A-local")

    assert digest.schema == "LocalizationDigest"
    assert digest.kind == "LocalizationDigest"
    assert digest.typed["locations"][0]["path"] == "dhee/context_state.py"
    assert digest.typed["locations"][0]["range"] == "120-140"
    assert "schema=LocalizationDigest" in rendered
    assert "confidence=high" in rendered


def test_bug_repro_digest_extracts_contract_fields():
    text = """
command: pytest tests/test_context_state.py
observed: KeyError while reading rollover_receipts
expected: reread is suppressed with receipt guidance
minimal repro: call rollover then read the same file hash
confidence: medium
"""

    digest = digest_agent(text, kind="BugReproDigest")

    assert digest.schema == "BugReproDigest"
    assert digest.typed["command"] == "pytest tests/test_context_state.py"
    assert digest.typed["observed"] == ["KeyError while reading rollover_receipts"]
    assert digest.typed["expected"] == ["reread is suppressed with receipt guidance"]
    assert digest.typed["confidence"] == "medium"


def test_read_digest_keeps_excerpts_and_skipped_sections():
    text = """
- relevant: dhee/router/handlers.py:700 calls digest_agent
skipped: unrelated CLI command table
confidence: 0.7
"""

    digest = digest_agent(text, kind="ReadDigest")

    assert digest.schema == "ReadDigest"
    assert "dhee/router/handlers.py:700" in digest.typed["file_refs"]
    assert digest.typed["skipped_sections"] == ["unrelated CLI command table"]


def test_search_digest_ranks_hits():
    text = """
1. dhee/context_state.py:586 record_admission writes receipts
2. dhee/router/quality_report.py:74 replay section
confidence: low
"""

    digest = digest_agent(text, kind="SearchDigest")
    rendered = digest.render("A-search")

    assert digest.schema == "SearchDigest"
    assert digest.typed["ranked_hits"][0]["ref"] == "dhee/context_state.py:586"
    assert "ranked_hits:" in rendered


def test_agent_digest_generic_fallback_preserves_legacy_kind():
    digest = digest_agent("plain text without file references")

    assert digest.schema == "GenericDigest"
    assert digest.kind == "prose"
