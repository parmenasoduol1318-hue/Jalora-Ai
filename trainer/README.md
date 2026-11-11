# Jalora PEFT Training Guide

## Overview
This guide explains how to train LoRA adapters for Jalora using PEFT (Parameter-Efficient Fine-Tuning).

## Current Status

### What's Done
1. ✅ PEFT dependencies installed (peft, transformers, accelerate, trl)
2. ✅ Training data generated (84+ examples)
3. ✅ Training script created (`config/peft_lora.py`)
4. ⏳ CPU training in progress (slow due to model access)

### Training Data
- **Training:** 84 examples in `training_data/jalora_train.jsonl`
- **Ollama-verified:** 81 examples in `training_data/jalora_ollama_train.jsonl`
- **Validation:** 16 examples in `training_data/jalora_val.jsonl`

## Prerequisites

### Option A: HuggingFace Models (Recommended)
```bash
# Set HuggingFace token (required for Qwen models)
export HF_TOKEN="your_hf_token"

# Install local model (first time only)
python -c "from huggingface_hub import snapshot_download; snapshot_download('Qwen/Qwen1.5-0.5B-Instruct')"
```

### Option B: Ollama Models
```bash
# Export model from Ollama to GGUF format
ollama pull qwen2.5:0.5b
python -c "from ollama import list; print([m for m in list()['models'] if '0.5b' in m['name']])"
```

## Running Training

### CPU Training (Slow - ~30 minutes to 2 hours)
```bash
cd jalora
python config/peft_lora.py
```

### GPU Training (Fast - 5-15 minutes)
```bash
CUDA_VISIBLE_DEVICES=0 python config/peft_lora.py
```

### With Custom Model
```bash
MODEL_NAME=Qwen/Qwen1.5-7B python config/peft_lora.py
```

## Training Configuration

| Parameter | CPU (Current) | GPU |
|-----------|---------------|-----|
| r | 8 | 64 |
| epochs | 1 | 3 |
| batch_size | 1 | 4 |
| gradient_accumulation | 4 | 4 |
| learning_rate | 2e-5 | 2e-5 |

## Output

After training completes, you'll find:
```
lora_adapters/jalora_lora/
├── adapter_config.json
├── adapter_model.safetensors
├── special_tokens_map.json
└── tokenizer/
    ├── tokenizer.json
    ├── special_tokens_map.json
    └── vocab.json
```

## Next Steps After Training

### 1. Export to GGUF
```bash
python -c "
from peft import PeftModel
from transformers import AutoModelForCausalLM
import torch

model = AutoModelForCausalLM.from_pretrained('Qwen/Qwen1.5-0.5B-Instruct')
peft_model = PeftModel.from_pretrained(model, 'lora_adapters/jalora_lora')
merged_model = peft_model.merge_and_unload()
merged_model.save_pretrained('merged_model')
"
```

### 2. Convert to GGUF with llama.cpp
```bash
cd llama.cpp
python convert_hf_to_gguf.py ../merged_model --outfile jalora-qwen-0.5b.Q4_K_M.gguf
./quantize jalora-qwen-0.5b.Q4_K_M.gguf jalora-qwen-0.5b.Q4_K_M.gguf q4_k_m
```

### 3. Import to Ollama
```bash
ollama create jalora-local -f Modelfile
echo 'FROM ./jalora-qwen-0.5b.Q4_K_M.gguf' > Modelfile
echo 'PARAMETER temperature 0.3' >> Modelfile
ollama create jalora-local -f Modelfile
```

## Troubleshooting

### Model Download Issues
```bash
# Clear HuggingFace cache
rm -rf ~/.cache/huggingface/hub

# Use offline mode with local model
python configure_peft_training.py --local-model /path/to/model
```

### Memory Issues on CPU
Reduce training data or use smaller model:
```bash
MODEL_NAME=Qwen/Qwen1.5-0.3B python config/peft_lora.py
```

### Training Too Slow
Consider using a GPU or cloud instance with:
- 1x A100 40GB
- 1x RTX 3090/4090 24GB
- AWS g5.xlarge or g6.xlarge