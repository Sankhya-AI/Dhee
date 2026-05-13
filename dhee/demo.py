"""Built-in demos that make Dhee's context-governance value concrete."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from dhee.router import bash_digest, digest as read_digest

CHARS_PER_TOKEN = 3.5


def _tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) if text else 0


@dataclass
class TokenRouterCase:
    name: str
    surface: str
    decision: str
    raw_tokens: int
    digest_tokens: int
    saved_tokens: int
    saved_pct: float
    ptr: str
    digest: str
    expand: str


def _pytest_output() -> str:
    header = [
        "============================= test session starts =============================",
        "platform darwin -- Python 3.12.0, pytest-8.3.4",
        "collected 842 items",
        "",
        "tests/test_checkout.py::test_retries_on_409 FAILED",
        "tests/test_checkout.py::test_preserves_idempotency_key PASSED",
        "",
        "=================================== FAILURES ===================================",
        "FAILED tests/test_checkout.py::test_retries_on_409 - AssertionError: retry budget exhausted",
        "E   AssertionError: expected status=complete",
        "E   assert 'pending' == 'complete'",
        "",
    ]
    noisy_block = [
        "Captured stdout call",
        "checkout worker poll attempt=1 status=pending",
        "checkout worker poll attempt=2 status=pending",
        "checkout worker poll attempt=3 status=pending",
        "debug payload: {'cart_id': 'demo', 'retry_after_ms': 250, 'trace': 'redacted'}",
    ]
    tail = [
        "",
        "=========================== short test summary info ===========================",
        "FAILED tests/test_checkout.py::test_retries_on_409 - AssertionError: retry budget exhausted",
        "1 failed, 841 passed, 12 skipped in 88.43s",
    ]
    return "\n".join(header + noisy_block * 180 + tail) + "\n"


def _git_diff_output() -> str:
    chunks: list[str] = []
    for idx in range(1, 26):
        chunks.extend(
            [
                f"diff --git a/src/service_{idx}.py b/src/service_{idx}.py",
                f"--- a/src/service_{idx}.py",
                f"+++ b/src/service_{idx}.py",
                "@@ -10,7 +10,12 @@ def handle(request):",
                "-    return process(request)",
                "+    result = process(request)",
                "+    if result.needs_retry:",
                "+        audit_retry(request.id)",
                "+        return retry(result)",
                "+    return result",
                "",
            ]
        )
    return "\n".join(chunks)


def _source_file() -> str:
    head = [
        "from __future__ import annotations",
        "",
        "from dataclasses import dataclass",
        "from typing import Any",
        "",
        "",
        "@dataclass",
        "class ContextDecision:",
        "    source: str",
        "    reason: str",
        "    evidence_ptr: str",
        "    tokens_saved: int",
        "",
        "",
        "class ContextGovernor:",
        "    def __init__(self, policy: dict[str, Any]):",
        "        self.policy = policy",
        "",
    ]
    funcs: list[str] = []
    for idx in range(1, 90):
        funcs.extend(
            [
                f"    def route_signal_{idx}(self, observation: dict[str, Any]) -> ContextDecision:",
                f"        reason = observation.get('reason') or 'demo route {idx}'",
                f"        source = observation.get('source') or 'tool-output-{idx}'",
                "        if observation.get('is_stale'):",
                "            reason = 'stale signal tombstoned before injection'",
                "        return ContextDecision(source=source, reason=reason, evidence_ptr='R-demo', tokens_saved=1024)",
                "",
            ]
        )
    return "\n".join(head + funcs)


def _case_from_bash(name: str, command: str, stdout: str, ptr: str, decision: str) -> TokenRouterCase:
    digest_obj = bash_digest.digest_bash(
        cmd=command,
        exit_code=1 if "pytest" in command else 0,
        duration_ms=4312,
        stdout=stdout,
        stderr="",
    )
    rendered = digest_obj.render(ptr)
    raw_tokens = _tokens(stdout)
    digest_tokens = _tokens(rendered)
    saved = max(0, raw_tokens - digest_tokens)
    return TokenRouterCase(
        name=name,
        surface="dhee_bash",
        decision=decision,
        raw_tokens=raw_tokens,
        digest_tokens=digest_tokens,
        saved_tokens=saved,
        saved_pct=round(saved / raw_tokens * 100, 2) if raw_tokens else 0.0,
        ptr=ptr,
        digest=rendered,
        expand=f'dhee_expand_result(ptr="{ptr}")',
    )


def _case_from_read() -> TokenRouterCase:
    text = _source_file()
    digest_obj = read_digest.digest_read(
        "src/context_governor.py",
        text,
        depth="normal",
        query="what decides whether context enters the model",
        task_intent="explain context governance",
    )
    ptr = "R-demo-source"
    rendered = digest_obj.render(ptr)
    raw_tokens = _tokens(text)
    digest_tokens = _tokens(rendered)
    saved = max(0, raw_tokens - digest_tokens)
    return TokenRouterCase(
        name="source file read",
        surface="dhee_read",
        decision="Expose structure, symbols, and task-relevant focus; keep exact source behind a pointer.",
        raw_tokens=raw_tokens,
        digest_tokens=digest_tokens,
        saved_tokens=saved,
        saved_pct=round(saved / raw_tokens * 100, 2) if raw_tokens else 0.0,
        ptr=ptr,
        digest=rendered,
        expand=f'dhee_expand_result(ptr="{ptr}")',
    )


def token_router_demo() -> dict[str, Any]:
    """Return a deterministic token-router demo report."""
    cases = [
        _case_from_bash(
            "pytest failure log",
            "pytest tests/test_checkout.py -q",
            _pytest_output(),
            "B-demo-pytest",
            "Show pass/fail counts and first failure; hide repetitive debug logs.",
        ),
        _case_from_bash(
            "large git diff",
            "git diff src tests",
            _git_diff_output(),
            "B-demo-diff",
            "Show changed-file and hunk totals; keep raw patch available only on demand.",
        ),
        _case_from_read(),
    ]
    raw = sum(case.raw_tokens for case in cases)
    digest = sum(case.digest_tokens for case in cases)
    saved = max(0, raw - digest)
    return {
        "format": "dhee_token_router_demo",
        "version": 1,
        "positioning": "Dhee is the context firewall for AI coding agents: the agent sees the right thing, not everything.",
        "aggregate": {
            "cases": len(cases),
            "raw_tokens": raw,
            "digest_tokens": digest,
            "saved_tokens": saved,
            "saved_pct": round(saved / raw * 100, 2) if raw else 0.0,
        },
        "cases": [asdict(case) for case in cases],
        "next_step": "Run real reports with `dhee router report` or harvest replay fixtures with `dhee router harvest`.",
    }


def _preview(text: str, *, lines: int = 12) -> str:
    parts = text.splitlines()
    shown = parts[:lines]
    if len(parts) > lines:
        shown.append(f"... ({len(parts) - lines} more digest lines)")
    return "\n".join(shown)


def format_token_router_demo(report: dict[str, Any], *, show_digests: bool = True) -> str:
    aggregate = report.get("aggregate") or {}
    lines = [
        "Dhee token-router demo",
        "  context firewall: agent sees the right thing, not everything",
        f"  raw tokens:       {aggregate.get('raw_tokens', 0):,}",
        f"  digest tokens:    {aggregate.get('digest_tokens', 0):,}",
        f"  saved:            {aggregate.get('saved_tokens', 0):,} ({aggregate.get('saved_pct', 0.0):.1f}%)",
        "",
    ]
    for case in report.get("cases", []):
        lines.extend(
            [
                f"[{case['surface']}] {case['name']}",
                f"  decision: {case['decision']}",
                f"  raw -> digest: {case['raw_tokens']:,} -> {case['digest_tokens']:,} tokens",
                f"  saved: {case['saved_tokens']:,} ({case['saved_pct']:.1f}%)",
                f"  evidence: {case['expand']}",
            ]
        )
        if show_digests:
            lines.append("  what the agent sees:")
            for digest_line in _preview(case["digest"]).splitlines():
                lines.append(f"    {digest_line}")
        lines.append("")
    lines.append(str(report.get("next_step") or ""))
    return "\n".join(lines).rstrip()
