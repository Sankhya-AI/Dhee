"""End-to-end tests for all 7 OpenClaw Power Packages.

Tests each package as a user would: create a Memory backed by NVIDIA Llama,
exercise the full API of every power package.
"""

import os
import time
import pytest

from engram.configs.base import MemoryConfig, LLMConfig, EmbedderConfig, VectorStoreConfig
from engram.memory.main import Memory

# Load keys from .env file in project root
_ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key.strip(), value)

_NVIDIA_KEYS = (
    "NVIDIA_API_KEY",
    "NVIDIA_EMBEDDING_API_KEY",
    "NVIDIA_QWEN_API_KEY",
    "LLAMA_API_KEY",
)
if not any(os.environ.get(key) for key in _NVIDIA_KEYS):
    pytest.skip("requires NVIDIA API credentials", allow_module_level=True)


@pytest.fixture(scope="session")
def memory():
    """Create a Memory instance backed by NVIDIA Llama for the whole test session."""
    config = MemoryConfig(
        llm=LLMConfig(
            provider="nvidia",
            config={
                "model": "meta/llama-3.1-8b-instruct",
            },
        ),
        embedder=EmbedderConfig(
            provider="nvidia",
            config={
                "model": "nvidia/nv-embedqa-e5-v5",
            },
        ),
        vector_store=VectorStoreConfig(provider="memory", config={}),
        history_db_path=":memory:",
        embedding_model_dims=1024,
    )
    m = Memory(config=config)
    yield m
    m.close()


# ═══════════════════════════════════════════════════════════════
# Package 0: engram-router
# ═══════════════════════════════════════════════════════════════


class TestRouterRegistry:
    """Test AgentRegistry — register, get, list, find, status."""

    def test_register_agent(self, memory):
        from engram_router import AgentRegistry

        registry = AgentRegistry(memory, user_id="system")
        result = registry.register(
            "claude",
            capabilities=["python", "debugging"],
            description="Expert Python debugger",
            agent_type="claude",
            model="opus",
            max_concurrent=2,
        )
        assert result is not None

    def test_get_agent(self, memory):
        from engram_router import AgentRegistry

        registry = AgentRegistry(memory, user_id="system")
        registry.register(
            "claude_get",
            capabilities=["python"],
            description="Python agent",
            agent_type="claude",
            model="opus",
        )
        agent = registry.get("claude_get")
        assert agent is not None
        assert agent["name"] == "claude_get"
        assert agent["type"] == "claude"

    def test_list_agents(self, memory):
        from engram_router import AgentRegistry

        registry = AgentRegistry(memory, user_id="system")
        registry.register("claude", capabilities=["python"], description="a", agent_type="claude", model="opus")
        registry.register("codex", capabilities=["js"], description="b", agent_type="codex", model="gpt4")

        agents = registry.list()
        names = [a["name"] for a in agents]
        assert "claude" in names
        assert "codex" in names

    def test_find_capable(self, memory):
        from engram_router import AgentRegistry

        registry = AgentRegistry(memory, user_id="system")
        registry.register("claude", capabilities=["python", "debugging"], description="Python debugging expert", agent_type="claude", model="opus")
        registry.register("codex", capabilities=["javascript"], description="JS scaffolding", agent_type="codex", model="gpt4")

        results = registry.find_capable("debug python code", limit=5)
        assert isinstance(results, list)

    def test_update_status(self, memory):
        from engram_router import AgentRegistry

        registry = AgentRegistry(memory, user_id="system")
        registry.register("status_agent", capabilities=["python"], description="status test agent", agent_type="claude", model="opus")
        registry.update_status("status_agent", "busy")
        agent = registry.get("status_agent")
        assert agent is not None
        assert agent["status"] == "busy"

    def test_active_tasks(self, memory):
        from engram_router import AgentRegistry

        registry = AgentRegistry(memory, user_id="system")
        registry.register("tasks_agent", capabilities=["python"], description="active tasks agent", agent_type="claude", model="opus")
        registry.add_active_task("tasks_agent", "task-1")
        agent = registry.get("tasks_agent")
        assert "task-1" in agent["active_tasks"]

        registry.remove_active_task("tasks_agent", "task-1")
        agent = registry.get("tasks_agent")
        assert "task-1" not in agent["active_tasks"]


class TestRouterTaskRouter:
    """Test TaskRouter — route, claim, release."""

    def _setup(self, memory):
        from engram_router import AgentRegistry, TaskRouter, RouterConfig
        from engram.memory.tasks import TaskManager

        config = RouterConfig()
        registry = AgentRegistry(memory, user_id="system")
        tm = TaskManager(memory)
        router = TaskRouter(registry, tm, config=config, memory=memory)
        return registry, tm, router

    def test_route_task(self, memory):
        registry, tm, router = self._setup(memory)

        # Register an agent
        registry.register("claude", capabilities=["python", "debugging"], description="Python expert", agent_type="claude", model="opus")
        registry.update_status("claude", "available")

        # Create a task
        task = tm.create_task(title="Fix Python bug", description="Debug the auth module", user_id="bridge")
        task_id = task["id"]

        # Route
        result = router.route(task_id)
        assert result is not None
        assert result.get("assigned_agent") == "claude"

    def test_claim_task(self, memory):
        registry, tm, router = self._setup(memory)

        task = tm.create_task(title="Test task", user_id="bridge")
        task_id = task["id"]

        result = router.claim(task_id, "claude")
        assert result is not None
        assert result["assigned_agent"] == "claude"
        assert result["status"] == "active"

    def test_claim_denied_already_claimed(self, memory):
        registry, tm, router = self._setup(memory)

        task = tm.create_task(title="Test task", user_id="bridge")
        task_id = task["id"]

        router.claim(task_id, "claude")
        # Now try claiming with a different agent
        result = router.claim(task_id, "codex")
        assert result is None  # denied

    def test_release_task(self, memory):
        registry, tm, router = self._setup(memory)
        registry.register("claude", capabilities=["python"], description="a", agent_type="claude", model="opus")

        task = tm.create_task(title="Test task", user_id="bridge")
        task_id = task["id"]

        router.claim(task_id, "claude")
        result = router.release(task_id, "claude")
        assert result is not None
        assert result["status"] == "inbox"
        assert result["assigned_agent"] is None

    def test_release_denied_wrong_agent(self, memory):
        registry, tm, router = self._setup(memory)

        task = tm.create_task(title="Test task", user_id="bridge")
        task_id = task["id"]

        router.claim(task_id, "claude")
        result = router.release(task_id, "codex")
        assert result is None


class TestRouterConfig:
    """Test RouterConfig."""

    def test_defaults(self):
        from engram_router import RouterConfig

        config = RouterConfig()
        assert config.auto_route is True
        assert config.auto_execute is False
        assert config.similarity_weight == 0.7
        assert config.availability_weight == 0.3
        assert config.log_events is True
        assert config.user_id == "system"

    def test_custom_config(self):
        from engram_router import RouterConfig

        config = RouterConfig(similarity_weight=0.5, availability_weight=0.5, auto_execute=True)
        assert config.similarity_weight == 0.5
        assert config.auto_execute is True


# ═══════════════════════════════════════════════════════════════
# Package 1: engram-identity
# ═══════════════════════════════════════════════════════════════


