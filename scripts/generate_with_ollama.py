#!/usr/bin/env python
"""Generate training data using Ollama models.

Since HuggingFace models require authentication, we use Ollama's locally
downloaded models to generate responses and create training data.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx


def generate_with_ollama(prompt: str, model: str = "qwen2.5:0.5b") -> str:
    """Generate a response using Ollama."""
    try:
        response = httpx.post(
            "http://localhost:11434/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.3, "max_tokens": 500}
            },
            timeout=60.0
        )
        result = response.json()
        return result.get("response", "")
    except Exception as e:
        print(f"Error: {e}")
        return ""


def main():
    training_file = Path("training_data/jalora_train.jsonl")
    
    # Generate training data from existing examples
    output_file = Path("training_data/jalora_ollama_train.jsonl")
    
    with open(training_file, "r", encoding="utf-8") as f:
        examples = [json.loads(line) for line in f if line.strip()]
    
    print(f"Loaded {len(examples)} examples")
    print("Generating responses with Ollama...")
    
    generated = []
    for i, ex in enumerate(examples):
        instruction = ex["instruction"]
        inp = ex.get("input", "")
        
        prompt = f"Instruction: {instruction}\nInput: {inp}\n\nResponse:"
        
        response = generate_with_ollama(prompt)
        
        if response:
            generated.append({
                "instruction": instruction,
                "input": inp,
                "output": response
            })
            print(f"  {i+1}/{len(examples)}: Generated response")
    
    # Write to file
    with open(output_file, "w", encoding="utf-8") as f:
        for ex in generated:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")
    
    print(f"\nGenerated {len(generated)} training examples")
    print(f"Saved to {output_file}")


if __name__ == "__main__":
    main()