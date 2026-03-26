"""Sandboxed code-exec counting for deterministic aggregation.

Instead of asking the LLM to count/sum items across multiple sessions,
this module has the LLM emit Python code that enumerates items, then
executes it in a restricted sandbox to produce a deterministic answer.
"""

from __future__ import annotations

import io
import logging
import re
import threading
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Allowed builtins for the sandbox — no file I/O, no imports, no exec/eval.
# Includes datetime types for date arithmetic (e.g., days between two dates).
_SAFE_BUILTINS = {
    "len": len,
    "sum": sum,
    "max": max,
    "min": min,
    "sorted": sorted,
    "set": set,
    "list": list,
    "dict": dict,
    "int": int,
    "float": float,
    "str": str,
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "print": print,  # will be redirected to StringIO
    "abs": abs,
    "round": round,
    "tuple": tuple,
    "True": True,
    "False": False,
    "None": None,
    # Date/time types — safe, no I/O, needed for temporal arithmetic
    "datetime": datetime,
    "date": date,
    "timedelta": timedelta,
}

# Safe import lines that can be stripped before the blocked-pattern check.
# LLMs habitually emit these even when the types are already available.
_SAFE_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+datetime\s+import\s+[\w\s,]+|import\s+datetime)\s*$",
    re.MULTILINE,
)

# Patterns that indicate dangerous code.
# Only match import/from at statement-start (^) to avoid false positives in
# comments like "# data from session 1" or strings like "trip from NYC".
_BLOCKED_PATTERNS = re.compile(
    r"\b(__\w+__|exec|eval|compile|globals|locals|getattr|setattr|delattr"
    r"|subprocess|shutil|pathlib)\b"
    r"|^import\s+(?!datetime\b)"          # block `import X` unless X is datetime
    r"|^from\s+(?!datetime\b)\w"          # block `from X` unless X is datetime
    r"|\bos\.\w"                          # block os.anything
    r"|\bsys\.\w"                         # block sys.anything
    r"|\bopen\s*\(",                      # block open() calls, not the word "open"
    re.MULTILINE,
)

_CODE_BLOCK_RE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)
_ANSWER_RE = re.compile(r"^ANSWER:\s*(.+)$", re.MULTILINE)
_ITEMS_RE = re.compile(r"^ITEMS:\s*(.+)$", re.MULTILINE)


_MAX_CODE_EXEC_CONTEXT_CHARS = 12000


def build_code_counting_prompt(
    question: str,
    retrieved_context: str,
    question_date: str = "",
) -> str:
    """Build a prompt that asks the LLM to generate Python counting code."""
    # Truncate context to fit smaller models' context windows
    ctx = retrieved_context[:_MAX_CODE_EXEC_CONTEXT_CHARS] if len(retrieved_context) > _MAX_CODE_EXEC_CONTEXT_CHARS else retrieved_context
    date_line = f"\nQuestion Date (today): {question_date}" if question_date else ""
    return f"""You are a precise counting and date-arithmetic assistant. Read the question and context below,
then write a short Python script following this EXACT pattern:

```python
# Step 1: Define ONE list of (label, value) tuples — one entry per distinct item
items = [
    ("item description from session X", numeric_value),
    ("item description from session Y", numeric_value),
]

# Step 2: Compute answer from the items list ONLY
answer = sum(v for _, v in items)  # for sum/duration questions
# OR: answer = len(items)          # for counting questions

# Step 3: Print results — the answer MUST be derived from the items list above
print(f"ANSWER: {{answer}}")
print(f"ITEMS: {{[label for label, _ in items]}}")
```

CRITICAL RULES:
- Create exactly ONE list called `items` with ALL relevant entries from ALL sessions
- For "how many hours/days/weeks" questions: each tuple is ("description", hours_or_days_number), answer = sum()
- For "how many times/items" questions: each tuple is ("description", 1), answer = len()
- For "how much money" questions: each tuple is ("description", dollar_amount), answer = sum()
- For "how many days/months between X and Y" questions: use datetime(year, month, day) to compute exact differences
- For "how many months/days ago" questions: compute from the Question Date using datetime arithmetic
- The ANSWER line MUST be computed from the `items` list or datetime math — never hardcode it
- Available without import: datetime, date, timedelta, len, sum, min, max, abs, round
- Read EVERY session in the context — missing one entry means a wrong answer
- If the same event appears in multiple sessions, include it only ONCE (deduplicate)
- Add the unit to the ANSWER (e.g., "8 days", "140 hours", "$5850", "30 days")

Question: {question}{date_line}

Context:
{ctx}

Write ONLY the Python code inside a code block:
```python
"""


