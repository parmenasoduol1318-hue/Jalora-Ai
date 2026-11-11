"""Phase L metrics endpoint test (§58)."""
from __future__ import annotations

from app.services import metrics as metrics_service


def test_metrics_endpoint_returns_counters(client, auth_headers):
    """GET /api/metrics returns request/error counters."""
    headers = auth_headers()
    # Make a few requests.
    client.get("/api/health")
    client.get("/api/health")
    resp = client.get("/api/metrics", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["total_requests"] >= 2
    assert "per_path" in data
    assert "/api/health" in data["per_path"]


def test_metrics_record_increments(client, auth_headers):
    """The metrics middleware records requests automatically."""
    metrics_service.reset_metrics()
    client.get("/api/health")
    snapshot = metrics_service.get_snapshot()
    assert snapshot["total_requests"] >= 1


def test_metrics_endpoint_requires_authentication(client):
    assert client.get("/api/metrics").status_code == 401


def test_metrics_use_route_templates_not_user_supplied_paths(client, auth_headers):
    headers = auth_headers()
    metrics_service.reset_metrics()

    client.get("/api/tasks/user-id-one", headers=headers)
    client.get("/api/tasks/user-id-two", headers=headers)

    paths = metrics_service.get_snapshot()["per_path"]
    assert "/api/tasks/{task_id}" in paths
    assert "user-id-one" not in str(paths)
    assert "user-id-two" not in str(paths)


def test_unmatched_paths_share_a_bounded_metrics_bucket(client):
    metrics_service.reset_metrics()

    client.get("/api/not-a-route-one")
    client.get("/api/not-a-route-two")

    paths = metrics_service.get_snapshot()["per_path"]
    assert "/__unmatched__" in paths
    assert "not-a-route-one" not in str(paths)
    assert "not-a-route-two" not in str(paths)
