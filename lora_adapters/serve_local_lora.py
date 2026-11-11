#!/usr/bin/env python3
"""
Jalora Local LoRA Inference Server
===================================

Merges the trained LoRA adapter with the base model and serves an
OpenAI-compatible /v1/chat/completions endpoint on localhost.

The Jalora backend's model router connects to this server automatically
when JALORA_LORA_ENABLED=true is set in the environment.

Usage:
    1. Install dependencies:
       pip install -q fastapi uvicorn transformers torch peft accelerate

    2. Start the server:
       python -m app.services.local_lora_server
       # or
       python jalora/lora_adapters/serve_local_lora.py

    3. Enable in backend (.env):
       JALORA_LORA_ENABLED=true
       JALORA_LORA_ADAPTER_PATH=jalora/lora_adapters/jalora_lora
       JALORA_LORA_BASE_MODEL=Qwen/Qwen1.5-0.5B-Chat

    4. The backend will now route to the fine-tuned model as the "lora" provider.
"""
import json
import logging
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from transformers import (
    AutoTokenizer,
    AutoModelForCausalLM,
    TextIteratorStreamer,
)
from peft import PeftModel
import torch
from torch import multiprocessing as mp

logger = logging.getLogger("jalora.local_lora_server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# Resolve paths relative to project root
BACKEND_DIR = Path(__file__).resolve().parents[1]  # .../backend
PROJECT_ROOT = BACKEND_DIR.parent                  # .../jalora
ADAPTER_PATH = PROJECT_ROOT / "lora_adapters" / "jalora_lora"
BASE_MODEL = os.environ.get("JALORA_LORA_BASE_MODEL", "Qwen/Qwen1.5-0.5B-Chat")
PORT = int(os.environ.get("JALORA_LORA_PORT", "8001"))
MAX_TOKENS = int(os.environ.get("JALORA_LORA_MAX_TOKENS", "2048"))


# ---------------------------------------------------------------------------
# Model loading (singleton)
# ---------------------------------------------------------------------------
_model: AutoModelForCausalLM | None = None
_tokenizer: AutoTokenizer | None = None


def _load_model():
    """Load base model, merge LoRA adapter, keep merged model in memory."""
    global _model, _tokenizer

    if _model is not None and _tokenizer is not None:
        return _model, _tokenizer

    if not ADAPTER_PATH.exists():
        logger.warning(f"Adapter path not found: {ADAPTER_PATH}. Serving base model only.")

    logger.info(f"Loading tokenizer: {BASE_MODEL}")
    _tokenizer = AutoTokenizer.from_pretrained(
        str(ADAPTER_PATH) if ADAPTER_PATH.exists() else BASE_MODEL,
        use_fast=True,
        trust_remote_code=True,
    )
    if _tokenizer.pad_token is None:
        _tokenizer.pad_token = _tokenizer.eos_token

    logger.info(f"Loading base model: {BASE_MODEL}")
    _model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
        device_map="auto" if torch.cuda.is_available() else "cpu",
        trust_remote_code=True,
    )

    # Merge LoRA adapter if available
    if ADAPTER_PATH.exists() and (ADAPTER_PATH / "adapter_config.json").exists():
        logger.info(f"Merging LoRA adapter from: {ADAPTER_PATH}")
        _model = PeftModel.from_pretrained(_model, str(ADAPTER_PATH))
        _model = _model.merge_and_unload()
        logger.info("LoRA adapter merged successfully!")
    else:
        logger.warning("No LoRA adapter found — serving base model only.")

    _model.eval()
    logger.info("Model loaded and ready for inference.")
    return _model, _tokenizer


