# Jalora LoRA/QLoRA Training Guide

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
