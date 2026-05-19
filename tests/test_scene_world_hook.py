from __future__ import annotations

import sys
import types

from dhee.hooks.claude_code.renderer import render_context
from dhee.hooks.scene_world import route_task


def test_scene_world_route_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DHEE_SCENE_WORLD_ENABLED", raising=False)
    monkeypatch.delenv("DHEE_SCENE_WORLD", raising=False)

    result = route_task("fix failing pytest", repo="/tmp/example")

    assert result == {"enabled": False, "status": "disabled"}


def test_scene_world_route_uses_lazy_adapter(monkeypatch):
    monkeypatch.setenv("DHEE_SCENE_WORLD_ENABLED", "1")
    monkeypatch.delenv("DHEE_SCENE_WORLD_MODEL", raising=False)
    monkeypatch.delenv("SCENE_WORLD_MODEL_PATH", raising=False)

    package = types.ModuleType("sankhya_wm")
    adapter = types.ModuleType("sankhya_wm.dhee_scene_world_adapter")
    calls = {}

    def fake_predict_next_action(task, **kwargs):
        calls["task"] = task
        calls["kwargs"] = kwargs
        return {
            "route_id": "route-1",
            "task": task,
            "source": "dhee",
            "era": "repo_work",
            "active_project": "Dhee",
            "best_action": {
                "action": "inspect_repo",
                "expected_reward": 0.75,
                "confidence": 0.8,
                "predicted_next_scene": "Agent reads source before editing.",
                "likely_user_reaction": "Good. This is grounded.",
                "risks": ["extra_tool_cost"],
            },
            "ranked_actions": [],
            "warnings": [],
        }

    adapter.predict_next_action = fake_predict_next_action
    monkeypatch.setitem(sys.modules, "sankhya_wm", package)
    monkeypatch.setitem(sys.modules, "sankhya_wm.dhee_scene_world_adapter", adapter)

    result = route_task("fix failing pytest", repo="/tmp/example", user_id="default", top_k=2, record=True)

    assert result["status"] == "ok"
    assert result["route"]["best_action"]["action"] == "inspect_repo"
    assert calls["task"] == "fix failing pytest"
    assert calls["kwargs"]["top_k"] == 2
    assert calls["kwargs"]["record"] is True


def test_renderer_includes_scene_world_block():
    xml = render_context(
        {},
        task_description="fix tests",
        scene_world={
            "route_id": "route-1",
            "source": "dhee",
            "era": "repo_work",
            "active_project": "Dhee",
            "_scene_world": {"harness": "claude-code"},
            "best_action": {
                "action": "inspect_repo",
                "expected_reward": 0.72,
                "confidence": 0.81,
                "predicted_next_scene": "Agent reads source before editing.",
                "likely_user_reaction": "User sees grounded work, not generic talk.",
                "risks": ["tool_cost"],
            },
            "ranked_actions": [
                {"action": "inspect_repo", "expected_reward": 0.72, "confidence": 0.81, "risks": ["tool_cost"]},
                {"action": "answer_directly", "expected_reward": -0.2, "confidence": 0.5, "risks": ["generic"]},
            ],
            "warnings": ["forecast only"],
        },
    )

    assert "<scene_world " in xml
    assert 'action="inspect_repo"' in xml
    assert "forecast not command" in xml
    assert "User sees grounded work" in xml


def test_slim_mcp_scene_world_route_disabled(monkeypatch):
    from dhee import mcp_slim

    monkeypatch.delenv("DHEE_SCENE_WORLD_ENABLED", raising=False)
    monkeypatch.delenv("DHEE_SCENE_WORLD", raising=False)

    result = mcp_slim.HANDLERS["dhee_scene_world_route"]({"task": "continue repo work"})

    assert result["status"] == "disabled"
