#!/usr/bin/env python3
"""
Colab Training Script for Jalora LoRA Fine-Tuning
===================================================
Trains ALL 4 JSONL training data files in one unified run.

Files included (all optional — just upload what you have):
  1. jalora_comprehensive_all.jsonl  - COMPREHENSIVE: ALL ~766 examples in one file
     (covers every Jalora capability — recommended single upload)
  2. jalora_comprehensive_train.jsonl - 85% train split from comprehensive set
  3. jalora_comprehensive_val.jsonl   - 15% val split from comprehensive set
  4. jalora_train.jsonl         - Original training set (fallback)
  5. jalora_val.jsonl           - Original validation set (fallback)
  6. jalora_ollama_train.jsonl  - Ollama-verified responses (fallback)
  7. jalora_train_jalora.jsonl  - Jalora-specific training (fallback)

Usage in Colab:
  - Upload this script + jalora_comprehensive_all.jsonl to Colab (simplest)
  - OR upload the train/val split pair for best training results
  - OR upload the original 4 files (will be merged automatically)
  - Run all cells or copy-paste into your Colab notebook

Model: Qwen/Qwen1.5-7B-Instruct
"""
import json
import os
import random
import math
from pathlib import Path

import torch
from datasets import Dataset
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import (
    AutoTokenizer,
    AutoModelForCausalLM,
    TrainingArguments,
    Trainer,
    DataCollatorForLanguageModeling,
)

# ============================================================
# Cell 1: Install Dependencies (uncomment in Colab)
# ============================================================
# !pip install -q peft transformers accelerate trl datasets sentencepiece bitsandbytes

# ============================================================
# Cell 2: Configuration
# ============================================================
BASE_MODEL = "Qwen/Qwen1.5-7B-Instruct"
OUTPUT_DIR = "./jalora-lora-merged"
LORA_RANK = 64
LORA_ALPHA = 128
LORA_DROPOUT = 0.1
LEARNING_RATE = 2e-5
NUM_EPOCHS = 3
PER_DEVICE_BATCH_SIZE = 2
GRADIENT_ACCUMULATION_STEPS = 8
MAX_SEQ_LENGTH = 2048
SEED = 42

