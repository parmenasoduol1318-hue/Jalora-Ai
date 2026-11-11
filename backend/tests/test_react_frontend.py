"""P4 gate: the React build is what the API serves (when the toolchain exists).

- If node_modules is present the build MUST exist and be served at `/`.
- If the toolchain itself is absent (no npm install yet) the test skips, so the
  backend suite still runs offline on a machine without Node.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REACT = ROOT / "frontend-react"
DIST = REACT / "dist"

pytestmark = pytest.mark.skipif(
    not (REACT / "node_modules").exists(),
    reason="frontend-react toolchain not installed (npm install not run)",
)


def test_build_exists():
    assert (DIST / "index.html").exists(), "run `npm run build` in frontend-react/"


def test_react_index_is_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert 'id="root"' in html
    assert "assets/index-" in html  # hashed vite bundle
    # API routes must never be swallowed by the SPA fallback
    assert client.get("/api/health").status_code == 200


def test_react_assets_are_served(client):
    html = client.get("/").text
    bundle = re.search(r'src="(/assets/index-[^"]+\.js)"', html)
    assert bundle, f"no script bundle referenced: {html[:300]}"
    resp = client.get(bundle.group(1))
    assert resp.status_code == 200
    assert len(resp.content) > 10_000, "bundle suspiciously small"
    css = re.search(r'href="(/assets/index-[^"]+\.css)"', html)
    if css:
        assert client.get(css.group(1)).status_code == 200


def test_spa_deep_link_falls_back_to_index(client):
    resp = client.get("/some/client/route")
    assert resp.status_code == 200
    assert 'id="root"' in resp.text


def test_unknown_api_route_still_404(client):
    resp = client.get("/api/definitely-not-a-route")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_index_shell_served_no_cache(client):
    """Phase J stale-build fix: the SPA shell is served with a no-cache policy so
    users always receive the freshest bundle after a rebuild (never a cached
    pre-math shell)."""
    resp = client.get("/")
    assert resp.headers.get("Cache-Control") == "no-cache, no-store, must-revalidate"


def test_built_bundle_ships_math_rendering_assets(client):
    """Stale-build guard tied to the visual-output work: the production build
    must reflect the spec #5 edits — MathJax in the shell (LaTeX rendering) and
    `.math-answer` in a shipped CSS/JS bundle (highlighted final answers).
    If frontend source is changed but `npm run build` is not re-run, this fails
    and forces a fresh build."""
    html = client.get("/").text
    assert "MathJax" in html, "stale build: MathJax not wired into the shell"

    js = re.search(r'src="(/assets/index-[^"]+\.js)"', html)
    css = re.search(r'href="(/assets/index-[^"]+\.css)"', html)
    shipped = []
    for m in (js, css):
        if not m:
            continue
        r = client.get(m.group(1))
        assert r.status_code == 200, m.group(1)
        shipped.append(r.text)
    assert any(".math-answer" in body for body in shipped), (
        "stale build: .math-answer CSS not shipped in any asset"
    )
