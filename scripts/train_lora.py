#!/usr/bin/env python
"""QLoRA/LoRA training script for Jalora.

This script provides templates for different training approaches:
1. Unsloth (fastest, Windows/macOS/Linux) - RECOMMENDED
2. Axolotl (HuggingFace-native)
3. HuggingFace PEFT (basic)

Prerequisites:
- Choose ONE approach and install its dependencies

For Unsloth (recommended for this hardware):
  pip install "unsloth[accelerate]" torch --index-url https://download.pytorch.org/whl/cu121

For Axolotl (Linux only, needs CUDA):
  pip install "axolotl[flash-attn]"

For basic PEFT (CPU/GPU compatible):
  pip install peft transformers accelerate bitsandbytes

Usage:
  python scripts/train_lora.py --config config/unsloth_lora.yml
  python scripts/train_lora.py --config config/axolotl_lora.yml
  python scripts/train_lora.py --config config/peft_lora.py
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any


# Training configuration templates
UNSLOTH_CONFIG = """
# Unsloth QLoRA Training Configuration

# Base model (Qwen2.5 7B in Q4_K_M format via Ollama or HuggingFace)
base_model: "Qwen/Qwen1.5-7B"  # Or "microsoft/DialoGPT-medium" for testing

# LoRA adapter settings
lora_r: 32
lora_alpha: 64
lora_dropout: 0.1

# Quantization
load_in_4bit: true
bnb_4bit_quant_type: "nf4"
bnb_4bit_compute_dtype: "float16"
bnb_4bit_use_double_quant: true

# Training parameters
per_device_train_batch_size: 4
gradient_accumulation_steps: 4
num_train_epochs: 3
learning_rate: 2e-4
lr_scheduler_type: "cosine"
warmup_steps: 100

# Output
output_dir: "./lora_adapters/jalora_qwen_lora"
logging_steps: 10
save_strategy: "steps"
save_steps: 500

# Dataset paths (JSONL format)
train_dataset: "./training_data/jalora_train.jsonl"
eval_dataset: "./training_data/jalora_val.jsonl"
"""

AXOLOTL_CONFIG = """
# Axolotl QLoRA Configuration

# Model settings
model_name_or_path: Qwen/Qwen1.5-7B

# Dataset
dataset: ./training_data/jalora_train.jsonl
dataset_ar: ./training_data/jalora_val.jsonl
dataset_on_disk: false

# Fine-tuning
sequence_len: 2048
micro_batch_size: 2
gradient_accumulation_steps: 4
num_epochs: 3
learning_rate: 2e-5
lr_scheduler: cosine
warmup_ratio: 0.1

# LoRA
lora_r: 64
lora_alpha: 16
lora_dropout: 0.1

# QLoRA
qlora: true
bits: 4
quant_type: nf4
double_quant: true
quant_method: bitsandbytes

# Output
output_dir: ./lora_adapters/jalora_qwen_lora
"""

PEFT_TRAINING_SCRIPT = '''
#!/usr/bin/env python
"""Basic PEFT LoRA training script for Jalora.

