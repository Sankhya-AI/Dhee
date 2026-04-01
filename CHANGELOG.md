# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [3.0.0] - 2026-04-01 — Event-Sourced Cognition Substrate

Dhee v3 is a ground-up architectural overhaul that transforms the memory layer into an immutable-event, versioned cognition substrate. Raw memory is now immutable truth; derived cognition (beliefs, policies, insights, heuristics) is provisional, rebuildable, and auditable.

### New Architecture

- **Immutable raw events** — `remember()` writes to `raw_memory_events`. Corrections create new events with `supersedes_event_id`, never mutate originals.
- **Type-specific derived stores** — Beliefs, Policies, Anchors, Insights, Heuristics each have their own table with type-appropriate schemas, indexes, lifecycle rules, and invalidation behavior.
- **Derived lineage** — Every derived object traces to its source raw events via `derived_lineage` table with contribution weights.
- **Candidate promotion pipeline** — Consolidation no longer writes through `memory.add()`. Distillation produces candidates; promotion validates, dedupes, and transactionally promotes them to typed stores.

### Three-Tier Invalidation

- **Hard invalidation** — Source deleted: derived objects tombstoned, excluded from retrieval.
- **Soft invalidation** — Source corrected: derived objects marked stale, repair job enqueued.
- **Partial invalidation** — One of N sources changed with contribution weight < 30%: confidence penalty, not full re-derive. Weight >= 30% escalates to soft invalidation.

### 5-Stage RRF Fusion Retrieval

1. Per-index retrieval (raw, distilled, episodic — parallel, zero LLM)
2. Min-max score normalization within each index
3. Weighted Reciprocal Rank Fusion (k=60, distilled=1.0, episodic=0.7, raw=0.5)
4. Post-fusion adjustments (recency boost, confidence normalization, staleness penalty, invalidation exclusion, contradiction penalty)
5. Dedup + final ranking — zero LLM calls on the hot path

### Conflict Handling

- **Cognitive conflicts table** — Contradictions are explicit rows, not silent resolution.
- **Auto-resolution** — When confidence gap >= 0.5 (one side >= 0.8, other <= 0.3), auto-resolve in favor of high-confidence side.

### Job Registry

- Replaces phantom `agi_loop.py` with real, observable maintenance jobs.
- SQLite lease manager prevents concurrent execution of same job.
- Jobs are named, idempotent, leasable, retryable, and independently testable.

### Anchor Resolution

- Per-field anchor candidates with individual confidence scores.
- Re-anchoring: corrections re-resolve without touching raw events.

### Materialized Read Model

- `retrieval_view` materialized table for fast cold-path queries.
- Delta overlay for hot-path freshness.

### Migration Bridge

- Dual-write: v2 path + v3 raw events in parallel (`DHEE_V3_WRITE=1`, default on).
- Backfill: idempotent migration of v2 memories into v3 raw events via content-hash dedup.
- Feature flag `DHEE_V3_READ` (default off) for gradual cutover.

### Observability

- `v3_health()` reports: raw event counts, derived invalidation counts per type, open conflicts, active leases, candidate stats, job health, retrieval view freshness, lineage coverage.

### Consolidation Safety

- Breaks the feedback loop: `_promote_to_passive()` uses `infer=False`, tags `source="consolidated"`.
- `_should_promote()` rejects already-consolidated content.

### Other Changes

- `UniversalEngram.to_dict(sparse=True)` — omits None, empty strings, empty lists, empty dicts.
- `agi_loop.py` cleaned: removed all phantom `engram_*` package imports.
- 913 tests passing.

### New Files

- `dhee/core/storage.py` — Schema DDL for all v3 tables
- `dhee/core/events.py` — RawEventStore
- `dhee/core/derived_store.py` — BeliefStore, PolicyStore, AnchorStore, InsightStore, HeuristicStore, DerivedLineageStore, CognitionStore
- `dhee/core/anchor_resolver.py` — AnchorCandidateStore, AnchorResolver
- `dhee/core/invalidation.py` — Three-tier InvalidationEngine
- `dhee/core/conflicts.py` — ConflictStore with auto-resolution
- `dhee/core/read_model.py` — Materialized ReadModel
- `dhee/core/fusion_v3.py` — 5-stage RRF fusion pipeline
- `dhee/core/v3_health.py` — Observability metrics
- `dhee/core/v3_migration.py` — Dual-write bridge + backfill
- `dhee/core/lease_manager.py` — SQLite lease manager
- `dhee/core/jobs.py` — JobRegistry + concrete jobs
- `dhee/core/promotion.py` — PromotionEngine

