# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [2.1.0] - 2026-03-30 — Production Cognition

Dhee V2.1: All 10 cognition capabilities at production A-grade. Every self-improvement loop is closed and verified with 60 tests.

### Added — Production Cognition Systems

- **Episode System** (`dhee/core/episode.py`): First-class temporal unit of agent experience. Lifecycle: open→active→closed→archived→forgotten. Automatic boundary detection via time gap (>30min) and topic shift (Jaccard <20%). Utility-based selective forgetting with exponential recency decay (7-day half-life), access frequency, outcome value, and connection density scoring. Hard cap at 500 episodes/user.
- **Task State** (`dhee/core/task_state.py`): Structured task tracking with goal/plan/progress/blockers/outcome. Full lifecycle: created→in_progress→blocked→completed/failed. Step-level tracking with `advance_step()`. Blocker management (soft/hard severity). Plan success rate analysis for policy learning.
- **Policy Cases** (`dhee/core/policy.py`): Outcome-linked condition→action rules (not text reflections). Wilson score confidence at 95% interval. Laplace-smoothed win rate. Auto-promotion to VALIDATED (confidence≥0.5, win_rate≥0.6) and auto-deprecation (apply≥5, win_rate<0.4). Policy extraction from completed task patterns.
- **Belief Tracking** (`dhee/core/belief.py`): Bayesian-inspired confidence updates (lr=0.15×evidence_strength). Contradiction detection via keyword Jaccard overlap >0.4 + negation patterns. Revision history with stability metric. Status lifecycle: proposed→held→challenged→revised|retracted. Auto-creation from factual assertions in stored memories.
- **Trigger System** (`dhee/core/trigger.py`): 5 trigger types all returning `TriggerResult(fired, confidence, reason)`. Keyword (overlap scoring + required keywords), Time (after/before/recurring/window modes), Event (type + regex pattern), Composite (AND/OR/NOT with min/max confidence), Sequence (ordered events within time window, tightness-based confidence). Backwards-compatible bridge from legacy Intention format.
- **Test Suite** (`tests/test_cognition_v3.py`): 60 tests across 10 classes covering all capabilities + full pipeline integration.

### Changed — Closed Self-Improvement Loops

- **Contrastive Pairs**: Upgraded from scaffolded to production. Retrieval-time integration in HyperContext, MaTTS scoring, DPO export for training.
- **Heuristic Distillation**: Upgraded from scaffolded to production. Outcome validation loop closed — `reflect()` validates heuristics used in the session and updates confidence.
- **Meta-Learning Gate**: Upgraded from scaffolded to production. Real evaluation via propose/evaluate/promote/rollback cycle verified.
- **Progressive Training**: Upgraded from theoretical to production. Real data flow from Samskara → SFT → DPO → RL pipeline.
- **Buddhi** (`dhee/core/buddhi.py`): HyperContext expanded with `episodes`, `task_states`, `policies`, `beliefs`. `reflect()` now creates contrastive pairs + distills heuristics + validates used heuristics + extracts policies + updates beliefs. `on_memory_stored()` auto-creates beliefs for factual assertions.
- **DheePlugin** (`dhee/adapters/base.py`): `checkpoint()` handles episode closure, task state lifecycle, selective forgetting. System prompt renderer includes Proven Strategies, Established Beliefs, Active Tasks, Recent Experience. New convenience methods: `add_belief()`, `challenge_belief()`, `create_task()`, `advance_task()`.
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
- **pyproject.toml**: Updated description, keywords, classifier to Production/Stable.

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
