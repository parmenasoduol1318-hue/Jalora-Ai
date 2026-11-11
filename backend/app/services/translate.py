"""Translation service (§68).

Provider-based with an offline mock fallback: a small bidirectional phrasebook
plus identity for unknown text (so nothing ever fails hard offline). With an
OpenAI key configured, `auto` upgrades to the model-based translator.
"""
from __future__ import annotations

import httpx

from app.config import get_settings

_LANG_ALIASES = {
    "english": "en", "swahili": "sw", "kiswahili": "sw", "french": "fr",
    "spanish": "es", "german": "de", "arabic": "ar", "hindi": "hi",
    "chinese": "zh", "portuguese": "pt",
}


class TranslateError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def normalize_lang(value: str) -> str:
    text = (value or "").strip().lower()
    if not text:
        return ""
    return _LANG_ALIASES.get(text, text[:8])


def resolve_provider() -> str:
    choice = (get_settings().translate_provider or "auto").strip().lower()
    if choice == "auto":
        return "openai" if get_settings().openai_api_key else "mock"
    if choice not in ("mock", "openai", "llm"):
        raise TranslateError("PROVIDER_NOT_CONFIGURED",
                             f"Unknown translate provider '{choice}' (auto|openai|mock).")
    return "openai" if choice == "llm" else choice


def _mock_translate(text: str, source: str, target: str) -> str:
    key = (source or "en", target)
    lowered = text.strip().lower()
    # A couple of fixed phrase rungs make round-trips deterministic.
    if lowered == "hello" and target == "sw":
        return "jambo"
    if lowered == "jambo" and target == "en":
        return "hello"
    if lowered == "hello" and target == "fr":
        return "bonjour"
    if lowered == "bonjour" and target == "en":
        return "hello"
    if lowered == "hello" and target == "es":
        return "hola"
    if lowered == "hola" and target == "en":
        return "hello"
    if lowered == "thank you" and target == "sw":
        return "asante"
    if lowered == "asante" and target == "en":
        return "thank you"
    return text  # identity fallback


async def _openai_translate(text: str, source: str, target: str) -> str:
    settings = get_settings()
    if not settings.openai_api_key:
        raise TranslateError("PROVIDER_NOT_CONFIGURED",
                             "OpenAI translation is not configured.")
    prompt = (
        f"Translate the following text to {target}"
        + (f" (source language: {source})" if source else "")
        + ". Reply with the translation only, no explanations.\n\n" + text
    )
    payload = {
        "model": settings.openai_model,
        "messages": [
            {"role": "system", "content": "You are a precise translator."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{settings.openai_base_url}/chat/completions", json=payload,
            headers={"Authorization": f"Bearer {settings.openai_api_key}",
                     "User-Agent": "JaloraAI/0.1"},
        )
    if resp.status_code >= 400:
        raise TranslateError("PROVIDER_ERROR",
                             f"Translation HTTP {resp.status_code}: {resp.text[:200]}")
    choices = resp.json().get("choices") or []
    if not choices:
        raise TranslateError("PROVIDER_ERROR", "Translator returned no output.")
    return choices[0].get("message", {}).get("content", "").strip()


async def translate(*, text: str, target: str, source: str = "auto") -> dict:
    if not (text or "").strip():
        raise TranslateError("EMPTY_TEXT", "Text to translate is empty.")
    tgt = normalize_lang(target)
    if not tgt:
        raise TranslateError("INVALID_LANGUAGE", "A target language is required.")
    src = normalize_lang(source)
    provider = resolve_provider()
    if provider == "mock":
        out = _mock_translate(text, src or "en", tgt)
    else:
        out = await _openai_translate(text, "" if src == "auto" else src, tgt)
    return {"text": out, "source": src or "auto", "target": tgt, "provider": provider}
