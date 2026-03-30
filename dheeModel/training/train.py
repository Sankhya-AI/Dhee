"""DheeModel Training — QLoRA fine-tuning of Qwen3.5-0.8B via Unsloth.

Usage:
    python -m dhee.training.train --data_dir ~/.dhee/training_data

Produces a GGUF Q4_K_M model for CPU inference via llama.cpp.
"""

import argparse
import json
import logging
import os
import sys
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_BASE_MODEL = "Qwen/Qwen3.5-0.8B"
_DEFAULT_OUTPUT_DIR = os.path.join(os.path.expanduser("~"), ".dhee", "models")
_DEFAULT_DATA_DIR = os.path.join(os.path.expanduser("~"), ".dhee", "training_data")


def train(
    base_model: str = _DEFAULT_BASE_MODEL,
    data_dir: str = _DEFAULT_DATA_DIR,
    output_dir: str = _DEFAULT_OUTPUT_DIR,
    epochs: int = 3,
    batch_size: int = 4,
    learning_rate: float = 2e-4,
    lora_r: int = 16,
    lora_alpha: int = 16,
    lora_dropout: float = 0.1,
    max_seq_length: int = 4096,
    quantization: str = "Q4_K_M",
    use_unsloth: bool = True,
) -> dict:
    """Fine-tune Qwen3.5-0.8B with QLoRA and export GGUF.

    Steps:
    1. Load base model with 4-bit quantization (via Unsloth or transformers)
    2. Apply QLoRA adapters
    3. Train on instruction-tuning data
    4. Merge adapters
    5. Export to GGUF for llama.cpp
    """
    os.makedirs(output_dir, exist_ok=True)

    train_path = os.path.join(data_dir, "train.jsonl")
    val_path = os.path.join(data_dir, "val.jsonl")

    if not os.path.exists(train_path):
        return {"error": f"Training data not found at {train_path}. Run data_formatter.py first."}

    # Load training data
    train_data = _load_jsonl(train_path)
    val_data = _load_jsonl(val_path) if os.path.exists(val_path) else []

    logger.info(
        "Training data: %d train, %d val samples",
        len(train_data), len(val_data),
    )

    if use_unsloth:
        return _train_unsloth(
            base_model=base_model,
            train_data=train_data,
            val_data=val_data,
            output_dir=output_dir,
            epochs=epochs,
            batch_size=batch_size,
            learning_rate=learning_rate,
            lora_r=lora_r,
            lora_alpha=lora_alpha,
            lora_dropout=lora_dropout,
            max_seq_length=max_seq_length,
            quantization=quantization,
        )
    else:
        return _train_transformers(
            base_model=base_model,
            train_data=train_data,
            val_data=val_data,
            output_dir=output_dir,
            epochs=epochs,
            batch_size=batch_size,
            learning_rate=learning_rate,
            lora_r=lora_r,
            lora_alpha=lora_alpha,
            max_seq_length=max_seq_length,
            quantization=quantization,
        )


