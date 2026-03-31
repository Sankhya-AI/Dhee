# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

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