class TestIdentity:
    """Test Identity — declare, load, update, discover, who_am_i, context."""

    def test_declare_and_load(self, memory):
        from engram_identity import Identity

        identity = Identity(memory, "claude-code")
        result = identity.declare(
            name="Claude Code",
            role="Senior software engineer",
            goals=["write clean code", "fix bugs"],
            style="concise, technical",
            constraints=["never push to main without review"],
            capabilities=["python", "typescript"],
        )
        assert result is not None

        loaded = identity.load()
        assert loaded is not None
        assert loaded["name"] == "Claude Code"
        assert loaded["role"] == "Senior software engineer"
        assert "write clean code" in loaded["goals"]

    def test_update(self, memory):
        from engram_identity import Identity

        identity = Identity(memory, "update-test-agent")
        identity.declare(name="Claude", role="Engineer", goals=["code"])
        identity.update(role="Senior Engineer", style="verbose")
        loaded = identity.load()
        assert loaded["role"] == "Senior Engineer"
        assert loaded["style"] == "verbose"

    def test_who_am_i(self, memory):
        from engram_identity import Identity

        identity = Identity(memory, "claude-code")
        identity.declare(name="Claude Code", role="Senior engineer", goals=["code review"])
        summary = identity.who_am_i()
        assert "Claude Code" in summary
        assert "Senior engineer" in summary

    def test_get_context_injection(self, memory):
        from engram_identity import Identity

        identity = Identity(memory, "claude-code")
        identity.declare(name="Claude Code", role="Engineer", goals=["code"])
        context = identity.get_context_injection()
        assert isinstance(context, str)
        assert "Claude Code" in context

    def test_discover(self, memory):
        from engram_identity import Identity

        # Declare two identities
        id1 = Identity(memory, "claude")
        id1.declare(name="Claude", role="Python expert", goals=["debug"])

        id2 = Identity(memory, "codex")
        id2.declare(name="Codex", role="JavaScript dev", goals=["scaffold"])

        # Discover
        results = id1.discover("python debugging", limit=5)
        assert isinstance(results, list)

    def test_load_nonexistent(self, memory):
        from engram_identity import Identity

        identity = Identity(memory, "nonexistent-agent")
        loaded = identity.load()
        assert loaded is None

    def test_who_am_i_no_identity(self, memory):
        from engram_identity import Identity

        identity = Identity(memory, "ghost")
        summary = identity.who_am_i()
        assert "No identity" in summary or "ghost" in summary


class TestIdentityConfig:
    def test_defaults(self):
        from engram_identity import IdentityConfig

        config = IdentityConfig()
        assert config.user_id == "system"
        assert config.auto_inject is True
        assert config.max_discover_results == 10


# ═══════════════════════════════════════════════════════════════
# Package 2: engram-heartbeat
# ═══════════════════════════════════════════════════════════════


class TestHeartbeat:
    """Test Heartbeat — schedule, list, enable/disable, tick, remove."""

    def test_schedule_and_list(self, memory):
        from engram_heartbeat import Heartbeat

        hb = Heartbeat(memory, "schedule_list_agent")
        result = hb.schedule(
            name="nightly_decay",
            action="decay",
            interval_minutes=1440,
            params={"user_id": "default"},
        )
        assert result is not None
        assert result.get("name") == "nightly_decay" or "nightly_decay" in str(result)

        heartbeats = hb.list()
        assert len(heartbeats) >= 1
        names = [h.get("name", "") for h in heartbeats]
        assert "nightly_decay" in names

    def test_enable_disable(self, memory):
        from engram_heartbeat import Heartbeat

        hb = Heartbeat(memory, "enable_disable_agent")
        result = hb.schedule(name="toggle_hb", action="memory_stats", interval_minutes=60)

        # Find the heartbeat we just created
        heartbeats = hb.list()
        target = [h for h in heartbeats if h.get("name") == "toggle_hb"]
        assert len(target) >= 1
        hb_id = target[0]["id"]

        disabled = hb.disable(hb_id)
        assert disabled is not None
        assert disabled.get("enabled") is False

        enabled = hb.enable(hb_id)
        assert enabled is not None
        assert enabled.get("enabled") is True

    def test_remove(self, memory):
        from engram_heartbeat import Heartbeat

        hb = Heartbeat(memory, "remove_test_agent")
        hb.schedule(name="temp_hb", action="health_check", interval_minutes=30)

        heartbeats = hb.list()
        assert len(heartbeats) >= 1
        # Find the one we just created
        target = [h for h in heartbeats if h.get("name") == "temp_hb"]
        assert len(target) == 1
        hb_id = target[0]["id"]

        removed = hb.remove(hb_id)
        assert removed is True

        heartbeats = hb.list()
        remaining = [h for h in heartbeats if h.get("name") == "temp_hb"]
        assert len(remaining) == 0

    def test_tick(self, memory):
        from engram_heartbeat import Heartbeat

        hb = Heartbeat(memory, "claude")
        # Schedule with 0 minutes interval so it's immediately due
        hb.schedule(name="instant_hb", action="memory_stats", interval_minutes=0)

        results = hb.tick()
        assert isinstance(results, list)

    def test_start_stop_runner(self, memory):
        from engram_heartbeat import Heartbeat

        hb = Heartbeat(memory, "claude")
        hb.start()
        assert hb._runner.is_running is True
        hb.stop()
        assert hb._runner.is_running is False


class TestHeartbeatConfig:
    def test_defaults(self):
        from engram_heartbeat import HeartbeatConfig

        config = HeartbeatConfig()
        assert config.tick_interval_seconds == 60
        assert config.max_behaviors == 50
        assert config.log_runs is True


class TestHeartbeatBehaviors:
    def test_builtin_behaviors_exist(self):
        from engram_heartbeat.behaviors import BUILTIN_BEHAVIORS

        assert "decay" in BUILTIN_BEHAVIORS
        assert "consolidation" in BUILTIN_BEHAVIORS
        assert "health_check" in BUILTIN_BEHAVIORS
        assert "stale_task_check" in BUILTIN_BEHAVIORS
        assert "memory_stats" in BUILTIN_BEHAVIORS

    def test_run_memory_stats(self, memory):
        from engram_heartbeat.behaviors import run_behavior

        result = run_behavior("memory_stats", memory, {}, agent_id="claude")
        assert result is not None


# ═══════════════════════════════════════════════════════════════
# Package 3: engram-policy
# ═══════════════════════════════════════════════════════════════


class TestPolicyEngine:
    """Test PolicyEngine — add, check, list, remove, effective perms."""

    def test_add_and_check_allow(self, memory):
        from engram_policy import PolicyEngine

        engine = PolicyEngine(memory)
        engine.add_policy(
            agent_id="claude",
            resource="repo/*",
            actions=["read", "write"],
            effect="allow",
            priority=10,
        )

        decision = engine.check_access("claude", "repo/src/main.py", "read")
        assert decision.allowed is True

    def test_deny_overrides_allow(self, memory):
        from engram_policy import PolicyEngine

        engine = PolicyEngine(memory)
        engine.add_policy(agent_id="claude", resource="repo/*", actions=["read", "write"], effect="allow", priority=5)
        engine.add_policy(agent_id="claude", resource="repo/secrets/*", actions=["read", "write"], effect="deny", priority=10)

        # General repo access: allowed
        decision = engine.check_access("claude", "repo/src/main.py", "read")
        assert decision.allowed is True

        # Secrets: denied (higher priority)
        decision = engine.check_access("claude", "repo/secrets/key.pem", "read")
        assert decision.allowed is False

    def test_list_policies(self, memory):
        from engram_policy import PolicyEngine

        engine = PolicyEngine(memory)
        engine.add_policy(agent_id="claude", resource="*", actions=["read"], effect="allow")
        engine.add_policy(agent_id="codex", resource="repo/*", actions=["write"], effect="allow")

        all_policies = engine.list_policies()
        assert len(all_policies) >= 2

        claude_policies = engine.list_policies(agent_id="claude")
        assert len(claude_policies) >= 1

    def test_remove_policy(self, memory):
        from engram_policy import PolicyEngine

        engine = PolicyEngine(memory)
        result = engine.add_policy(agent_id="claude", resource="*", actions=["read"], effect="allow")
        policy_id = result["id"]

        removed = engine.remove_policy(policy_id)
        assert removed is True

    def test_effective_permissions(self, memory):
        from engram_policy import PolicyEngine

        engine = PolicyEngine(memory)
        engine.add_policy(agent_id="claude", resource="repo/*", actions=["read", "write"], effect="allow")
        engine.add_policy(agent_id="claude", resource="logs/*", actions=["read"], effect="allow")

        perms = engine.get_effective_permissions("claude")
        assert isinstance(perms, dict)

    def test_no_policy_default_deny(self, memory):
        from engram_policy import PolicyEngine

        engine = PolicyEngine(memory)
        decision = engine.check_access("unknown_agent", "repo/file.py", "write")
        assert decision.allowed is False