---

## [2.2.0b1] - 2026-03-31 — Architectural Cleanup

Beta release focused on internal discipline rather than new features.

### Changed — Architecture

- **main.py decomposition**: Extracted `SearchPipeline`, `MemoryWritePipeline`, `OrchestrationEngine` from the 6,129-line monolith. main.py is now ~3,100 lines (49% reduction).
- **Public surface**: `dhee/__init__.py` rewritten for clean, narrow exports. `Memory = CoreMemory` (not FullMemory). Cognitive subsystems intentionally kept internal.
- **MCP split**: `mcp_slim.py` (4-tool product surface) vs `mcp_server.py` (24-tool power surface). Clear separation of concerns.

### Changed — Rename Debt

- All `FADEM_*` env vars → `DHEE_*` (with `FADEM_*` fallback for backward compat).
- Internal `fadem_config` → `fade_config` across memory package.
- Default collection name `fadem_memories` → `dhee_memories`.
- Config field `MemoryConfig.engram` → `MemoryConfig.fade`.
- CLI, MCP server, observability, presets: all `engram` product references removed.

### Added — D2Skill Policy Improvements

- **Dual-granularity policies**: `PolicyGranularity.TASK` (strategy) vs `PolicyGranularity.STEP` (local correction). Inspired by D2Skill (arXiv:2603.28716).
- **Utility scoring**: EMA-smoothed performance delta tracking on policies. Three-signal retrieval ranking (condition match + sigmoid utility + UCB exploration bonus).
- **Utility-based pruning**: `PolicyStore.prune()` removes deprecated policies first, protects validated ones.
- **Buddhi wiring**: `reflect()` accepts `outcome_score`, computes baseline vs actual delta, feeds it to policy `record_outcome()`.

### Fixed

- `buddhi.py`: Replaced dead `memory.get_last_session_digest()` call with working `get_last_session()` import.
- `mcp_server.py`: Fixed wrong "8 tools total" comment (actually 24), fixed `-> Memory` type hints (Memory not imported).
- CLI: Wired `benchmark` command into parser (existed but was unreachable).
- `PolicyStore.prune()`: Fixed bug where `candidates.pop()` pruned validated policies instead of deprecated ones.
- CHANGELOG 2.1.0: Removed false "Production/Stable" and "A-grade" claims.

### Changed — Packaging

- Version: 2.1.0 → 2.2.0b1
- Classifier: `Development Status :: 4 - Beta` (was falsely claiming Production/Stable)

---

## [2.1.0] - 2026-03-30 — Cognition Primitives

Dhee V2.1: Adds first-class cognitive primitives (episodes, tasks, policies, beliefs, triggers) and a 60-test suite that exercises them. These are internal building blocks — the public API remains the 4-operation surface (remember/recall/context/checkpoint).

### Added — Cognitive Primitives (internal, importable from `dhee.core`)

- **Episode System** (`dhee/core/episode.py`): Temporal unit of agent experience. Lifecycle: open→active→closed→archived→forgotten. Boundary detection via time gap and topic shift. Utility-based selective forgetting. JSON-file-backed persistence (suitable for local-first use; production systems should own their own persistence).
- **Task State** (`dhee/core/task_state.py`): Structured task tracking with goal/plan/progress/blockers/outcome. Step-level tracking. Blocker management. Plan success rate analysis.
- **Policy Cases** (`dhee/core/policy.py`): Outcome-linked condition→action rules with Wilson score confidence. Auto-promotion and auto-deprecation based on win rate.
- **Belief Tracking** (`dhee/core/belief.py`): Confidence updates with evidence tracking. Contradiction detection via keyword overlap + negation patterns. Revision history.
- **Trigger System** (`dhee/core/trigger.py`): 5 trigger types (keyword, time, event, composite, sequence) returning `TriggerResult(fired, confidence, reason)`. Backwards-compatible bridge from legacy intentions.
- **Test Suite** (`tests/test_cognition_v3.py`): 60 tests covering cognition primitives, contrastive pairs, heuristic distillation, meta-learning, and Buddhi pipeline wiring.

### Changed

- **Buddhi**: HyperContext expanded with episodes, task_states, policies, beliefs. `reflect()` creates contrastive pairs, distills heuristics, extracts policies, updates beliefs.
- **DheePlugin**: `checkpoint()` handles episode closure and task lifecycle. System prompt renderer includes strategies, beliefs, tasks, experience.
- **Version**: 2.0.0 → 2.1.0