# ============================================================
# Cell 3: Load and Merge All Training Data
# ============================================================
def load_jsonl(path):
    """Load a JSONL file and return list of examples."""
    examples = []
    with open(path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  WARNING: Skipping line {line_num} in {path.name}: {e}")
                continue
            # Validate keys
            if not {"instruction", "input", "output"}.issuperset(obj.keys()):
                print(f"  WARNING: Skipping line {line_num} in {path.name}: missing keys")
                continue
            # Ensure input field exists
            obj.setdefault("input", "")
            obj.setdefault("instruction", "")
            obj.setdefault("output", "")
            examples.append(obj)
    return examples


def format_prompt(example):
    """Format example into conversation-style prompt."""
    instruction = example.get("instruction", "")
    user_input = example.get("input", "")
    output = example.get("output", "")

    # Combine instruction and input
    if user_input and user_input.strip():
        user_text = f"{instruction}\n\nContext:\n{user_input}"
    else:
        user_text = instruction

    # Qwen chat format
    prompt = f"<|im_start|>system\nYou are Jalora, a multimodal personal AI assistant.<|im_end|>\n<|im_start|>user\n{user_text}<|im_end|>\n<|im_start|>assistant\n{output}<|im_end|><|im_end|>"

    return {"text": prompt}


def merge_all_datasets():
    """Load and merge training data from all available JSONL files.

    Prefers the comprehensive dataset (single file with all capabilities).
    Falls back to individual files if comprehensive is not present.
    Deduplicates by (instruction, output) hash.
    """
    # File paths (adjust for Colab upload location)
    base = "."

    # Try multiple possible locations
    candidates = [
        Path(base),
        Path("/content"),
        Path("."),
    ]

    all_files = {}
    # Prefer comprehensive files first, fall back to original 4
    file_names = [
        "jalora_comprehensive_all.jsonl",
        "jalora_comprehensive_train.jsonl",
        "jalora_comprehensive_val.jsonl",
        "jalora_train.jsonl",
        "jalora_val.jsonl",
        "jalora_ollama_train.jsonl",
        "jalora_train_jalora.jsonl",
    ]

    for fname in file_names:
        for d in candidates:
            p = d / fname
            if p.exists():
                all_files[fname] = p
                print(f"  Found: {p}")
                break
        if fname not in all_files:
            print(f"  (not found: {fname})")

    if not all_files:
        print("ERROR: No training files found! Please upload JSONL files.")
        return [], []

    # Load all files
    train_examples = []
    val_examples = []

    # If comprehensive train/val split files exist, use those directly
    if "jalora_comprehensive_train.jsonl" in all_files:
        train_examples = load_jsonl(all_files["jalora_comprehensive_train.jsonl"])
        print(f"  Loaded {len(train_examples)} train examples from comprehensive split")
    if "jalora_comprehensive_val.jsonl" in all_files:
        val_examples = load_jsonl(all_files["jalora_comprehensive_val.jsonl"])
        print(f"  Loaded {len(val_examples)} val examples from comprehensive split")

    # If only the single comprehensive all-in-one file, use it for training
    # and auto-split 10% for validation
    if "jalora_comprehensive_all.jsonl" in all_files and not train_examples:
        all_exs = load_jsonl(all_files["jalora_comprehensive_all.jsonl"])
        train_examples = all_exs
        print(f"  Loaded {len(all_exs)} examples from comprehensive all-in-one")

    # Fallback: load individual files if no comprehensive data was found
    if not train_examples:
        train_file_names = [
            "jalora_train.jsonl",
            "jalora_ollama_train.jsonl",
            "jalora_train_jalora.jsonl",
        ]
        for fname in train_file_names:
            if fname in all_files:
                exs = load_jsonl(all_files[fname])
                train_examples.extend(exs)
                print(f"  Loaded {len(exs)} examples from {fname}")

        if "jalora_val.jsonl" in all_files:
            val_examples = load_jsonl(all_files["jalora_val.jsonl"])
            print(f"  Loaded {len(val_examples)} examples from jalora_val.jsonl")

    # If no validation data found, split 10% from training
    if not val_examples and train_examples:
        random.seed(SEED)
        shuffled = train_examples[:]
        random.shuffle(shuffled)
        split = int(len(shuffled) * 0.10)
        val_examples = shuffled[:split]
        train_examples = shuffled[split:]
        print(f"  Auto-split: {len(train_examples)} train + {len(val_examples)} val")

    print(f"\n  Total merged: {len(train_examples)} training + {len(val_examples)} validation")

    # Deduplicate by instruction+output
    seen = set()
    unique_train = []
    for ex in train_examples:
        key = hash((ex["instruction"], ex["output"]))
        if key not in seen:
            seen.add(key)
            unique_train.append(ex)

    unique_val = []
    for ex in val_examples:
        key = hash((ex["instruction"], ex["output"]))
        if key not in seen:  # Also check against train set
            seen.add(key)
            unique_val.append(ex)

    print(f"  Unique: {len(unique_train)} training + {len(unique_val)} validation")

    return unique_train, unique_val


# ============================================================
# Cell 4: Initialize Tokenizer and Model
# ============================================================
def load_model_and_tokenizer():
    """Load base model and tokenizer with 4-bit quantization."""
    print(f"Loading tokenizer: {BASE_MODEL}")
    tokenizer = AutoTokenizer.from_pretrained(
        BASE_MODEL,
        use_fast=True,
        padding_side="right",
        truncation_side="right",
    )
    tokenizer.pad_token = tokenizer.eos_token
    tokenizer.model_max_length = MAX_SEQ_LENGTH

    print(f"Loading model (4-bit QLoRA): {BASE_MODEL}")
    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        load_in_4bit=True,
        device_map="auto",
        torch_dtype=torch.bfloat16,
        trust_remote_code=True,
        use_cache=False,
    )

    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)

    print("Applying LoRA configuration:")
    peft_config = LoraConfig(
        r=LORA_RANK,
        lora_alpha=LORA_ALPHA,
        target_modules=[
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
        lora_dropout=LORA_DROPOUT,
        bias="none",
        task_type="CAUSAL_LM",
    )

    model = get_peft_model(model, peft_config)
    model.print_trainable_parameters()

    return model, tokenizer


# ============================================================
# Cell 5: Tokenize Data
# ============================================================
def tokenize_function(examples, tokenizer):
    """Tokenize prompt-completion pairs."""
    outputs = tokenizer(
        examples["text"],
        truncation=True,
        max_length=MAX_SEQ_LENGTH,
        padding="max_length",
    )
    # For causal LM, labels = input_ids
    outputs["labels"] = outputs["input_ids"].copy()
    return outputs


# ============================================================
# Cell 6: Train
# ============================================================
def train():
    """Main training function - run in Colab after uploading files."""
    print("=" * 70)
    print("  Jalora Comprehensive LoRA Training (all datasets merged)")
    print("=" * 70)

    # Set seed for reproducibility
    random.seed(SEED)
    torch.manual_seed(SEED)

    # 1. Load and merge all data
    print("\n[1/4] Loading and merging training data...")
    train_data, val_data = merge_all_datasets()

    if not train_data:
        print("ERROR: No training data found!")
        return

    # Format prompts
    print("\n[2/4] Formatting prompts...")
    train_texts = [format_prompt(ex) for ex in train_data]
    val_texts = [format_prompt(ex) for ex in val_data] if val_data else []

    train_dataset = Dataset.from_list(train_texts)
    val_dataset = Dataset.from_list(val_texts) if val_texts else None

    print(f"  Train samples: {len(train_dataset)}")
    if val_dataset:
        print(f"  Validation samples: {len(val_dataset)}")

    # 2. Load model
    print("\n[3/4] Loading model and tokenizer...")
    model, tokenizer = load_model_and_tokenizer()

    # 3. Tokenize
    print("\n[4/4] Tokenizing datasets...")
    train_dataset = train_dataset.map(
        lambda ex: tokenize_function(ex, tokenizer),
        batched=True,
        remove_columns=["text"],
    )
    if val_dataset:
        val_dataset = val_dataset.map(
            lambda ex: tokenize_function(ex, tokenizer),
            batched=True,
            remove_columns=["text"],
        )

    # 4. Training
    print("\nStarting training...")
    training_args = TrainingArguments(
        output_dir=OUTPUT_DIR,
        num_train_epochs=NUM_EPOCHS,
        per_device_train_batch_size=PER_DEVICE_BATCH_SIZE,
        per_device_eval_batch_size=PER_DEVICE_BATCH_SIZE * 2,
        gradient_accumulation_steps=GRADIENT_ACCUMULATION_STEPS,
        learning_rate=LEARNING_RATE,
        weight_decay=0.01,
        warmup_ratio=0.03,
        fp16=torch.cuda.is_available() and torch.cuda.get_device_capability()[0] < 8,
        bf16=torch.cuda.is_available() and torch.cuda.get_device_capability()[0] >= 8,
        logging_steps=10,
        eval_strategy="steps" if val_dataset else "no",
        eval_steps=50 if val_dataset else 0,
        save_steps=100,
        save_total_limit=2,
        load_best_model_at_end=True if val_dataset else False,
        metric_for_best_model="loss" if val_dataset else None,
        report_to="none",
        optim="paged_adamw_8bit",
    )

    data_collator = DataCollatorForLanguageModeling(
        tokenizer=tokenizer, mlm=False
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        data_collator=data_collator,
    )

    trainer.train()

    # Save the adapter
    print(f"\nSaving LoRA adapter to {OUTPUT_DIR}...")
    model.save_pretrained(OUTPUT_DIR)
    tokenizer.save_pretrained(OUTPUT_DIR)

    # Merge and save full model (optional - uncomment if you have enough VRAM after training)
    # print("\nMerging LoRA weights into base model...")
    # merged_model = model.merge_and_unload()
    # merged_model.save_pretrained(OUTPUT_DIR + "_merged")
    # tokenizer.save_pretrained(OUTPUT_DIR + "_merged")

    print("\n" + "=" * 70)
    print("  TRAINING COMPLETE!")
    print(f"  LoRA adapter saved to: {OUTPUT_DIR}")
    print("  Files: adapter_model.safetensors, adapter_config.json")
    print("=" * 70)


# ============================================================
# Cell 7: Run Training
# ============================================================
if __name__ == "__main__":
    train()
else:
    print("Import successful. Run train() to start training, or call merge_all_datasets() to inspect data.")