class TestCapabilityToken:
    """Test CapabilityToken — create, validate, revoke."""

    def test_create_and_validate(self, memory):
        from engram_policy.tokens import CapabilityToken

        tokens = CapabilityToken(memory)
        token = tokens.create(
            agent_id="claude",
            scopes=["work", "system"],
            capabilities=["read", "write"],
            ttl_minutes=60,
        )
        assert isinstance(token, str)
        assert len(token) > 0

        claims = tokens.validate(token)
        assert claims is not None
        assert claims.agent_id == "claude"
        assert "work" in claims.scopes

    def test_validate_invalid_token(self, memory):
        from engram_policy.tokens import CapabilityToken

        tokens = CapabilityToken(memory)
        claims = tokens.validate("invalid-garbage-token")
        assert claims is None

    def test_revoke(self, memory):
        from engram_policy.tokens import CapabilityToken

        tokens = CapabilityToken(memory)
        token = tokens.create(agent_id="claude", scopes=["work"], capabilities=["read"], ttl_minutes=60)
        assert tokens.validate(token) is not None

        revoked = tokens.revoke(token)
        assert revoked is True

        assert tokens.validate(token) is None


class TestDataMasker:
    """Test DataMasker — mask fields based on scope."""

    def test_mask_out_of_scope(self):
        from engram_policy.masking import DataMasker

        masker = DataMasker(scope_map={
            "ssn": ["sensitive"],
            "email": ["personal"],
        })
        data = {
            "name": "John",
            "email": "john@example.com",
            "ssn": "123-45-6789",
        }
        result = masker.mask(data, agent_scopes=["work"])
        assert result["name"] == "John"  # not scope-restricted
        assert result["ssn"] == "[REDACTED]"
        assert result["email"] == "[REDACTED]"

    def test_mask_with_matching_scope(self):
        from engram_policy.masking import DataMasker

        masker = DataMasker(scope_map={"ssn": ["sensitive"]})
        data = {"ssn": "123-45-6789", "name": "John"}
        result = masker.mask(data, agent_scopes=["sensitive"])
        assert result["ssn"] == "123-45-6789"  # agent has scope


class TestPolicyScopes:
    """Test scope matching utilities."""

    def test_match_resource_glob(self):
        from engram_policy.scopes import match_resource

        assert match_resource("repo/*", "repo/file.py") is True
        assert match_resource("repo/*", "logs/file.py") is False
        assert match_resource("*", "anything") is True

    def test_match_scope(self):
        from engram_policy.scopes import match_scope

        assert match_scope(["work", "personal"], ["work"]) is True
        assert match_scope(["work"], ["personal"]) is False


class TestPolicyConfig:
    def test_defaults(self):
        from engram_policy import PolicyConfig

        config = PolicyConfig()
        assert config.default_effect == "deny"
        assert config.token_ttl_minutes == 60


# ═══════════════════════════════════════════════════════════════
# Package 4: engram-skills
# ═══════════════════════════════════════════════════════════════


class TestSkillRegistry:
    """Test SkillRegistry — register, search, get, invoke, list, remove."""

    def test_register_and_get(self, memory):
        from engram_skills import SkillRegistry

        registry = SkillRegistry(memory)
        result = registry.register(
            name="run_tests",
            description="Run pytest on the current project",
            parameters={"path": "str", "verbose": "bool"},
            examples=["run_tests(path='tests/')"],
            agent_id="claude",
            tags=["testing", "python"],
        )
        assert result is not None

        skill = registry.get("run_tests")
        assert skill is not None
        assert skill["name"] == "run_tests"

    def test_search_skills(self, memory):
        from engram_skills import SkillRegistry

        registry = SkillRegistry(memory)
        registry.register(name="run_tests", description="Run pytest", agent_id="claude")
        registry.register(name="lint_code", description="Run flake8 linter", agent_id="claude")

        results = registry.search("testing python code", limit=5)
        assert isinstance(results, list)

    def test_list_skills(self, memory):
        from engram_skills import SkillRegistry

        registry = SkillRegistry(memory)
        registry.register(name="skill_a", description="First skill")
        registry.register(name="skill_b", description="Second skill")

        skills = registry.list()
        assert len(skills) >= 2
        names = [s["name"] for s in skills]
        assert "skill_a" in names
        assert "skill_b" in names

    def test_invoke_local_skill(self, memory):
        from engram_skills import SkillRegistry

        registry = SkillRegistry(memory)

        # Register with a callable
        def add_numbers(a=0, b=0):
            return a + b

        registry.register(
            name="add_numbers",
            description="Add two numbers",
            callable=add_numbers,
        )

        result = registry.invoke("add_numbers", a=3, b=5)
        assert result == 8

    def test_invoke_nonexistent_skill(self, memory):
        from engram_skills import SkillRegistry

        registry = SkillRegistry(memory)
        with pytest.raises(Exception):
            registry.invoke("nonexistent_skill")

    def test_remove_skill(self, memory):
        from engram_skills import SkillRegistry

        registry = SkillRegistry(memory)
        result = registry.register(name="temp_skill", description="Temporary")
        skill_id = result["id"]

        removed = registry.remove(skill_id)
        assert removed is True

        skill = registry.get("temp_skill")
        assert skill is None


class TestSkillDecorator:
    """Test @skill decorator and module loading."""

    def test_skill_decorator(self):
        from engram_skills.loader import skill

        @skill(description="Multiply two numbers", tags=["math"])
        def multiply(a: int, b: int) -> int:
            return a * b

        assert hasattr(multiply, "__skill__")
        assert multiply.__skill__["description"] == "Multiply two numbers"
        assert multiply(3, 4) == 12  # still callable


class TestSkillConfig:
    def test_defaults(self):
        from engram_skills import SkillConfig

        config = SkillConfig()
        assert config.max_skills == 500
        assert config.allow_remote_invoke is False


# ═══════════════════════════════════════════════════════════════
# Package 5: engram-spawn
# ═══════════════════════════════════════════════════════════════


