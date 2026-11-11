#!/usr/bin/env python
"""Export PEFT LoRA adapter for Ollama.

Usage:
    python export_for_ollama.py --adapter jalora_lora_adapters --base-model Qwen/Qwen1.5-1.5B-Instruct
"""

import argparse
import os
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer


def merge_and_save(adapter_path: str, base_model: str, output_dir: str):
    """Merge LoRA adapter with base model and save."""
    
    print(f"Loading base model: {base_model}")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Using device: {device}")
    
    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        device_map="auto" if device == "cuda" else "cpu",
        torch_dtype=torch.float16 if device == "cuda" else torch.float32,
        trust_remote_code=True
    )
    
    print(f"Loading adapter: {adapter_path}")
    model = PeftModel.from_pretrained(model, adapter_path)
    
    print("Merging adapter into base model...")
    merged_model = model.merge_and_unload()
    
    print(f"Saving merged model to: {output_dir}")
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    merged_model.save_pretrained(output_dir)
    
    print("Done!")
    return output_dir


def create_ollama_modelfile(output_dir: str, modelfile_path: str = "Modelfile"):
    """Create Ollama Modelfile for the trained model."""
    
    modelfile_content = f'''# Jalora Fine-tuned Model
FROM ./{Path(output_dir).name}/gguf-model.Q4_K_M.gguf

# Model parameters
PARAMETER temperature 0.3
PARAMETER num_ctx 8192

# System prompt
SYSTEM """
You are Jalora, a helpful AI assistant optimized for accuracy and safety.
Use verified knowledge only. Cite sources when available.
"""
'''
    
    with open(modelfile_path, "w") as f:
        f.write(modelfile_content)
    
    print(f"Modelfile created at: {modelfile_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", default="jalora_lora_adapters", help="Path to LoRA adapter")
    parser.add_argument("--base-model", default="Qwen/Qwen1.5-1.5B-Instruct", help="Base model name")
    parser.add_argument("--output", default="merged_model", help="Output directory")
    parser.add_argument("--modelfile", default="Modelfile", help="Ollama Modelfile path")
    
    args = parser.parse_args()
    
    # Merge and save
    merge_and_save(args.adapter, args.base_model, args.output)
    
    # Create Modelfile
    create_ollama_modelfile(args.output, args.modelfile)
    
    print("""
Next steps:
1. Convert to GGUF with llama.cpp:
   cd llama.cpp
   python convert_hf_to_gguf.py ../{output} --outfile model.gguf
   ./quantize model.gguf model.Q4_K_M.gguf q4_k_m

2. Import to Ollama:
   ollama create jalora -f {modelfile}
   ollama run jalora
""".format(output=args.output, modelfile=args.modelfile))


if __name__ == "__main__":
    main()