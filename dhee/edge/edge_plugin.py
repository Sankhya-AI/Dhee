"""DheeEdge — minimal cognition plugin for edge/hardware deployment.

Designed for humanoid robots, IoT devices, and AI hardware products.
All computation runs locally — no cloud API calls, no internet required.

Constraints:
  - LLM: DheeModel (GGUF Q4, ~1.5GB) or mock fallback
  - Embedder: ONNX MiniLM (22MB) or hash-based fallback
  - Vector store: sqlite_vec (local file)
  - RAM: <500MB working set
  - No external API calls ever

Adds embodiment hooks for hardware integration:
  - on_sensor_input()   — process sensor data into episodic memory
  - on_action_result()  — record action outcomes for environment learning
  - predict_environment() — predict next state from memory patterns

Usage:
    from dhee.edge import DheeEdge

    d = DheeEdge(data_dir="/data/dhee")
    d.remember("User prefers quiet mode after 10pm")
    d.on_sensor_input("microphone", {"volume_db": 85, "duration": 3.0})
    d.on_action_result("reduce_volume", success=True, env_state={"volume_db": 40})
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from dhee.adapters.base import DheePlugin

logger = logging.getLogger(__name__)


class DheeEdge(DheePlugin):
    """Minimal cognition plugin for edge deployment.

    Forces all-offline providers. No API calls, no internet.
    Extends DheePlugin with embodiment hooks for hardware.

    Args:
        data_dir: Storage directory (required for edge — no temp dirs).
        model_path: Path to GGUF model file for local LLM inference.
        user_id: Default user ID.
    """

    def __init__(
        self,
        data_dir: Union[str, Path],
        model_path: Optional[str] = None,
        user_id: str = "default",
    ):
        # Force offline — never make API calls.
        # Try persistent storage first; fall back to in-memory if
        # sqlite_vec extension isn't available on this platform.
        try:
            super().__init__(
                data_dir=data_dir,
                provider="mock",
                user_id=user_id,
                in_memory=False,
                offline=True,
            )
        except (AttributeError, OSError) as e:
            logger.debug("Persistent storage unavailable (%s), using in-memory", e)
            super().__init__(
                data_dir=data_dir,
                provider="mock",
                user_id=user_id,
                in_memory=True,
                offline=True,
            )

        # Embodiment state
        self._sensor_history: List[Dict[str, Any]] = []
        self._action_history: List[Dict[str, Any]] = []
        self._environment_model: Dict[str, Any] = {}

        # Try to upgrade to local GGUF model
        if model_path:
            self._try_load_local_model(model_path)

    def _try_load_local_model(self, model_path: str) -> None:
        """Attempt to load a local GGUF model for on-device LLM inference."""
        if not os.path.exists(model_path):
            logger.debug("GGUF model not found at %s, using mock LLM", model_path)
            return
        try:
            from dhee.llms.dhee import DheeLLM
            self._engram._memory.llm = DheeLLM(
                config={"model_path": model_path, "backend": "gguf"}
            )
            logger.info("Loaded local GGUF model: %s", model_path)
        except ImportError:
            logger.debug("llama-cpp-python not available, using mock LLM")
        except Exception as e:
            logger.debug("Failed to load GGUF model: %s", e)

    # ------------------------------------------------------------------
    # Embodiment hooks (from Self-evolving Embodied AI, arXiv:2602.04411)
    # ------------------------------------------------------------------

    def on_sensor_input(
        self,
        sensor_type: str,
        data: Dict[str, Any],
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Process sensor data into episodic memory.

        Converts raw sensor readings into natural language memories that
        can be recalled later. Tracks sensor patterns for environment
        prediction.

        Args:
            sensor_type: Type of sensor (e.g., "microphone", "camera", "imu")
            data: Sensor data dict with readings and metadata
            user_id: Override default user_id

        Returns:
            {"stored": bool, "id": str, "description": str}
        """
        uid = user_id or self._user_id
        timestamp = data.get("timestamp", time.time())

        # Build natural language description from sensor data
        description = self._describe_sensor_data(sensor_type, data)

        # Store as memory
        result = self.remember(
            content=description,
            user_id=uid,
            metadata={
                "source": "sensor",
                "sensor_type": sensor_type,
                "timestamp": timestamp,
                "raw_data": data,
            },
        )

        # Track in sensor history (bounded)
        record = {
            "sensor_type": sensor_type,
            "data": data,
            "timestamp": timestamp,
            "description": description,
        }
        self._sensor_history.append(record)
        if len(self._sensor_history) > 500:
            self._sensor_history = self._sensor_history[-500:]

        # Update environment model
        self._update_environment_model(sensor_type, data)

        result["description"] = description
        return result

    def on_action_result(
        self,
        action: str,
        success: bool,
        env_state: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record action outcomes for environment self-prediction.

        Builds a causal model: action + context → outcome. Over time,
        the system learns which actions work in which states.

        Args:
            action: What the agent did (e.g., "reduce_volume", "move_forward")
            success: Whether the action achieved its goal
            env_state: Environment state after action
            user_id: Override default user_id
        """
        uid = user_id or self._user_id

        # Store as memory with outcome
        outcome_word = "succeeded" if success else "failed"
        content = f"Action '{action}' {outcome_word}"
        if env_state:
            state_summary = ", ".join(f"{k}={v}" for k, v in list(env_state.items())[:5])
            content += f". Environment state: {state_summary}"

        result = self.remember(
            content=content,
            user_id=uid,
            metadata={
                "source": "action_result",
                "action": action,
                "success": success,
                "env_state": env_state,
            },
        )

        # Track action history
        record = {
            "action": action,
            "success": success,
            "env_state": env_state,
            "timestamp": time.time(),
        }
        self._action_history.append(record)
        if len(self._action_history) > 500:
            self._action_history = self._action_history[-500:]

        # Record outcome for performance tracking
        task_type = f"action_{action}"
        self._buddhi.record_outcome(
            user_id=uid,
            task_type=task_type,
            score=1.0 if success else 0.0,
        )

        return result

    def predict_environment(
        self,
        current_state: Dict[str, Any],
        proposed_action: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Predict next environment state from memory patterns.

        Uses action history to estimate what will happen if a given
        action is taken in the current state.

        Args:
            current_state: Current environment state dict
            proposed_action: Action being considered (optional)

        Returns:
            {"prediction": str, "confidence": float, "similar_outcomes": list}
        """
        # Build query from current state + proposed action
        state_desc = ", ".join(f"{k}={v}" for k, v in list(current_state.items())[:5])
        query = f"environment state: {state_desc}"
        if proposed_action:
            query += f", action: {proposed_action}"

        # Search for similar past situations
        similar = self.recall(query=query, limit=5)

        # Compute confidence from action history
        confidence = 0.0
        outcomes = []
        if proposed_action and self._action_history:
            matching = [
                a for a in self._action_history
                if a["action"] == proposed_action
            ]
            if matching:
                success_rate = sum(1 for a in matching if a["success"]) / len(matching)
                confidence = success_rate
                outcomes = matching[-3:]  # last 3 similar actions

        # Simple prediction based on success rate
        prediction = "unknown"
        if confidence > 0.7:
            prediction = f"Action '{proposed_action}' is likely to succeed (confidence: {confidence:.0%})"
        elif confidence > 0.3:
            prediction = f"Action '{proposed_action}' has mixed results (confidence: {confidence:.0%})"
        elif confidence > 0 and proposed_action:
            prediction = f"Action '{proposed_action}' has often failed (confidence: {confidence:.0%})"

        return {
            "prediction": prediction,
            "confidence": round(confidence, 3),
            "similar_memories": similar[:3],
            "recent_outcomes": [
                {"action": o["action"], "success": o["success"]}
                for o in outcomes
            ],
        }

    def adapt_embodiment(
        self,
        capabilities: Dict[str, Any],
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update self-model when hardware capabilities change.

        Call when sensors are added/removed, actuators change, or the
        physical form factor is modified.

        Args:
            capabilities: New capability dict (e.g., {"has_camera": True, "arm_reach_cm": 60})
        """
        uid = user_id or self._user_id
        cap_desc = ", ".join(f"{k}: {v}" for k, v in capabilities.items())
        content = f"Embodiment update: {cap_desc}"
        return self.remember(
            content=content,
            user_id=uid,
            metadata={"source": "embodiment_update", "capabilities": capabilities},
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _describe_sensor_data(self, sensor_type: str, data: Dict[str, Any]) -> str:
        """Convert raw sensor data to natural language for memory storage."""
        readings = ", ".join(
            f"{k}={v}" for k, v in data.items()
            if k != "timestamp" and not isinstance(v, (dict, list))
        )
        return f"Sensor[{sensor_type}]: {readings}"

    def _update_environment_model(
        self, sensor_type: str, data: Dict[str, Any],
    ) -> None:
        """Update the running environment model with new sensor data."""
        self._environment_model[sensor_type] = {
            "last_reading": data,
            "last_updated": time.time(),
        }
