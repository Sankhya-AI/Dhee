"""Cognitive Decomposition Engine — memory-grounded recursive problem solving.

The memory layer DICTATES the LLM:
- Memory provides grounded facts
- Memory identifies gaps
- Memory decomposes the problem
- The external LLM only does final reasoning on CLEAN, GROUNDED inputs
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Decomposition prompt
_DECOMPOSE_PROMPT = """Break down this question into simple yes/no sub-questions that can be answered from memory.

QUESTION: {question}

{known_context_block}

Return a JSON array of sub-questions:
[
  {{
    "question": "the sub-question",
    "is_yes_no": true,
    "search_queries": ["query1", "query2"],
    "context_filters": {{}},
    "parent_question": "{question}"
  }}
]

Rules:
- Each sub-question should be independently searchable
- Include multiple search angles (synonyms, related terms)
- Keep sub-questions atomic and concrete
- Return valid JSON only"""

# Synthesis prompt
_SYNTHESIS_PROMPT = """Given these VERIFIED facts, answer the question.

QUESTION: {question}

VERIFIED FACTS:
{facts_block}

GAPS (unanswered sub-questions):
{gaps_block}

Rules:
- Only use the verified facts above
- Do not hallucinate or invent information
- If gaps exist, acknowledge what is unknown
- Be concise and direct"""


@dataclass
class SubQuestion:
    """A decomposed sub-question for memory search."""
    question: str = ""
    is_yes_no: bool = True
    search_queries: List[str] = field(default_factory=list)
    context_filters: Dict[str, Any] = field(default_factory=dict)
    parent_question: str = ""


@dataclass
class GroundedFact:
    """A fact grounded in memory with provenance."""
    question: str = ""
    answer: Optional[str] = None
    source: str = "gap"                    # memory_deterministic|memory_search|user|gap
    confidence: float = 0.0
    memory_ids: List[str] = field(default_factory=list)
    resolver_path: str = ""                # context->sql|vector->rerank|episodic

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "source": self.source,
            "confidence": self.confidence,
            "memory_ids": self.memory_ids,
            "resolver_path": self.resolver_path,
        }


@dataclass
class CognitionResult:
    """Result of cognitive decomposition and grounding."""
    answer: str = ""
    grounded_facts: List[GroundedFact] = field(default_factory=list)
    gaps: List[str] = field(default_factory=list)
    reasoning_trace: List[Dict[str, Any]] = field(default_factory=list)
    stored_memory_id: Optional[str] = None
    depth_used: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "answer": self.answer,
            "grounded_facts": [f.to_dict() for f in self.grounded_facts],
            "gaps": self.gaps,
            "reasoning_trace": self.reasoning_trace,
            "stored_memory_id": self.stored_memory_id,
            "depth_used": self.depth_used,
        }


class CognitionEngine:
    """Memory-grounded recursive problem solving.

    The memory layer DICTATES the LLM:
    - Memory provides grounded facts
    - Memory identifies gaps
    - Memory decomposes the problem
    - The external LLM only does final reasoning on CLEAN, GROUNDED inputs
    """

    def __init__(
        self,
        memory,
        external_llm=None,
        max_depth: int = 3,
        max_sub_questions: int = 10,
        store_solutions: bool = True,
    ):
        self.memory = memory
        self.external_llm = external_llm
        self.max_depth = max_depth
        self.max_sub_questions = max_sub_questions
        self.store_solutions = store_solutions

    def think(
        self,
        question: str,
        user_id: str = "default",
        max_depth: Optional[int] = None,
        ask_user_fn: Optional[Callable] = None,
    ) -> CognitionResult:
        """Run cognitive decomposition loop.

        1. Decompose question into sub-questions
        2. Search memory for each sub-question
        3. Ground facts from memory results
        4. Synthesize answer from grounded facts
        5. Store solution as new memory
        """
        depth = max_depth or self.max_depth
        trace = []

        # Step 1: Try direct memory search first
        direct_result = self._try_direct_search(question, user_id)
        if direct_result and direct_result.confidence >= 0.9:
            return CognitionResult(
                answer=direct_result.answer or "",
                grounded_facts=[direct_result],
                depth_used=0,
                reasoning_trace=[{
                    "step": "direct_search",
                    "question": question,
                    "result": direct_result.to_dict(),
                }],
            )

        # Step 2: Decompose
        sub_questions = self._decompose(question, [])
        trace.append({
            "step": "decompose",
            "question": question,
            "sub_questions": [sq.question for sq in sub_questions],
        })

        # Step 3: Ground each sub-question
        grounded_facts = []
        gaps = []
        for sq in sub_questions[:self.max_sub_questions]:
            fact = self._ground(sq, user_id, depth - 1, ask_user_fn)
            if fact.confidence > 0:
                grounded_facts.append(fact)
            else:
                gaps.append(sq.question)
            trace.append({
                "step": "ground",
                "question": sq.question,
                "result": fact.to_dict(),
            })

        # Step 4: Synthesize
        answer = self._synthesize(question, grounded_facts, gaps)
        trace.append({
            "step": "synthesize",
            "answer": answer,
            "facts_used": len(grounded_facts),
            "gaps": gaps,
        })

        # Step 5: Store solution
        stored_id = None
        if self.store_solutions and answer:
            stored_id = self._store_solution(question, answer, grounded_facts, user_id)

        return CognitionResult(
            answer=answer,
            grounded_facts=grounded_facts,
            gaps=gaps,
            reasoning_trace=trace,
            stored_memory_id=stored_id,
            depth_used=1,
        )

    def _decompose(
        self, question: str, known_context: List[Dict[str, Any]]
    ) -> List[SubQuestion]:
        """Break question into searchable sub-questions."""
        # Try LLM decomposition
        if self.external_llm:
            sub_qs = self._decompose_with_llm(question, known_context)
            if sub_qs:
                return sub_qs

        # Rule-based decomposition
        return self._decompose_rule_based(question)

    def _decompose_with_llm(
        self, question: str, known_context: List[Dict[str, Any]]
    ) -> List[SubQuestion]:
        """LLM-based question decomposition."""
        context_block = ""
        if known_context:
            context_block = (
                "KNOWN CONTEXT:\n"
                + json.dumps(known_context, default=str, indent=2)
            )

        prompt = _DECOMPOSE_PROMPT.format(
            question=question,
            known_context_block=context_block,
        )

        try:
            response = self.external_llm.generate(prompt)
            parsed = self._parse_json_response(response)
            if isinstance(parsed, list):
                return [
                    SubQuestion(
                        question=item.get("question", ""),
                        is_yes_no=item.get("is_yes_no", True),
                        search_queries=item.get("search_queries", []),
                        context_filters=item.get("context_filters", {}),
                        parent_question=question,
                    )
                    for item in parsed
                    if item.get("question")
                ]
        except Exception as e:
            logger.warning("LLM decomposition failed: %s", e)

        return []

    def _decompose_rule_based(self, question: str) -> List[SubQuestion]:
        """Simple rule-based decomposition."""
        # Generate search variants
        queries = [question]

        # Add keyword-focused query
        stop_words = {
            "what", "when", "where", "who", "how", "why", "which", "is",
            "are", "was", "were", "do", "does", "did", "the", "a", "an",
            "my", "your", "i", "me", "we", "can", "could", "would",
            "should", "have", "has", "had",
        }
        keywords = [
            w for w in question.lower().split()
            if w.strip("?.,!") not in stop_words and len(w) > 2
        ]
        if keywords:
            queries.append(" ".join(keywords))

        return [
            SubQuestion(
                question=question,
                search_queries=queries,
                parent_question=question,
            )
        ]

    def _ground(
        self,
        sub_q: SubQuestion,
        user_id: str,
        remaining_depth: int,
        ask_user_fn: Optional[Callable],
    ) -> GroundedFact:
        """Search memory for answer to a sub-question."""
        # Try context resolver first (deterministic)
        if hasattr(self.memory, "context_resolver") and self.memory.context_resolver:
            resolver_result = self.memory.context_resolver.resolve(
                sub_q.question, user_id=user_id
            )
            if resolver_result and resolver_result.answer and resolver_result.has_grounding():
                return GroundedFact(
                    question=sub_q.question,
                    answer=resolver_result.answer,
                    source="memory_deterministic",
                    confidence=resolver_result.confidence,
                    memory_ids=resolver_result.memory_ids,
                    resolver_path=resolver_result.resolver_path,
                )

        # Try vector search
        for query in sub_q.search_queries or [sub_q.question]:
            try:
                search_result = self.memory.search(
                    query=query,
                    user_id=user_id,
                    limit=5,
                )
                results = search_result.get("results", [])
                if results:
                    top = results[0]
                    memory_text = top.get("memory", "")
                    return GroundedFact(
                        question=sub_q.question,
                        answer=memory_text,
                        source="memory_search",
                        confidence=min(1.0, top.get("score", 0.5)),
                        memory_ids=[top.get("id", "")],
                        resolver_path="vector->rerank",
                    )
            except Exception as e:
                logger.debug("Search failed for '%s': %s", query, e)

        # Ask user if available
        if ask_user_fn and remaining_depth <= 0:
            try:
                user_answer = ask_user_fn(sub_q.question)
                if user_answer:
                    return GroundedFact(
                        question=sub_q.question,
                        answer=user_answer,
                        source="user",
                        confidence=1.0,
                    )
            except Exception:
                pass

        # Gap — no answer found
        return GroundedFact(
            question=sub_q.question,
            source="gap",
            confidence=0.0,
        )

    def _try_direct_search(self, question: str, user_id: str) -> Optional[GroundedFact]:
        """Try to answer directly from memory without decomposition."""
        try:
            search_result = self.memory.search(
                query=question,
                user_id=user_id,
                limit=3,
            )
            results = search_result.get("results", [])
            if results and results[0].get("score", 0) >= 0.85:
                top = results[0]
                return GroundedFact(
                    question=question,
                    answer=top.get("memory", ""),
                    source="memory_search",
                    confidence=top.get("score", 0.5),
                    memory_ids=[top.get("id", "")],
                    resolver_path="vector->direct",
                )
        except Exception:
            pass
        return None

    def _synthesize(
        self,
        question: str,
        facts: List[GroundedFact],
        gaps: List[str],
    ) -> str:
        """Synthesize answer from grounded facts."""
        if not facts:
            if gaps:
                return f"I don't have enough information to answer. Missing: {', '.join(gaps)}"
            return "No relevant information found in memory."

        # If single high-confidence fact, return directly
        if len(facts) == 1 and facts[0].confidence >= 0.9:
            return facts[0].answer or ""

        # Use LLM for multi-fact synthesis
        if self.external_llm:
            return self._synthesize_with_llm(question, facts, gaps)

        # Simple concatenation
        answers = [f.answer for f in facts if f.answer]
        return " ".join(answers)

    def _synthesize_with_llm(
        self,
        question: str,
        facts: List[GroundedFact],
        gaps: List[str],
    ) -> str:
        """LLM-based synthesis from grounded facts."""
        facts_block = "\n".join(
            f"- [{f.source}, confidence={f.confidence:.1f}] {f.answer}"
            for f in facts
            if f.answer
        )
        gaps_block = "\n".join(f"- {g}" for g in gaps) if gaps else "None"

        prompt = _SYNTHESIS_PROMPT.format(
            question=question,
            facts_block=facts_block,
            gaps_block=gaps_block,
        )

        try:
            return self.external_llm.generate(prompt)
        except Exception as e:
            logger.warning("LLM synthesis failed: %s", e)
            answers = [f.answer for f in facts if f.answer]
            return " ".join(answers)

    def _store_solution(
        self,
        question: str,
        answer: str,
        facts: List[GroundedFact],
        user_id: str,
    ) -> Optional[str]:
        """Store the cognitive result as a new memory."""
        try:
            content = f"Q: {question}\nA: {answer}"
            result = self.memory.add(
                content,
                user_id=user_id,
                metadata={
                    "source": "cognition_engine",
                    "grounded_from": [f.to_dict() for f in facts],
                    "memory_type": "semantic",
                },
            )
            if isinstance(result, dict):
                results = result.get("results", [])
                if results:
                    return results[0].get("id")
            return None
        except Exception as e:
            logger.debug("Failed to store cognition result: %s", e)
            return None

    def _parse_json_response(self, response: str) -> Any:
        """Parse JSON from LLM response."""
        if not response:
            return None
        text = response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            bracket_start = text.find("[")
            bracket_end = text.rfind("]")
            if bracket_start >= 0 and bracket_end > bracket_start:
                try:
                    return json.loads(text[bracket_start : bracket_end + 1])
                except json.JSONDecodeError:
                    pass
            return None