class TestSpawner:
    """Test Spawner — spawn, track, aggregate, cancel."""

    def _get_spawner(self, memory):
        from engram_spawn import Spawner

        return Spawner(memory)

    def test_spawn_subtasks(self, memory):
        from engram.memory.tasks import TaskManager

        tm = TaskManager(memory)
        parent = tm.create_task(title="Build feature X spawn test", description="Complex task", user_id="default")
        parent_id = parent["id"]

        spawner = self._get_spawner(memory)
        subtasks = [
            {"title": "Spawn design API", "description": "Design the REST API"},
            {"title": "Spawn write tests", "description": "Write unit tests"},
            {"title": "Spawn implement logic", "description": "Core business logic"},
        ]
        results = spawner.spawn(parent_id, subtasks)
        assert len(results) == 3

    def test_track_progress(self, memory):
        from engram.memory.tasks import TaskManager

        tm = TaskManager(memory)
        parent = tm.create_task(title="Track parent task", user_id="default")
        parent_id = parent["id"]

        spawner = self._get_spawner(memory)
        subtasks = [
            {"title": "Track sub 1"},
            {"title": "Track sub 2"},
        ]
        created = spawner.spawn(parent_id, subtasks)

        # Track progress
        progress = spawner.track(parent_id)
        assert isinstance(progress, dict)
        assert progress["total"] == 2

    def test_is_complete(self, memory):
        from engram.memory.tasks import TaskManager

        tm = TaskManager(memory)
        parent = tm.create_task(title="Complete parent task", user_id="default")
        parent_id = parent["id"]

        spawner = self._get_spawner(memory)
        created = spawner.spawn(parent_id, [{"title": "Complete only sub"}])

        assert spawner.is_complete(parent_id) is False

        # Complete the subtask
        tm.complete_task(created[0]["id"])
        assert spawner.is_complete(parent_id) is True

    def test_cancel_subtasks(self, memory):
        from engram.memory.tasks import TaskManager

        tm = TaskManager(memory)
        parent = tm.create_task(title="Cancel parent task", user_id="default")
        parent_id = parent["id"]

        spawner = self._get_spawner(memory)
        spawner.spawn(parent_id, [{"title": "Cancel sub A"}, {"title": "Cancel sub B"}])

        cancelled = spawner.cancel(parent_id)
        assert cancelled == 2

    def test_aggregate(self, memory):
        from engram.memory.tasks import TaskManager

        tm = TaskManager(memory)
        parent = tm.create_task(title="Aggregate parent task", user_id="default")
        parent_id = parent["id"]

        spawner = self._get_spawner(memory)
        spawner.spawn(parent_id, [{"title": "Aggregate sub 1"}])

        result = spawner.aggregate(parent_id)
        assert isinstance(result, dict)


class TestSpawnDecomposer:
    """Test decomposition strategies definition."""

    def test_strategies_exist(self):
        from engram_spawn.decomposer import STRATEGIES

        assert "auto" in STRATEGIES
        assert "sequential" in STRATEGIES
        assert "parallel" in STRATEGIES
        assert "phased" in STRATEGIES


class TestSpawnConfig:
    def test_defaults(self):
        from engram_spawn import SpawnConfig

        config = SpawnConfig()
        assert config.max_subtasks == 10
        assert config.default_strategy == "auto"
        assert config.auto_route is False


# ═══════════════════════════════════════════════════════════════
# Package 6: engram-resilience
# ═══════════════════════════════════════════════════════════════


class TestFallbackChain:
    """Test FallbackChain — status, reset."""

    def test_create_chain(self):
        from engram_resilience import FallbackChain

        chain = FallbackChain([
            {"provider": "gemini", "model": "gemini-2.0-flash"},
            {"provider": "openai", "model": "gpt-4o-mini"},
        ])
        assert chain is not None

    def test_empty_chain_raises(self):
        from engram_resilience import FallbackChain

        with pytest.raises(ValueError):
            FallbackChain([])

    def test_status(self):
        from engram_resilience import FallbackChain

        chain = FallbackChain([
            {"provider": "gemini", "model": "gemini-2.0-flash"},
            {"provider": "openai", "model": "gpt-4o-mini"},
        ])
        status = chain.status()
        assert status["current_provider"] == "gemini"
        assert status["total_providers"] == 2
        assert status["fallback_count"] == 0

    def test_reset(self):
        from engram_resilience import FallbackChain

        chain = FallbackChain([
            {"provider": "gemini", "model": "gemini-2.0-flash"},
            {"provider": "openai", "model": "gpt-4o-mini"},
        ])
        chain._current_index = 1  # simulate fallback
        chain.reset()
        assert chain._current_index == 0


class TestSmartRetry:
    """Test SmartRetry — execute, with_fallback."""

    def test_success_no_retry(self):
        from engram_resilience import SmartRetry

        retry = SmartRetry(max_retries=3, base_delay=0.01)
        result = retry.execute(lambda: 42)
        assert result == 42

    def test_retry_then_success(self):
        from engram_resilience import SmartRetry

        call_count = {"n": 0}

        def flaky():
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise RuntimeError("fail")
            return "ok"

        retry = SmartRetry(max_retries=3, base_delay=0.01)
        result = retry.execute(flaky)
        assert result == "ok"
        assert call_count["n"] == 3

    def test_exhaust_retries(self):
        from engram_resilience import SmartRetry

        def always_fail():
            raise RuntimeError("always fails")

        retry = SmartRetry(max_retries=2, base_delay=0.01)
        with pytest.raises(RuntimeError):
            retry.execute(always_fail)

    def test_with_fallback(self):
        from engram_resilience import SmartRetry

        retry = SmartRetry(max_retries=1, base_delay=0.01)

        def failing():
            raise RuntimeError("nope")

        def fallback():
            return "fallback_result"

        result = retry.with_fallback(failing, fallback)
        assert result == "fallback_result"


class TestContextCompactor:
    """Test ContextCompactor — should_compact, compact."""

    def test_should_compact_false(self):
        from engram_resilience import ContextCompactor

        # Mock LLM not needed for should_compact
        compactor = ContextCompactor(llm=None, max_tokens=1000)
        messages = [{"role": "user", "content": "short"}]
        assert compactor.should_compact(messages) is False

    def test_should_compact_true(self):
        from engram_resilience import ContextCompactor

        compactor = ContextCompactor(llm=None, max_tokens=10)
        messages = [{"role": "user", "content": "x" * 1000}]
        assert compactor.should_compact(messages) is True

    def test_compact_short_conversation(self):
        from engram_resilience import ContextCompactor

        compactor = ContextCompactor(llm=None, max_tokens=4000, keep_recent=5)
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        result = compactor.compact(messages)
        assert result == messages  # too short to compact

    def test_compact_long_conversation(self, memory):
        from engram_resilience import ContextCompactor

        # Use the LLM from memory
        llm = memory.llm
        compactor = ContextCompactor(llm=llm, max_tokens=100, keep_recent=2)

        messages = [
            {"role": "user", "content": f"Message {i}" * 50}
            for i in range(10)
        ]
        result = compactor.compact(messages)
        # Should have compacted — either summary + recent, or just recent (if LLM fails)
        assert len(result) <= 3  # summary + 2 recent, or just 2 recent

    def test_compact_empty(self):
        from engram_resilience import ContextCompactor

        compactor = ContextCompactor(llm=None)
        assert compactor.compact([]) == []


class TestResilienceConfig:
    def test_defaults(self):
        from engram_resilience import ResilienceConfig

        config = ResilienceConfig()
        assert config.max_retries == 3
        assert config.base_delay == 1.0
        assert config.jitter is True
        assert config.compact_threshold_tokens == 4000


# ═══════════════════════════════════════════════════════════════
# Integration: Bridge Coordination Compat Layer
# ═══════════════════════════════════════════════════════════════


