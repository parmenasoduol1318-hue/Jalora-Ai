# Jalora Training Equipment Checklist

## Ready-to-Run Google Colab Notebook

**File:** `trainer/train_jalora_colab.ipynb`

Click "Open in Colab" to start training immediately:
```
https://colab.research.google.com/github/your-username/Jalora/blob/main/trainer/train_jalora_colab.ipynb
```

## Equipment Required for Training

### 1. Google Account
- [ ] Google account (free)
- [ ] Google Drive storage (optional for saving results)

### 2. HuggingFace Account
- [ ] Create account at huggingface.co
- [ ] Get access token (Settings → Access Tokens → New token)
- [ ] Accept Qwen model license at:
  - https://huggingface.co/Qwen/Qwen1.5-0.5B-Instruct
  - https://huggingface.co/Qwen/Qwen1.5-1.5B-Instruct
  - https://huggingface.co/Qwen/Qwen1.5-7B-Instruct

### 3. Git Repository
- [ ] GitHub/GitLab account
- [ ] Push your training data to repository
- [ ] `training_data/jalora_train.jsonl` (84+ examples)
- [ ] `training_data/jalora_val.jsonl` (16 validation examples)

## Training Data Format

Each JSONL line must have:
```json
{
  "instruction": "What is the capital of France?",
  "input": "",
  "output": "The capital of France is Paris..."
}
```

## Model Selection Guide

| Model | VRAM Required | Speed | Quality |
|-------|---------------|-------|---------|
| Qwen1.5-0.5B-Instruct | 1-2 GB | Fast | Basic |
| Qwen1.5-1.5B-Instruct | 2-4 GB | Medium | Good |
| Qwen1.5-7B-Instruct | 4-8 GB | Slow | Best |
| Mistral-7B-v0.1 | 4-8 GB | Slow | Good |
| Llama-3.2-3B | 3-6 GB | Medium | Good |

## Training Functions (in Notebook)

### Cell 1: Environment Setup
```python
pip install peft transformers accelerate trl datasets sentencepiece
```

### Cell 2: Mount Google Drive
```python
from google.colab import drive
drive.mount('/content/drive')
```

### Cell 3: Clone Repository
```python
git clone YOUR_REPO_URL
```

### Cell 4: Set HuggingFace Token
```python
# Input your token when prompted
```

### Cell 5: Load Training Data
```python
train_data = load_jsonl('training_data/jalora_train.jsonl')
val_data = load_jsonl('training_data/jalora_val.jsonl')
```

### Cell 6: Check GPU
```python
torch.cuda.is_available()  # Should be True
torch.cuda.get_device_name()  # Shows GPU model
```

### Cell 7: Model Selection
```python
MODEL_NAME = 'Qwen/Qwen1.5-7B-Instruct'  # Adjust based on VRAM
```

### Cell 8: Load Model (Auto GPU/CPU)
```python
model = AutoModelForCausalLM.from_pretrained(
    MODEL_NAME, load_in_4bit=True, device_map='auto'
)
```

### Cell 9: Configure LoRA
```python
lora_config = LoraConfig(r=64, alpha=16, target_modules=[...])
model = get_peft_model(model, lora_config)
```

### Cell 10: Training Dataset
```python
# Format prompts and create HuggingFace Dataset
```

### Cell 11: Training Arguments
```python
TrainingArguments(
    num_train_epochs=3,
    per_device_train_batch_size=2,
    gradient_accumulation_steps=4,
    learning_rate=2e-5
)
```

### Cell 12: Start Training
```python
trainer = Trainer(model=model, args=training_args, ...)
trainer.train()
```

### Cell 13: Save Adapter
```python
model.save_pretrained(OUTPUT_DIR)
```

### Cell 14: Test Model
```python
# Generate test response to verify training
```

### Cell 15: Export to GGUF
```python
# Convert for Ollama
```

## Expected Training Time

| Model | GPU VRAM | Est. Time |
|-------|----------|-----------|
| 0.5B | 1-2 GB | 10-20 min |
| 1.5B | 2-4 GB | 30-60 min |
| 7B | 4-8 GB | 2-4 hours |

## After Training: Export for Ollama

```bash
# In Colab, run:
cd /content/llama.cpp
./quantize model.gguf model.Q4_K_M.gguf q4_k_m
```

Create Ollama Modelfile:
```dockerfile
FROM ./model.Q4_K_M.gguf
PARAMETER temperature 0.3
PARAMETER num_ctx 8192

SYSTEM """
You are Jalora, a helpful AI assistant.
Use verified knowledge only.
"""
```

```bash
ollama create jalora-finetuned -f Modelfile
ollama run jalora-finetuned
```

## Troubleshooting

### "401 Unauthorized" from HuggingFace
- Make sure your HF token is correct
- Accept model licenses on huggingface.co

### "CUDA out of memory"
- Reduce `per_device_train_batch_size` to 1
- Use smaller model (0.5B instead of 7B)
- Enable `load_in_4bit=True`

### "Repository not found"
- Check repository URL is correct
- Make sure repository is public or you have access

### Training too slow
- Use T4 GPU (free tier gives T4)
- Reduce epochs to 1-2
- Use smaller model

## Files in This Directory

| File | Description |
|------|-------------|
| `train_jalora_colab.ipynb` | Full training notebook |
| `TRAINING_CHECKLIST.md` | This checklist |
| `train_lora_simple.py` | Simple standalone script |
| `export_for_ollama.py` | Export script after training |

## Quick Start

1. Open `train_jalora_colab.ipynb` in Google Colab
2. Run Cell 1 (install deps) - takes 1-2 min
3. Run Cell 2 (mount drive) - authorize
4. Run Cell 3 (clone repo) - your repo URL
5. Run Cell 4 (HF token) - paste your token
6. Run Cell 5-7 (load data, check GPU)
7. Run Cell 8+ to start training
8. Wait for completion (check time estimates above)