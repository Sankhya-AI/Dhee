"""Tests for the Structural Intelligence layer.

Covers: Slot, StructuredStep, SkillStructure, heuristic extraction,
structural similarity, gap analysis, integration with Skill schema,
and structural_signature_hash.
"""

import pytest

from dhee.skills.structure import (
    GapReport,
    Slot,
    SkillStructure,
    StructuredStep,
    _lcs_length,
    _normalize_template,
    analyze_gaps,
    extract_slots_heuristic,
    structural_similarity,
)
from dhee.skills.hashing import structural_signature_hash
from dhee.skills.schema import Skill


# ── Slot tests ──


class TestSlot:
    def test_roundtrip_serialization(self):
        slot = Slot(
            name="language",
            description="Programming language",
            slot_type="string",
            default="python",
            examples=["python", "go"],
            required=True,
        )
        d = slot.to_dict()
        restored = Slot.from_dict(d)
        assert restored.name == "language"
        assert restored.description == "Programming language"
        assert restored.slot_type == "string"
        assert restored.default == "python"
        assert restored.examples == ["python", "go"]
        assert restored.required is True

    def test_from_dict_defaults(self):
        slot = Slot.from_dict({"name": "x"})
        assert slot.slot_type == "string"
        assert slot.default is None
        assert slot.examples == []
        assert slot.required is True


# ── StructuredStep tests ──


class TestStructuredStep:
    def test_render_with_bindings(self):
        step = StructuredStep(
            template="Build {language} app",
            slot_refs=["language"],
        )
        assert step.render({"language": "Go"}) == "Build Go app"

    def test_render_missing_slots_passthrough(self):
        step = StructuredStep(
            template="Run {test_framework} suite",
            slot_refs=["test_framework"],
        )
        result = step.render({})
        assert result == "Run {test_framework} suite"

    def test_render_multiple_slots(self):
        step = StructuredStep(
            template="Deploy {language} to {deploy_target}",
            slot_refs=["language", "deploy_target"],
        )
        result = step.render({"language": "Rust", "deploy_target": "AWS"})
        assert result == "Deploy Rust to AWS"

    def test_roundtrip_serialization(self):
        step = StructuredStep(
            template="Run {test_framework}",
            role="variable",
            slot_refs=["test_framework"],
            confidence=0.8,
            success_count=5,
            fail_count=1,
            order_index=2,
        )
        d = step.to_dict()
        restored = StructuredStep.from_dict(d)
        assert restored.template == step.template
        assert restored.role == "variable"
        assert restored.confidence == 0.8
        assert restored.success_count == 5
        assert restored.fail_count == 1
        assert restored.order_index == 2


# ── SkillStructure tests ──


class TestSkillStructure:
    def test_render_steps(self):
        structure = SkillStructure(
            slots=[Slot(name="language"), Slot(name="deploy_target")],
            structured_steps=[
                StructuredStep(template="Build {language} app", slot_refs=["language"]),
                StructuredStep(template="Deploy to {deploy_target}", slot_refs=["deploy_target"]),
            ],
        )
        rendered = structure.render_steps({"language": "Go", "deploy_target": "GCP"})
        assert rendered == ["Build Go app", "Deploy to GCP"]

    def test_compute_structural_signature_deterministic(self):
        structure = SkillStructure(
            slots=[Slot(name="language"), Slot(name="test_framework")],
            structured_steps=[
                StructuredStep(template="Build {language} app", role="variable"),
                StructuredStep(template="Run {test_framework}", role="variable"),
            ],
        )
        sig1 = structure.compute_structural_signature()
        sig2 = structure.compute_structural_signature()
        assert sig1 == sig2
        assert len(sig1) == 64  # SHA-256 hex

    def test_roundtrip_serialization(self):
        structure = SkillStructure(
            slots=[Slot(name="language", examples=["python"])],
            structured_steps=[
                StructuredStep(template="Build {language} app", role="variable", slot_refs=["language"]),
                StructuredStep(template="Review PR", role="structural"),
            ],
            known_bindings={"language": ["python", "go"]},
            context_bindings={"prod": {"language": "go"}},
        )
        structure.compute_structural_signature()
        d = structure.to_dict()
        restored = SkillStructure.from_dict(d)
        assert len(restored.slots) == 1
        assert len(restored.structured_steps) == 2
        assert restored.known_bindings["language"] == ["python", "go"]
        assert restored.context_bindings["prod"] == {"language": "go"}
        assert restored.structural_signature == structure.structural_signature

    def test_from_dict_empty(self):
        s = SkillStructure.from_dict({})
        assert s.slots == []
        assert s.structured_steps == []

    def test_from_dict_none(self):
        s = SkillStructure.from_dict(None)
        assert s.slots == []


# ── Heuristic extraction tests ──


