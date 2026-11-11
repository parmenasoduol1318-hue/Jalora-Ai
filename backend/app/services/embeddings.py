"""Embedding backends with an automatic, honest fallback chain.

Priority (JALORA_EMBEDDING_PROVIDER=auto):
  1. OpenAI embeddings (if key present)
  2. Ollama local embeddings (if reachable)
  3. Deterministic local hashing embeddings (offline, lexical-quality)

The backend actually used is always reported so nothing pretends to be
better than it is.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re

import httpx

from app.config import get_settings

logger = logging.getLogger("jalora.embeddings")

LOCAL_DIM = 512
_token_re = re.compile(r"[a-z0-9']+")


def _normalize(vec: list[float]) -> list[float]:
    norm = sum(v * v for v in vec) ** 0.5
    if norm == 0:
        return vec
    return [v / norm for v in vec]


def _local_embed_one(text: str, dim: int = LOCAL_DIM) -> list[float]:
    """Feature-hashing embedding: offline, deterministic, lexical similarity.

    Uses word tokens, word bigrams and character trigrams so morphological
    variants ("answer" / "answers") still share signal."""
    vec = [0.0] * dim
    tokens = _token_re.findall(text.lower())
    grams = list(tokens) + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
    for tok in tokens:
        padded = f"^{tok}$"
        grams.extend(padded[i:i + 3] for i in range(len(padded) - 2))
    for gram in grams:
        digest = hashlib.md5(gram.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[idx] += sign
    return _normalize(vec)


async def embed_texts(texts: list[str]) -> tuple[list[list[float]], str]:
    """Return (vectors, backend_name). Never lies about which backend ran."""
    if not texts:
        return [], "none"
    settings = get_settings()
    provider = settings.embedding_provider

    if provider in ("auto", "openai") and settings.openai_api_key:
        try:
            return await _openai_embed(texts, settings.openai_api_key,
                                       f"{settings.openai_base_url.rstrip('/')}/embeddings"), "openai"
        except Exception as exc:  # noqa: BLE001
            if provider == "openai":
                raise
            logger.warning("openai embeddings failed (%s); trying fallback", exc)

    if provider in ("auto", "ollama"):
        try:
            return await _ollama_embed(texts, settings.ollama_base_url), "ollama"
        except Exception as exc:  # noqa: BLE001
            if provider == "ollama":
                raise
            logger.info("ollama embeddings unavailable (%s); using local hashing embeddings", exc)

    if provider == "local" or provider == "auto":
        return [_local_embed_one(t) for t in texts], "local-hash"

    raise RuntimeError(f"No embedding backend available for provider '{provider}'")


async def embed_query(text: str) -> tuple[list[float], str]:
    vectors, backend = await embed_texts([text])
    return vectors[0], backend


async def _openai_embed(texts: list[str], api_key: str, url: str) -> list[list[float]]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": "text-embedding-3-small", "input": texts},
        )
        resp.raise_for_status()
        data = resp.json()["data"]
    return [item["embedding"] for item in sorted(data, key=lambda d: d["index"])]


async def _ollama_embed(texts: list[str], base_url: str) -> list[list[float]]:
    vectors: list[list[float]] = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for text in texts:
            resp = await client.post(
                f"{base_url.rstrip('/')}/api/embeddings",
                json={"model": "nomic-embed-text", "prompt": text},
            )
            resp.raise_for_status()
            vectors.append(resp.json()["embedding"])
    return vectors
