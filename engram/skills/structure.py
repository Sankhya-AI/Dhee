"""Structural Intelligence — recipe/ingredient decomposition for skills.

Every task has ingredients (swappable components) and a recipe (transferable
structure). This module provides:

- Slot/StructuredStep/SkillStructure dataclasses
- Heuristic and LLM-enhanced slot extraction from flat step lists
- Structural similarity (analogical retrieval via LCS)
- Gap analysis for transfer to new contexts
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class Slot:
    """A named variable in a skill recipe (the 'ingredient')."""

    name: str
    description: str = ""
    slot_type: str = "string"  # string | tool | path | config
    default: Optional[str] = None
    examples: List[str] = field(default_factory=list)
    required: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "slot_type": self.slot_type,
            "default": self.default,
            "examples": self.examples,
            "required": self.required,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Slot":
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            slot_type=data.get("slot_type", "string"),
            default=data.get("default"),
            examples=data.get("examples", []),
            required=data.get("required", True),
        )


@dataclass
class StructuredStep:
    """A single step in a skill recipe, with template slots."""

    template: str  # e.g. "Run tests using {test_framework}"
    role: str = "structural"  # "structural" (core recipe) | "variable" (context-dependent)
    slot_refs: List[str] = field(default_factory=list)
    confidence: float = 1.0
    success_count: int = 0
    fail_count: int = 0
    order_index: int = 0

    def render(self, bindings: Dict[str, str]) -> str:
        """Replace {slot} placeholders with bound values."""
        result = self.template
        for slot_name, value in bindings.items():
            result = result.replace(f"{{{slot_name}}}", value)
        return result

    def to_dict(self) -> Dict[str, Any]:
        return {
            "template": self.template,
            "role": self.role,
            "slot_refs": self.slot_refs,
            "confidence": self.confidence,
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "order_index": self.order_index,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StructuredStep":
        return cls(
            template=data.get("template", ""),
            role=data.get("role", "structural"),
            slot_refs=data.get("slot_refs", []),
            confidence=float(data.get("confidence", 1.0)),
            success_count=int(data.get("success_count", 0)),
            fail_count=int(data.get("fail_count", 0)),
            order_index=int(data.get("order_index", 0)),
        )


@dataclass
class SkillStructure:
    """The full structural decomposition of a skill."""

    slots: List[Slot] = field(default_factory=list)
    structured_steps: List[StructuredStep] = field(default_factory=list)
    known_bindings: Dict[str, List[str]] = field(default_factory=dict)  # slot_name -> [proven values]
    context_bindings: Dict[str, Dict[str, str]] = field(default_factory=dict)  # context_tag -> {slot: value}
    structural_signature: str = ""

    def render_steps(self, bindings: Dict[str, str]) -> List[str]:
        """Render all steps with the given slot bindings."""
        return [step.render(bindings) for step in self.structured_steps]

    def compute_structural_signature(self) -> str:
        """Compute a hash that captures the recipe structure, ignoring slot values."""
        from engram.skills.hashing import structural_signature_hash
        self.structural_signature = structural_signature_hash(
            step_templates=[s.template for s in self.structured_steps],
            step_roles=[s.role for s in self.structured_steps],
            slot_names=[s.name for s in self.slots],
        )
        return self.structural_signature

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slots": [s.to_dict() for s in self.slots],
            "structured_steps": [s.to_dict() for s in self.structured_steps],
            "known_bindings": self.known_bindings,
            "context_bindings": self.context_bindings,
            "structural_signature": self.structural_signature,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkillStructure":
        if not data:
            return cls()
        return cls(
            slots=[Slot.from_dict(s) for s in data.get("slots", [])],
            structured_steps=[StructuredStep.from_dict(s) for s in data.get("structured_steps", [])],
            known_bindings=data.get("known_bindings", {}),
            context_bindings=data.get("context_bindings", {}),
            structural_signature=data.get("structural_signature", ""),
        )


# ---------------------------------------------------------------------------
# Heuristic slot extraction patterns
# ---------------------------------------------------------------------------

_SLOT_PATTERNS: Dict[str, re.Pattern] = {
    "language": re.compile(
        r"\b(python|javascript|typescript|go|golang|rust|java|ruby|c\+\+|"
        r"csharp|c#|scala|kotlin|swift|php|perl|elixir|haskell|lua|r)\b",
        re.IGNORECASE,
    ),
    "test_framework": re.compile(
        r"\b(pytest|jest|mocha|vitest|go\s+test|cargo\s+test|junit|rspec|"
        r"minitest|phpunit|nose|unittest|cypress|playwright)\b",
        re.IGNORECASE,
    ),
    "package_manager": re.compile(
        r"\b(pip|npm|yarn|pnpm|cargo|gem|maven|gradle|composer|bun|poetry|"
        r"conda|brew|apt|dnf|pacman)\b",
        re.IGNORECASE,
    ),
    "deploy_target": re.compile(
        r"\b(aws|gcp|azure|heroku|docker|kubernetes|k8s|vercel|netlify|"
        r"fly\.io|cloudflare|digitalocean|lambda|ecs|ec2|s3)\b",
        re.IGNORECASE,
    ),
    "file_path": re.compile(
        r"(?<!\w)(?:[a-zA-Z_][\w]*(?:/[\w._-]+)+\.\w{1,10})(?!\w)",
    ),
    "tool": re.compile(
        r"\b(grep|find|sed|awk|git|docker|curl|wget|make|cmake|gcc|clang|"
        r"terraform|ansible|helm|kubectl)\b",
        re.IGNORECASE,
    ),
}


def extract_slots_heuristic(
    steps: List[str],
    tags: Optional[List[str]] = None,
) -> Tuple[List[Slot], List[StructuredStep]]:
    """Extract slots and create structured steps from flat step list using heuristics.

    Returns (slots, structured_steps).
    """
    tags = tags or []
    discovered_slots: Dict[str, Slot] = {}
    structured_steps: List[StructuredStep] = []

    for idx, step in enumerate(steps):
        template = step
        slot_refs: List[str] = []

        for slot_name, pattern in _SLOT_PATTERNS.items():
            matches = list(pattern.finditer(template))
            for match in reversed(matches):  # reverse so indices stay valid
                matched_text = match.group(0)
                # Replace match with {slot_name} placeholder
                template = template[:match.start()] + f"{{{slot_name}}}" + template[match.end():]

                if slot_name not in slot_refs:
                    slot_refs.append(slot_name)

                if slot_name not in discovered_slots:
                    discovered_slots[slot_name] = Slot(
                        name=slot_name,
                        slot_type=_infer_slot_type(slot_name),
                        examples=[matched_text],
                    )
                else:
                    if matched_text not in discovered_slots[slot_name].examples:
                        discovered_slots[slot_name].examples.append(matched_text)

        # Classify role: variable if it has slots, structural if not
        role = "variable" if slot_refs else "structural"

        structured_steps.append(StructuredStep(
            template=template,
            role=role,
            slot_refs=slot_refs,
            order_index=idx,
        ))

    return list(discovered_slots.values()), structured_steps


def _infer_slot_type(slot_name: str) -> str:
    """Infer slot type from its name."""
    if slot_name == "file_path":
        return "path"
    if slot_name == "tool":
        return "tool"
    if slot_name in ("deploy_target",):
        return "config"
    return "string"


# ---------------------------------------------------------------------------
# LLM-enhanced slot extraction
# ---------------------------------------------------------------------------

_LLM_DECOMPOSE_PROMPT = """Analyze this skill and decompose it into a recipe with variable slots.