Works with CPU or GPU and requires peft, transformers, accelerate.
For Windows with no GPU, this falls back to CPU training (slow but works).
"""
import os
import json
from pathlib import Path
from typing import Any

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments
from transformers import DataCollatorForLanguageModeling
from trl import SFTTrainer


def load_jsonl(path: str) -> list[dict[str, Any]]:
    """Load training data from JSONL file."""
    with open(path, 'r', encoding='utf-8') as f:
        return [json.loads(line) for line in f]


def format_prompt(example: dict[str, Any]) -> str:
    """Format training example as expected by the model."""
    instruction = example.get("instruction", "")
    input_text = example.get("input", "")
    output = example.get("output", "")
    
    if input_text:
        return f"<|im_start|>user\\n{instruction}\\n{input_text}<|im_end|>\\n<|im_start|>assistant\\n{output}<|im_end|>"
    return f"<|im_start|>user\\n{instruction}<|im_end|>\\n<|im_start|>assistant\\n{output}<|im_end|>"


def main():
    # Configuration
    MODEL_NAME = os.environ.get("MODEL_NAME", "Qwen/Qwen1.5-7B")
    TRAIN_DATA = Path("training_data/jalora_train.jsonl")
    OUTPUT_DIR = Path("lora_adapters/jalora_qwen_lora")
    
    # Check for GPU availability
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")
    
    # Load tokenizer
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, use_fast=True)
    tokenizer.pad_token = tokenizer.eos_token
    
    # Load model with 4-bit quantization if GPU available
    if device.type == "cuda":
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_NAME,
            load_in_4bit=True,
            device_map="auto",
            torch_dtype=torch.bfloat16,
        )
    else:
        # CPU fallback - no quantization
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_NAME,
            device_map="cpu",
            torch_dtype=torch.float32,
        )
    
    # Load training data
    train_data = load_jsonl(str(TRAIN_DATA))
    print(f"Loaded {len(train_data)} training examples")
    
    # Training arguments
    training_args = TrainingArguments(
        output_dir=str(OUTPUT_DIR),
        num_train_epochs=3,
        per_device_train_batch_size=1 if device.type == "cpu" else 4,
        gradient_accumulation_steps=8 if device.type == "cpu" else 4,
        learning_rate=2e-5,
        lr_scheduler_type="cosine",
        warmup_ratio=0.1,
        logging_steps=10,
        save_steps=100,
        save_total_limit=2,
        report_to="none",
        fp16=device.type == "cuda",
        bf16=False,
        optim="adamw_torch",
    )
    
    # Data collator
    collator = DataCollatorForLanguageModeling(tokenizer, mlm=False)
    
    # Create trainer
    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=train_data,
        data_collator=collator,
        dataset_text_field="text",
        max_seq_length=2048,
    )
    
    # Train
    trainer.train()
    
    # Save adapter
    trainer.save_model(str(OUTPUT_DIR))
    print(f"LoRA adapter saved to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
'''


def write_template_configs(base_dir: Path) -> None:
    """Write training configuration templates."""
    config_dir = base_dir / "config"
    config_dir.mkdir(exist_ok=True)
    
    # Write Unsloth config (YAML format for Axolotl/Unsloth)
    (config_dir / "unsloth_lora.yml").write_text(UNSLOTH_CONFIG)
    
    # Write Axolotl config
    (config_dir / "axolotl_lora.yml").write_text(AXOLOTL_CONFIG)
    
    # Write PEFT training script
    (config_dir / "peft_lora.py").write_text(PEFT_TRAINING_SCRIPT)
    
    print(f"Configuration files written to {config_dir}")


def create_makefile(base_dir: Path) -> None:
    """Create a Makefile with common training commands."""
    makefile = """# Jalora LoRA Training Makefile

.PHONY: train unsloth axolotl peft info clean

train: unsloth
\t@echo "Training complete"

unsloth:
\tpip install "unsloth[accelerate]" torch
\tpython -c "from unsloth import FastLanguageModel; print('Unsloth installed successfully')"
\t@echo "Ready to train with Unsloth"

axolotl:
\tpip install "axolotl[flash-attn]"
\tpython -c "import axolotl; print('Axolotl installed successfully')"
\t@echo "Ready to train with Axolotl"

peft:
\tpip install peft transformers accelerate trl
\tpython scripts/train_lora.py --config config/peft_lora.py

info:
\t@echo "Jalora Training Environment"
\t@pip show transformers 2>/dev/null | grep Version || echo "transformers not installed"
\t@pip show peft 2>/dev/null | grep Version || echo "peft not installed"
\t@pip show unsloth 2>/dev/null | grep Version || echo "unsloth not installed"
\t@python -c "import torch; print(f'PyTorch: {torch.__version__}')"
\t@python -c "import accelerate; print(f'Accelerate: {accelerate.__version__}')"

clean:
\trm -rf lora_adapters/
\trm -rf .cache/huggingface/
"""
    (base_dir / "Makefile").write_text(makefile)


def create_readme(base_dir: Path) -> None:
    """Create training documentation."""
    readme = """# Jalora LoRA/QLoRA Training Guide

## Prerequisites

### Hardware Requirements
- **GPU (Recommended):** Minimum 8GB VRAM for 7B models with QLoRA
- **CPU-only:** Will work with PEFT (slower, no quantization)
- **For Windows without GPU:** Use PEFT mode (no bitsandbytes support on Windows)

### Software Requirements
- Python 3.10-3.12
- CUDA 11.8+ (if using GPU)
- Ollama or HuggingFace access token