def _train_unsloth(
    base_model: str,
    train_data: list,
    val_data: list,
    output_dir: str,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    lora_r: int,
    lora_alpha: int,
    lora_dropout: float,
    max_seq_length: int,
    quantization: str,
) -> dict:
    """Train with Unsloth (2x faster, 70% less VRAM)."""
    try:
        from unsloth import FastLanguageModel
        from datasets import Dataset
        from trl import SFTTrainer
        from transformers import TrainingArguments
    except ImportError as e:
        return {
            "error": f"Unsloth training requires: pip install unsloth datasets trl. Missing: {e}"
        }

    # Load model with 4-bit quantization
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=base_model,
        max_seq_length=max_seq_length,
        load_in_4bit=True,
    )

    # Apply LoRA
    model = FastLanguageModel.get_peft_model(
        model,
        r=lora_r,
        lora_alpha=lora_alpha,
        lora_dropout=lora_dropout,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
    )

    # Format dataset
    def _format_prompt(example):
        return {
            "text": f"<|im_start|>user\n{example['instruction']}<|im_end|>\n"
                    f"<|im_start|>assistant\n{example['output']}<|im_end|>"
        }

    train_dataset = Dataset.from_list(train_data).map(_format_prompt)
    val_dataset = Dataset.from_list(val_data).map(_format_prompt) if val_data else None

    # Training arguments
    training_args = TrainingArguments(
        output_dir=os.path.join(output_dir, "checkpoints"),
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        gradient_accumulation_steps=4,
        learning_rate=learning_rate,
        weight_decay=0.01,
        warmup_ratio=0.1,
        logging_steps=10,
        save_strategy="epoch",
        evaluation_strategy="epoch" if val_dataset else "no",
        fp16=True,
        optim="adamw_8bit",
        report_to="none",
    )

    # Train
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        args=training_args,
        dataset_text_field="text",
        max_seq_length=max_seq_length,
    )
    trainer.train()

    # Export GGUF
    gguf_path = os.path.join(output_dir, f"dhee-qwen3.5-0.8b-{quantization.lower()}.gguf")
    model.save_pretrained_gguf(
        output_dir,
        tokenizer,
        quantization_method=quantization.lower().replace("_", "-"),
    )

    logger.info("DheeModel trained and exported to %s", gguf_path)
    return {
        "model_path": gguf_path,
        "base_model": base_model,
        "epochs": epochs,
        "train_samples": len(train_data),
        "val_samples": len(val_data),
        "quantization": quantization,
    }


def _train_transformers(
    base_model: str,
    train_data: list,
    val_data: list,
    output_dir: str,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    lora_r: int,
    lora_alpha: int,
    max_seq_length: int,
    quantization: str,
) -> dict:
    """Fallback: train with standard transformers + PEFT."""
    try:
        from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments
        from peft import LoraConfig, get_peft_model
        from datasets import Dataset
        from trl import SFTTrainer
    except ImportError as e:
        return {
            "error": f"Training requires: pip install transformers peft datasets trl. Missing: {e}"
        }

    # Load model
    tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        load_in_4bit=True,
        device_map="auto",
        trust_remote_code=True,
    )

    # LoRA config
    lora_config = LoraConfig(
        r=lora_r,
        lora_alpha=lora_alpha,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        lora_dropout=0.1,
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_config)

    # Format dataset
    def _format_prompt(example):
        return {
            "text": f"<|im_start|>user\n{example['instruction']}<|im_end|>\n"
                    f"<|im_start|>assistant\n{example['output']}<|im_end|>"
        }

    train_dataset = Dataset.from_list(train_data).map(_format_prompt)

    training_args = TrainingArguments(
        output_dir=os.path.join(output_dir, "checkpoints"),
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        learning_rate=learning_rate,
        logging_steps=10,
        save_strategy="epoch",
        report_to="none",
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=train_dataset,
        args=training_args,
        dataset_text_field="text",
        max_seq_length=max_seq_length,
    )
    trainer.train()

    # Save merged model
    merged_dir = os.path.join(output_dir, "merged")
    model.merge_and_unload().save_pretrained(merged_dir)
    tokenizer.save_pretrained(merged_dir)

    logger.info("Model trained. Convert to GGUF with llama.cpp convert script.")
    return {
        "merged_model_dir": merged_dir,
        "note": "Run llama.cpp convert-hf-to-gguf.py to create GGUF",
    }


def _load_jsonl(path: str) -> list:
    """Load JSONL file."""
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def main():
    parser = argparse.ArgumentParser(description="Train DheeModel (Qwen3.5-0.8B QLoRA)")
    parser.add_argument("--base_model", default=_DEFAULT_BASE_MODEL)
    parser.add_argument("--data_dir", default=_DEFAULT_DATA_DIR)
    parser.add_argument("--output_dir", default=_DEFAULT_OUTPUT_DIR)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch_size", type=int, default=4)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--lora_r", type=int, default=16)
    parser.add_argument("--quantization", default="Q4_K_M")
    parser.add_argument("--no-unsloth", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    result = train(
        base_model=args.base_model,
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        lora_r=args.lora_r,
        quantization=args.quantization,
        use_unsloth=not args.no_unsloth,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
