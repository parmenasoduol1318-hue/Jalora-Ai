def test_register_login_me(client):
    reg = client.post("/api/auth/register", json={
        "email": "alice@example.com", "password": "longenough1", "name": "Alice",
    })
    assert reg.status_code == 201, reg.text
    token = reg.json()["access_token"]
    assert token

    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["email"] == "alice@example.com"

    login = client.post("/api/auth/login", json={
        "email": "alice@example.com", "password": "longenough1",
    })
    assert login.status_code == 200
    assert login.json()["user"]["email"] == "alice@example.com"


def test_duplicate_email_conflicts(client):
    payload = {"email": "bob@example.com", "password": "longenough1", "name": "Bob"}
    assert client.post("/api/auth/register", json=payload).status_code == 201
    dup = client.post("/api/auth/register", json=payload)
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "EMAIL_TAKEN"


def test_wrong_password_rejected(client):
    client.post("/api/auth/register", json={
        "email": "c@example.com", "password": "longenough1", "name": "C",
    })
    bad = client.post("/api/auth/login", json={
        "email": "c@example.com", "password": "wrongpassword",
    })
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_missing_token_rejected(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_chat_requires_auth(client):
    resp = client.post("/api/chat", json={"message": "hi"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] in ("NO_TOKEN", "TOKEN_INVALID", "UNAUTHORIZED")