Skill: {name}
Description: {description}
Steps:
{steps_text}
Tags: {tags}

Identify which parts of each step are:
1. STRUCTURAL (the core recipe pattern that transfers to any similar task)
2. VARIABLE (swappable components: languages, tools, frameworks, paths, targets)

Return a JSON object:
{{
  "slots": [
    {{"name": "slot_name", "description": "what this slot represents", "slot_type": "string|tool|path|config", "examples": ["value1", "value2"]}}
  ],
  "structured_steps": [
    {{"template": "Step text with {{slot_name}} placeholders", "role": "structural|variable"}}
  ]
}}

Respond with ONLY the JSON object."""


def extract_slots_llm(
    name: str,
    description: str,
    steps: List[str],
    tags: List[str],
    llm: Any,
) -> Tuple[List[Slot], List[StructuredStep]]:
    """Use an LLM to identify slots and rewrite steps as templates.

    Falls back to heuristic extraction on failure.
    """
    steps_text = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(steps))
    prompt = _LLM_DECOMPOSE_PROMPT.format(
        name=name,
        description=description,
        steps_text=steps_text,
        tags=", ".join(tags),
    )

    try:
        response = llm.generate(prompt)
        response_text = response.strip()
        # Strip code fences if present
        if response_text.startswith("```"):
            response_text = response_text.strip("`").strip()
            if response_text.startswith("json"):
                response_text = response_text[4:].strip()

        data = json.loads(response_text)

        slots = []
        for sd in data.get("slots", []):
            slots.append(Slot(
                name=sd.get("name", ""),
                description=sd.get("description", ""),
                slot_type=sd.get("slot_type", "string"),
                examples=sd.get("examples", []),
            ))

        structured_steps = []
        for idx, sd in enumerate(data.get("structured_steps", [])):
            template = sd.get("template", "")
            role = sd.get("role", "structural")
            # Detect slot refs from template
            slot_refs = re.findall(r"\{(\w+)\}", template)
            structured_steps.append(StructuredStep(
                template=template,
                role=role,
                slot_refs=slot_refs,
                order_index=idx,
            ))

        if slots or structured_steps:
            return slots, structured_steps

    except Exception as e:
        logger.warning("LLM slot extraction failed, falling back to heuristic: %s", e)

    return extract_slots_heuristic(steps, tags)


# ---------------------------------------------------------------------------
# Structural similarity (analogical retrieval)
# ---------------------------------------------------------------------------

def _normalize_template(template: str) -> str:
    """Normalize a template for structural comparison.

    Replaces all {slot_name} with a generic {SLOT} and lowercases.
    """
    return re.sub(r"\{\w+\}", "{SLOT}", template).strip().lower()


def _lcs_length(a: Sequence[str], b: Sequence[str]) -> int:
    """Longest Common Subsequence length (dynamic programming)."""
    m, n = len(a), len(b)
    if m == 0 or n == 0:
        return 0
    # Space-optimized: only need two rows
    prev = [0] * (n + 1)
    curr = [0] * (n + 1)
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev, curr = curr, [0] * (n + 1)
    return prev[n]


def structural_similarity(a_steps: List[StructuredStep], b_steps: List[StructuredStep]) -> float:
    """Compute structural similarity between two skill recipes.

    Normalizes templates (replacing slot names with generic {SLOT}),
    then computes Dice coefficient over the LCS:
        similarity = 2 * len(LCS) / (len(a) + len(b))

    Returns 0.0..1.0. Two skills with the same recipe but different slot
    values will score ~1.0.
    """
    if not a_steps and not b_steps:
        return 1.0
    if not a_steps or not b_steps:
        return 0.0

    a_normalized = [_normalize_template(s.template) for s in a_steps]
    b_normalized = [_normalize_template(s.template) for s in b_steps]

    lcs = _lcs_length(a_normalized, b_normalized)
    return (2.0 * lcs) / (len(a_normalized) + len(b_normalized))


# ---------------------------------------------------------------------------
# Gap analysis
# ---------------------------------------------------------------------------


@dataclass
class GapReport:
    """Analysis of what transfers vs what needs experimentation."""

    skill_id: str = ""
    total_slots: int = 0
    bound_slots: List[Dict[str, Any]] = field(default_factory=list)
    unbound_slots: List[Dict[str, Any]] = field(default_factory=list)
    transfer_confidence: float = 0.0
    structural_coverage: float = 0.0
    variable_coverage: float = 0.0
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "total_slots": self.total_slots,
            "bound_slots": self.bound_slots,
            "unbound_slots": self.unbound_slots,
            "transfer_confidence": round(self.transfer_confidence, 4),
            "structural_coverage": round(self.structural_coverage, 4),
            "variable_coverage": round(self.variable_coverage, 4),
            "recommendations": self.recommendations,
        }


def analyze_gaps(
    structure: SkillStructure,
    target_context: Dict[str, str],
    skill_confidence: float = 0.5,
) -> GapReport:
    """Analyze what transfers from a skill to a new context.

    For each slot:
    - target provides value AND value in known_bindings → proven (high confidence)
    - target provides value but value not in known_bindings → untested
    - target doesn't provide value → unknown (needs input)
    """
    report = GapReport(total_slots=len(structure.slots))
    recommendations = []

    structural_steps = [s for s in structure.structured_steps if s.role == "structural"]
    variable_steps = [s for s in structure.structured_steps if s.role == "variable"]

    # Structural coverage: structural steps are always transferable
    total_steps = len(structure.structured_steps)
    if total_steps > 0:
        report.structural_coverage = len(structural_steps) / total_steps
    else:
        report.structural_coverage = 1.0

    bound_count = 0
    for slot in structure.slots:
        target_value = target_context.get(slot.name)
        known_values = structure.known_bindings.get(slot.name, [])

        if target_value and target_value.lower() in [v.lower() for v in known_values]:
            # Proven binding
            report.bound_slots.append({
                "slot": slot.name,
                "value": target_value,
                "status": "proven",
                "confidence": "high",
            })
            bound_count += 1
        elif target_value:
            # Untested binding
            report.bound_slots.append({
                "slot": slot.name,
                "value": target_value,
                "status": "untested",
                "confidence": "low",
            })
            recommendations.append(
                f"Slot '{slot.name}' has untested value '{target_value}'. "
                f"Known values: {known_values or ['none']}. Experiment carefully."
            )
            bound_count += 0.5  # Partial credit
        else:
            # Unknown
            report.unbound_slots.append({
                "slot": slot.name,
                "status": "unknown",
                "known_values": known_values,
                "required": slot.required,
            })
            if slot.required:
                recommendations.append(
                    f"Slot '{slot.name}' needs a value. "
                    f"Known options: {known_values or ['none']}."
                )

    # Variable coverage
    if structure.slots:
        report.variable_coverage = bound_count / len(structure.slots)
    else:
        report.variable_coverage = 1.0

    # Transfer confidence: weighted combination
    report.transfer_confidence = (
        0.4 * report.structural_coverage
        + 0.3 * report.variable_coverage
        + 0.3 * skill_confidence
    )

    report.recommendations = recommendations
    return report
