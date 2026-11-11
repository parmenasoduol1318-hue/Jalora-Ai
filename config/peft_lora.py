#!/usr/bin/env python
"""PEFT LoRA training script for Jalora - CPU optimized.

Simple training without datasets library dependencies.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments
from peft import LoraConfig, get_peft_model


def load_jsonl(path: str) -> list[dict[str, Any]]:
    """Load training data from JSONL file."""
    with open(path, 'r', encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def format_prompt(example: dict[str, Any]) -> str:
    """Format training example as expected by the model."""
    instruction = example.get("instruction", "")
    input_text = example.get("input", "")
    output = example.get("output", "")
    
    if input_text:
        return f"<|im_start|>user\n{instruction}\n{input_text}<|im_end|>\n<|im_start|>assistant\n{output}<|im_end|>"
    return f"<|im_start|>user\n{instruction}<|im_end|>\n<|im_start|>assistant\n{output}<|im_end|>"


def main():
    # Configuration - use smaller model for CPU training
    MODEL_NAME = os.environ.get("MODEL_NAME", "Qwen/Qwen1.5-0.5B-Instruct")
    TRAIN_DATA_PATH = Path(os.environ.get("TRAIN_DATA", "training_data/jalora_train.jsonl"))
    OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "lora_adapters/jalora_lora"))
    
    # Check for GPU availability
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")
    print(f"Model: {MODEL_NAME}")
    print(f"Training data: {TRAIN_DATA_PATH}")
    print(f"Output directory: {OUTPUT_DIR}")
    
    # Load training data
    print("\nLoading training data...")
    raw_data = load_jsonl(str(TRAIN_DATA_PATH))
    print(f"Loaded {len(raw_data)} training examples")
    
    # Format data for training
    print("Formatting training data...")
    formatted_data = [format_prompt(ex) for ex in raw_data]
    print(f"Formatted {len(formatted_data)} examples")
    
    # Load tokenizer
    print("\nLoading tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, use_fast=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    print(f"Tokenizer loaded: {len(tokenizer)} tokens")
    
    # Load model
    print("Loading model...")
    if device.type == "cuda":
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_NAME,
            load_in_4bit=True,
            device_map="auto",
            torch_dtype=torch.bfloat16,
            trust_remote_code=True,
        )
    else:
        # CPU fallback - use float32
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_NAME,
            device_map="cpu",
            torch_dtype=torch.float32,
            trust_remote_code=True,
        )
    print(f"Model loaded")
    
    # Configure LoRA - small r for CPU training
    lora_config = LoraConfig(
        r=8,  # Small for CPU training
        lora_alpha=16,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        lora_dropout=0.1,
        bias="none",
        task_type="CAUSAL_LM",
    )
    
    # Apply LoRA to model
    model = get_peft_model(model, lora_config)
    print(f"LoRA applied: r={lora_config.r}, alpha={lora_config.alpha}")
    
    # Create output directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # Training loop - simple PyTorch
    print("\nStarting training...")
    model.train()
    
    optimizer = torch.optim.AdamW(model.parameters(), lr=2e-5)
    
    batch_size = 1
    accum_steps = 4
    num_epochs = 1  # Just 1 epoch for CPU test
    
    total_steps = 0
    num_examples = len(formatted_data)
    
    for epoch in range(num_epochs):
        print(f"Epoch {epoch + 1}/{num_epochs}")
        
        # Process in batches
        for i in range(0, num_examples, batch_size * accum_steps):
            batch_texts = formatted_data[i:i + batch_size * accum_steps]
            
            # Tokenize batch
            batch = tokenizer(
                batch_texts,
                truncation=True,
                max_length=512,
                padding=True,
                return_tensors="pt"
            )
            batch = {k: v.to(device) for k, v in batch.items()}
            batch["labels"] = batch["input_ids"].clone()
            
            # Forward pass
            outputs = model(**batch)
            loss = outputs.loss / accum_steps
            loss.backward()
            
            # Update
            if (i // (batch_size * accum_steps) + 1) % accum_steps == 0:
                optimizer.step()
                optimizer.zero_grad()
                total_steps += 1
                
                if total_steps % 5 == 0:
                    print(f"  Step {total_steps}, Loss: {loss.item() * accum_steps:.4f}")
    
    # Save adapter
    print(f"\nSaving LoRA adapter to {OUTPUT_DIR}...")
    model.save_pretrained(str(OUTPUT_DIR))
    tokenizer.save_pretrained(str(OUTPUT_DIR))
    print("Training complete!")
    print(f"Total training steps: {total_steps}")


if __name__ == "__main__":
    main()