def _unload_model():
    """Free GPU/CPU memory by unloading the model."""
    global _model, _tokenizer
    if _model is not None:
        del _model
        _model = None
    if _tokenizer is not None:
        del _tokenizer
        _tokenizer = None
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load model on startup, unload on shutdown."""
    logger.info("Starting Jalora Local LoRA Inference Server...")
    _load_model()
    yield
    logger.info("Shutting down Jalora Local LoRA Inference Server...")
    _unload_model()


app = FastAPI(title="Jalora Local LoRA Server", version="1.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request/Response models (OpenAI-compatible)
# ---------------------------------------------------------------------------
class ChatCompletionRequest(BaseModel):
    model: str = "jalora-lora"
    messages: list[dict[str, Any]] = []
    temperature: float = 0.7
    top_p: float = 0.9
    max_tokens: int | None = None
    stream: bool = False
    n: int = 1


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[dict[str, Any]]
    usage: dict[str, Any]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/")
async def root():
    return {"status": "ok", "model": "jalora-lora", "base_model": BASE_MODEL}


@app.get("/health")
async def health():
    return {"status": "healthy", "model_loaded": _model is not None}


@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": "jalora-lora",
                "object": "model",
                "created": 0,
                "owned_by": "jalora",
                "permission": [],
                "max_model_len": MAX_TOKENS,
            }
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest):
    """OpenAI-compatible chat completions endpoint."""
    model, tokenizer = _load_model()

    # Format messages for Qwen chat template
    if hasattr(tokenizer, "apply_chat_template"):
        prompt = tokenizer.apply_chat_template(
            req.messages, tokenize=False, add_generation_prompt=True
        )
    else:
        prompt = format_messages_manual(req.messages, tokenizer)

    inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=MAX_TOKENS)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    inputs = {k: v.to(device) for k, v in inputs.items()}

    max_new = req.max_tokens or 512
    total_tokens = inputs["input_ids"].shape[1] + max_new

    # Check context window
    if total_tokens > model.config.max_position_embeddings:
        # Truncate input
        inputs["input_ids"] = inputs["input_ids"][:, -model.config.max_position_embeddings + max_new :]
        inputs["attention_mask"] = inputs["attention_mask"][:, -model.config.max_position_embeddings + max_new :]

    if req.stream:
        return await _stream_response(model, tokenizer, inputs, req, max_new, total_tokens, device)
    else:
        return await _non_stream_response(model, tokenizer, inputs, req, max_new, total_tokens, device)


def format_messages_manual(messages: list[dict], tokenizer) -> str:
    """Manual formatting fallback if apply_chat_template is unavailable."""
    parts = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            parts.append(f"<|im_start|>system\n{content}<|im_end|>")
        elif role == "user":
            parts.append(f"<|im_start|>user\n{content}<|im_end|>")
        elif role == "assistant":
            parts.append(f"<|im_start|>assistant\n{content}<|im_end|>")
    parts.append("<|im_start|>assistant\n")
    return "".join(parts)


async def _non_stream_response(model, tokenizer, inputs, req, max_new, total_tokens, device):
    """Generate a non-streaming response."""
    import time
    start = time.time()

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_new,
            temperature=req.temperature,
            top_p=req.top_p,
            do_sample=True,
            pad_token_id=tokenizer.pad_token_id,
        )

    generated = tokenizer.decode(outputs[0], skip_special_tokens=True)
    # Strip the prompt from the output
    prompt_text = tokenizer.decode(inputs["input_ids"][0], skip_special_tokens=True)
    output_text = generated[len(prompt_text):].strip()

    elapsed = time.time() - start
    prompt_tokens = inputs["input_ids"].shape[1]
    completion_tokens = outputs.shape[1] - prompt_tokens

    return ChatCompletionResponse(
        id=f"chatcmpl-local-{int(time.time()*1000)}",
        created=int(time.time()),
        model="jalora-lora",
        choices=[{
            "index": 0,
            "message": {"role": "assistant", "content": output_text},
            "finish_reason": "stop",
        }],
        usage={
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    ).model_dump()


async def _stream_response(model, tokenizer, inputs, req, max_new, total_tokens, device):
    """Generate a streaming response (SSE format)."""
    from fastapi import Request
    from sse_starlette.sse import EventSourceResponse

    streamer = TextIteratorStreamer(
        tokenizer, timeout=60, skip_prompt=True, skip_special_tokens=True
    )

    generation_kwargs = dict(
        **inputs,
        max_new_tokens=max_new,
        temperature=req.temperature,
        top_p=req.top_p,
        do_sample=True,
        pad_token_id=tokenizer.pad_token_id,
    )
    thread = mp.dummy.ThreadPoolExecutor() if hasattr(mp, 'dummy') else None

    # Use threading for streaming
    import threading
    t = threading.Thread(
        target=model.generate,
        kwargs={**generation_kwargs, "streamer": streamer},
    )
    t.start()

    async def event_stream():
        import time
        chunk_id = f"chatcmpt-{int(time.time()*1000)}"
        first = True
        for text in streamer:
            if first:
                first = False
                yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': 'jalora-lora', 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]})}\n\n"
            yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': 'jalora-lora', 'choices': [{'index': 0, 'delta': {'content': text}, 'finish_reason': None}]})}\n\n"
        yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': 'jalora-lora', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
        yield "data: [DONE]\n\n"

    return EventSourceResponse(event_stream())


if __name__ == "__main__":
    import uvicorn
    _load_model()  # Load eagerly
    print(f"\nStarting Jalora Local LoRA Server on http://localhost:{PORT}")
    print(f"  Base model: {BASE_MODEL}")
    print(f"  Adapter:    {ADAPTER_PATH}")
    print(f"  Endpoint:   http://localhost:{PORT}/v1/chat/completions")
    print(f"  Models:     http://localhost:{PORT}/v1/models\n")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