---

## [2.0.0] - 2026-03-30

Dhee V2: Self-Evolving Cognition Plugin. This release transforms Dhee from a memory layer into a **self-improving cognition plugin** that can make any agent — local or cloud, software or embodied — a HyperAgent that gets better with every interaction.

### Added — Phase 1: Universal Plugin

- **DheePlugin** (`dhee/adapters/base.py`): Framework-agnostic entry point wrapping Engram + Buddhi behind 4 tools (remember/recall/context/checkpoint), with session lifecycle (frozen snapshot pattern) and trajectory recording for skill mining.
- **DheeEdge** (`dhee/edge/`): Minimal-footprint offline plugin for hardware/humanoid deployment. All-local inference (GGUF + ONNX), embodiment hooks (`on_sensor_input`, `on_action_result`, `predict_environment`), <500MB working set.
- **BuddhiMini** (`dhee/mini/`): Scaffold for trainable model with 3 new task heads (`[MEMORY_OP]`, `[HEURISTIC]`, `[RETRIEVAL_JUDGE]`) on top of DheeModel. Includes `TraceSegmenter` that splits agent trajectories into `[REASON]/[ACT]/[MEMORY_OP]` spans for structured training data.
- Export `DheePlugin` from `dhee.__init__` and `dhee/adapters/__init__`.
- `pyproject.toml`: Added `edge` optional dependency group.
- `SamskaraCollector.get_training_data()`: Exports SFT samples, DPO pairs, and vasana reports for the training pipeline.
- `DheeLLM`: 3 new convenience methods (`classify_memory_op`, `generate_heuristic`, `judge_retrieval`).

### Added — Phase 2: Self-Evolving Cognition

- **ContrastiveStore** (`dhee/core/contrastive.py`): Success/failure pair storage with MaTTS re-ranking. Inspired by *ReasoningBank* (arXiv:2509.25140). Auto-creates pairs from `checkpoint(what_worked=..., what_failed=...)`. Exports DPO training pairs.
- **HeuristicDistiller** (`dhee/core/heuristic.py`): Distills abstract reasoning patterns at 3 levels (specific / domain / universal) from agent trajectories. Inspired by *ERL: Efficient Reinforcement Learning* (arXiv:2603.24639). Deduplicates via Jaccard similarity.
- **MetaBuddhi** (`dhee/core/meta_buddhi.py`): Self-referential cognition loop — proposes retrieval strategy mutations, evaluates them against samskara signals, promotes or rolls back. Inspired by *DGM-Hyperagents* (arXiv:2603.19461). The improvement procedure can improve itself.
- **RetrievalStrategy** (`dhee/core/strategy.py`): Versioned scoring weights stored as human-readable JSON files. Tunable knobs: semantic/keyword weights, recency boost, contrastive boost, heuristic relevance, context budgets.
- **ProgressiveTrainer** (`dhee/mini/progressive_trainer.py`): 3-stage training pipeline (SFT → DPO → RL gate). Inspired by *AgeMem* (arXiv:2601.01885). Weights samples by vasana degradation signals. Minimum thresholds prevent training on insufficient data.
- **HyperContext** gains `contrasts` and `heuristics` fields — agents now receive contrastive evidence (do/avoid) and learned heuristics at session start.
- **Buddhi** auto-wiring: `reflect()` auto-creates contrastive pairs and distills heuristics. `get_hyper_context()` populates contrasts and heuristics.
- **HybridSearcher**: Added `contrastive_boost` parameter — results aligned with past successes score higher.
- **EvolutionLayer**: Now runs dual loops — Nididhyasana (model training) + MetaBuddhi (strategy improvement).
- **SkillMiner**: Triggers heuristic distillation after successful skill mining.

### Added — Phase 3: Scale

