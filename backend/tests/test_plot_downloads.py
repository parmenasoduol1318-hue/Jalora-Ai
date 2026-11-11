"""Phase 9: downloadable professional plots end-to-end.

A "plot <expr>" turn is rendered locally (never the model), returns a PNG shown
inline plus downloadable SVG/PDF vector exports, and persists artifacts under the
user's upload folder. The artifacts endpoint serves images inline and forces an
attachment (with the original file name) when ``?download=1`` is passed.
"""
import json

from app.services import plotting

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PDF_MAGIC = b"%PDF"


def _parse(raw: str) -> dict:
    return json.loads(raw)


def test_plot_fast_path_renders_and_emits_artifacts(client, auth_headers):
    headers = auth_headers("plot-fast@test.com")
    resp = client.post("/api/chat", json={"message": "plot sin(x)"}, headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["status"] == "completed"
    assert data["model"] == "matplotlib"      # rendered locally, not a model
    assert data["intent"] == "math"
    assert "sin(x)" in data["reply"]
    assert len(data["diagrams"]) == 1
    assert data["diagrams"][0]["kind"] == "svg"

    mimes = {a["mime"] for a in data["artifacts"]}
    assert mimes == set(plotting.FORMAT_MIME.values())
    assert len(data["artifacts"]) == 3
    for artifact in data["artifacts"]:
        assert artifact["download_url"] == artifact["url"] + "?download=1"
        assert artifact["file_name"]
        assert artifact["size_bytes"] > 0


def test_plot_artifact_inline_and_download_dispositions(client, auth_headers):
    headers = auth_headers("plot-dl@test.com")
    resp = client.post("/api/chat", json={"message": "graph y = x^2 from -2 to 2"},
                       headers=headers)
    assert resp.status_code == 200, resp.text
    artifacts = resp.json()["artifacts"]
    png = next(a for a in artifacts if a["mime"] == "image/png")
    pdf = next(a for a in artifacts if a["mime"] == "application/pdf")

    inline = client.get(png["url"], headers=headers)
    assert inline.status_code == 200
    assert inline.headers.get("content-disposition") is None
    assert inline.content.startswith(PNG_MAGIC)

    download = client.get(pdf["download_url"], headers=headers)
    assert download.status_code == 200
    disposition = download.headers.get("content-disposition", "")
    assert "attachment" in disposition
    assert pdf["file_name"] in disposition
    assert download.content.startswith(PDF_MAGIC)


def test_artifact_endpoint_requires_owner(client, auth_headers):
    owner = auth_headers("plot-owner@test.com")
    resp = client.post("/api/chat", json={"message": "plot cos(x)"}, headers=owner)
    assert resp.status_code == 200, resp.text
    url = resp.json()["artifacts"][0]["url"]

    intruder = auth_headers("plot-intruder@test.com")
    assert client.get(url, headers=intruder).status_code == 403
    assert client.get(url).status_code in (401, 403)


def test_plot_verbs_do_not_hijack_plain_math(client, fake_router, auth_headers):
    from langchain_core.messages import AIMessage

    fake_router.script.append(AIMessage(content="Two plus two times five is twelve."))
    headers = auth_headers("plot-notmath@test.com")
    resp = client.post("/api/chat", json={"message": "2+2*5"}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["model"] == "sympy"


def test_graph_captures_all_formats_from_plot_tool(client, fake_router, auth_headers):
    """When the model reaches for the plot tool, the agent graph collects the
    full PNG/SVG/PDF export list (``data['artifacts']``), not just the primary."""
    from langchain_core.messages import AIMessage

    headers = auth_headers("plot-agent@test.com")
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "plot_function",
        "args": {"expression": "x^3 - 2*x", "x_min": -3, "x_max": 3},
        "id": "call_plot1", "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(content="Here is the curve you asked for."))

    resp = client.post("/api/chat",
                       json={"message": "I'd like this cubic relationship shown for my slides."},
                       headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "completed", data.get("error")
    assert data["model"] != "matplotlib"          # ran via the agent, not the fast-path
    assert "curve you asked for" in data["reply"]  # the scripted final model reply
    assert len(data["artifacts"]) == 3, data["artifacts"]
    mimes = {a["mime"] for a in data["artifacts"]}
    assert mimes == set(plotting.FORMAT_MIME.values())
    urls = [a["url"] for a in data["artifacts"]]
    assert len(urls) == len(set(urls))  # no duplicated primary
