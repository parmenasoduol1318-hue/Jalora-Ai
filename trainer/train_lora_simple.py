#!/usr/bin/env python
"""
Simple PEFT LoRA Training Script for Google Colab
===================================================

This script provides a streamlined training workflow for Jalora LoRA fine-tuning.
Designed to be run in Google Colab with GPU acceleration.

Usage in Colab:
    1. Set your HuggingFace token
    2. Run this script: python train_lora_simple.py

Environment Variables:
    HF_TOKEN      - HuggingFace access token
    MODEL_NAME    - Model to use (default: Qwen/Qwen1.5-1.5B-Instruct)
    OUTPUT_DIR    - Where to save adapters (default: jalora_lora_adapters)
    EPOCHS        - Number of training epochs (default: 3)
    BATCH_SIZE    - Training batch size (default: 2)
"""

import os
import json
from pathlib import Path
from typing import Any

import torch
from peft import LoraConfig, get_peft_model
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    TrainingArguments,
    Trainer,
)
from datasets import Dataset


# =============================================================================
# CONFIGURATION
# =============================================================================

# Model selection based on available VRAM
# Uncomment one based on your GPU:
MODEL_CONFIGS = {
    "0.5B": {
        "name": "Qwen/Qwen1.5-0.5B-Instruct",
        "min_vram_gb": 1,
        "description": "Fast training, basic quality"
    },
    "1.5B": {
        "name": "Qwen/Qwen1.5-1.5B-Instruct", 
        "min_vram_gb": 2,
        "description": "Medium training time, good quality"
    },
    "7B": {
        "name": "Qwen/Qwen1.5-7B-Instruct",
        "min_vram_gb": 4,
        "description": "Slow training, best quality"
    }
}

# Default model (change based on VRAM)
MODEL_NAME = os.environ.get("MODEL_NAME", MODEL_CONFIGS["1.5B"]["name"])
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "jalora_lora_adapters"))
TRAIN_DATA = Path("training_data/jalora_train.jsonl")
VAL_DATA = Path("training_data/jalora_val.jsonl")
HF_TOKEN = os.environ.get("HF_TOKEN", None)

# Training hyperparameters
EPOCHS = int(os.environ.get("EPOCHS", "3"))
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "2"))
GRADIENT_ACCUM = 4
LEARNING_RATE = 2e-5

print("=" * 60)
print("JALORA PEFT TRAINING CONFIGURATION")
print("=" * 60)
print(f"Model: {MODEL_NAME}")
print(f"Output: {OUTPUT_DIR}")
print(f"Epochs: {EPOCHS}")
print(f"Batch size: {BATCH_SIZE}")
print(f"Learning rate: {LEARNING_RATE}")


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load JSONL file into list of dictionaries."""
    if not path.exists():
        raise FileNotFoundError(f"Training data not found: {path}")
    
    with open(path, 'r', encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def format_prompt(example: dict[str, Any]) -> str:
    """Format training example for Qwen models."""
    instruction = example.get("instruction", "")
    input_text = example.get("input", "")
    output = example.get("output", "")
    
    if input_text:
        return f"<|im_start|>user\n{instruction}\n{input_text}<|im_end|>\n<|im_start|>assistant\n{output}<|im_end|>"
    return f"<|im_start|>user\n{instruction}<|im_end|>\n<|im_start|>assistant\n{output}<|im_end|>"


def check_gpu():
    """Check GPU availability and memory."""
    print("\n" + "=" * 60)
    print("GPU CHECK")
    print("=" * 60)
    
    print(f"PyTorch version: {torch.__version__}")
    print(f"CUDA available: {torch.cuda.is_available()}")
    
    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        gpu_mem = torch.cuda.get_device_properties(0).total_memory / 1e9
        print(f"GPU: {gpu_name}")
        print(f"VRAM: {gpu_mem:.1f} GB")
        return True
    else:
        print("Warning: No GPU detected. Training will be CPU-only (very slow).")
        return False


# =============================================================================
# TRAINING FUNCTIONS
# =============================================================================

def train_lora():
    """Main training function."""
    
    # Check GPU
    has_gpu = check_gpu()
    device = "cuda" if has_gpu else "cpu"
    
    # Load training data
    print("\n" + "=" * 60)
    print("LOADING DATA")
    print("=" * 60)
    
    train_data = load_jsonl(TRAIN_DATA)
    print(f"Training examples: {len(train_data)}")
    
    if VAL_DATA.exists():
        val_data = load_jsonl(VAL_DATA)
        print(f"Validation examples: {len(val_data)}")
    
    # Format data
    print("Formatting prompts...")
    formatted_train = [{"text": format_prompt(ex)} for ex in train_data]
    train_dataset = Dataset.from_list(formatted_train)
    
    # Load tokenizer
    print("\n" + "=" * 60)
    print("LOADING MODEL")
    print("=" * 60)
    
    tokenizer = AutoTokenizer.from_pretrained(
        MODEL_NAME,
        use_fast=True,
        token=HF_TOKEN,
        trust_remote_code=True
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    print(f"Tokenizer: {len(tokenizer)} tokens")
    
    # Load model with 4-bit quantization for GPU
    if has_gpu:
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_NAME,
            load_in_4bit=True,
            device_map="auto",
            torch_dtype=torch.float16,
            token=HF_TOKEN,
            trust_remote_code=True
        )
        print("Model loaded with 4-bit quantization")
    else:
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_NAME,
            device_map="cpu",
            torch_dtype=torch.float32,
            token=HF_TOKEN,
            trust_remote_code=True
        )
        print("Model loaded (float32, CPU)")
    
    # Configure LoRA
    # Smaller r for CPU, larger for GPU
    lora_r = 4 if not has_gpu else 64
    
    lora_config = LoraConfig(
        r=lora_r,
        lora_alpha=16,
        target_modules=[
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj"
        ],
        lora_dropout=0.1,
        bias="none",
        task_type="CAUSAL_LM"
    )
    
    model = get_peft_model(model, lora_config)
    
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"LoRA config: r={lora_r}, alpha=16")
    print(f"Trainable: {trainable:,} / {total:,} ({trainable/total*100:.2f}%)")
    
    # Training arguments
    training_args = TrainingArguments(
        output_dir=str(OUTPUT_DIR),
        num_train_epochs=EPOCHS,
        per_device_train_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRADIENT_ACCUM,
        learning_rate=LEARNING_RATE,
        lr_scheduler_type="cosine",
        warmup_ratio=0.1,
        logging_steps=5,
        save_steps=10,
        save_total_limit=2,
        report_to="none",
        fp16=has_gpu,
        bf16=has_gpu,
        optim="adamw_torch",
        dataloader_num_workers=0,  # Windows compatibility
    )
    
    # Create trainer
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        tokenizer=tokenizer,
    )
    
    # Train
    print("\n" + "=" * 60)
    print("STARTING TRAINING")
    print("=" * 60)
    trainer.train()
    
    # Save
    print(f"\nSaving adapter to {OUTPUT_DIR}...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(OUTPUT_DIR))
    tokenizer.save_pretrained(str(OUTPUT_DIR))
    
    print("\n" + "=" * 60)
    print("TRAINING COMPLETE")
    print("=" * 60)
    print(f"Adapter saved to: {OUTPUT_DIR}")
    print(f"To use: model = PeftModel.from_pretrained(base_model, '{OUTPUT_DIR}')")


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    train_lora()