def execute_counting_code(code: str, timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    """Execute counting code in a restricted sandbox.

    Returns {{"answer": str, "items": list}} or None on failure.
    """
    # Strip safe import lines (datetime) before the blocked-pattern check.
    # LLMs emit these habitually; the types are already in _SAFE_BUILTINS.
    code = _SAFE_IMPORT_RE.sub("", code)

    # Validate: block dangerous patterns
    if _BLOCKED_PATTERNS.search(code):
        logger.warning("Code-exec blocked: dangerous pattern detected in: %s", code[:200])
        return None

    # Capture stdout
    captured = io.StringIO()

    restricted_globals = {"__builtins__": {}}
    for name, obj in _SAFE_BUILTINS.items():
        restricted_globals[name] = obj

    # Redirect print to captured output
    def safe_print(*args, **kwargs):
        kwargs["file"] = captured
        print(*args, **kwargs)

    restricted_globals["print"] = safe_print

    result = {"completed": False, "error": None}

    def _run():
        try:
            exec(code, restricted_globals)  # noqa: S102
            result["completed"] = True
        except Exception as e:
            result["error"] = str(e)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    if not result["completed"]:
        if result["error"]:
            logger.warning("Code-exec error: %s", result["error"])
        else:
            logger.warning("Code-exec timeout after %.1fs", timeout)
        return None

    output = captured.getvalue()

    # Parse ANSWER line
    answer_match = _ANSWER_RE.search(output)
    if not answer_match:
        logger.debug("Code-exec: no ANSWER line in output: %r", output[:200])
        return None

    answer = answer_match.group(1).strip()

    # Parse ITEMS line (optional)
    items: List[str] = []
    items_match = _ITEMS_RE.search(output)
    if items_match:
        items_raw = items_match.group(1).strip()
        # Parse list by splitting on commas (avoid eval for safety)
        inner = items_raw.strip("[]")
        if inner:
            items = [x.strip().strip("'\"") for x in inner.split(",") if x.strip().strip("'\"")]

    return {"answer": answer, "items": items}


def refine_count_with_code_exec(
    *,
    llm: Any,
    question: str,
    question_type: str,
    retrieved_context: str,
    draft_answer: str,
    question_date: str = "",
) -> Optional[str]:
    """Full pipeline: prompt LLM to generate code -> exec -> parse answer.

    Returns the refined answer string, or None if code-exec fails.
    """
    prompt = build_code_counting_prompt(question, retrieved_context, question_date=question_date)

    try:
        raw_response = str(llm.generate(prompt)).strip()
    except Exception as e:
        logger.warning("Code-exec LLM call failed: %s", e)
        return None

    # Extract code block
    code_match = _CODE_BLOCK_RE.search(raw_response)
    if code_match:
        code = code_match.group(1).strip()
    else:
        # Try treating the entire response as code if it looks like Python
        lines = raw_response.strip().splitlines()
        code_lines = [ln for ln in lines if not ln.startswith("```")]
        if any(ln.strip().startswith(("items", "total", "count", "print", "#", "result")) for ln in code_lines):
            code = "\n".join(code_lines)
        else:
            logger.debug("Code-exec: no code block found in LLM response")
            return None

    result = execute_counting_code(code)
    if not result:
        return None

    answer = result["answer"]
    items = result.get("items", [])

    logger.info(
        "Code-exec result: answer=%r, items_count=%d, draft=%r",
        answer, len(items), draft_answer,
    )

    # Cross-check: if we have items and an answer, verify consistency
    if items and answer:
        try:
            answer_num = float(re.sub(r"[^\d.]", "", answer.split()[0]))
            # For pure counting, items list length should match
            q_lower = question.lower()
            if any(w in q_lower for w in ("how many times", "how many", "number of")):
                if abs(answer_num - len(items)) > 0.5 and not any(
                    w in q_lower for w in ("hours", "days", "weeks", "months", "minutes")
                ):
                    # Items count is more trustworthy for pure counting
                    logger.debug(
                        "Code-exec cross-check: stated %s but %d items; using items count",
                        answer, len(items),
                    )
                    answer = str(len(items))
        except (ValueError, IndexError):
            pass

    return answer if answer else None