class TestExtractSlotsHeuristic:
    def test_detects_language(self):
        slots, steps = extract_slots_heuristic(["Build Python app"])
        slot_names = [s.name for s in slots]
        assert "language" in slot_names
        assert "{language}" in steps[0].template
        assert steps[0].role == "variable"

    def test_detects_test_framework(self):
        slots, steps = extract_slots_heuristic(["Run pytest"])
        slot_names = [s.name for s in slots]
        assert "test_framework" in slot_names

    def test_detects_deploy_target(self):
        slots, steps = extract_slots_heuristic(["Deploy to AWS"])
        slot_names = [s.name for s in slots]
        assert "deploy_target" in slot_names

    def test_detects_package_manager(self):
        slots, steps = extract_slots_heuristic(["Install with pip"])
        slot_names = [s.name for s in slots]
        assert "package_manager" in slot_names

    def test_no_false_positives_generic_step(self):
        slots, steps = extract_slots_heuristic(["Review the pull request"])
        assert len(slots) == 0
        assert steps[0].role == "structural"

    def test_structural_role_for_generic_steps(self):
        slots, steps = extract_slots_heuristic([
            "Review code changes",
            "Write documentation",
        ])
        for step in steps:
            assert step.role == "structural"

    def test_multiple_slots_in_step(self):
        slots, steps = extract_slots_heuristic(["Build Python app and deploy to AWS"])
        slot_names = [s.name for s in slots]
        assert "language" in slot_names
        assert "deploy_target" in slot_names
        assert len(steps[0].slot_refs) == 2

    def test_examples_populated(self):
        slots, _ = extract_slots_heuristic(["Run pytest", "Run jest"])
        tf_slot = next(s for s in slots if s.name == "test_framework")
        assert "pytest" in tf_slot.examples
        assert "jest" in tf_slot.examples

    def test_order_index(self):
        _, steps = extract_slots_heuristic(["Step A", "Step B", "Step C"])
        for i, step in enumerate(steps):
            assert step.order_index == i


# ── Structural similarity tests ──


class TestStructuralSimilarity:
    def test_identical_steps(self):
        steps = [
            StructuredStep(template="Build {language} app"),
            StructuredStep(template="Run {test_framework}"),
            StructuredStep(template="Deploy to {deploy_target}"),
        ]
        assert structural_similarity(steps, steps) == 1.0

    def test_completely_different(self):
        a = [StructuredStep(template="Build {language} app")]
        b = [StructuredStep(template="Write documentation")]
        sim = structural_similarity(a, b)
        assert sim < 0.5

    def test_same_structure_different_slot_names(self):
        a = [
            StructuredStep(template="Build {language} app"),
            StructuredStep(template="Run {test_framework}"),
        ]
        b = [
            StructuredStep(template="Build {lang} app"),
            StructuredStep(template="Run {testing_tool}"),
        ]
        sim = structural_similarity(a, b)
        assert sim == 1.0  # {SLOT} normalization makes them identical

    def test_empty_steps(self):
        assert structural_similarity([], []) == 1.0
        assert structural_similarity([], [StructuredStep(template="x")]) == 0.0

    def test_partial_overlap(self):
        a = [
            StructuredStep(template="Build {SLOT} app"),
            StructuredStep(template="Run tests"),
            StructuredStep(template="Deploy to {SLOT}"),
        ]
        b = [
            StructuredStep(template="Build {SLOT} app"),
            StructuredStep(template="Deploy to {SLOT}"),
        ]
        sim = structural_similarity(a, b)
        # LCS = 2, dice = 2*2 / (3+2) = 0.8
        assert abs(sim - 0.8) < 0.01


# ── LCS helper tests ──


class TestLCS:
    def test_identical(self):
        assert _lcs_length(["a", "b", "c"], ["a", "b", "c"]) == 3

    def test_empty(self):
        assert _lcs_length([], ["a"]) == 0
        assert _lcs_length(["a"], []) == 0

    def test_partial(self):
        assert _lcs_length(["a", "b", "c"], ["a", "c"]) == 2


# ── Gap analysis tests ──


