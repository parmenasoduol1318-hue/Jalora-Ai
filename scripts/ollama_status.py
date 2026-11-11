#!/usr/bin/env python
"""Check Ollama status and model details."""
import httpx

def main():
    # Get tags list
    r = httpx.get('http://localhost:11434/api/tags', timeout=10.0)
    if r.status_code != 200:
        print("Ollama is not running")
        return
    
    data = r.json()
    print("=" * 60)
    print("OLLAMA MODEL STATUS")
    print("=" * 60)
    print("Installed models:")
    for m in data.get('models', []):
        print(f'  {m["name"]}')
    
    # Get running processes
    r = httpx.get('http://localhost:11434/api/ps', timeout=30.0)
    if r.status_code == 200:
        data = r.json()
        print("\nRunning models (memory usage):")
        for m in data.get('models', []):
            vram = m.get('size_vram', 0) / (1024**3) if m.get('size_vram') else 0
            rss = m.get('size_rss', 0) / (1024**3) if m.get('size_rss') else 0
            print(f'  {m["model"]}: {vram:.2f} GB VRAM, {rss:.2f} GB RAM')
    print("=" * 60)

if __name__ == "__main__":
    main()