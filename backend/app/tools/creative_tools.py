"""Creative generation tools for the chat agent (spec §55).

Exposes ``generate_image`` and ``edit_image`` so the model can route
"create an image of a cup" to actual image generation instead of falling
back to plotting tools. Generation delegates to the provider-independent
layer in ``app.services.creative.providers`` (OpenAI / Google / local
procedural fallback); editing delegates to ``image_engine.apply_ops``.

When a cloud provider raises a ``CreativeEngineError`` with a fallback code
(quota, auth, not-configured, etc.) the tool retries on the local provider.
Procedural output is not presented as a completed image-generation request.
"""
from __future__ import annotations

import io
import uuid
from pathlib import Path

from app.context import get_tool_context
from app.errors import ToolResult
from app.tools.base import PermissionLevel, registry

def _creative_disabled() -> ToolResult | None:
    """Return a failure result if creative is disabled, else None."""
    from app.config import get_settings

    if not get_settings().creative_enabled:
        return ToolResult.fail(
            "CREATIVE_DISABLED",
            "Creative Studio is disabled on this server.",
            message="I can't generate images right now — the creative engine is disabled.",
        )
    return None


def _artifact_info(asset, *, out) -> dict:
    """Build the artifact descriptor dict the graph expects from tools."""
    from app.api.creative._helpers import asset_url

    return {
        "id": asset.id,
        "kind": "image",
        "url": asset_url(asset.id),
        "download_url": f"{asset_url(asset.id)}?download=1",
        "mime": out.mime,
        "title": f"Image ({asset.id[:8]})",
        "size_bytes": asset.size_bytes,
    }


async def generate_image(prompt: str = "", negative_prompt: str | None = None,
                         aspect_ratio: str = "1:1",
                         num_variations: int = 1,
                         seed: int | None = None,
                         provider: str | None = None,
                         strength: float | None = None) -> ToolResult:
    """Generate an AI image from a text prompt.

    Uses the configured AI provider (OpenAI, Google, or local diffusion).
    In automatic mode it can try another configured AI provider if the first
    fails. Procedural placeholder output is never returned as the requested
    image. Returns download links for each generated image.
    aspect_ratio is a ratio string like '1:1', '16:9', '9:16', '4:3', '3:4'.
    provider selects 'auto', 'openai', 'google', or 'local'."""
    disabled = _creative_disabled()
    if disabled is not None:
        return disabled
    from app.config import get_settings
    from app.database.session import SessionLocal
    from app.services.creative import CreativeEngineError, providers
    from app.services.creative.providers import GenRequest
    from app.api.creative._helpers import persist_bytes

    settings = get_settings()
    user_id = get_tool_context().user_id

    gen_request = GenRequest(
        kind="image",
        prompt=prompt or "",
        negative_prompt=negative_prompt,
        aspect_ratio=aspect_ratio or "1:1",
        num_variations=max(1, min(int(num_variations or 1), 4)),
        seed=seed,
        extra={"strength": strength} if strength else {},
    )

    try:
        outputs, provider_obj, fallback = providers.generate_ai_image(
            gen_request, provider or "auto", settings
        )
    except CreativeEngineError as exc:
        return ToolResult.fail(
            exc.code or "GENERATION_ERROR",
            str(exc),
            message="I couldn't generate the requested image with an AI model. "
            "No placeholder image was created; check image-provider availability "
            "and try again.",
        )

    with SessionLocal() as db:
        assets = []
        for i, out in enumerate(outputs):
            filename = f"gen_image_{uuid.uuid4().hex[:8]}_{i}{out.extension}"
            meta = dict(out.meta)
            if fallback:
                meta["fallback"] = fallback
            asset = persist_bytes(
                db, user_id, out.data, filename,
                file_type="image", source="generated",
                subdir="generated", metadata=meta,
            )
            assets.append((asset, out))

    infos = [_artifact_info(a, out=o) for a, o in assets]
    provider_label = f"{provider_obj.name} (AI)"
    summary = f"Generated {len(assets)} image(s) of '{prompt[:60]}' via {provider_label}."
    if fallback:
        summary += f" (fell back to local after {fallback['from']} {fallback['reason']})"
    return ToolResult.ok(summary, {
        "artifact": infos[0],
        "artifacts": infos,
        "provider": provider_obj.name,
        "ai": provider_obj.ai,
        "fallback": fallback,
    })


async def edit_image(asset_id: str, ops: list[dict],
                     output_format: str = "png") -> ToolResult:
    """Edit an existing generated or uploaded image by applying a list of ops.

    Each op is a dict with an "op" key: "adjust" (tone/colour params),
    "curves", "filter" (name), "transform" (crop/rotate/resize/flip/scale),
    "text" (draw text/shapes), "background_remove", or "mask".
    Returns a download link for the edited image."""
    from app.database.session import SessionLocal
    from app.models.creative import MediaAsset
    from app.services.creative import image_engine, storage
    from app.api.creative._helpers import persist_bytes, asset_url

    user_id = get_tool_context().user_id

    with SessionLocal() as db:
        asset = db.get(MediaAsset, asset_id)
        if asset is None:
            return ToolResult.fail(
                "ASSET_NOT_FOUND", f"No image with id {asset_id}",
                message="That image is no longer available.",
            )
        if asset.user_id != user_id:
            return ToolResult.fail(
                "NOT_OWNER", "You do not own this asset.", message="Not your image.",
            )

    src_path = storage.resolve(user_id, asset.file_path)
    if not src_path.exists():
        return ToolResult.fail(
            "FILE_MISSING", f"Source image for asset {asset_id} is missing.",
            message="The source image is no longer available.",
        )

    try:
        img = image_engine.open_image(src_path)
        result = image_engine.apply_ops(img, ops or [])
    except Exception as exc:  # noqa: BLE001
        return ToolResult.fail(
            "EDIT_ERROR", f"{type(exc).__name__}: {exc}",
            message="Could not apply the requested edits to the image.",
        )

    buf = io.BytesIO()
    image_engine.export(result, buf, fmt=output_format)
    data = buf.getvalue()

    with SessionLocal() as db:
        filename = f"edit_{asset_id}_{uuid.uuid4().hex[:8]}{Path(src_path).suffix}"
        meta = {"source_asset_id": asset_id, "ops": ops}
        new_asset = persist_bytes(
            db, user_id, data, filename,
            file_type="image", source="edited",
            subdir="generated", metadata=meta,
        )

    info = {
        "id": new_asset.id,
        "kind": "image",
        "url": asset_url(new_asset.id),
        "download_url": f"{asset_url(new_asset.id)}?download=1",
        "mime": "image/png" if output_format == "png" else "image/jpeg",
        "title": f"Edited image ({new_asset.id[:8]})",
        "size_bytes": new_asset.size_bytes,
    }
    summary = f"Edited image with {len(ops or [])} operation(s). Exported as {output_format.upper()}."
    return ToolResult.ok(summary, {"artifact": info, "artifacts": [info]})


registry.register(generate_image, permission=PermissionLevel.AUTO, category="creative",
                  tags=["generate", "image", "creative"],
                  required_signals=["image_generation"])
registry.register(edit_image, permission=PermissionLevel.AUTO, category="creative",
                  tags=["edit", "image", "creative"])
