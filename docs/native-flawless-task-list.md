# Dhee Native Flawless Task List

Last audited: 2026-05-20

This list is intentionally strict. "Fully done" means the current repo has a working, tested V1 implementation for that capability. "Partially done" means Dhee has useful infrastructure, but it is not strong enough to claim native-flawless or premium-release completeness. "Missing" means no production implementation exists yet.

## Fully Done

- [x] Contract compiler V1: Dhee compiles a task into a deterministic task contract with goal, repo, mode, risk, allowed and forbidden paths, success criteria, rollback plan, context budget, action bytecode, and validation.
- [x] Action bytecode V1: actions are no longer free-form plans only; they compile into typed READ_FILE, SEARCH_CODE, LSP_SYMBOL, RUN_TEST, EDIT_FILE, ASK_USER, SPAWN_SUBAGENT, WRITE_MEMORY_NOTE, and SUBMIT_PATCH records with preconditions, execution notes, observations, postconditions, and memory update policy.
- [x] Supervisor V1: contract_supervisor validates actions against the active contract, enforces edit proof obligations, blocks obvious submit risks, records events, writes checkpoints, and builds proof bundles.
- [x] Repo intelligence store V1: Dhee now persists a git-SHA scoped repo brain under `.dhee/context/repo_brain/` with file manifest, Python AST symbols, imports, static call edges, test map, dependency graph, setup commands, risky files, dirty paths, and historical failure signature slots.
- [x] SWE localizer V1: Dhee now ranks candidate files and symbols using issue tokens, path matches, AST symbols, dirty state, test mapping, and historical failure signals.
- [x] VerificationCard compiler V1: task contracts now include fail-to-pass, pass-to-pass, nearest tests, import smoke checks, static checks, security checks, risk notes, and coverage gaps.
- [x] Repo brain CLI and MCP V1: `dhee context repo-brain index/show/localize` and MCP tools `dhee_repo_brain_index`, `dhee_repo_brain_get`, and `dhee_repo_brain_localize` exist.
- [x] Runtime persistence hardening V1: runtime JSON writes and event logs use atomic or checked persistence paths instead of quiet best-effort writes.
- [x] Contract runtime doctor V1: Dhee can report whether contract runtime protection is protected, partially protected, or unprotected.
- [x] Codex approval usability fix: Dhee tool approval config now uses supported Codex modes such as `auto`, not invalid values like `never`.

## Partially Done

- [ ] Full repo intelligence store: V1 is real, but not yet a complete SWE brain. Missing LSP, tree-sitter, cross-language symbols, richer call graph, incremental indexing, coverage map, flaky-test mining, and deeper historical failure extraction.
- [ ] Full SWE localizer: V1 is a serious static localizer, but it is not yet a multi-signal production localizer with LSP references, tree-sitter spans, stacktrace parsing, coverage, git blame, failure clusters, and confidence calibration.
- [ ] Verification engine: VerificationCard exists, and proof bundles check required tests and changed paths, but Dhee does not yet run a full verifier suite itself with fail-to-pass, pass-to-pass, nearest tests, static checks, security checks, import smoke checks, and structured verdicts.
- [ ] Replay system: Dhee has replay checkpoints and transcript/router replay, but not SWE-Replay style branch execution across isolated worktrees.
- [ ] Patch families: contracts describe minimal_fix, semantic_fix, edge_case_fix, regression_safe_fix, and alternative_hypothesis, but Dhee does not yet generate, execute, compare, and rank multiple patch candidates.
- [ ] Skill promotion gate: Dhee has gate primitives and outcome tracking, but not true intervention-tested A/B promotion for coding skills.
- [ ] Predictive critic: SceneWorld and pattern hooks exist, but there is no trained or validated action ranker that predicts success, failure class, token cost, time cost, or regression risk.
- [ ] Contamination firewall: Dhee records contamination status and proof-bundle fields, but does not yet enforce benchmark quarantine with hard provenance rules across all memory and context surfaces.
- [ ] Private eval harness: Dhee has benchmark and replay utilities, but not a private Dhee/Chotu SWE eval harness that measures native-flawless behavior end to end.
- [ ] Chotu/Kimi no-bypass integration: Dhee can supervise through its own contract runtime, but external Chotu/Kimi tool calls are not yet cryptographically or architecturally forced through Dhee.

## Still Missing For Native Flawless

- [ ] LSP and tree-sitter backed indexing and localization.
- [ ] Worktree-based patch candidate executor.
- [ ] Patch ranker with pass probability, regression risk, and overfit risk.
- [ ] Replay branching engine for isolated failed-attempt reuse.
- [ ] Full verifier runner with structured VerificationCard execution.
- [ ] Benchmark contamination firewall with hard provenance, quarantine, and benchmark-mode policy.
- [ ] Private Dhee/Chotu SWE eval harness.
- [ ] True skill A/B promotion pipeline.
- [ ] Hard Chotu/Kimi integration so coding agents cannot bypass Dhee in protected mode.
- [ ] Release gate that refuses a premium release unless install, package, runtime doctor, MCP parity, full tests, and git cleanliness all pass.

## Honest Release Verdict

Dhee is no longer just a memory layer or a top-k retrieval system. It now has a real compiler/proof-runtime spine and a useful V1 repo brain.

It is not yet native-flawless. The current state is best described as a strong internal alpha or serious developer preview for the compiler runtime. It should not be marketed as an Apple-grade premium release until the missing verifier runner, worktree patch execution, contamination firewall, eval harness, and Chotu/Kimi hard integration are implemented and tested.

## Audit Evidence

- Focused repo intelligence tests run during this audit: `python3 -m pytest -q tests/test_repo_intelligence.py` -> `4 passed`.
- Latest previously recorded broader verification from implementation work: compileall passed; focused compiler/MCP/runtime suite passed; full suite passed with skipped tests and warnings.
