"""Tests for parallel execution in Engram.

Tests correctness, thread safety, error propagation, and fallback behavior
of the ParallelExecutor and its integration with Memory.
"""

import time
import threading
import pytest

from engram.memory.parallel import ParallelExecutor


# ── ParallelExecutor unit tests ─────────────────────────────────────────

class TestParallelExecutor:
    def test_empty_tasks(self):
        executor = ParallelExecutor(max_workers=2)
        assert executor.run_parallel([]) == []
        executor.shutdown()

    def test_single_task_no_pool(self):
        """Single task bypasses thread pool for efficiency."""
        executor = ParallelExecutor(max_workers=2)
        result = executor.run_parallel([(lambda: 42, ())])
        assert result == [42]
        # Pool should not have been created
        assert executor._pool is None
        executor.shutdown()

    def test_multiple_tasks_parallel(self):
        executor = ParallelExecutor(max_workers=4)

        def slow_add(a, b):
            time.sleep(0.05)
            return a + b

        tasks = [(slow_add, (i, i * 10)) for i in range(4)]
        results = executor.run_parallel(tasks)
        assert results == [0, 11, 22, 33]
        executor.shutdown()

    def test_results_in_order(self):
        """Results must be returned in the same order as tasks."""
        executor = ParallelExecutor(max_workers=4)

        def identity(x):
            time.sleep(0.01 * (5 - x))  # Reverse timing
            return x

        tasks = [(identity, (i,)) for i in range(5)]
        results = executor.run_parallel(tasks)
        assert results == [0, 1, 2, 3, 4]
        executor.shutdown()

    def test_error_propagation(self):
        """Exception in a task must propagate to the caller."""
        executor = ParallelExecutor(max_workers=2)

        def fail():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            executor.run_parallel([(fail, ())])
        executor.shutdown()

    def test_shutdown_idempotent(self):
        executor = ParallelExecutor(max_workers=2)
        executor.run_parallel([(lambda: 1, ()), (lambda: 2, ())])
        executor.shutdown()
        executor.shutdown()  # Should not raise
        assert executor._pool is None

    def test_thread_safety_shared_state(self):
        """Multiple parallel tasks can safely read shared state."""
        executor = ParallelExecutor(max_workers=4)
        shared_list = list(range(100))

        def read_item(idx):
            return shared_list[idx]

        tasks = [(read_item, (i,)) for i in range(100)]
        results = executor.run_parallel(tasks)
        assert results == list(range(100))
        executor.shutdown()

    def test_parallel_is_actually_concurrent(self):
        """Verify that tasks run concurrently, not sequentially."""
        executor = ParallelExecutor(max_workers=4)

        def sleep_return(x):
            time.sleep(0.1)
            return x

        start = time.time()
        tasks = [(sleep_return, (i,)) for i in range(4)]
        results = executor.run_parallel(tasks)
        elapsed = time.time() - start

        assert results == [0, 1, 2, 3]
        # If parallel, should take ~0.1s. If sequential, ~0.4s.
        assert elapsed < 0.3, f"Took {elapsed:.2f}s, expected <0.3s for parallel"
        executor.shutdown()


# ── ParallelConfig integration ──────────────────────────────────────────

class TestParallelConfig:
    def test_config_defaults(self):
        from engram.configs.base import ParallelConfig
        config = ParallelConfig()
        assert config.enable_parallel is False
        assert config.max_workers == 4
        assert config.parallel_add is True
        assert config.parallel_reecho is True
        assert config.parallel_decay is True

    def test_config_in_memory_config(self):
        from engram.configs.base import MemoryConfig
        config = MemoryConfig()
        assert hasattr(config, "parallel")
        assert config.parallel.enable_parallel is False

    def test_config_enable_parallel(self):
        from engram.configs.base import MemoryConfig, ParallelConfig
        config = MemoryConfig(parallel=ParallelConfig(enable_parallel=True, max_workers=8))
        assert config.parallel.enable_parallel is True
        assert config.parallel.max_workers == 8

    def test_max_workers_clamped(self):
        from engram.configs.base import ParallelConfig
        # Too low
        config = ParallelConfig(max_workers=0)
        assert config.max_workers == 1
        # Too high
        config = ParallelConfig(max_workers=100)
        assert config.max_workers == 32


# ── Mock-based Memory integration ───────────────────────────────────────

class TestParallelMemoryIntegration:
    """Test that Memory correctly initializes and uses the executor."""

    def test_memory_no_executor_by_default(self):
        """With default config, no executor is created."""
        from engram.configs.base import MemoryConfig
        from engram.memory.main import Memory
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MemoryConfig(
                vector_store={"provider": "memory", "config": {}},
                llm={"provider": "mock", "config": {}},
                embedder={"provider": "simple", "config": {}},
                history_db_path=os.path.join(tmpdir, "test.db"),
                graph={"enable_graph": False},
                scene={"enable_scenes": False},
                profile={"enable_profiles": False},
                handoff={"enable_handoff": False},
            )
            m = Memory(config)
            assert m._executor is None
            m.close()

    def test_memory_creates_executor_when_enabled(self):
        """With enable_parallel=True, executor is created."""
        from engram.configs.base import MemoryConfig, ParallelConfig
        from engram.memory.main import Memory
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MemoryConfig(
                vector_store={"provider": "memory", "config": {}},
                llm={"provider": "mock", "config": {}},
                embedder={"provider": "simple", "config": {}},
                history_db_path=os.path.join(tmpdir, "test.db"),
                graph={"enable_graph": False},
                scene={"enable_scenes": False},
                profile={"enable_profiles": False},
                handoff={"enable_handoff": False},
                parallel=ParallelConfig(enable_parallel=True),
            )
            m = Memory(config)
            assert m._executor is not None
            m.close()
            assert m._executor is None

    def test_memory_close_shuts_down_executor(self):
        """close() cleanly shuts down the executor."""
        from engram.configs.base import MemoryConfig, ParallelConfig
        from engram.memory.main import Memory
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MemoryConfig(
                vector_store={"provider": "memory", "config": {}},
                llm={"provider": "mock", "config": {}},
                embedder={"provider": "simple", "config": {}},
                history_db_path=os.path.join(tmpdir, "test.db"),
                graph={"enable_graph": False},
                scene={"enable_scenes": False},
                profile={"enable_profiles": False},
                handoff={"enable_handoff": False},
                parallel=ParallelConfig(enable_parallel=True, max_workers=2),
            )
            m = Memory(config)
            executor = m._executor
            assert executor is not None
            m.close()
            assert m._executor is None