class TestGapAnalysis:
    def test_all_bound_and_proven(self):
        structure = SkillStructure(
            slots=[Slot(name="language"), Slot(name="deploy_target")],
            structured_steps=[
                StructuredStep(template="Build {language} app", role="variable"),
                StructuredStep(template="Deploy to {deploy_target}", role="variable"),
            ],
            known_bindings={"language": ["python"], "deploy_target": ["aws"]},
        )
        report = analyze_gaps(structure, {"language": "python", "deploy_target": "aws"})
        assert len(report.bound_slots) == 2
        assert len(report.unbound_slots) == 0
        assert all(b["status"] == "proven" for b in report.bound_slots)
        assert report.variable_coverage == 1.0

    def test_unbound_slots(self):
        structure = SkillStructure(
            slots=[Slot(name="language", required=True), Slot(name="deploy_target", required=True)],
            structured_steps=[],
            known_bindings={"language": ["python"]},
        )
        report = analyze_gaps(structure, {"language": "python"})
        assert len(report.unbound_slots) == 1
        assert report.unbound_slots[0]["slot"] == "deploy_target"
        assert len(report.recommendations) > 0

    def test_untested_bindings(self):
        structure = SkillStructure(
            slots=[Slot(name="language")],
            structured_steps=[],
            known_bindings={"language": ["python"]},
        )
        report = analyze_gaps(structure, {"language": "go"})
        assert len(report.bound_slots) == 1
        assert report.bound_slots[0]["status"] == "untested"
        assert any("untested" in r.lower() for r in report.recommendations)

    def test_structural_coverage(self):
        structure = SkillStructure(
            slots=[],
            structured_steps=[
                StructuredStep(template="Review code", role="structural"),
                StructuredStep(template="Build {language} app", role="variable"),
            ],
        )
        report = analyze_gaps(structure, {})
        assert report.structural_coverage == 0.5

    def test_transfer_confidence_range(self):
        structure = SkillStructure(
            slots=[Slot(name="language")],
            structured_steps=[StructuredStep(template="Build {language} app", role="variable")],
            known_bindings={"language": ["python"]},
        )
        report = analyze_gaps(structure, {"language": "python"}, skill_confidence=0.8)
        assert 0.0 <= report.transfer_confidence <= 1.0

    def test_gap_report_to_dict(self):
        report = GapReport(
            skill_id="test-123",
            total_slots=2,
            transfer_confidence=0.75,
        )
        d = report.to_dict()
        assert d["skill_id"] == "test-123"
        assert d["transfer_confidence"] == 0.75


# ── Integration: Skill with structure roundtrip ──


class TestSkillStructureIntegration:
    def test_skill_md_roundtrip_with_structure(self):
        skill = Skill(
            name="Deploy App",
            description="Deploy an application",
            steps=["Build Python app", "Run pytest", "Deploy to AWS"],
            tags=["deploy", "python"],
        )
        slots, steps = extract_slots_heuristic(skill.steps)
        structure = SkillStructure(
            slots=slots,
            structured_steps=steps,
            known_bindings={"language": ["python"]},
        )
        structure.compute_structural_signature()
        skill.set_structure(structure)

        # Roundtrip through SKILL.md
        md = skill.to_skill_md()
        restored = Skill.from_skill_md(md)

        assert restored.structure is not None
        restored_structure = restored.get_structure()
        assert len(restored_structure.slots) == len(slots)
        assert len(restored_structure.structured_steps) == len(steps)
        assert restored_structure.structural_signature == structure.structural_signature

    def test_skill_to_dict_with_structure(self):
        skill = Skill(name="Test Skill", steps=["Build Python app"])
        slots, steps = extract_slots_heuristic(skill.steps)
        structure = SkillStructure(slots=slots, structured_steps=steps)
        skill.set_structure(structure)

        d = skill.to_dict()
        assert "structure" in d
        assert d["structure"]["slots"][0]["name"] == "language"

    def test_skill_without_structure_backward_compatible(self):
        skill = Skill(name="Flat Skill", steps=["Do something"])
        assert skill.structure is None
        assert skill.get_structure() is None

        md = skill.to_skill_md()
        restored = Skill.from_skill_md(md)
        assert restored.structure is None
        assert restored.get_structure() is None


# ── structural_signature_hash tests ──


class TestStructuralSignatureHash:
    def test_deterministic(self):
        h1 = structural_signature_hash(
            ["build {language} app", "run {test_framework}"],
            ["variable", "variable"],
            ["language", "test_framework"],
        )
        h2 = structural_signature_hash(
            ["build {language} app", "run {test_framework}"],
            ["variable", "variable"],
            ["language", "test_framework"],
        )
        assert h1 == h2
        assert len(h1) == 64

    def test_different_for_different_structures(self):
        h1 = structural_signature_hash(
            ["build {language} app"],
            ["variable"],
            ["language"],
        )
        h2 = structural_signature_hash(
            ["deploy to {target}"],
            ["variable"],
            ["target"],
        )
        assert h1 != h2

    def test_slot_name_order_irrelevant(self):
        h1 = structural_signature_hash(
            ["t1"], ["structural"], ["b", "a"],
        )
        h2 = structural_signature_hash(
            ["t1"], ["structural"], ["a", "b"],
        )
        assert h1 == h2  # slot_names are sorted


# ── Normalize template test ──


class TestNormalizeTemplate:
    def test_replaces_all_slot_names(self):
        result = _normalize_template("Build {language} with {tool}")
        assert result == "build {slot} with {slot}"

    def test_lowercases(self):
        result = _normalize_template("REVIEW PR")
        assert result == "review pr"
