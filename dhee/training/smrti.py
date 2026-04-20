"""स्मृति (Smṛti) — Multi-trace LoRA adapter management.

SamsaraNet's MultiTrace tracked three EMA shadows of network weights:
  fast — the body's reflexes, born and dying with each life
  mid  — habits half-remembered across incarnations
  slow — the soul's wisdom, what survives the fire

For DheeModel, the same three traces track LoRA adapter weights:
  s_fast — current epoch's adapter state (volatile, may overfit)
  s_mid  — cross-epoch EMA (stable extraction patterns)
  s_slow — cross-phase EMA (permanent structured knowledge)

At death (curriculum phase boundary):
  s_fast is destroyed (epoch-specific overfitting discarded)
  s_mid partially survives based on karma
  s_slow almost fully survives (accumulated wisdom)

At birth (new curriculum phase):
  s_slow seeds the new adapter (ancestral knowledge)
  s_mid adds lighter echo (half-remembered patterns)
  Fresh noise adds regularization (the new body's individuality)

This is the single most valuable idea from SamsaraNet.
No other fine-tuning framework tracks adapter weights at multiple timescales.
"""

from __future__ import annotations

import copy
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import torch

logger = logging.getLogger(__name__)


@dataclass
class TraceConfig:
    """Multi-trace EMA configuration for LoRA adapters.

    Direct adaptation from SamsaraNet's TraceConfig.
    Momentum values are calibrated for LoRA adapter magnitudes,
    which are smaller than full network weights.
    """

    # EMA momentum (higher = slower change = more persistent)
    mid_momentum: float = 0.99       # ~100 updates to converge
    slow_momentum: float = 0.999     # ~1000 updates to converge

    # Death retention: how much survives phase transition
    death_mid_retention_base: float = 0.3
    death_mid_retention_karma_bonus: float = 0.5
    death_slow_retention_base: float = 0.7
    death_slow_retention_karma_bonus: float = 0.25

    # Birth: how the new phase is seeded
    birth_soul_weight: float = 0.6    # weight of slow trace (ancestral wisdom)
    birth_mid_weight: float = 0.25    # weight of mid trace (habits)
    birth_noise_scale: float = 0.01   # regularization noise


@dataclass
class AncestralAdapters:
    """What survives across curriculum phases.

    Analogous to SamsaraNet's AncestralPriors — the Pitri bank's offering.
    """

    soul_adapters: Dict[str, torch.Tensor] = field(default_factory=dict)
    mid_adapters: Dict[str, torch.Tensor] = field(default_factory=dict)
    phases_completed: int = 0
    karma_history: List[float] = field(default_factory=list)
    task_mastery: Dict[str, float] = field(default_factory=dict)


