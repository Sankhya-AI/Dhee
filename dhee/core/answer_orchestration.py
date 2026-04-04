"""Query planning utilities for orchestrated search.

Provides intent classification and query rewriting to improve retrieval.
No answer synthesis — Dhee retrieves context, the agent answers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

_RECENT_QUERY_RE = re.compile(r"\b(latest|most recent(?:ly)?|currently|current|recent(?:ly)?|as of|last)\b", re.I)
_SUPERLATIVE_RE = re.compile(
    r"\b(?:the\s+)?(?:most|least|fewest|highest|lowest|biggest|smallest|first|last"
    r"|(?:fly|flew|visit|use|eat|watch|play|buy|read|drive|travel)\w*\s+(?:the\s+)?most)\b",
    re.I,
)
_LOW_CONFIDENCE_RE = re.compile(
    r"\b(i\s+don['']?t\s+know|not\s+enough\s+information|insufficient\s+information|unknown|cannot\s+determine)\b",
    re.I,
)


class AnswerIntent(str, Enum):
    COUNT = "count"
    MONEY_SUM = "money_sum"
    DURATION = "duration"
    LATEST = "latest"
    SET_MEMBERS = "set_members"
    ANALYSIS = "analysis"
    FREEFORM = "freeform"


@dataclass
class QueryPlan:
    intent: AnswerIntent
    rewritten_query: str
    search_limit: int
    context_limit: int
    should_map_reduce: bool  # kept for API compat; always False in new path


def classify_answer_intent(question: str, question_type: str = "") -> AnswerIntent:
    q = str(question or "").strip().lower()
    qtype = str(question_type or "").strip().lower()

    if not q:
        return AnswerIntent.FREEFORM

    if re.search(r"\b(how long|duration|elapsed|time spent|total years?|total months?)\b", q):
        return AnswerIntent.DURATION
    if re.search(r"\bhow much time\b", q):
        return AnswerIntent.DURATION
    if re.search(r"\bhow many\s+(days?|weeks?|months?|years?|hours?|minutes?)\b", q):
        return AnswerIntent.DURATION

    money_signals = bool(
        re.search(r"\b(money|dollars?|usd|spent|spend|cost|price)\b", q)
    )
    if money_signals and re.search(r"\b(how much|total|sum|spent|cost)\b", q):
        if re.search(r"\b(days?|weeks?|months?|years?|hours?|minutes?)\s+(spent|in)\b", q):
            return AnswerIntent.DURATION
        if re.search(r"\bpercentage\b", q):
            return AnswerIntent.FREEFORM
        return AnswerIntent.MONEY_SUM

    _QUANTITY_UNITS = (
        r"points?|dollars?|credits?|tokens?|calories?|miles?|steps?|pounds?"
        r"|kilograms?|grams?|liters?|gallons?|servings?|reps?|sets?"
        r"|pages?|episodes?|copies?|followers?|views?|comments?|stars?"
        r"|likes?|shares?|subscribers?|downloads?|posts?"
        r"|people|persons?|viewers?"
        r"|videos?|items?|photos?|images?|songs?|tracks?|chapters?"
        r"|members?|participants?|attendees?|guests?|tickets?"
    )
    _QUANTITY_UNITS_RE = re.compile(
        r"\bhow many\s+(?:\w+\s+){0,3}(" + _QUANTITY_UNITS + r")\b"
    )
    if _QUANTITY_UNITS_RE.search(q):
        return AnswerIntent.FREEFORM
    if re.search(r"\b(page|word|calorie|step|follower|subscriber|view|video|item)\s+count\b", q):
        return AnswerIntent.FREEFORM

    _TOTAL_QUANTITY_RE = re.compile(
        r"\btotal\s+number\s+of\s+(?:\w+\s+){0,2}(" + _QUANTITY_UNITS + r")\b"
    )
    if _TOTAL_QUANTITY_RE.search(q):
        return AnswerIntent.FREEFORM

    if "knowledge-update" in qtype:
        return AnswerIntent.LATEST
    if re.search(r"\bhow much\b", q):
        return AnswerIntent.FREEFORM
    if re.search(r"\b(how many|number of|count|total number)\b", q):
        return AnswerIntent.COUNT

    if _SUPERLATIVE_RE.search(q):
        if re.search(r"\b(the most|most often|most frequent)\b", q, re.I):
            return AnswerIntent.COUNT
        if re.search(r"\b(most recent|first|earliest|latest|newest|oldest)\b", q, re.I):
            return AnswerIntent.LATEST

    if _RECENT_QUERY_RE.search(q):
        return AnswerIntent.LATEST
    if re.search(r"\b(which|what are|list|name all)\b", q):
        return AnswerIntent.SET_MEMBERS

    if re.search(
        r"\b(analy[sz]e|clarify|summarize|explain\b.*\b(reasoning|basis|context|approach))"
        r"|\b(legal opinion|legal basis|legal aid)"
        r"|\b(comprehensive|in[- ]?depth)\b",
        q,
    ):
        return AnswerIntent.ANALYSIS
    if re.search(r"\bhow should (i|we)\s+(reply|respond|draft|write|prepare)\b", q):
        return AnswerIntent.ANALYSIS
    if re.search(
        r"\b(check\s+(my|the|our)\s+\w+|flag\s+any|missing\s+or\s+incorrect"
        r"|inconsisten|verify|validate|cross[- ]?check)\b",
        q,
    ):
        return AnswerIntent.ANALYSIS
    if re.search(
        r"\b(my\s+(routine|usual|typical|approach|process|habit|workflow))"
        r"|\b(how\s+(do|did)\s+i\s+usually)\b"
        r"|\b(what\s+(is|are)\s+my\s+\w*\s*(like|routine|habit|approach))\b"
        r"|\b(can you\s+(take a look|check|help me))\b",
        q,
    ):
        return AnswerIntent.ANALYSIS
    if re.search(r"\b(categorize|categori[sz]e|filter|organize|sort|group)\b", q):
        return AnswerIntent.ANALYSIS
    if "hippocamp" in qtype and "profiling" in qtype:
        return AnswerIntent.ANALYSIS

    return AnswerIntent.FREEFORM


def rewrite_query_for_intent(question: str, intent: AnswerIntent) -> str:
    q = str(question or "").strip()
    if not q:
        return q

    if intent == AnswerIntent.COUNT:
        if _SUPERLATIVE_RE.search(q):
            return (
                f"{q}\nList each occurrence of each distinct item across ALL sessions. "
                f"Count how many times each item appears. "
                f"Return the item that appears MOST frequently as the answer."
            )
        return f"{q}\nList each distinct relevant item and return the final total count."
    if intent == AnswerIntent.MONEY_SUM:
        return f"{q}\nExtract every relevant monetary amount and compute one final total."
    if intent == AnswerIntent.DURATION:
        return f"{q}\nExtract each relevant duration and compute one final total duration."
    if intent == AnswerIntent.LATEST:
        return (
            f"{q}\nExtract the date or time each item was started/mentioned/occurred. "
            f"Order by date/time and return ONLY the most recent one as the answer."
        )
    if intent == AnswerIntent.SET_MEMBERS:
        return f"{q}\nList all distinct relevant items with deduplication."
    if intent == AnswerIntent.ANALYSIS:
        return (
            f"{q}\nExtract all relevant facts, details, and context from the retrieved documents. "
            f"Synthesize a comprehensive, grounded answer using ONLY information found in the evidence. "
            f"Cite specific details (names, dates, amounts, file references) where available."
        )
    if re.search(r"\bwhat time\b", q, re.I):
        return (
            f"{q}\nIf the exact answer is not stated directly, COMPUTE it from the "
            f"available facts (e.g., if wake-up is 7:00 AM and 15 minutes earlier "
            f"on certain days, the answer is 6:45 AM). Always give the final computed value."
        )
    return q


def build_query_plan(
    question: str,
    question_type: str,
    *,
    base_search_limit: int,
    base_context_limit: int,
    search_cap: int = 30,
    context_cap: int = 20,
) -> QueryPlan:
    intent = classify_answer_intent(question, question_type)
    rewritten = rewrite_query_for_intent(question, intent)

    search_limit = max(1, int(base_search_limit))
    context_limit = max(1, int(base_context_limit))

    should_expand = intent in {
        AnswerIntent.COUNT,
        AnswerIntent.MONEY_SUM,
        AnswerIntent.DURATION,
        AnswerIntent.LATEST,
        AnswerIntent.SET_MEMBERS,
        AnswerIntent.ANALYSIS,
    }
    if not should_expand and intent == AnswerIntent.FREEFORM:
        q_lower = question.lower()
        if re.search(
            r"\b(what time|what day|what date|at what age|how many|how much"
            r"|total number|in total|all the|list all|what are all"
            r"|based on|according to|please help|can you help)\b",
            q_lower,
        ):
            should_expand = True

    if should_expand:
        search_limit = max(search_limit, min(max(search_limit, int(search_cap)), search_limit + 10))
        context_limit = max(context_limit, min(max(context_limit, int(context_cap)), context_limit + 6))
        search_limit = max(search_limit, context_limit)

    return QueryPlan(
        intent=intent,
        rewritten_query=rewritten,
        search_limit=search_limit,
        context_limit=context_limit,
        should_map_reduce=should_expand,
    )


def is_low_confidence_answer(answer: str) -> bool:
    return bool(_LOW_CONFIDENCE_RE.search(str(answer or "").strip()))
