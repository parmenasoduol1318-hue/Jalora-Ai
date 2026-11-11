#!/usr/bin/env python3
"""
Load your trained Jalora LoRA adapter
=======================================

After training in Colab, download the adapter zip and extract its contents
into: jalora/lora_adapters/jalora_lora/

Directory structure after upload:
    jalora/lora_adapters/jalora_lora/
    ├── adapter_config.json
    ├── adapter_model.safetensors   (or pytorch_model.bin)
    ├── special_tokens_map.json
    └── tokenizer/
        ├── tokenizer.json
        ├── special_tokens_map.json
        └── vocab.json

Usage:
    python -m lora_adapters.load_jalora_adapter
    # or
    from lora_adapters.load_jalora_adapter import load_jalora_model
"""
import os
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel, PeftConfig


# Resolve base model name from the adapter's config, or override via env
DEFAULT_BASE_MODEL = "Qwen/Qwen1.5-0.5B-Chat"
ADAPTER_PATH = Path(__file__).parent / "jalora_lora"


def load_jalora_model(
    adapter_path: Path = ADAPTER_PATH,
    base_model: str | None = None,
    device: str = "auto",
):
    """
    Load the base model merged with the trained LoRA adapter.

    Parameters
    ----------
    adapter_path : Path
        Directory containing adapter_config.json and adapter_model.safetensors
    base_model : str | None
        HuggingFace model ID. If None, read from adapter_config.json.
    device : str
        "auto", "cuda", or "cpu"

    Returns
    -------
    (model, tokenizer)
        The fine-tuned model and tokenizer ready for inference.
    """
    if not adapter_path.exists():
        raise FileNotFoundError(
            f"No adapter found at {adapter_path}. "
            "Upload your trained adapter files (adapter_config.json, "
            "adapter_model.safetensors, tokenizer/) into "
            f"{adapter_path.relative_to(Path.cwd())}/"
        )

    # Read base model from adapter config if not explicitly provided
    adapter_cfg_path = adapter_path / "adapter_config.json"
    if adapter_cfg_path.exists():
        import json
        with open(adapter_cfg_path, "r", encoding="utf-8") as f:
            peft_cfg = json.load(f)
        if base_model is None:
            base_model = peft_cfg.get("base_model_name_or_path", DEFAULT_BASE_MODEL)

    if base_model is None:
        base_model = DEFAULT_BASE_MODEL

    print(f"Loading base model: {base_model}")
    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        torch_dtype=torch.bfloat16,
        device_map=device,
        trust_remote_code=True,
    )

    print(f"Applying LoRA adapter from: {adapter_path}")
    model = PeftModel.from_pretrained(
        model,
        str(adapter_path),
        is_trainable=False,
    )

    print("Loading tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(
        str(adapter_path),
        use_fast=True,
        trust_remote_code=True,
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    print("Jalora model loaded and ready!")
    return model, tokenizer


def generate_response(model, tokenizer, prompt: str, max_new_tokens: int = 512):
    """Generate a response using the fine-tuned Jalora model."""
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            temperature=0.7,
            top_p=0.9,
            do_sample=True,
            pad_token_id=tokenizer.pad_token_id,
        )
    response = tokenizer.decode(outputs[0], skip_special_tokens=True)
    return response


if __name__ == "__main__":
    model, tokenizer = load_jalora_model()
    test_prompt = "<|im_start|>user\nWho are you?<|im_end|>\n<|im_start|>assistant\n"
    result = generate_response(model, tokenizer, test_prompt)
    print(f"\nTest response:\n{result}")