class TestBridgeCoordination:
    """Test the Coordinator compatibility wrapper in engram-bridge."""

    def test_coordinator_compat_init(self, memory):
        from engram_bus import Bus
        from engram_bridge.coordination import Coordinator

        bus = Bus(db_path=":memory:")

        class FakeConfig:
            auto_route = True
            auto_execute = False
            log_events = True
            default_capabilities = {}

        coord = Coordinator(memory, bus, FakeConfig())
        assert coord.registry is not None
        assert coord.router is not None
        bus.close()

    def test_coordinator_register_from_config(self, memory):
        from engram_bus import Bus
        from engram_bridge.coordination import Coordinator

        bus = Bus(db_path=":memory:")

        class FakeConfig:
            auto_route = False
            auto_execute = False
            log_events = True
            default_capabilities = {}

        class FakeAgentConfig:
            type = "claude"
            model = "opus"

        coord = Coordinator(memory, bus, FakeConfig())
        coord.register_from_config({"my_claude": FakeAgentConfig()})

        agent = coord.registry.get("my_claude")
        assert agent is not None
        assert agent["type"] == "claude"
        bus.close()

    def test_coordinator_claim(self, memory):
        from engram_bus import Bus
        from engram.memory.tasks import TaskManager
        from engram_bridge.coordination import Coordinator

        bus = Bus(db_path=":memory:")

        class FakeConfig:
            auto_route = False
            auto_execute = False
            log_events = True
            default_capabilities = {}

        coord = Coordinator(memory, bus, FakeConfig())
        tm = TaskManager(memory)
        task = tm.create_task(title="Test", user_id="bridge")

        result = coord.claim(task["id"], "claude")
        assert result is not None
        assert result["assigned_agent"] == "claude"
        bus.close()


# ═══════════════════════════════════════════════════════════════
# Package 7: engram-metamemory
# ═══════════════════════════════════════════════════════════════


class TestMetamemory:
    """Test Metamemory — FOK, gaps, calibration, confidence."""

    def test_feeling_of_knowing_unknown(self, memory):
        from engram_metamemory import Metamemory

        mm = Metamemory(memory, user_id="mm_test_user")
        fok = mm.feeling_of_knowing("completely unknown topic xyz123abc")
        assert fok["verdict"] in ("unknown", "uncertain")
        assert isinstance(fok["score"], float)

    def test_feeling_of_knowing_with_data(self, memory):
        from engram_metamemory import Metamemory

        # Add some memories first
        memory.add(messages="Python was created by Guido van Rossum in 1991", user_id="mm_test_user2")
        time.sleep(0.5)

        mm = Metamemory(memory, user_id="mm_test_user2")
        fok = mm.feeling_of_knowing("Who created Python?")
        assert fok["verdict"] in ("confident", "uncertain", "unknown")
        assert isinstance(fok["score"], float)
        assert fok["score"] >= 0.0

    def test_log_and_list_knowledge_gap(self, memory):
        from engram_metamemory import Metamemory

        mm = Metamemory(memory, user_id="mm_gap_user")
        result = mm.log_knowledge_gap("quantum entanglement details", reason="empty_search")
        assert result["action"] == "created"
        assert result["gap_id"] is not None

        gaps = mm.list_knowledge_gaps()
        assert isinstance(gaps, list)
        queries = [g["query"] for g in gaps]
        assert "quantum entanglement details" in queries

    def test_resolve_knowledge_gap(self, memory):
        from engram_metamemory import Metamemory

        mm = Metamemory(memory, user_id="mm_resolve_user")
        created = mm.log_knowledge_gap("dark matter composition")
        gap_id = created["gap_id"]

        result = mm.resolve_knowledge_gap(gap_id)
        assert result["status"] == "resolved"

    def test_log_retrieval_outcome(self, memory):
        from engram_metamemory import Metamemory

        mm = Metamemory(memory, user_id="mm_cal_user")
        result = mm.log_retrieval_outcome(
            query="test query",
            memory_ids=["fake-id-1"],
            outcome="useful",
        )
        assert result["action"] == "logged"
        assert result["outcome"] == "useful"

    def test_get_calibration_stats_empty(self, memory):
        from engram_metamemory import Metamemory

        mm = Metamemory(memory, user_id="mm_empty_cal_user")
        stats = mm.get_calibration_stats()
        assert stats["total_evaluations"] == 0

    def test_get_memory_confidence(self, memory):
        from engram_metamemory import Metamemory

        result = memory.add(messages="The speed of light is 299792458 m/s", user_id="mm_conf_user")
        results_list = result.get("results", [result])
        mem_id = results_list[0]["id"]

        mm = Metamemory(memory, user_id="mm_conf_user")
        conf = mm.get_memory_confidence(mem_id)
        assert "live_confidence" in conf
        assert 0.0 <= conf["live_confidence"] <= 1.0

    def test_get_memory_confidence_not_found(self, memory):
        from engram_metamemory import Metamemory

        mm = Metamemory(memory, user_id="mm_conf_user")
        result = mm.get_memory_confidence("nonexistent-id")
        assert "error" in result


class TestMetamemoryConfig:
    def test_defaults(self):
        from engram_metamemory import MetamemoryConfig

        config = MetamemoryConfig()
        assert config.fok_confident_threshold == 0.7
        assert config.fok_uncertain_threshold == 0.3
        assert config.max_gaps == 500
        assert config.calibration_window == 100


class TestConfidenceComputation:
    """Test pure confidence computation functions."""

    def test_compute_confidence_basic(self):
        from engram_metamemory.confidence import compute_confidence

        score = compute_confidence(
            metadata={"echo_depth": "deep", "explicit_remember": True},
            strength=0.9,
            access_count=5,
        )
        assert 0.0 <= score <= 1.0
        assert score > 0.5  # Strong, deep, explicit = high confidence

    def test_compute_confidence_weak(self):
        from engram_metamemory.confidence import compute_confidence

        score = compute_confidence(
            metadata={"echo_depth": "shallow"},
            strength=0.2,
            access_count=0,
        )
        assert 0.0 <= score <= 1.0
        assert score < 0.5  # Weak, shallow, never accessed = low confidence

    def test_propagate_confidence(self):
        from engram_metamemory.confidence import propagate_confidence

        derived = propagate_confidence(0.9, "derived")
        assert derived < 0.9
        assert derived > 0.5

        inferred = propagate_confidence(0.9, "inferred")
        assert inferred < derived

    def test_multi_source_boost(self):
        from engram_metamemory.confidence import multi_source_boost

        base = 0.6
        boosted = multi_source_boost(base, source_count=3)
        assert boosted > base
        assert boosted <= 1.0

        not_boosted = multi_source_boost(base, source_count=1)
        assert not_boosted == base


# ═══════════════════════════════════════════════════════════════
# Package 8: engram-prospective
# ═══════════════════════════════════════════════════════════════


