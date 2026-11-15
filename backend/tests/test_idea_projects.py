from __future__ import annotations

from sqlalchemy import select

from app.database.session import SessionLocal
from app.models.conversation import Conversation
from app.models.task import Task


def _register(client, email: str):
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": "IdeaPass123!", "name": "Idea owner"},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_idea_project_crud_archive_and_relogin_persistence(client):
    email = "idea-crud@test.com"
    headers = _register(client, email)
    created = client.post(
        "/api/idea-projects",
        headers=headers,
        json={
            "title": "Community garden",
            "brief": "A shared place to grow food.",
            "goals": "Recruit neighbors",
            "next_steps": "Find a location",
            "notes": "Talk to the library",
            "milestones": ["Confirm site", "Start first beds"],
        },
    )
    assert created.status_code == 201, created.text
    project = created.json()
    project_id = project["id"]
    assert project["milestones"] == ["Confirm site", "Start first beds"]
    assert project["archived"] is False

    updated = client.patch(
        f"/api/idea-projects/{project_id}",
        headers=headers,
        json={"next_steps": "Ask the city about permits", "milestones": []},
    )
    assert updated.status_code == 200
    assert updated.json()["next_steps"] == "Ask the city about permits"
    assert updated.json()["milestones"] == []

    archived = client.post(f"/api/idea-projects/{project_id}/archive", headers=headers)
    assert archived.status_code == 200
    assert archived.json()["archived"] is True
    assert client.get("/api/idea-projects", headers=headers).json() == []
    assert client.get(
        "/api/idea-projects?include_archived=true", headers=headers
    ).json()[0]["id"] == project_id

    reopened = client.post(f"/api/idea-projects/{project_id}/reopen", headers=headers)
    assert reopened.status_code == 200
    assert reopened.json()["archived"] is False

    relogin = client.post(
        "/api/auth/login",
        json={"email": email, "password": "IdeaPass123!"},
    )
    assert relogin.status_code == 200
    relogin_headers = {"Authorization": f"Bearer {relogin.json()['access_token']}"}
    persisted = client.get(f"/api/idea-projects/{project_id}", headers=relogin_headers)
    assert persisted.status_code == 200
    assert persisted.json()["brief"] == "A shared place to grow food."
    assert persisted.json()["next_steps"] == "Ask the city about permits"


def test_idea_projects_are_owner_scoped_and_links_must_be_owned(client):
    owner_headers = _register(client, "idea-owner@test.com")
    other_headers = _register(client, "idea-other@test.com")
    conversation_response = client.post(
        "/api/conversations", headers=owner_headers, json={"title": "Research chat"}
    )
    assert conversation_response.status_code == 201, conversation_response.text
    conversation_id = conversation_response.json()["id"]

    with SessionLocal() as db:
        owner_id = db.scalar(
            select(Conversation.user_id).where(Conversation.id == conversation_id)
        )
        task = Task(user_id=owner_id, goal="Confirm local demand", status="completed")
        db.add(task)
        db.commit()
        task_id = task.id

    created = client.post(
        "/api/idea-projects",
        headers=owner_headers,
        json={
            "title": "Market research",
            "task_ids": [task_id],
            "conversation_ids": [conversation_id],
        },
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    assert created.json()["task_ids"] == [task_id]
    assert created.json()["conversation_ids"] == [conversation_id]

    assert client.get("/api/idea-projects", headers=other_headers).json() == []
    assert client.get(
        f"/api/idea-projects/{project_id}", headers=other_headers
    ).status_code == 404
    assert client.patch(
        f"/api/idea-projects/{project_id}",
        headers=other_headers,
        json={"title": "Stolen"},
    ).status_code == 404

    other_conversation = client.post(
        "/api/conversations", headers=other_headers, json={"title": "Private chat"}
    ).json()
    rejected = client.patch(
        f"/api/idea-projects/{project_id}",
        headers=owner_headers,
        json={"conversation_ids": [other_conversation["id"]]},
    )
    assert rejected.status_code == 400
    assert client.post(
        "/api/idea-projects",
        headers=owner_headers,
        json={"title": "Invalid link", "task_ids": [task_id, "not-owned"]},
    ).status_code == 400


def test_idea_project_requires_nonblank_title_and_authentication(client):
    headers = _register(client, "idea-validation@test.com")
    assert client.post(
        "/api/idea-projects", headers=headers, json={"title": "   "}
    ).status_code == 400
    assert client.get("/api/idea-projects").status_code == 401