class AdapterMultiTrace:
    """Multi-timescale EMA tracking for LoRA adapter weights.

    This is the core innovation transplanted from SamsaraNet.
    Instead of tracking full network weights (too expensive for transformers),
    we track only the LoRA adapter parameters — the delta that defines
    DheeModel's structured extraction capability.

    Three traces:
    - fast: IS the current adapter state (no separate tracking needed)
    - mid: EMA shadow updated after each training step
    - slow: EMA shadow updated after each training step (much slower)

    The fast trace is just the live model — no copy needed.
    Mid and slow are separate copies that lag behind.
    """

    def __init__(self, model: torch.nn.Module, config: TraceConfig):
        self.config = config

        # Identify LoRA adapter parameters (lora_A, lora_B matrices)
        self.adapter_keys = [
            name for name, _ in model.named_parameters()
            if "lora" in name.lower() and _.requires_grad
        ]

        if not self.adapter_keys:
            logger.warning(
                "No LoRA parameters found. Multi-trace will be inactive."
            )

        # Initialize mid and slow traces from current adapter state
        state = {
            name: param.detach().clone()
            for name, param in model.named_parameters()
            if name in self.adapter_keys
        }
        self.mid = {k: v.clone() for k, v in state.items()}
        self.slow = {k: v.clone() for k, v in state.items()}

        self._update_count = 0
        logger.info(
            "Multi-trace initialized: tracking %d adapter parameters",
            len(self.adapter_keys),
        )

    def update(self, model: torch.nn.Module) -> None:
        """Called after each optimizer step. Let the EMA shadows follow.

        SamsaraNet called this after each PPO update.
        DheeModel calls this after each gradient step.
        """
        tau_mid = self.config.mid_momentum
        tau_slow = self.config.slow_momentum

        for name, param in model.named_parameters():
            if name not in self.adapter_keys:
                continue
            current = param.detach()
            self.mid[name] = tau_mid * self.mid[name] + (1 - tau_mid) * current
            self.slow[name] = tau_slow * self.slow[name] + (1 - tau_slow) * current

        self._update_count += 1

    def sleep(self) -> None:
        """Within-phase consolidation.

        Gently transfers fast-trace patterns into mid-trace.
        SamsaraNet did this every N environment steps.
        DheeModel does this at epoch boundaries within a phase.
        """
        rate = 0.05
        for key in self.adapter_keys:
            if key in self.mid:
                # We don't have fast trace explicitly — mid already follows
                # Instead, nudge slow toward mid (gentle consolidation)
                transfer = (self.mid[key] - self.slow[key]) * rate
                self.slow[key] = self.slow[key] + transfer

    def die(self, karma_net: float) -> Dict[str, Dict[str, torch.Tensor]]:
        """Phase death — extract surviving adapter traces.

        Good karma → more of mid-trace survives.
        Slow trace is nearly indestructible.

        Returns surviving adapters for the Pitri bank (AncestralAdapters).
        """
        cfg = self.config
        karma_factor = max(0.0, min(1.0, (karma_net + 1.0) / 2.0))

        mid_retention = (
            cfg.death_mid_retention_base
            + cfg.death_mid_retention_karma_bonus * karma_factor
        )
        slow_retention = (
            cfg.death_slow_retention_base
            + cfg.death_slow_retention_karma_bonus * karma_factor
        )

        logger.info(
            "Phase death: karma=%.3f → mid_retention=%.3f, slow_retention=%.3f",
            karma_net,
            mid_retention,
            slow_retention,
        )

        return {
            "mid": {k: v.clone() * mid_retention for k, v in self.mid.items()},
            "slow": {k: v.clone() * slow_retention for k, v in self.slow.items()},
        }

    def birth(
        self,
        model: torch.nn.Module,
        priors: Optional[AncestralAdapters] = None,
    ) -> None:
        """Seed a new curriculum phase with ancestral adapter wisdom.

        SamsaraNet's birth: soul weights + mid weights + noise → new encoder.
        DheeModel's birth: slow adapters + mid adapters + noise → new LoRA.

        The Garuda Purana says: in the womb, the soul remembers all past karma,
        then at birth, memory is destroyed. But samskaras remain embedded.

        We implement this as: ancestral slow adapters seed the new LoRA
        (remembering), then fresh noise adds regularization (forgetting),
        but the deep structure persists (samskaras).
        """
        cfg = self.config

        if priors is None or not priors.soul_adapters:
            # First life — nothing to seed
            state = {
                name: param.detach().clone()
                for name, param in model.named_parameters()
                if name in self.adapter_keys
            }
            self.mid = {k: v.clone() for k, v in state.items()}
            self.slow = {k: v.clone() for k, v in state.items()}
            return

        # Seed from ancestral priors
        with torch.no_grad():
            for name, param in model.named_parameters():
                if name not in self.adapter_keys:
                    continue

                if name in priors.soul_adapters:
                    ancestral_slow = priors.soul_adapters[name]
                    ancestral_mid = priors.mid_adapters.get(
                        name, torch.zeros_like(param)
                    )

                    # Shape check — LoRA dimensions might change between phases
                    if ancestral_slow.shape != param.shape:
                        logger.warning(
                            "Shape mismatch for %s: %s vs %s. Skipping transfer.",
                            name,
                            ancestral_slow.shape,
                            param.shape,
                        )
                        continue

                    noise = torch.randn_like(param) * cfg.birth_noise_scale

                    new_val = (
                        cfg.birth_soul_weight * ancestral_slow
                        + cfg.birth_mid_weight * ancestral_mid
                        + (1 - cfg.birth_soul_weight - cfg.birth_mid_weight) * noise
                    )
                    param.copy_(new_val)

        # Reset traces to newborn state
        state = {
            name: param.detach().clone()
            for name, param in model.named_parameters()
            if name in self.adapter_keys
        }
        self.mid = {k: v.clone() for k, v in state.items()}

        # Slow trace remembers deeper than the body knows
        self.slow = {}
        for k in self.adapter_keys:
            if priors.soul_adapters and k in priors.soul_adapters:
                if priors.soul_adapters[k].shape == state[k].shape:
                    self.slow[k] = priors.soul_adapters[k].clone()
                else:
                    self.slow[k] = state[k].clone()
            else:
                self.slow[k] = state[k].clone()

        self._update_count = 0
        logger.info(
            "Birth complete: seeded %d adapter parameters from ancestral priors "
            "(phases_completed=%d)",
            len(self.adapter_keys),
            priors.phases_completed,
        )


