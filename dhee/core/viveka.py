"""विवेक (Viveka) — Continuous discriminative awareness for memory quality.

Yoga Sutra 2.26: "viveka-khyatir aviplava hanopayah"
Uninterrupted discriminative awareness is the means of liberation [from error].

Every memory operation passes through viveka — not periodic evaluation,
but CONTINUOUS assessment. Each output is classified as:

  - Aklishta (non-afflicted): correct, well-formed, useful
  - Klishta (afflicted): erroneous, malformed, misleading

Viveka does NOT fix problems. It detects them and reports to the
SamskaraCollector, which accumulates signals into vasanas.
Fixing happens in nididhyasana (model retraining).

Assessment depth follows Pancha Kosha (five sheaths):
  1. Annamaya  — structural checks (is it well-formed?)
  2. Pranamaya — energy checks (does it have substance?)
  3. Manomaya  — consistency checks (does it contradict itself?)
  4. Vijnanamaya — coherence checks (does it fit the context?)
  5. Anandamaya — completeness checks (did it miss anything?)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from dhee.core.samskara import SamskaraCollector

logger = logging.getLogger(__name__)


class VivekaVerdict(IntEnum):
    """Granular quality verdict — not just pass/fail."""

    AKLISHTA = 2        # non-afflicted, high quality
    SATTVA = 1          # acceptable, minor issues
    RAJAS = 0           # uncertain, needs attention
    TAMAS = -1          # poor quality, likely wrong
    KLISHTA = -2        # afflicted, definitely wrong


class AssessmentKosha(IntEnum):
    """Pancha Kosha depth levels — each checks progressively deeper."""

    ANNAMAYA = 1    # structural: is it well-formed?
    PRANAMAYA = 2   # energy: does it have substance?
    MANOMAYA = 3    # consistency: does it contradict itself?
    VIJNANAMAYA = 4 # coherence: does it fit context?
    ANANDAMAYA = 5  # completeness: did it capture everything?


@dataclass
class VivekaAssessment:
    """Result of viveka assessment on a single operation output."""

    verdict: VivekaVerdict
    confidence: float = 1.0         # 0-1, how sure is the assessment
    kosha_reached: AssessmentKosha = AssessmentKosha.ANNAMAYA

    # Dimensional quality (maps to KarmaAxis for training signals)
    dimension_scores: Dict[str, float] = field(default_factory=dict)

    # What was assessed
    operation: str = ""             # e.g. "extraction", "retrieval", "answer"
    memory_id: str = ""
    query: str = ""

    # Issues found
    issues: List[str] = field(default_factory=list)

    # Suggestions (not fixes — viveka observes, doesn't act)
    notes: List[str] = field(default_factory=list)

    @property
    def is_klishta(self) -> bool:
        return self.verdict.value < 0

    @property
    def is_aklishta(self) -> bool:
        return self.verdict.value > 0


# ---------------------------------------------------------------------------
# Heuristic assessment functions (zero-LLM, deterministic)
# ---------------------------------------------------------------------------

def _check_extraction_annamaya(facts: List[Dict], content: str) -> List[str]:
    """Annamaya (structural): Are extracted facts well-formed?"""
    issues = []
    for i, fact in enumerate(facts):
        if not fact.get("subject"):
            issues.append(f"fact[{i}]: missing subject")
        if not fact.get("predicate"):
            issues.append(f"fact[{i}]: missing predicate")
        if not fact.get("value"):
            issues.append(f"fact[{i}]: missing value")
        canonical = fact.get("canonical_key", "")
        if canonical and canonical.count("|") < 2:
            issues.append(f"fact[{i}]: malformed canonical_key '{canonical}'")
    return issues


def _check_extraction_pranamaya(facts: List[Dict], content: str) -> List[str]:
    """Pranamaya (substance): Do facts have real content, not just structure?"""
    issues = []
    content_lower = content.lower()
    for i, fact in enumerate(facts):
        value = str(fact.get("value", ""))
        # Vacuous values
        if value.lower() in ("unknown", "n/a", "none", "null", ""):
            issues.append(f"fact[{i}]: vacuous value '{value}'")
        # Subject/predicate/value identical
        if fact.get("subject") == fact.get("value"):
            issues.append(f"fact[{i}]: subject equals value (tautological)")
        # Value not grounded in content (hallucination signal)
        if len(value) > 3 and value.lower() not in content_lower:
            # Allow numeric values and common transformations
            if not re.match(r"^[\d.$,\-+]+$", value):
                issues.append(f"fact[{i}]: value '{value[:50]}' not found in source text")
    return issues


def _check_extraction_manomaya(facts: List[Dict]) -> List[str]:
    """Manomaya (consistency): Do facts contradict each other?"""
    issues = []
    # Check for contradictory facts with same canonical_key
    seen_keys: Dict[str, str] = {}
    for i, fact in enumerate(facts):
        key = fact.get("canonical_key", "")
        if not key:
            continue
        value = str(fact.get("value", ""))
        if key in seen_keys and seen_keys[key] != value:
            issues.append(
                f"fact[{i}]: contradicts earlier fact with same key '{key}': "
                f"'{seen_keys[key]}' vs '{value}'"
            )
        seen_keys[key] = value

    # Check temporal consistency
    for i, fact in enumerate(facts):
        valid_from = fact.get("valid_from", "")
        valid_until = fact.get("valid_until", "")
        if valid_from and valid_until and valid_from > valid_until:
            issues.append(f"fact[{i}]: valid_from > valid_until (temporal inversion)")
    return issues


def _check_extraction_vijnanamaya(
    facts: List[Dict], context: Optional[Dict],
) -> List[str]:
    """Vijnanamaya (coherence): Do facts fit the context anchor?"""
    issues = []
    if not context:
        return issues

    era = context.get("era", "")
    place = context.get("place", "")

    for i, fact in enumerate(facts):
        time_val = fact.get("time", "")
        # If fact has a time and context has an era, check plausibility
        if time_val and era:
            # School era with dates after 2020 is suspicious
            if "school" in era.lower() and time_val.startswith("202"):
                issues.append(
                    f"fact[{i}]: time '{time_val}' seems late for era '{era}'"
                )
        # If fact references a place different from context place
        value = str(fact.get("value", "")).lower()
        predicate = str(fact.get("predicate", "")).lower()
        if place and predicate in ("visited", "traveled_to", "went_to"):
            # This is expected — travel facts naturally reference other places
            pass
    return issues


def _check_extraction_anandamaya(
    facts: List[Dict], content: str,
) -> List[str]:
    """Anandamaya (completeness): Did extraction miss obvious content?"""
    issues = []
    content_lower = content.lower()
    fact_values_lower = {str(f.get("value", "")).lower() for f in facts}

    # Check for unextracted monetary amounts
    money_pattern = re.findall(r"\$\s*[\d,]+(?:\.\d+)?", content)
    for m in money_pattern:
        amount = m.replace("$", "").replace(",", "").strip()
        if not any(amount in v for v in fact_values_lower):
            issues.append(f"missed monetary amount: {m}")

    # Check for unextracted dates
    date_pattern = re.findall(
        r"\b(?:January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+\d{1,2},?\s*\d{4}\b",
        content,
    )
    for d in date_pattern:
        if not any(d.lower() in v for v in fact_values_lower):
            issues.append(f"missed date reference: {d}")

    # Check for unextracted named entities (capitalized multi-word)
    entities = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b", content)
    entity_names = {str(f.get("subject", "")).lower() for f in facts} | fact_values_lower
    for ent in set(entities):
        if ent.lower() not in entity_names and len(ent) > 4:
            issues.append(f"potentially missed entity: {ent}")

    return issues


def _check_retrieval_quality(
    query: str,
    results: List[Dict],
    top_k: int = 10,
) -> VivekaAssessment:
    """Assess retrieval quality without LLM."""
    issues = []
    notes = []
    scores: Dict[str, float] = {}

    if not results:
        return VivekaAssessment(
            verdict=VivekaVerdict.KLISHTA,
            confidence=0.9,
            kosha_reached=AssessmentKosha.ANNAMAYA,
            operation="retrieval",
            query=query[:200],
            issues=["zero results returned"],
            dimension_scores={"retrieval_recall": -1.0},
        )

    # Annamaya: structural checks
    for i, r in enumerate(results[:top_k]):
        if not r.get("memory") and not r.get("content"):
            issues.append(f"result[{i}]: empty content")
        score = r.get("score") or r.get("similarity") or 0
        if isinstance(score, (int, float)) and score < 0.1:
            issues.append(f"result[{i}]: very low similarity ({score:.3f})")

    # Pranamaya: do results have substance?
    query_words = set(re.findall(r"\b\w{3,}\b", query.lower()))
    query_words -= {"the", "what", "how", "did", "does", "was", "were", "are", "has", "have"}
    hit_count = 0
    for r in results[:top_k]:
        text = str(r.get("memory") or r.get("content") or "").lower()
        result_words = set(re.findall(r"\b\w{3,}\b", text))
        if query_words & result_words:
            hit_count += 1

    if hit_count == 0:
        issues.append("no results contain any query terms")
        scores["retrieval_precision"] = -0.8
    else:
        precision = hit_count / min(len(results), top_k)
        scores["retrieval_precision"] = 2.0 * precision - 1.0

    # Manomaya: consistency across results
    # (multiple results shouldn't contradict each other for simple queries)
    # This is lightweight — full contradiction detection is in conflict.py

    # Determine verdict
    if not issues:
        verdict = VivekaVerdict.AKLISHTA
        confidence = 0.7
    elif len(issues) <= 2:
        verdict = VivekaVerdict.SATTVA
        confidence = 0.6
    elif any("zero results" in i for i in issues):
        verdict = VivekaVerdict.KLISHTA
        confidence = 0.9
    else:
        verdict = VivekaVerdict.RAJAS
        confidence = 0.5

    return VivekaAssessment(
        verdict=verdict,
        confidence=confidence,
        kosha_reached=AssessmentKosha.PRANAMAYA,
        operation="retrieval",
        query=query[:200],
        issues=issues,
        notes=notes,
        dimension_scores=scores,
    )


def _check_answer_quality(
    query: str,
    answer: str,
    source_memories: List[str],
) -> VivekaAssessment:
    """Assess answer quality without LLM.

    Checks structural quality, grounding, and format.
    Deep semantic assessment requires the DheeModel.
    """
    issues = []
    notes = []
    scores: Dict[str, float] = {}

    # Annamaya: structural
    if not answer or not answer.strip():
        return VivekaAssessment(
            verdict=VivekaVerdict.KLISHTA,
            confidence=1.0,
            kosha_reached=AssessmentKosha.ANNAMAYA,
            operation="answer",
            query=query[:200],
            issues=["empty answer"],
            dimension_scores={"answer_quality": -1.0},
        )

    if len(answer) < 2:
        issues.append("answer too short (< 2 chars)")

    # Pranamaya: substance
    # Detect non-answer patterns
    non_answers = [
        r"(?i)^i\s+(?:don't|do not|cannot|can't)\s+(?:know|remember|recall|find)",
        r"(?i)^(?:sorry|unfortunately|i'm not sure)",
        r"(?i)^no\s+(?:information|data|memory|record)",
        r"(?i)^(?:there is no|i have no)\s+(?:information|data|memory|record)",
    ]
    for pattern in non_answers:
        if re.match(pattern, answer.strip()):
            issues.append("answer is a non-answer / refusal")
            scores["answer_quality"] = -0.5
            break

    # Check if answer is grounded in source memories
    if source_memories:
        answer_lower = answer.lower()
        source_text = " ".join(source_memories).lower()
        # Extract key content words from answer
        answer_words = set(re.findall(r"\b\w{4,}\b", answer_lower))
        answer_words -= {
            "that", "this", "with", "from", "they", "were", "have",
            "been", "also", "about", "which", "their", "some", "would",
        }
        if answer_words:
            grounded_count = sum(
                1 for w in answer_words if w in source_text
            )
            grounding_ratio = grounded_count / len(answer_words)
            if grounding_ratio < 0.3:
                issues.append(
                    f"low grounding: only {grounding_ratio:.0%} of answer "
                    f"terms found in source memories"
                )
                scores["answer_quality"] = -0.3
            else:
                scores["answer_quality"] = 2.0 * grounding_ratio - 1.0

    # Manomaya: consistency — does answer contradict the query?
    query_lower = query.lower()
    answer_lower = answer.lower()

    # "how many" questions should get numeric answers
    if re.search(r"\bhow many\b", query_lower):
        if not re.search(r"\d", answer):
            issues.append("counting question but answer has no number")
            scores["temporal_reasoning"] = -0.5

    # "when" questions should get date/time answers
    if re.search(r"\bwhen\b", query_lower):
        date_in_answer = bool(re.search(
            r"\b\d{4}\b|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\b"
            r"|\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
            r"|\blast\s+(?:week|month|year)\b",
            answer_lower,
        ))
        if not date_in_answer:
            issues.append("temporal question but answer has no date/time reference")
            scores["temporal_reasoning"] = -0.4

    # Determine verdict
    if not issues:
        verdict = VivekaVerdict.AKLISHTA
        confidence = 0.6  # heuristics alone can't be fully confident
    elif len(issues) == 1 and "non-answer" not in issues[0]:
        verdict = VivekaVerdict.SATTVA
        confidence = 0.5
    elif any("empty" in i or "non-answer" in i for i in issues):
        verdict = VivekaVerdict.KLISHTA
        confidence = 0.9
    else:
        verdict = VivekaVerdict.RAJAS
        confidence = 0.5

    return VivekaAssessment(
        verdict=verdict,
        confidence=confidence,
        kosha_reached=AssessmentKosha.MANOMAYA,
        operation="answer",
        query=query[:200],
        issues=issues,
        notes=notes,
        dimension_scores=scores,
    )


# ---------------------------------------------------------------------------
# Viveka — the continuous discriminator
# ---------------------------------------------------------------------------

class Viveka:
    """Continuous discriminative awareness for every memory operation.

    Aviplava — uninterrupted. Not periodic batch evaluation,
    but inline assessment that runs on every operation.

    Uses heuristic checks (zero-LLM cost) at all five kosha depths.
    Reports to SamskaraCollector for vasana accumulation.
    """

    def __init__(
        self,
        samskara_collector: Optional[SamskaraCollector] = None,
        strict_mode: bool = False,
    ):
        self.samskara = samskara_collector
        self.strict_mode = strict_mode  # if True, raise on klishta

        # Running stats (lightweight counters, not stored)
        self._assessed = 0
        self._klishta_count = 0
        self._aklishta_count = 0

    def assess_extraction(
        self,
        content: str,
        facts: List[Dict],
        context: Optional[Dict] = None,
        memory_id: str = "",
        user_id: str = "default",
    ) -> VivekaAssessment:
        """Assess fact extraction quality through all five koshas.

        Called after EngramExtractor produces structured output.
        """
        all_issues: List[str] = []
        all_notes: List[str] = []
        dimension_scores: Dict[str, float] = {}
        deepest_kosha = AssessmentKosha.ANNAMAYA

        # Kosha 1: Annamaya — structural well-formedness
        issues = _check_extraction_annamaya(facts, content)
        all_issues.extend(issues)
        if not issues:
            deepest_kosha = AssessmentKosha.PRANAMAYA

            # Kosha 2: Pranamaya — substance
            issues = _check_extraction_pranamaya(facts, content)
            all_issues.extend(issues)
            hallucination_issues = [
                i for i in issues if "not found in source" in i
            ]
            if hallucination_issues:
                dimension_scores["fact_precision"] = -0.5
            if not issues:
                deepest_kosha = AssessmentKosha.MANOMAYA

                # Kosha 3: Manomaya — internal consistency
                issues = _check_extraction_manomaya(facts)
                all_issues.extend(issues)
                if not issues:
                    deepest_kosha = AssessmentKosha.VIJNANAMAYA

                    # Kosha 4: Vijnanamaya — contextual coherence
                    issues = _check_extraction_vijnanamaya(facts, context)
                    all_issues.extend(issues)
                    if context:
                        dimension_scores["context_accuracy"] = (
                            0.8 if not issues else -0.3
                        )
                    if not issues:
                        deepest_kosha = AssessmentKosha.ANANDAMAYA

                        # Kosha 5: Anandamaya — completeness
                        issues = _check_extraction_anandamaya(facts, content)
                        all_issues.extend(issues)
                        missed = [
                            i for i in issues if "missed" in i
                        ]
                        if missed:
                            dimension_scores["fact_recall"] = (
                                -0.3 * min(len(missed), 3)
                            )

        # Determine verdict
        structural_issues = [
            i for i in all_issues
            if "missing" in i or "malformed" in i
        ]
        hallucination_issues = [
            i for i in all_issues if "not found in source" in i
        ]
        contradiction_issues = [
            i for i in all_issues if "contradicts" in i
        ]

        if not all_issues:
            verdict = VivekaVerdict.AKLISHTA
            confidence = 0.8
            dimension_scores.setdefault("fact_precision", 0.8)
            dimension_scores.setdefault("fact_recall", 0.5)
        elif structural_issues:
            verdict = VivekaVerdict.KLISHTA
            confidence = 0.9
            dimension_scores.setdefault("fact_precision", -0.8)
        elif hallucination_issues:
            verdict = VivekaVerdict.TAMAS
            confidence = 0.7
        elif contradiction_issues:
            verdict = VivekaVerdict.TAMAS
            confidence = 0.8
        elif len(all_issues) <= 2:
            verdict = VivekaVerdict.SATTVA
            confidence = 0.6
        else:
            verdict = VivekaVerdict.RAJAS
            confidence = 0.5

        assessment = VivekaAssessment(
            verdict=verdict,
            confidence=confidence,
            kosha_reached=deepest_kosha,
            operation="extraction",
            memory_id=memory_id,
            issues=all_issues,
            notes=all_notes,
            dimension_scores=dimension_scores,
        )

        self._record(assessment, user_id, content)
        return assessment

    def assess_retrieval(
        self,
        query: str,
        results: List[Dict],
        top_k: int = 10,
        user_id: str = "default",
    ) -> VivekaAssessment:
        """Assess retrieval quality.

        Called after search() returns results, before answer synthesis.
        """
        assessment = _check_retrieval_quality(query, results, top_k)
        self._record(assessment, user_id)
        return assessment

    def assess_answer(
        self,
        query: str,
        answer: str,
        source_memories: Optional[List[str]] = None,
        user_id: str = "default",
    ) -> VivekaAssessment:
        """Assess answer quality.

        Called after answer synthesis, before returning to user.
        """
        assessment = _check_answer_quality(
            query, answer, source_memories or [],
        )
        self._record(assessment, user_id)
        return assessment

    def assess_storage(
        self,
        content: str,
        memory_id: str,
        is_duplicate: bool = False,
        duplicate_of: str = "",
        user_id: str = "default",
    ) -> VivekaAssessment:
        """Assess storage operation quality.

        Lightweight: mainly checks for vacuous or duplicate storage.
        """
        issues = []
        scores: Dict[str, float] = {}

        # Annamaya: is content substantive?
        stripped = content.strip()
        if not stripped:
            issues.append("empty content stored")
        elif len(stripped) < 5:
            issues.append(f"very short content ({len(stripped)} chars)")

        # Pranamaya: is it a meaningful dedup?
        if is_duplicate and duplicate_of:
            # Dedup detection is GOOD — it's a positive signal
            return VivekaAssessment(
                verdict=VivekaVerdict.AKLISHTA,
                confidence=0.8,
                kosha_reached=AssessmentKosha.PRANAMAYA,
                operation="storage",
                memory_id=memory_id,
                issues=[],
                notes=[f"correctly deduplicated with {duplicate_of}"],
                dimension_scores={"dedup_quality": 0.8},
            )

        verdict = VivekaVerdict.AKLISHTA if not issues else VivekaVerdict.RAJAS
        return VivekaAssessment(
            verdict=verdict,
            confidence=0.7,
            kosha_reached=AssessmentKosha.PRANAMAYA,
            operation="storage",
            memory_id=memory_id,
            issues=issues,
            dimension_scores=scores,
        )

    # ------------------------------------------------------------------
    # Integration with SamskaraCollector
    # ------------------------------------------------------------------

    def _record(
        self,
        assessment: VivekaAssessment,
        user_id: str = "default",
        input_text: str = "",
    ) -> None:
        """Report assessment to samskara collector and update counters."""
        self._assessed += 1
        if assessment.is_klishta:
            self._klishta_count += 1
        elif assessment.is_aklishta:
            self._aklishta_count += 1

        if not self.samskara:
            return

        # Map operation to samskara recording methods
        if assessment.operation == "extraction":
            fact_count = 0
            if assessment.is_aklishta:
                # Estimate fact count from dimension scores
                fact_count = max(1, int(
                    assessment.dimension_scores.get("fact_recall", 0.5) * 5
                ))
            self.samskara.on_extraction(
                memory_id=assessment.memory_id,
                input_text=input_text[:500],
                extracted_output="; ".join(assessment.issues) if assessment.issues else "ok",
                fact_count=fact_count,
                user_id=user_id,
            )

        elif assessment.operation == "retrieval":
            was_useful = assessment.is_aklishta
            self.samskara.on_retrieval(
                query=assessment.query,
                retrieved_ids=[assessment.memory_id] if assessment.memory_id else [],
                was_useful=was_useful,
                user_id=user_id,
            )

        elif assessment.operation == "answer":
            if assessment.is_aklishta:
                self.samskara.on_answer_accepted(
                    query=assessment.query,
                    answer="",  # we don't have the answer text here
                    memory_ids=[],
                    user_id=user_id,
                )

        if assessment.issues:
            logger.debug(
                "Viveka [%s] %s: %s (%d issues, kosha=%s)",
                assessment.operation,
                assessment.verdict.name,
                assessment.memory_id or assessment.query[:40],
                len(assessment.issues),
                AssessmentKosha(assessment.kosha_reached).name,
            )

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Get viveka assessment statistics."""
        total = self._assessed or 1
        return {
            "total_assessed": self._assessed,
            "klishta_count": self._klishta_count,
            "aklishta_count": self._aklishta_count,
            "klishta_ratio": self._klishta_count / total,
            "aklishta_ratio": self._aklishta_count / total,
        }

    @property
    def quality_ratio(self) -> float:
        """Overall quality ratio: aklishta / total. Higher is better."""
        total = self._assessed or 1
        return self._aklishta_count / total
