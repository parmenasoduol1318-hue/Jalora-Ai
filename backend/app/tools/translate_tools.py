"""Translation tool (§68): provider-based, offline mock fallback."""
from __future__ import annotations

from app.errors import ToolResult
from app.services import translate as translate_service
from app.tools.base import PermissionLevel, registry


async def translate(text: str, target: str, source: str = "auto") -> ToolResult:
    """Translate text into a target language (e.g. "swahili", "fr"). `source` defaults to auto-detect."""
    try:
        result = await translate_service.translate(text=text, target=target, source=source)
    except translate_service.TranslateError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
    return ToolResult.ok(
        f"{result['source']}->{result['target']}: {result['text']}", result)


registry.register(translate, permission=PermissionLevel.AUTO, category="translate",
                  tags=["translate"])
