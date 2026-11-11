"""Tests for Jalora Creative Studio backend APIs."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_creative_projects_flow(client: TestClient, auth_headers, monkeypatch):
    headers = auth_headers()

    async def fake_plan_edits(instruction: str, **kwargs):
        return {
            "ops": [],
            "planner": "test",
            "errors": [],
            "explanation": "Deterministic test plan.",
        }

    def no_image_provider(*_args, **_kwargs):
        from app.services.creative import CreativeEngineError

        raise CreativeEngineError(
            "No AI image provider is configured.",
            "AI_IMAGE_GENERATION_UNAVAILABLE",
        )

    from app.services.creative import ai_editor, providers

    monkeypatch.setattr(ai_editor, "plan_edits", fake_plan_edits)
    monkeypatch.setattr(providers, "generate_ai_image", no_image_provider)

    # 1. List projects (initially empty)
    res = client.get("/api/creative/projects", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert isinstance(data["projects"], list)

    # 2. Create project
    res = client.post("/api/creative/projects", json={
        "title": "Test Cinematic Project",
        "description": "Automated test project",
        "project_type": "video",
    }, headers=headers)
    assert res.status_code == 201
    proj_data = res.json()
    assert proj_data["success"] is True
    proj_id = proj_data["project"]["id"]
    assert proj_id is not None

    # 3. Get project detail
    res = client.get(f"/api/creative/projects/{proj_id}", headers=headers)
    assert res.status_code == 200
    detail = res.json()
    assert detail["success"] is True
    assert detail["project"]["title"] == "Test Cinematic Project"
    assert len(detail["project"]["tracks"]) >= 3

    # 4. Without a configured AI generator, fail explicitly instead of
    #    presenting procedural placeholder art as an image of the requested scene.
    res = client.post("/api/creative/generate/image", json={
        "prompt": "Futuristic cityscape at night",
        "aspect_ratio": "16:9",
    }, headers=headers)
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "AI_IMAGE_GENERATION_UNAVAILABLE"

    # 5. AI Edit action
    res = client.post("/api/creative/ai-edit", json={
        "project_id": proj_id,
        "instruction": "Add animated captions",
    }, headers=headers)
    assert res.status_code == 200
    ai_data = res.json()
    assert ai_data["success"] is True

    # 6. Templates
    res = client.get("/api/creative/templates", headers=headers)
    assert res.status_code == 200
    tpl_data = res.json()
    assert tpl_data["success"] is True
    assert len(tpl_data["templates"]) > 0
