#!/usr/bin/env python
"""Pre-warm Ollama models for faster initial responses.

This script loads the Ollama models into VRAM/RAM so subsequent requests
are much faster. Run this before benchmarking or production use.
"""
import time
import httpx

OLLAMA_URL = "http://localhost:11434"
MODELS = [
    "qwen2.5:7b",       # Heavy model
    "llama3.2:latest",  # Mid model
    "qwen2.5:1.5b",     # Light model
]

def prewarm_model(model_name: str) -> bool:
    """Load a model into Ollama's context."""
    print(f"Pre-warming model: {model_name}")
    
    try:
        # Check if Ollama is running
        resp = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=5.0)
        if resp.status_code != 200:
            print(f"  ERROR: Ollama not responding (status {resp.status_code})")
            return False
        
        # Use /api/generate to load the model (minimal prompt)
        payload = {
            "model": model_name,
            "prompt": "Hello",
            "stream": False,
            "options": {
                "num_ctx": 8192,  # Reduced context for faster load
                "temperature": 0.0,
            }
        }
        
        start = time.time()
        resp = httpx.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=120.0)
        elapsed = time.time() - start
        
        if resp.status_code == 200:
            print(f"  OK (loaded in {elapsed:.1f}s)")
            return True
        else:
            print(f"  ERROR: {resp.status_code} - {resp.text[:100]}")
            return False
            
    except Exception as e:
        print(f"  ERROR: {e}")
        return False

def main():
    print("=" * 60)
    print("OLLAMA MODEL PRE-WARMING")
    print("=" * 60)
    
    start_time = time.time()
    results = {}
    
    for model in MODELS:
        results[model] = prewarm_model(model)
    
    total_time = time.time() - start_time
    
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for model, success in results.items():
        status = "PASS" if success else "FAIL"
        print(f"  {model}: {status}")
    print(f"Total time: {total_time:.1f}s")
    
    return 0 if all(results.values()) else 1

if __name__ == "__main__":
    exit(main())