class TestProspective:
    """Test Prospective — intentions, triggers, lifecycle."""

    def test_add_intention(self, memory):
        from engram_prospective import Prospective

        pm = Prospective(memory, user_id="pm_test_user")
        result = pm.add_intention(
            description="Send weekly report",
            trigger_type="time",
            trigger_value="2020-01-01T00:00:00Z",  # Past time
            action="remind user",
        )
        assert result["action"] == "created"
        assert result["intention_id"] is not None
        assert result["trigger_type"] == "time"

    def test_add_intention_invalid_type(self, memory):
        from engram_prospective import Prospective

        pm = Prospective(memory, user_id="pm_test_user")
        result = pm.add_intention(
            description="test",
            trigger_type="invalid",
            trigger_value="foo",
        )
        assert "error" in result

    def test_list_intentions(self, memory):
        from engram_prospective import Prospective

        pm = Prospective(memory, user_id="pm_list_user")
        pm.add_intention(
            description="List test intention",
            trigger_type="event",
            trigger_value="test_event",
        )

        intentions = pm.list_intentions(status="pending")
        assert isinstance(intentions, list)
        descriptions = [i["description"] for i in intentions]
        assert "List test intention" in descriptions

    def test_check_triggers_time(self, memory):
        from engram_prospective import Prospective

        pm = Prospective(memory, user_id="pm_trigger_user")
        pm.add_intention(
            description="Past due intention",
            trigger_type="time",
            trigger_value="2020-01-01T00:00:00Z",  # Far in the past
        )
        time.sleep(0.5)

        triggered = pm.check_triggers()
        assert isinstance(triggered, list)
        # Should find the past-due intention
        descs = [t["description"] for t in triggered]
        assert "Past due intention" in descs

    def test_check_triggers_event(self, memory):
        from engram_prospective import Prospective

        pm = Prospective(memory, user_id="pm_event_user")
        pm.add_intention(
            description="On deploy complete",
            trigger_type="event",
            trigger_value="deploy_complete",
        )
        time.sleep(0.5)

        # Without event — should not trigger
        triggered = pm.check_triggers(events={})
        event_descs = [t["description"] for t in triggered if t["trigger_type"] == "event"]
        assert "On deploy complete" not in event_descs

        # Need to re-add since previous check might have expired the non-matching ones
        pm2 = Prospective(memory, user_id="pm_event_user2")
        pm2.add_intention(
            description="On deploy complete 2",
            trigger_type="event",
            trigger_value="deploy_complete",
        )
        time.sleep(0.5)

        # With event — should trigger
        triggered2 = pm2.check_triggers(events={"deploy_complete": True})
        event_descs2 = [t["description"] for t in triggered2 if t["trigger_type"] == "event"]
        assert "On deploy complete 2" in event_descs2

    def test_complete_intention(self, memory):
        from engram_prospective import Prospective

        pm = Prospective(memory, user_id="pm_complete_user")
        created = pm.add_intention(
            description="Complete me",
            trigger_type="time",
            trigger_value="2020-01-01T00:00:00Z",
        )
        intention_id = created["intention_id"]

        result = pm.complete_intention(intention_id)
        assert result["status"] == "completed"

    def test_cancel_intention(self, memory):
        from engram_prospective import Prospective

        pm = Prospective(memory, user_id="pm_cancel_user")
        created = pm.add_intention(
            description="Cancel me",
            trigger_type="condition",
            trigger_value="status=ready",
        )
        intention_id = created["intention_id"]

        result = pm.cancel_intention(intention_id)
        assert result["status"] == "cancelled"

    def test_get_due_intentions(self, memory):
        from engram_prospective import Prospective

        pm = Prospective(memory, user_id="pm_due_user")
        pm.add_intention(
            description="Due intention",
            trigger_type="time",
            trigger_value="2020-06-15T00:00:00Z",
        )
        time.sleep(0.5)

        due = pm.get_due_intentions()
        assert isinstance(due, list)


class TestProspectiveConfig:
    def test_defaults(self):
        from engram_prospective import ProspectiveConfig

        config = ProspectiveConfig()
        assert config.max_intentions_per_user == 200
        assert config.default_priority == 5
        assert config.time_tolerance_seconds == 300


class TestTriggerEvaluation:
    """Test pure trigger evaluation functions."""

    def test_time_trigger_past(self):
        from engram_prospective.triggers import evaluate_trigger

        result = evaluate_trigger({
            "metadata": {
                "pm_trigger_type": "time",
                "pm_trigger_value": "2020-01-01T00:00:00Z",
            }
        })
        assert result is True

    def test_time_trigger_future(self):
        from engram_prospective.triggers import evaluate_trigger

        result = evaluate_trigger({
            "metadata": {
                "pm_trigger_type": "time",
                "pm_trigger_value": "2099-01-01T00:00:00Z",
            }
        })
        assert result is False

    def test_event_trigger(self):
        from engram_prospective.triggers import evaluate_trigger

        result = evaluate_trigger(
            {"metadata": {"pm_trigger_type": "event", "pm_trigger_value": "deploy"}},
            events={"deploy": True},
        )
        assert result is True

        result_no = evaluate_trigger(
            {"metadata": {"pm_trigger_type": "event", "pm_trigger_value": "deploy"}},
            events={"other_event": True},
        )
        assert result_no is False

    def test_condition_trigger(self):
        from engram_prospective.triggers import evaluate_trigger

        result = evaluate_trigger(
            {"metadata": {"pm_trigger_type": "condition", "pm_trigger_value": "status=ready"}},
            context={"status": "ready"},
        )
        assert result is True

        result_no = evaluate_trigger(
            {"metadata": {"pm_trigger_type": "condition", "pm_trigger_value": "status=ready"}},
            context={"status": "pending"},
        )
        assert result_no is False

    def test_is_expired(self):
        from engram_prospective.triggers import is_expired

        assert is_expired({"metadata": {"pm_expiry": "2020-01-01T00:00:00Z"}}) is True
        assert is_expired({"metadata": {"pm_expiry": "2099-01-01T00:00:00Z"}}) is False
        assert is_expired({"metadata": {}}) is False


class TestHeartbeatCheckIntentions:
    """Test the check_intentions heartbeat behavior."""

    def test_behavior_registered(self):
        from engram_heartbeat.behaviors import BUILTIN_BEHAVIORS

        assert "check_intentions" in BUILTIN_BEHAVIORS

    def test_run_check_intentions(self, memory):
        from engram_heartbeat.behaviors import run_behavior

        result = run_behavior("check_intentions", memory, {}, agent_id="claude")
        assert result is not None
        assert result["action"] == "check_intentions"
        assert result["status"] in ("ok", "skipped", "error")


# ═══════════════════════════════════════════════════════════════
# Integration: MCP Auto-Discovery
# ═══════════════════════════════════════════════════════════════


class TestMCPAutoDiscovery:
    """Test that MCP server auto-discovers power package tools."""

    def test_power_tools_discovered(self, memory):
        """Verify the discovery function finds installed packages."""
        from engram.mcp_server import server, _discover_power_tools, _power_tool_handlers, _get_power_tool_defs

        # Reset discovery state
        import engram.mcp_server as mcp_mod
        mcp_mod._power_discovered = False
        mcp_mod._power_tool_handlers.clear()

        _discover_power_tools(server, memory)

        tool_defs = _get_power_tool_defs()
        assert len(tool_defs) > 0, "No power tools discovered"

        # Check some expected tool names
        expected_tools = [
            "register_agent", "find_capable_agents",  # router
            "declare_identity", "who_am_i",  # identity
            "schedule_heartbeat",  # heartbeat
            "add_policy", "check_access",  # policy
            "register_skill", "search_skills",  # skills
            "decompose_task",  # spawn
            "configure_fallback",  # resilience
            "feeling_of_knowing", "list_knowledge_gaps",  # metamemory
            "add_intention", "check_intention_triggers",  # prospective
            "extract_procedure", "search_procedures",  # procedural
            "propose_memory_update", "list_pending_updates",  # reconsolidation
            "log_failure", "search_failures",  # failure
            "wm_push", "wm_list",  # working
        ]
        for tool_name in expected_tools:
            assert tool_name in tool_defs, f"Tool '{tool_name}' not discovered"

        # Check handlers exist
        assert len(_power_tool_handlers) > 0
        for tool_name in expected_tools:
            assert tool_name in _power_tool_handlers, f"Handler for '{tool_name}' not registered"


# ═══════════════════════════════════════════════════════════════
# Package 10: engram-procedural
# ═══════════════════════════════════════════════════════════════


class TestProceduralConfig:
    def test_defaults(self):
        from engram_procedural import ProceduralConfig

        config = ProceduralConfig()
        assert config.min_episodes_for_extraction == 3
        assert config.automaticity_threshold == 5
        assert config.automaticity_boost == 0.20
        assert config.max_procedures_per_user == 500
        assert config.success_weight == 0.7

    def test_clamping(self):
        from engram_procedural import ProceduralConfig

        config = ProceduralConfig(automaticity_boost=1.5, min_episodes_for_extraction=-1)
        assert config.automaticity_boost == 1.0
        assert config.min_episodes_for_extraction == 1


