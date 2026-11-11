"""Phase D media tests (§42–44): upload sniffing, per-type caps, owner-only
serving, analyze_image/transcribe_audio routing and vision chat integration."""
import asyncio
import io
import json

from langchain_core.messages import AIMessage

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c636000000200010003a104f20000000049454e44ae426082"
)
MP4_MAGIC = b"ftypmp42" + b"\x00" * 8
MP3_MAGIC = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 8


def _headers(client, email="media@test.com"):
    resp = client.post("/api/auth/register", json={
        "email": email, "password": "longenough1", "name": "Media User",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]["id"]


def _upload(client, headers, filename, content, mime):
    resp = client.post("/api/media", headers=headers, files={
        "file": (filename, io.BytesIO(content), mime),
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_upload_image_sniffs_mime_and_serves(client):
    headers, user_id = _headers(client)
    media = _upload(client, headers, "cat.png", PNG, "image/png")
    assert media["kind"] == "image"
    assert media["url"] == f"/api/artifacts/{user_id}/media/{media['id']}.png"
    served = client.get(media["url"], headers=headers)
    assert served.status_code == 200
    assert served.content == PNG
    assert served.headers["content-type"].startswith("image/png")


def test_upload_sniffs_despite_wrong_extension(client):
    # The sniffed audio/mpeg wins even though the name claims .bin (§39).
    headers, _ = _headers(client, "sniff@test.com")
    media = _upload(client, headers, "voice.bin", MP3_MAGIC, "application/octet-stream")
    assert media["kind"] == "audio"
    assert media["mime_type"] == "audio/mpeg"


def test_upload_rejects_non_media(client):
    headers, _ = _headers(client, "nope@test.com")
    resp = client.post("/api/media", headers=headers, files={
        "file": ("movie.mp4", io.BytesIO(b"definitely not a video"), "video/mp4"),
    })
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "UNSUPPORTED_FORMAT"


def test_media_isolation_across_users(client):
    h1, _ = _headers(client, "owner@test.com")
    h2, _ = _headers(client, "intruder@test.com")
    media = _upload(client, h1, "cat.png", PNG, "image/png")
    assert client.get(media["url"], headers=h2).status_code in (403, 404)


def test_analyze_image_routes_to_vision_model(client, fake_router, monkeypatch):
    headers, user_id = _headers(client)
    media = _upload(client, headers, "cat.png", PNG, "image/png")

    import app.services.model_router as mr

    monkeypatch.setattr(mr, "get_model_router", lambda: fake_router)
    fake_router.script.append(AIMessage(content="A fluffy orange cat on a sofa."))

    from app.context import ToolContext, set_tool_context
    from app.tools import registry

    set_tool_context(ToolContext(user_id=user_id, run_id="r1"))
    try:
        spec = registry.get("analyze_image")
        parsed = json.loads(asyncio.run(spec.tool.ainvoke({"file_id": media["id"]})))
    finally:
        set_tool_context(None)

    assert parsed["success"] is True
    assert "cat" in parsed["data"]["analysis"]
    # the vision message the model saw carried the actual image bytes
    seen = fake_router.model.tape[0]
    human = next(m for m in seen if getattr(m, "type", "") == "human")
    blocks = human.content
    assert isinstance(blocks, list)
    assert any(b.get("type") == "image_url" for b in blocks)


def test_analyze_image_missing_media_fails_honestly(client, fake_router, monkeypatch):
    headers, user_id = _headers(client, "miss@test.com")
    import app.services.model_router as mr

    monkeypatch.setattr(mr, "get_model_router", lambda: fake_router)
    from app.context import ToolContext, set_tool_context
    from app.tools import registry

    set_tool_context(ToolContext(user_id=user_id, run_id="r1"))
    try:
        spec = registry.get("analyze_image")
        parsed = json.loads(asyncio.run(spec.tool.ainvoke({"file_id": "nope"})))
    finally:
        set_tool_context(None)
    assert parsed["success"] is False
    assert parsed["error"]["code"] == "MEDIA_NOT_FOUND"


def test_transcribe_audio_offline_mock(client, fake_router, monkeypatch):
    # No OpenAI key and no whisper binary in tests -> deterministic mock path.
    headers, user_id = _headers(client, "audio@test.com")
    media = _upload(client, headers, "memo.mp3", MP3_MAGIC, "audio/mpeg")

    import app.services.model_router as mr

    monkeypatch.setattr(mr, "get_model_router", lambda: fake_router)
    from app.context import ToolContext, set_tool_context
    from app.tools import registry

    set_tool_context(ToolContext(user_id=user_id, run_id="r1"))
    try:
        spec = registry.get("transcribe_audio")
        parsed = json.loads(asyncio.run(spec.tool.ainvoke({"file_id": media["id"]})))
    finally:
        set_tool_context(None)

    assert parsed["success"] is True
    assert parsed["data"]["provider"] == "mock"
    assert "memo.mp3" in parsed["data"]["transcript"]


def test_analyze_video_fails_honestly_without_ffmpeg(client, monkeypatch):
    headers, user_id = _headers(client, "video@test.com")
    media = _upload(client, headers, "clip.mp4", MP4_MAGIC, "video/mp4")

    monkeypatch.setattr("shutil.which", lambda name: None)
    from app.context import ToolContext, set_tool_context
    from app.tools import registry

    set_tool_context(ToolContext(user_id=user_id, run_id="r1"))
    try:
        spec = registry.get("analyze_video")
        parsed = json.loads(asyncio.run(spec.tool.ainvoke({"file_id": media["id"]})))
    finally:
        set_tool_context(None)

    assert parsed["success"] is False
    assert parsed["error"]["code"] == "FFMPEG_UNAVAILABLE"


def _find_human(messages_list):
    return next(m for m in messages_list if getattr(m, "type", "") == "human")


def test_chat_with_image_passes_multimodal_block_to_model(client, fake_router):
    headers, _ = _headers(client, "vision@test.com")
    media = _upload(client, headers, "cat.png", PNG, "image/png")
    fake_router.script.append(AIMessage(content="I can see your image now."))

    resp = client.post("/api/chat", headers=headers, json={
        "message": "What is in this picture?",
        "media_ids": [media["id"]],
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "completed"
    assert "image" in data["reply"]

    human = _find_human(fake_router.model.tape[0])
    assert isinstance(human.content, list)
    assert any(
        isinstance(b, dict) and b.get("type") == "image_url" and b["image_url"]["url"].startswith("data:image/png")
        for b in human.content
    )


def test_chat_with_audio_advertises_transcribe_tool(client, fake_router):
    headers, _ = _headers(client, "hearing@test.com")
    media = _upload(client, headers, "memo.mp3", MP3_MAGIC, "audio/mpeg")
    fake_router.script.append(AIMessage(content="I will transcribe that voice memo."))

    resp = client.post("/api/chat", headers=headers, json={
        "message": "Transcribe my voice memo, please.",
        "media_ids": [media["id"]],
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] in ("completed", "paused")

    human = _find_human(fake_router.model.tape[0])
    text = human.content if isinstance(human.content, str) else str(human.content)
    assert "transcribe_audio" in text
    assert media["id"] in text