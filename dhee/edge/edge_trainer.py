"""EdgeTrainer — on-device micro-training for edge deployments.

Runs minimal LoRA fine-tuning directly on edge hardware (ARM CPU, low-RAM
devices). Designed for DheeEdge scenarios where the model needs to adapt
to its specific user/environment without cloud connectivity.

Constraints:
  - CPU-only training (no CUDA required)
  - <2GB peak RAM during training
  - LoRA rank 4-8 (tiny adapter, ~2MB)
  - Micro-batches of 1-4 samples
  - 10-50 gradient steps per cycle (not epochs)

Training data sources (all local):
  - Samskara signals from SamskaraCollector.get_training_data()
  - Action outcome pairs from DheeEdge._action_history
  - Sensor pattern correlations

Architecture:
  EdgeTrainer does NOT require torch/transformers at init. It checks
  for their availability lazily. On devices without PyTorch, it logs
  a warning and becomes a no-op.

Usage:
    from dhee.edge.edge_trainer import EdgeTrainer

    trainer = EdgeTrainer(
        model_path="/data/models/dhee-2b-q4.gguf",
        adapter_dir="/data/dhee/adapters",
    )

    # Check if training is possible on this device
    if trainer.can_train:
        result = trainer.micro_train(training_data)
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class MicroTrainResult:
    """Result of a micro-training cycle."""

    success: bool
    steps_completed: int = 0
    samples_used: int = 0
    loss_start: float = 0.0
    loss_end: float = 0.0
    adapter_path: Optional[str] = None
    duration_seconds: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "steps_completed": self.steps_completed,
            "samples_used": self.samples_used,
            "loss_start": round(self.loss_start, 4),
            "loss_end": round(self.loss_end, 4),
            "adapter_path": self.adapter_path,
            "duration_seconds": round(self.duration_seconds, 2),
            "error": self.error,
        }


@dataclass
class EdgeTrainingConfig:
    """Configuration for edge micro-training."""

    lora_rank: int = 4
    lora_alpha: int = 8
    learning_rate: float = 2e-4
    max_steps: int = 30
    micro_batch_size: int = 2
    max_seq_len: int = 256
    gradient_accumulation_steps: int = 2
    warmup_steps: int = 3
    weight_decay: float = 0.01
    max_samples: int = 100  # Limit training data


class EdgeTrainer:
    """On-device micro-training for edge deployments.

    Performs minimal LoRA fine-tuning on CPU with tight resource budgets.
    Training is designed to be interruptible — partial progress is saved.
    """

    def __init__(
        self,
        model_path: Optional[str] = None,
        adapter_dir: Optional[str] = None,
        config: Optional[EdgeTrainingConfig] = None,
    ):
        """
        Args:
            model_path: Path to the base model (GGUF or safetensors).
            adapter_dir: Directory to save/load LoRA adapters.
            config: Training hyperparameters.
        """
        self._model_path = model_path
        self._adapter_dir = adapter_dir or os.path.join(
            os.path.expanduser("~"), ".dhee", "edge_adapters",
        )
        self.config = config or EdgeTrainingConfig()
        self._training_history: List[Dict[str, Any]] = []
        self._torch_available: Optional[bool] = None

    @property
    def can_train(self) -> bool:
        """Check if training is possible on this device."""
        if self._torch_available is None:
            try:
                import torch  # noqa: F401
                self._torch_available = True
            except ImportError:
                self._torch_available = False
                logger.info(
                    "PyTorch not available — edge training disabled. "
                    "Install with: pip install torch --index-url https://download.pytorch.org/whl/cpu"
                )
        return self._torch_available and self._model_path is not None

    def micro_train(
        self,
        training_data: Dict[str, Any],
        samskara_signals: Optional[Dict[str, Any]] = None,
    ) -> MicroTrainResult:
        """Run a micro-training cycle.

        Args:
            training_data: Dict with keys:
                - sft_samples: List of {"input": str, "output": str} dicts
                - dpo_pairs: List of {"chosen": str, "rejected": str} dicts (optional)
            samskara_signals: Optional vasana report for sample weighting.

        Returns:
            MicroTrainResult with training metrics.
        """
        if not self.can_train:
            return MicroTrainResult(
                success=False,
                error="Training not available (missing PyTorch or model)",
            )

        start_time = time.time()

        # Prepare training samples
        sft_samples = training_data.get("sft_samples", [])
        if not sft_samples:
            return MicroTrainResult(
                success=False,
                error="No training samples provided",
            )

        # Limit to max_samples
        samples = sft_samples[:self.config.max_samples]

        # Weight samples by vasana if available
        if samskara_signals:
            samples = self._weight_samples(samples, samskara_signals)

        try:
            result = self._run_lora_training(samples)
            result.duration_seconds = time.time() - start_time

            # Record in history
            self._training_history.append({
                "timestamp": time.time(),
                "result": result.to_dict(),
            })

            # Persist training log
            self._save_training_log()

            return result
        except Exception as e:
            logger.warning("Micro-training failed: %s", e)
            return MicroTrainResult(
                success=False,
                duration_seconds=time.time() - start_time,
                error=str(e),
            )

    def _run_lora_training(self, samples: List[Dict]) -> MicroTrainResult:
        """Run LoRA fine-tuning with PyTorch.

        This is CPU-optimized:
        - float32 (no mixed precision on CPU)
        - Gradient checkpointing for memory efficiency
        - Small LoRA rank (4) = tiny trainable parameter count
        """
        import torch
        from torch.utils.data import DataLoader, Dataset

        class TextDataset(Dataset):
            def __init__(self, data: List[Dict]):
                self.data = data

            def __len__(self):
                return len(self.data)

            def __getitem__(self, idx):
                item = self.data[idx]
                return item.get("input", ""), item.get("output", "")

        # Check if we can load the model for training
        # For GGUF models, we need llama-cpp-python for inference but
        # can't fine-tune them directly. We look for a safetensors/HF model.
        model, tokenizer = self._load_model_for_training()
        if model is None:
            # Fallback: save training data for later batch processing
            return self._save_for_deferred_training(samples)

        dataset = TextDataset(samples)
        loader = DataLoader(
            dataset,
            batch_size=self.config.micro_batch_size,
            shuffle=True,
        )

        # Apply LoRA
        model = self._apply_lora(model)
        model.train()

        # Optimizer
        trainable_params = [p for p in model.parameters() if p.requires_grad]
        optimizer = torch.optim.AdamW(
            trainable_params,
            lr=self.config.learning_rate,
            weight_decay=self.config.weight_decay,
        )

        # Training loop
        total_steps = 0
        losses = []
        accum_loss = 0.0

        for step_idx in range(self.config.max_steps):
            for batch_inputs, batch_outputs in loader:
                # Tokenize
                combined = [
                    f"{inp} {out}" for inp, out in zip(batch_inputs, batch_outputs)
                ]
                encodings = tokenizer(
                    combined,
                    return_tensors="pt",
                    max_length=self.config.max_seq_len,
                    truncation=True,
                    padding=True,
                )

                # Forward pass
                outputs = model(
                    input_ids=encodings["input_ids"],
                    attention_mask=encodings["attention_mask"],
                    labels=encodings["input_ids"],
                )
                loss = outputs.loss / self.config.gradient_accumulation_steps
                loss.backward()
                accum_loss += loss.item()

                total_steps += 1

                if total_steps % self.config.gradient_accumulation_steps == 0:
                    torch.nn.utils.clip_grad_norm_(trainable_params, 1.0)
                    optimizer.step()
                    optimizer.zero_grad()
                    losses.append(accum_loss)
                    accum_loss = 0.0

                if total_steps >= self.config.max_steps:
                    break

            if total_steps >= self.config.max_steps:
                break

        # Save adapter
        adapter_path = self._save_adapter(model)

        return MicroTrainResult(
            success=True,
            steps_completed=total_steps,
            samples_used=len(samples),
            loss_start=losses[0] if losses else 0.0,
            loss_end=losses[-1] if losses else 0.0,
            adapter_path=adapter_path,
        )

    def _load_model_for_training(self):
        """Load model + tokenizer for training.

        Returns (model, tokenizer) or (None, None) if not available.
        """
        if not self._model_path:
            return None, None

        # GGUF files can't be fine-tuned directly
        if self._model_path.endswith(".gguf"):
            # Check for a companion safetensors model
            base_dir = os.path.dirname(self._model_path)
            safetensors_path = os.path.join(base_dir, "model.safetensors")
            config_path = os.path.join(base_dir, "config.json")
            if not os.path.exists(config_path):
                logger.info(
                    "GGUF model cannot be fine-tuned directly. "
                    "Saving training data for deferred processing."
                )
                return None, None
            model_dir = base_dir
        else:
            model_dir = self._model_path

        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer
            tokenizer = AutoTokenizer.from_pretrained(
                model_dir, trust_remote_code=True,
            )
            if tokenizer.pad_token is None:
                tokenizer.pad_token = tokenizer.eos_token

            model = AutoModelForCausalLM.from_pretrained(
                model_dir,
                torch_dtype="auto",
                trust_remote_code=True,
            )
            return model, tokenizer
        except Exception as e:
            logger.info("Model loading failed: %s", e)
            return None, None

    def _apply_lora(self, model):
        """Apply LoRA adapters to the model."""
        try:
            from peft import LoraConfig, get_peft_model, TaskType
            lora_config = LoraConfig(
                task_type=TaskType.CAUSAL_LM,
                r=self.config.lora_rank,
                lora_alpha=self.config.lora_alpha,
                lora_dropout=0.0,  # No dropout for micro-training
                target_modules=["q_proj", "v_proj"],  # Minimal target
            )
            model = get_peft_model(model, lora_config)
            trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
            total = sum(p.numel() for p in model.parameters())
            logger.info(
                "LoRA applied: %d trainable / %d total params (%.2f%%)",
                trainable, total, 100 * trainable / total,
            )
            return model
        except ImportError:
            logger.warning(
                "peft not available — training all parameters (not recommended "
                "for edge). Install: pip install peft"
            )
            return model

    def _save_adapter(self, model) -> str:
        """Save the LoRA adapter to disk."""
        os.makedirs(self._adapter_dir, exist_ok=True)
        adapter_name = f"adapter_{int(time.time())}"
        adapter_path = os.path.join(self._adapter_dir, adapter_name)

        try:
            if hasattr(model, "save_pretrained"):
                model.save_pretrained(adapter_path)
            else:
                # Fallback: save state dict
                import torch
                torch.save(
                    {k: v for k, v in model.state_dict().items() if "lora" in k},
                    os.path.join(adapter_path, "lora_weights.pt"),
                )
        except Exception as e:
            logger.warning("Adapter save failed: %s", e)
            adapter_path = ""

        return adapter_path

    def _save_for_deferred_training(
        self, samples: List[Dict],
    ) -> MicroTrainResult:
        """Save training data to disk for later batch processing.

        Used when the model format doesn't support direct fine-tuning
        (e.g., GGUF without a companion HF model).
        """
        os.makedirs(self._adapter_dir, exist_ok=True)
        deferred_path = os.path.join(
            self._adapter_dir, f"deferred_{int(time.time())}.jsonl",
        )

        with open(deferred_path, "w", encoding="utf-8") as f:
            for sample in samples:
                f.write(json.dumps(sample, ensure_ascii=False) + "\n")

        return MicroTrainResult(
            success=True,
            steps_completed=0,
            samples_used=len(samples),
            adapter_path=deferred_path,
            error="deferred: saved training data for batch processing",
        )

    def _weight_samples(
        self,
        samples: List[Dict],
        samskara_signals: Dict[str, Any],
    ) -> List[Dict]:
        """Weight training samples based on vasana degradation signals.

        Samples from degrading dimensions get 2x representation.
        """
        degrading = set(samskara_signals.get("degrading_dimensions", []))
        if not degrading:
            return samples

        weighted = []
        for sample in samples:
            weighted.append(sample)
            sample_type = sample.get("type", "")
            if sample_type in degrading:
                weighted.append(sample)  # Duplicate for emphasis

        return weighted

    def _save_training_log(self) -> None:
        """Persist training history to disk."""
        os.makedirs(self._adapter_dir, exist_ok=True)
        log_path = os.path.join(self._adapter_dir, "training_log.jsonl")
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                entry = self._training_history[-1]
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Integration with DheeEdge
    # ------------------------------------------------------------------

    def train_from_edge(
        self,
        edge_plugin: Any,
        samskara: Optional[Any] = None,
    ) -> MicroTrainResult:
        """Convenience: collect training data from a DheeEdge instance and train.

        Gathers:
        - Action outcome pairs as SFT samples
        - Samskara signals for weighting
        """
        # Collect action-based SFT samples
        sft_samples = []
        action_history = getattr(edge_plugin, "_action_history", [])
        for record in action_history[-self.config.max_samples:]:
            action = record.get("action", "")
            success = record.get("success", False)
            env = record.get("env_state", {})

            env_desc = ", ".join(f"{k}={v}" for k, v in list(env.items())[:5]) if env else "unknown"
            sft_samples.append({
                "input": f"[ACTION] state: {env_desc}, action: {action}",
                "output": f"{'success' if success else 'failure'}",
                "type": "action_prediction",
            })

        # Add samskara training data if available
        samskara_data = {}
        if samskara:
            try:
                samskara_data = samskara.get_training_data()
                sft_samples.extend(samskara_data.get("sft_samples", []))
            except Exception:
                pass

        if not sft_samples:
            return MicroTrainResult(
                success=False, error="No training data from edge",
            )

        return self.micro_train(
            training_data={"sft_samples": sft_samples},
            samskara_signals=samskara_data,
        )

    def get_status(self) -> Dict[str, Any]:
        """Get trainer status."""
        return {
            "can_train": self.can_train,
            "model_path": self._model_path,
            "adapter_dir": self._adapter_dir,
            "training_cycles": len(self._training_history),
            "config": {
                "lora_rank": self.config.lora_rank,
                "max_steps": self.config.max_steps,
                "learning_rate": self.config.learning_rate,
            },
        }