## Quick Start

### Option 1: Unsloth (Recommended - Fast, Windows/Mac/Linux compatible)
```bash
pip install "unsloth[accelerate]" torch
python scripts/train_lora.py --config config/unsloth_lora.yml
```

### Option 2: PEFT (Universal, works everywhere)
```bash
pip install peft transformers accelerate trl
python scripts/train_lora.py --config config/peft_lora.py
```

### Option 3: Axolotl (Linux/CUDA only)
```bash
pip install "axolotl[flash-attn]"
axolotl train config/axolotl_lora.yml
```

## Training Data Format

The training data should be in JSONL format:
```json
{"instruction": "Question or task", "input": "Optional context", "output": "Expected response"}
{"instruction": "Another question", "input": "", "output": "Expected response"}
```

Generated training data is in `training_data/jalora_train.jsonl`.

## QLoRA Workflow

1. **Prepare training data** (already done):
   ```bash
   python scripts/generate_training_data.py
   ```

2. **Choose your training method** (see Quick Start above)

3. **Run training**:
   ```bash
   python scripts/train_lora.py --config config/peft_lora.py
   ```

4. **Export to GGUF** (after training completes):
   ```bash
   # Use llama.cpp conversion scripts or HuggingFace export
   ```

5. **Quantize to Q4_K_M**:
   ```bash
   # Use llama.cpp or ollama for quantization
   ```

6. **Create Ollama Modelfile**:
   ```
   FROM ./my-finetuned-model-q4_k_m.gguf
   PARAMETER temperature 0.3
   PARAMETER num_ctx 8192
   SYSTEM "You are Jalora AI, a multimodal personal AI assistant..."
   ```
   
   ```bash
   ollama create jalora-finetuned -f Modelfile
   ```

## Configuration Parameters

### Key LoRA Parameters
- `lora_r`: Rank of the LoRA adapter (32-64 recommended)
- `lora_alpha`: Scaling factor (64 recommended)
- `lora_dropout`: Dropout rate (0.05-0.1)

### Training Parameters
- `learning_rate`: 1e-4 to 3e-4 (higher for LoRA)
- `batch_size`: 1-4 per GPU (use gradient accumulation)
- `epochs`: 1-3 for fine-tuning

## Model Selection

For CPU-only or limited VRAM:
- Use a smaller base model (Qwen1.5-0.5B or 1.5B)
- Train LoRA adapters
- Merge adapters at inference time

For GPU with 8GB+ VRAM:
- Use Qwen1.5-7B or Qwen2.5-7B
- Enable 4-bit QLoRA
- Full fine-tuning possible

## Post-Training Steps

1. **Evaluate the trained model** against the golden problems test set
2. **Compare with base model** using A/B testing
3. **Update Jalora configuration** to use the new model
4. **Deploy to Ollama** using the Modelfile approach

## Troubleshooting

### Out of Memory (OOM)
- Reduce `batch_size` and increase `gradient_accumulation_steps`
- Use a smaller model
- Enable gradient checkpointing

### Slow Training on CPU
- Use smaller model (0.5B or 1.5B)
- Reduce `max_seq_length`
- Expect 10-100x slower than GPU

### CUDA Out of Memory
- Enable QLoRA 4-bit quantization
- Reduce `per_device_train_batch_size`
- Use `gradient_checkpointing`

## Next Steps

After training is complete:
1. Run verification tests: `python -m app.services.verification`
2. Test with real user questions
3. Update `JALORA_TRAINED_MODEL` environment variable
4. Add to health check model status
"""
    (base_dir / "TRAINING.md").write_text(readme)


def main():
    parser = argparse.ArgumentParser(description="Jalora LoRA Training Setup")
    parser.add_argument("--setup", action="store_true", help="Create configuration files")
    parser.add_argument("--generate-dataset", action="store_true", help="Generate training data")
    args = parser.parse_args()
    
    base_dir = Path(__file__).parent.parent
    
    if args.setup or args.generate_dataset:
        write_template_configs(base_dir)
        create_makefile(base_dir)
        create_readme(base_dir)
        print("Setup complete!")
    
    if args.generate_dataset:
        import subprocess
        subprocess.run(["python", str(base_dir / "scripts" / "generate_training_data.py")])


if __name__ == "__main__":
    main()