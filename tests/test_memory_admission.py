from __future__ import annotations

from datetime import date

from dhee import Dhee, Engram, evaluate_memory_candidate


def _screen_metadata(**overrides):
    metadata = {
        "source": "chotu_screen_memory",
        "type": "screen_activity",
        "source_app": "chotu",
        "confidence": 0.91,
        "retention_policy": "durable",
        "evidence": {
            "kind": "screen_context",
            "app": "Google Chrome",
            "bundle_id": "com.google.Chrome",
            "title": "YouTube - DreamerV3 world models tutorial",
            "dwell_seconds": 95,
        },
    }
    evidence_overrides = overrides.pop("evidence", None)
    if evidence_overrides:
        metadata["evidence"].update(evidence_overrides)
    metadata.update(overrides)
    return metadata


def test_admission_rejects_noisy_passive_screen_observation():
    content = "\n".join(
        [
            "Chotu observed visible screen activity.",
            "App: Codex",
            "Title: Codex",
            "Visible text: iIDJiaiiru /MVVLlkllLIVII)/knIIVLU&PVV VIVV< IfVUI Illf LUI IfLL alValfU",
        ]
    )
    metadata = _screen_metadata(
        evidence={
            "app": "Codex",
            "bundle_id": "com.openai.codex",
            "title": "Codex",
            "dwell_seconds": 0,
        }
    )

    decision = evaluate_memory_candidate(content, metadata)

    assert decision.applies is True
    assert decision.should_store is False
    assert decision.retention_policy == "ephemeral"
    assert decision.skip_reason in {"low_quality_signal", "low_ocr_quality"}


def test_admission_marks_useful_long_dwell_screen_memory_durable():
    content = "\n".join(
        [
            "Chotu observed visible screen activity.",
            "App: Google Chrome",
            "Title: YouTube - DreamerV3 world models tutorial",
            "Visible text: The user is watching a tutorial about DreamerV3, reinforcement learning, world models, and model based agents.",
        ]
    )

    decision = evaluate_memory_candidate(content, _screen_metadata())

    assert decision.applies is True
    assert decision.should_store is True
    assert decision.retention_policy == "durable"
    assert "interest_signal" in decision.reasons


def test_dhee_remember_returns_not_stored_for_rejected_passive_observation(tmp_path):
    dhee = Dhee(provider="mock", in_memory=True, data_dir=str(tmp_path))
    result = dhee.remember(
        "Chotu observed visible screen activity.\nApp: Codex\nTitle: Codex",
        metadata=_screen_metadata(
            evidence={
                "app": "Codex",
                "bundle_id": "com.openai.codex",
                "title": "Codex",
                "dwell_seconds": 0,
            }
        ),
    )

    assert result["stored"] is False
    assert result["event"] == "SKIP"
    assert result["admission"]["should_store"] is False
    assert dhee.recall("Codex") == []


def test_engram_add_applies_admission_metadata_and_session_expiration(tmp_path):
    memory = Engram(provider="mock", in_memory=True, data_dir=str(tmp_path))
    result = memory.add(
        "Chotu observed visible screen activity.\n"
        "App: Google Chrome\n"
        "Title: Google Search - learn trading\n"
        "Visible text: Learning to trade effectively requires mastering market mechanics and risk management.",
        user_id="default",
        agent_id="chotu",
        source_app="chotu",
        metadata=_screen_metadata(
            evidence={
                "title": "Google Search - learn trading",
                "dwell_seconds": 35,
            }
        ),
        infer=False,
    )

    stored = result["results"][0]
    memory_id = stored["id"]
    loaded = memory.get(memory_id)

    assert stored["event"] == "ADD"
    assert stored["admission"]["should_store"] is True
    assert loaded["agent_id"] == "chotu"
    assert loaded["source_app"] == "chotu"
    assert loaded["metadata"]["dhee_admission"]["retention_policy"] == "session"
    assert loaded["metadata"]["retention_policy"] == "session"
    assert loaded["metadata"]["dhee_lite_path"] is True
    assert loaded["metadata"]["enrichment_status"] == "pending"
    assert stored["echo_depth"] is None
    assert loaded["expiration_date"] is not None
    assert date.fromisoformat(loaded["expiration_date"]) >= date.today()


def test_admission_strips_raw_ocr_when_vision_summary_is_available(tmp_path):
    memory = Engram(provider="mock", in_memory=True, data_dir=str(tmp_path))
    result = memory.add(
        "Chotu observed useful visible screen activity.\n"
        "App: Google Chrome\n"
        "Title: Home / X\n"
        "Visible text excerpt:\n"
        "X Hcth-iX x.comlhome ForN 8uild in Ptsblit Q Search poweror VUVAurve\n"
        "Visual summary:\n"
        "User is browsing the X home feed in Google Chrome and viewing posts about India's offshore exploration mission.",
        user_id="default",
        agent_id="chotu",
        source_app="chotu",
        metadata=_screen_metadata(
            evidence={
                "title": "Home / X",
                "dwell_seconds": 45,
                "vision_summary_sha256": "abc123",
            }
        ),
        infer=False,
    )

    loaded = memory.get(result["results"][0]["id"])

    assert "Visual summary:" in loaded["memory"]
    assert "Visible text excerpt:" not in loaded["memory"]
    assert "Hcth-iX" not in loaded["memory"]
    assert loaded["metadata"]["dhee_admission"]["include_ocr_excerpt"] is False
    assert loaded["metadata"]["dhee_lite_path"] is True


def test_dhee_sweep_admission_flags_and_deletes_legacy_noise(tmp_path):
    dhee = Dhee(provider="mock", in_memory=True, data_dir=str(tmp_path))
    memories = [
        {
            "id": "legacy-noise",
            "memory": "Chotu observed visible screen activity.\n"
            "App: Codex\n"
            "Title: Codex\n"
            "Visible text: iIDJiaiiru /MVVLlkllLIVII)/knIIVLU&PVV",
            "metadata": _screen_metadata(
                evidence={
                    "app": "Codex",
                    "bundle_id": "com.openai.codex",
                    "title": "Codex",
                    "dwell_seconds": 0,
                }
            ),
        },
        {
            "id": "useful",
            "memory": "User prefers Python for backend services.",
            "metadata": {"source": "manual_note"},
        },
    ]
    deleted = []
    dhee._engram.get_all = lambda **_: memories
    dhee._engram.delete = lambda memory_id: deleted.append(memory_id)

    dry_run = dhee.sweep_admission(dry_run=True)
    applied = dhee.sweep_admission(dry_run=False)

    assert dry_run["candidate_count"] == 1
    assert dry_run["deleted_count"] == 0
    assert dry_run["candidates"][0]["id"] == "legacy-noise"
    assert applied["deleted_count"] == 1
    assert deleted == ["legacy-noise"]
