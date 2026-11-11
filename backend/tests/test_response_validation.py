from app.services.response_validation import infer_constraints, validate_response
from langchain_core.messages import AIMessage


def test_chat_repairs_an_explicit_sentence_constraint(client, fake_router):
    fake_router.script.append(AIMessage(content="First sentence. Second sentence."))
    fake_router.script.append(AIMessage(content="A corrected single sentence."))
    registered = client.post("/api/auth/register", json={
        "email": "constraints@test.com", "password": "longenough1", "name": "Constraints",
    })
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    response = client.post("/api/chat", headers=headers, json={
        "message": "Answer in one sentence.",
    })
    assert response.status_code == 200
    assert response.json()["reply"] == "A corrected single sentence."


def test_exact_bullet_and_per_bullet_limits_are_checked():
    prompt = "Return exactly 3 bullet points, each under 5 words."
    assert infer_constraints(prompt).exact_bullets == 3
    assert validate_response(prompt, "- one\n- two\n- three") == []
    assert validate_response(prompt, "- one\n- two\n- this bullet has seven extra words")


def test_sentence_and_json_constraints_are_checked():
    assert validate_response("Answer in one sentence.", "First. Second.")
    assert validate_response("Return valid JSON only.", '{"ok": true}') == []
    assert validate_response("Return valid JSON only.", "Here is the answer: yes")