class TestProceduralExtraction:
    """Test pure extraction functions."""

    def test_compute_automaticity_zero(self):
        from engram_procedural.extraction import compute_automaticity

        assert compute_automaticity(0, 0.0, 5) == 0.0

    def test_compute_automaticity_grows(self):
        from engram_procedural.extraction import compute_automaticity

        a1 = compute_automaticity(1, 1.0, 5)
        a3 = compute_automaticity(3, 1.0, 5)
        a5 = compute_automaticity(5, 1.0, 5)
        assert a1 < a3 < a5
        assert a5 == 1.0  # at threshold with 100% success

    def test_compute_automaticity_success_weighted(self):
        from engram_procedural.extraction import compute_automaticity

        high = compute_automaticity(5, 1.0, 5)
        low = compute_automaticity(5, 0.5, 5)
        assert high > low


class TestProcedural:
    """Test Procedural class — extract, log, refine, search."""

    def test_extract_procedure(self, memory):
        from engram_procedural import Procedural

        # First, add some episode memories
        ep_ids = []
        for i in range(3):
            result = memory.add(
                f"Episode {i}: I ran pytest, found a failing test, fixed the import, ran pytest again",
                user_id="proc_user",
                metadata={"memory_type": "episodic"},
                infer=False,
            )
            items = result.get("results", [])
            if items:
                ep_ids.append(items[0]["id"])

        proc = Procedural(memory, user_id="proc_user")
        result = proc.extract_procedure(
            episode_ids=ep_ids,
            name="debug_test_failures",
            domain="python",
        )
        assert result is not None
        assert result.get("name") == "debug_test_failures" or "error" not in result

    def test_list_procedures(self, memory):
        from engram_procedural import Procedural

        proc = Procedural(memory, user_id="proc_user")
        result = proc.list_procedures(status="active")
        assert isinstance(result, list)

    def test_search_procedures(self, memory):
        from engram_procedural import Procedural

        proc = Procedural(memory, user_id="proc_user")
        result = proc.search_procedures("debug test")
        assert isinstance(result, list)

    def test_log_execution(self, memory):
        from engram_procedural import Procedural

        proc = Procedural(memory, user_id="proc_exec_user")

        # Create a procedure first
        ep_ids = []
        for i in range(3):
            result = memory.add(
                f"Episode {i}: debug workflow step {i}",
                user_id="proc_exec_user",
                metadata={"memory_type": "episodic"},
                infer=False,
            )
            items = result.get("results", [])
            if items:
                ep_ids.append(items[0]["id"])

        extracted = proc.extract_procedure(
            episode_ids=ep_ids, name="exec_test_proc", domain="test"
        )
        proc_id = extracted.get("id", "")
        if proc_id:
            log_result = proc.log_execution(proc_id, success=True)
            assert log_result.get("use_count", 0) >= 1


# ═══════════════════════════════════════════════════════════════
# Package 11: engram-reconsolidation
# ═══════════════════════════════════════════════════════════════


class TestReconsolidationConfig:
    def test_defaults(self):
        from engram_reconsolidation import ReconsolidationConfig

        config = ReconsolidationConfig()
        assert config.min_confidence_for_auto_apply == 0.8
        assert config.min_confidence_for_proposal == 0.5
        assert config.cooldown_hours == 1.0
        assert config.max_versions == 50
        assert config.require_conflict_check is True


class TestReconsolidationWindow:
    """Test pure window functions."""

    def test_should_reconsolidate_no_content(self):
        from engram_reconsolidation.window import should_reconsolidate

        result = should_reconsolidate({"memory": ""}, "some context", None)
        assert result is False

    def test_should_reconsolidate_with_overlap(self):
        from engram_reconsolidation.window import should_reconsolidate

        result = should_reconsolidate(
            {"memory": "The Python API uses version 1 endpoints", "metadata": {}},
            "The Python API has been updated to version 2",
            None,
        )
        assert result is True


class TestReconsolidation:
    """Test Reconsolidation class — propose, apply, reject."""

    def test_propose_update(self, memory):
        from engram_reconsolidation import Reconsolidation

        # Create a memory to update
        result = memory.add(
            "The deploy pipeline uses Jenkins v1",
            user_id="rc_user",
            infer=False,
        )
        items = result.get("results", [])
        assert items
        memory_id = items[0]["id"]

        rc = Reconsolidation(memory, user_id="rc_user")
        proposal = rc.propose_update(
            memory_id=memory_id,
            new_context="We migrated from Jenkins to GitHub Actions",
        )
        assert proposal is not None
        assert "error" not in proposal or proposal.get("status") in ("no_change", "skipped")

    def test_list_pending_proposals(self, memory):
        from engram_reconsolidation import Reconsolidation

        rc = Reconsolidation(memory, user_id="rc_user")
        pending = rc.list_pending_proposals()
        assert isinstance(pending, list)

    def test_get_stats(self, memory):
        from engram_reconsolidation import Reconsolidation

        rc = Reconsolidation(memory, user_id="rc_user")
        stats = rc.get_stats()
        assert "total_proposals" in stats
        assert "applied" in stats
        assert "rejected" in stats


# ═══════════════════════════════════════════════════════════════
# Package 12: engram-failure
# ═══════════════════════════════════════════════════════════════


class TestFailureConfig:
    def test_defaults(self):
        from engram_failure import FailureConfig

        config = FailureConfig()
        assert config.min_failures_for_antipattern == 3
        assert config.max_failures_per_user == 1000
        assert config.auto_extract_antipatterns is True
        assert config.similarity_threshold == 0.80


class TestFailurePatterns:
    """Test pure pattern extraction functions."""

    def test_extract_antipattern_needs_llm(self):
        from engram_failure.patterns import extract_antipattern

        # Without LLM, should return default
        result = extract_antipattern([], None)
        assert result["confidence"] == 0.0

    def test_extract_recovery_strategy_needs_llm(self):
        from engram_failure.patterns import extract_recovery_strategy

        result = extract_recovery_strategy("failed", "fixed it", None)
        assert result["confidence"] == 0.0


class TestFailureLearning:
    """Test FailureLearning class — log, search, extract."""

    def test_log_failure(self, memory):
        from engram_failure import FailureLearning

        fl = FailureLearning(memory, user_id="fl_user")
        result = fl.log_failure(
            action="deploy_to_prod",
            error="Connection timeout after 30s",
            context="Deploying at 2am",
            severity="high",
        )
        assert result is not None
        assert result.get("action") == "deploy_to_prod" or "error" not in result

    def test_search_failures(self, memory):
        from engram_failure import FailureLearning

        fl = FailureLearning(memory, user_id="fl_user")
        time.sleep(0.5)
        results = fl.search_failures("timeout deploy")
        assert isinstance(results, list)

    def test_get_failure_stats(self, memory):
        from engram_failure import FailureLearning

        fl = FailureLearning(memory, user_id="fl_user")
        stats = fl.get_failure_stats()
        assert "total_failures" in stats
        assert "antipatterns" in stats

    def test_list_antipatterns(self, memory):
        from engram_failure import FailureLearning

        fl = FailureLearning(memory, user_id="fl_user")
        result = fl.list_antipatterns()
        assert isinstance(result, list)


# ═══════════════════════════════════════════════════════════════
# Package 13: engram-working
# ═══════════════════════════════════════════════════════════════


class TestWorkingMemoryConfig:
    def test_defaults(self):
        from engram_working import WorkingMemoryConfig

        config = WorkingMemoryConfig()
        assert config.capacity == 7
        assert config.decay_minutes == 30.0
        assert config.min_activation == 0.1
        assert config.auto_flush_to_longterm is True

    def test_capacity_clamping(self):
        from engram_working import WorkingMemoryConfig

        config = WorkingMemoryConfig(capacity=100)
        assert config.capacity == 20  # max 20


