"""Deep Research tool registration for the shared agent registry."""
from __future__ import annotations

from app.errors import ToolResult
from app.services.deep_research import research
from app.tools.base import PermissionLevel, registry


async def deep_research(query: str, subquestions: list[str] | None = None) -> ToolResult:
    """Run bounded, cited public-web research and open source pages before citing."""
    result = await research(query, subquestions=subquestions)
    if result["status"] in {"completed", "partial"}:
        return ToolResult.ok(
            f"Research opened {len(result['sources'])} source page(s); "
            f"{len(result['claims'])} excerpt-backed claim(s) are available.",
            result,
        )
    return ToolResult.fail(
        "RESEARCH_INSUFFICIENT_EVIDENCE",
        "No source-backed claims could be verified from opened pages.",
        message=(
            "Research could not verify enough evidence. Do not treat search "
            "snippets as facts; explain the limitation and retry only if useful."
        ),
        data=result,
    )


registry.register(
    deep_research,
    permission=PermissionLevel.AUTO,
    category="research",
    requires_internet=True,
    tags=["research", "web", "evidence", "source", "browser", "citations"],
)