class PitriBank:
    """Ancestral adapter bank — accumulates wisdom across curriculum phases.

    Direct adaptation of SamsaraNet's PitriBank.
    Stores the best adapter snapshots and their associated karma,
    enabling selective knowledge transfer across training phases.
    """

    def __init__(self, merge_rate: float = 0.3):
        self.merge_rate = merge_rate
        self.priors: Optional[AncestralAdapters] = None

    def absorb(
        self,
        surviving_traces: Dict[str, Dict[str, torch.Tensor]],
        karma_net: float,
        task_mastery: Dict[str, float],
    ) -> None:
        """Absorb surviving adapter traces into the ancestral bank.

        Uses exponential moving average to blend new wisdom with accumulated.
        """
        if self.priors is None:
            self.priors = AncestralAdapters(
                soul_adapters=surviving_traces.get("slow", {}),
                mid_adapters=surviving_traces.get("mid", {}),
                phases_completed=1,
                karma_history=[karma_net],
                task_mastery=dict(task_mastery),
            )
            return

        # Merge new traces with existing using EMA
        alpha = self.merge_rate
        for key in surviving_traces.get("slow", {}):
            new_val = surviving_traces["slow"][key]
            if key in self.priors.soul_adapters:
                old_val = self.priors.soul_adapters[key]
                if old_val.shape == new_val.shape:
                    self.priors.soul_adapters[key] = (
                        (1 - alpha) * old_val + alpha * new_val
                    )
                else:
                    self.priors.soul_adapters[key] = new_val
            else:
                self.priors.soul_adapters[key] = new_val

        for key in surviving_traces.get("mid", {}):
            new_val = surviving_traces["mid"][key]
            if key in self.priors.mid_adapters:
                old_val = self.priors.mid_adapters[key]
                if old_val.shape == new_val.shape:
                    self.priors.mid_adapters[key] = (
                        (1 - alpha) * old_val + alpha * new_val
                    )
                else:
                    self.priors.mid_adapters[key] = new_val
            else:
                self.priors.mid_adapters[key] = new_val

        self.priors.phases_completed += 1
        self.priors.karma_history.append(karma_net)
        for task, score in task_mastery.items():
            self.priors.task_mastery[task] = max(
                self.priors.task_mastery.get(task, 0.0), score
            )

    def get_priors(self) -> Optional[AncestralAdapters]:
        return self.priors

    def save(self, path: str) -> None:
        """Persist ancestral bank to disk."""
        os.makedirs(path, exist_ok=True)
        if self.priors is None:
            return

        # Save adapter tensors
        if self.priors.soul_adapters:
            torch.save(
                self.priors.soul_adapters,
                os.path.join(path, "soul_adapters.pt"),
            )
        if self.priors.mid_adapters:
            torch.save(
                self.priors.mid_adapters,
                os.path.join(path, "mid_adapters.pt"),
            )

        # Save metadata
        import json

        meta = {
            "phases_completed": self.priors.phases_completed,
            "karma_history": self.priors.karma_history,
            "task_mastery": self.priors.task_mastery,
        }
        with open(os.path.join(path, "meta.json"), "w") as f:
            json.dump(meta, f, indent=2)

    def load(self, path: str) -> None:
        """Restore ancestral bank from disk."""
        import json

        meta_path = os.path.join(path, "meta.json")
        if not os.path.exists(meta_path):
            return

        with open(meta_path) as f:
            meta = json.load(f)

        soul_path = os.path.join(path, "soul_adapters.pt")
        mid_path = os.path.join(path, "mid_adapters.pt")

        self.priors = AncestralAdapters(
            soul_adapters=(
                torch.load(soul_path, weights_only=True)
                if os.path.exists(soul_path)
                else {}
            ),
            mid_adapters=(
                torch.load(mid_path, weights_only=True)
                if os.path.exists(mid_path)
                else {}
            ),
            phases_completed=meta["phases_completed"],
            karma_history=meta["karma_history"],
            task_mastery=meta.get("task_mastery", {}),
        )
        logger.info(
            "Pitri bank loaded: %d phases, karma_history=%s",
            self.priors.phases_completed,
            self.priors.karma_history,
        )