- **EvolvingGraph** (`dhee/core/graph_evolution.py`): Extends KnowledgeGraph with entity versioning (append-only JSONL), personalized PageRank per user/agent, and schema-free entity extraction via LLM (entities are typed as `DYNAMIC` when they don't match the fixed schema).
- **HiveMemory** (`dhee/hive/hive_memory.py`): Multi-agent shared cognition on top of engram-bus. Agents publish insights, heuristics, and skills to the hive. Quality gating via Wilson score lower bound. Voting and adoption tracking.
- **CRDT Sync** (`dhee/hive/sync.py`): Offline/edge sync protocol. LWW-Register for content, G-Counter for votes, OR-Set for adoption lists. `SyncEnvelope` wire format (JSON over bytes). Nodes converge after arbitrary offline periods.
- **Framework Adapters**:
  - `dhee/adapters/openai_funcs.py` — `OpenAIToolAdapter` with `tool_definitions()` and `execute()` dispatch. Works with any API-compatible provider.
  - `dhee/adapters/langchain.py` — `get_dhee_tools()` returns 4 LangChain `BaseTool` instances. Lazy import — no hard dependency.
  - `dhee/adapters/autogen.py` — `get_autogen_functions()` for v0.2, `get_autogen_tool_specs()` for v0.4+. `register_dhee_tools()` for auto-registration.
  - `dhee/adapters/system_prompt.py` — `generate_snapshot()` renders HyperContext as a frozen system prompt block. Configurable sections, minimal mode for edge.
- **EdgeTrainer** (`dhee/edge/edge_trainer.py`): On-device micro-training. LoRA rank-4, CPU-only, <2GB RAM. Deferred training mode for GGUF models. Vasana-weighted sample emphasis.
- **KnowledgeGraph**: Added `DYNAMIC` entity type, `save()`/`load()` JSON persistence.

### Changed

- **Version**: 1.0.0 → 2.0.0
- **MCP server** (`dhee/mcp_slim.py`): Refactored to wrap `DheePlugin` as backing singleton.
- **pyproject.toml**: Updated description, keywords, classifiers.

### Research References

This release was informed by the following research (March 2026):

| Paper | Key Idea Applied |
|-------|-----------------|
| *DGM-Hyperagents* (arXiv:2603.19461) | Self-referential meta-agents that modify their own improvement procedure → MetaBuddhi |
| *ERL* (arXiv:2603.24639) | Distill trajectories into abstract heuristics, not raw logs → HeuristicDistiller |
| *ReasoningBank* (arXiv:2509.25140) | Contrastive learning from success/failure pairs, MaTTS scoring → ContrastiveStore |
| *AgeMem* (arXiv:2601.01885) | Memory ops as RL-optimized tool calls, 3-stage progressive training → ProgressiveTrainer |
| *Structured Agent Distillation* (arXiv:2505.13820) | [REASON]/[ACT] segmented traces for training small models → TraceSegmenter |

### Migration from V1

V2 is backwards-compatible with V1. Existing code using `Memory`, `Engram`, or `Dhee` classes continues to work unchanged. The new `DheePlugin` is additive — adopt it when you want the self-evolution capabilities.

```python
# V1 (still works)
from dhee import Memory
m = Memory()
m.add("fact")

# V2 (new universal plugin)
from dhee import DheePlugin
p = DheePlugin()
p.remember("fact")
ctx = p.context("what am I working on?")
prompt = p.session_start("fixing auth bug")
```

---

## [1.0.0] - 2026-03-22

### Changed
- Renamed project from Engram to Dhee.
- Clean repository for public push.
- All imports updated (`engram.*` → `dhee.*`).

## [0.4.0] - 2025-02-09

### Added
- Docker support (Dockerfile + docker-compose.yml)
- GitHub Actions CI workflow (Python 3.9, 3.11, 3.12)
- `engram serve` command (alias for `server`)
- `engram status` command (version, config paths, DB stats)
- Landing page waitlist section for hosted cloud
- CHANGELOG.md

### Changed
- Version bump to 0.4.0
- README rewritten as product-focused documentation
- CLI `--version` now pulls from `engram.__version__`
- pyproject.toml updated with Beta classifier and new keywords

## [0.3.0] - 2025-01-15

### Added
- PMK v2: staged writes with policy gateway
- Dual retrieval (semantic + episodic)
- Namespace and agent trust system
- Session tokens with capability scoping
- Sleep-cycle background maintenance
- Reference-aware decay (preserve strongly referenced memories)

## [0.2.0] - 2025-01-01

### Added
- Episodic scenes (CAST grouping with time gap and topic shift detection)
- Character profiles (extraction, self-profile, narrative generation)
- Dashboard visualizer
- Claude Code plugin (hooks, commands, skill)
- OpenClaw integration

## [0.1.0] - 2024-12-01

### Added
- FadeMem: dual-layer memory (SML/LML) with Ebbinghaus decay
- EchoMem: multi-modal encoding (keywords, paraphrases, implications, questions)
- CategoryMem: dynamic hierarchical category organization
- MCP server for Claude Code, Cursor, Codex
- REST API server
- Knowledge graph with entity extraction and linking
- Hybrid search (semantic + keyword)
- CLI with add, search, list, stats, decay, export, import commands
- Ollama support for local LLMs
