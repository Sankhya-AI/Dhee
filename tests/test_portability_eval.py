"""M7.6 regression — portability eval scorecard.

Plan reference: encapsulated-rolling-bengio.md, Movement 7.6.

These tests lock in what `dhee portability-eval` actually measures. The
point isn't to replicate ``test_protocol_v1``'s raw round-trip (that's
covered there) — it's to exercise the *scorecard* contract so future
changes can't silently claim portability they don't deliver.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dhee import Dhee
from dhee.benchmarks.portability import (
    PortabilityScorecard,
    SubstrateDelta,
    run_portability_eval,
)
from dhee.core.artifacts import ArtifactManager


def _make_dhee(tmp_path: Path) -> Dhee:
    return Dhee(
        provider="mock",
        data_dir=tmp_path,
        user_id="default",
        in_memory=True,
        auto_context=False,
        auto_checkpoint=False,
    )


# ---------------------------------------------------------------------------
# Scorecard dataclass contract
# ---------------------------------------------------------------------------


class TestScorecardContract:
    def test_substrate_retention_math(self):
        d = SubstrateDelta(
            name="memories",
            source_count=10,
            imported_count=10,
            preserved_ids=9,
        )
        assert d.retention == pytest.approx(0.9)

    def test_empty_source_counts_as_perfect_retention(self):
        d = SubstrateDelta(name="memories")
        assert d.retention == 1.0

    def test_scorecard_to_dict_round_trip(self):
        s = PortabilityScorecard(user_id="default", pack_path="/tmp/x")
        s.substrates.append(SubstrateDelta(name="memories", source_count=1,
                                            imported_count=1,
                                            preserved_ids=1))
        d = s.to_dict()
        assert d["user_id"] == "default"
        assert d["substrates"][0]["name"] == "memories"
        assert d["substrates"][0]["retention"] == 1.0


# ---------------------------------------------------------------------------
# Full round-trip scorecard
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_clean_dhee_state_scores_passed(self, tmp_path):
        source = _make_dhee(tmp_path / "source")
        source.remember("User prefers concise architecture writeups.")
        source.remember("User strongly dislikes trailing whitespace.")

        paper = tmp_path / "paper.pdf"
        paper.write_bytes(b"%PDF-1.4 portable bytes")
        manager = ArtifactManager(
            source._engram.memory.db, engram=source._engram,
        )
        parsed = manager.capture_host_parse(
            path=str(paper),
            extracted_text="Portable archive content.",
            user_id="default",
            cwd=str(tmp_path),
            harness="claude_code",
            extraction_source="claude_read",
        )
        assert parsed is not None

        scorecard = run_portability_eval(
            source_db=source._engram.memory.db,
            source_vector_store=source._engram.memory.vector_store,
            user_id="default",
            key_dir=str(tmp_path / "keys"),
            output_dir=str(tmp_path / "pack"),
        )

        assert scorecard.passed is True
        assert scorecard.handoff_survived is True

        mem = next(s for s in scorecard.substrates if s.name == "memories")
        assert mem.source_count >= 2
        assert mem.preserved_ids == mem.source_count
        assert mem.retention == 1.0

        art = next(s for s in scorecard.substrates
                   if s.name == "artifacts")
        assert art.source_count == 1
        assert art.preserved_ids == 1

        # No substrate may leave the retention below the default
        # threshold without tripping the scorecard.
        assert all(s.retention >= 0.95 for s in scorecard.substrates)

    def test_empty_source_still_passes(self, tmp_path):
        source = _make_dhee(tmp_path / "source")
        scorecard = run_portability_eval(
            source_db=source._engram.memory.db,
            source_vector_store=source._engram.memory.vector_store,
            user_id="default",
            key_dir=str(tmp_path / "keys"),
            output_dir=str(tmp_path / "pack"),
        )
        # With zero memories every substrate has retention=1.0 by
        # definition. The handoff snapshot is always emitted by
        # export_pack (so handoff_survived=True).
        assert scorecard.passed is True
        assert scorecard.handoff_survived is True

    def test_intra_pack_content_hash_collisions_preserve_all_memories(
        self, tmp_path
    ):
        """Regression for M7.6b.

        Two distinct memories that happen to share a ``content_hash``
        must both survive a round-trip. Earlier the content-hash dedup
        on the target side collapsed the second one because the first
        had just been imported from the same pack.
        """
        source = _make_dhee(tmp_path / "source")
        db = source._engram.memory.db
        # Hand-craft two memories with identical content_hash but
        # distinct IDs — the shape export_pack can legitimately emit
        # when two captures produced byte-identical content.
        shared_hash = "sha256:duplicate"
        db.add_memory({
            "id": "mem-a",
            "memory": "User dislikes ambiguity.",
            "user_id": "default",
            "content_hash": shared_hash,
        })
        db.add_memory({
            "id": "mem-b",
            "memory": "User dislikes ambiguity.",  # same content, different moment
            "user_id": "default",
            "content_hash": shared_hash,
        })

        scorecard = run_portability_eval(
            source_db=db,
            source_vector_store=source._engram.memory.vector_store,
            user_id="default",
            key_dir=str(tmp_path / "keys"),
            output_dir=str(tmp_path / "pack"),
        )
        mem = next(s for s in scorecard.substrates if s.name == "memories")
        assert mem.source_count == 2
        assert mem.preserved_ids == 2
        assert mem.retention == 1.0
        assert scorecard.passed is True

    def test_lowering_threshold_still_records_deltas(self, tmp_path):
        # Even when we explicitly set an unreachable threshold, the
        # scorecard should report retention numbers honestly.
        source = _make_dhee(tmp_path / "source")
        source.remember("Memory 1")
        scorecard = run_portability_eval(
            source_db=source._engram.memory.db,
            source_vector_store=source._engram.memory.vector_store,
            user_id="default",
            key_dir=str(tmp_path / "keys"),
            output_dir=str(tmp_path / "pack"),
            retention_threshold=1.1,  # impossible to pass
        )
        # Every substrate round-tripped, but threshold was impossible.
        assert scorecard.passed is False
        assert any("worst substrate" in note for note in scorecard.notes)