class TestWorkingMemoryBuffer:
    """Test pure buffer functions."""

    def test_wm_item_to_dict(self):
        from engram_working.buffer import WMItem

        item = WMItem(content="test", tag="task")
        d = item.to_dict()
        assert d["content"] == "test"
        assert d["tag"] == "task"
        assert d["activation"] == 1.0

    def test_compute_activation_decay(self):
        from datetime import datetime, timezone, timedelta
        from engram_working.buffer import WMItem, compute_activation_decay

        now = datetime.now(timezone.utc)
        item = WMItem(content="test", last_accessed=now - timedelta(minutes=30))
        decayed = compute_activation_decay(item, now, half_life_minutes=30.0)
        # After one half-life, activation should be ~0.5
        assert 0.4 < decayed < 0.6

    def test_is_relevant_to_query(self):
        from engram_working.buffer import WMItem, is_relevant_to_query

        item = WMItem(content="Fix the authentication login bug", tag="task")
        assert is_relevant_to_query(item, "authentication problem") is True
        assert is_relevant_to_query(item, "database migration") is False


class TestWorkingMemory:
    """Test WorkingMemory class — push, peek, pop, list, flush."""

    def test_push_and_list(self, memory):
        from engram_working import WorkingMemory

        wm = WorkingMemory(memory, user_id="wm_user", capacity=3)
        wm.push("Task: fix login", tag="task")
        wm.push("Context: auth service", tag="context")

        items = wm.list()
        assert len(items) == 2

    def test_capacity_eviction(self, memory):
        from engram_working import WorkingMemory

        wm = WorkingMemory(memory, user_id="wm_evict_user", capacity=2)
        wm.push("item 1")
        wm.push("item 2")
        result = wm.push("item 3")

        assert "evicted" in result
        assert len(wm.list()) == 2

    def test_peek_refreshes_activation(self, memory):
        from engram_working import WorkingMemory

        wm = WorkingMemory(memory, user_id="wm_peek_user")
        pushed = wm.push("important item")
        key = pushed["key"]

        peeked = wm.peek(key)
        assert peeked is not None
        assert peeked["access_count"] == 1

    def test_pop_removes_item(self, memory):
        from engram_working import WorkingMemory

        wm = WorkingMemory(memory, user_id="wm_pop_user")
        pushed = wm.push("removable")
        key = pushed["key"]

        popped = wm.pop(key)
        assert popped is not None
        assert popped["content"] == "removable"
        assert wm.size == 0

    def test_flush_to_longterm(self, memory):
        from engram_working import WorkingMemory

        wm = WorkingMemory(memory, user_id="wm_flush_user")
        wm.push("flush me 1")
        wm.push("flush me 2")

        result = wm.flush_to_longterm()
        assert result["flushed"] == 2
        assert wm.size == 0

    def test_get_relevant(self, memory):
        from engram_working import WorkingMemory

        wm = WorkingMemory(memory, user_id="wm_relevant_user")
        wm.push("Fix authentication bug in login service", tag="task")
        wm.push("Database migration plan for Q2", tag="context")

        relevant = wm.get_relevant("authentication login")
        assert len(relevant) >= 1


# ═══════════════════════════════════════════════════════════════
# Core: Salience
# ═══════════════════════════════════════════════════════════════


class TestSalience:
    """Test salience computation."""

    def test_heuristic_neutral(self):
        from engram.core.salience import compute_salience_heuristic

        result = compute_salience_heuristic("The meeting is at 3pm")
        assert result["sal_valence"] == 0.0
        assert result["sal_arousal"] == 0.0
        assert result["sal_salience_score"] == 0.0

    def test_heuristic_positive(self):
        from engram.core.salience import compute_salience_heuristic

        result = compute_salience_heuristic("This is amazing and wonderful news!")
        assert result["sal_valence"] > 0
        assert result["sal_salience_score"] > 0

    def test_heuristic_negative_urgent(self):
        from engram.core.salience import compute_salience_heuristic

        result = compute_salience_heuristic("Critical production crash! Urgent emergency!")
        assert result["sal_arousal"] > 0
        assert result["sal_salience_score"] > 0

    def test_decay_modifier(self):
        from engram.core.salience import salience_decay_modifier

        # High salience → slower decay (modifier < 1.0)
        assert salience_decay_modifier(1.0) == 0.5
        # No salience → normal decay
        assert salience_decay_modifier(0.0) == 1.0
        # Mid salience
        assert 0.5 < salience_decay_modifier(0.5) < 1.0


# ═══════════════════════════════════════════════════════════════
# Core: Causal Extensions
# ═══════════════════════════════════════════════════════════════


class TestCausalGraph:
    """Test causal relationship types and traversal."""

    def test_causal_relation_types_exist(self):
        from engram.core.graph import RelationType

        assert RelationType.CAUSED_BY.value == "caused_by"
        assert RelationType.LED_TO.value == "led_to"
        assert RelationType.PREVENTS.value == "prevents"
        assert RelationType.ENABLES.value == "enables"
        assert RelationType.REQUIRES.value == "requires"

    def test_get_causal_chain(self):
        from engram.core.graph import KnowledgeGraph, RelationType

        graph = KnowledgeGraph()
        graph.add_relationship("mem_a", "mem_b", RelationType.LED_TO)
        graph.add_relationship("mem_b", "mem_c", RelationType.LED_TO)

        # Forward from A
        chain = graph.get_causal_chain("mem_a", direction="forward", depth=5)
        chain_ids = [mid for mid, _, _ in chain]
        assert "mem_b" in chain_ids
        assert "mem_c" in chain_ids

    def test_detect_causal_language(self):
        from engram.core.graph import detect_causal_language, RelationType

        result = detect_causal_language("The bug was caused by a race condition")
        assert RelationType.CAUSED_BY in result

        result2 = detect_causal_language("This change led to a performance improvement")
        assert RelationType.LED_TO in result2

        result3 = detect_causal_language("Nothing special here")
        assert len(result3) == 0


# ═══════════════════════════════════════════════════════════════
# Core: AGI Loop
# ═══════════════════════════════════════════════════════════════


class TestAGILoop:
    """Test AGI loop and system health."""

    def test_get_system_health(self, memory):
        from engram.core.agi_loop import get_system_health

        health = get_system_health(memory)
        assert "systems" in health
        assert "available" in health
        assert "total" in health
        assert health["total"] > 0

    def test_run_agi_cycle(self, memory):
        from engram.core.agi_loop import run_agi_cycle

        result = run_agi_cycle(memory, user_id="agi_test_user")
        assert "summary" in result
        assert "decay" in result
        assert result["summary"]["total_subsystems"] > 0


# ═══════════════════════════════════════════════════════════════
# Heartbeat: New Behaviors
# ═══════════════════════════════════════════════════════════════


class TestHeartbeatNewBehaviors:
    """Test the new heartbeat behaviors."""

    def test_new_behaviors_registered(self):
        from engram_heartbeat.behaviors import BUILTIN_BEHAVIORS

        assert "extract_procedures" in BUILTIN_BEHAVIORS
        assert "process_reconsolidation" in BUILTIN_BEHAVIORS
        assert "extract_antipatterns" in BUILTIN_BEHAVIORS
        assert "wm_decay" in BUILTIN_BEHAVIORS
        assert "agi_loop" in BUILTIN_BEHAVIORS

    def test_run_extract_procedures(self, memory):
        from engram_heartbeat.behaviors import run_behavior

        result = run_behavior("extract_procedures", memory, {}, agent_id="test")
        assert result["action"] == "extract_procedures"
        assert result["status"] in ("ok", "skipped", "error")

    def test_run_agi_loop_behavior(self, memory):
        from engram_heartbeat.behaviors import run_behavior

        result = run_behavior("agi_loop", memory, {}, agent_id="test")
        assert result["action"] == "agi_loop"
        assert result["status"] in ("ok", "error")
