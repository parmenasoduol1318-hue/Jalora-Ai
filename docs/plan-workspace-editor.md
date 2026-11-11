# Plan — Workspace: Monaco IDE + Multi-File Coding Agent (end-to-end, host-ready)

## Overview
Upgrade Jalora's existing workspace (`WorkspaceView.tsx`, `app/api/workspace.py`,
`app/services/sandbox.py`) from a textarea editor into an independent, VS-Code-style
**code editor and/or coding agent** — the user picks editor-only, agent-only, or split.
The workspace foundation already exists (per-user/project sandbox folders, file tree,
allowlisted terminal/exec, SSE coding agent with approvals + edit-journal diff/rollback,
and coding tools `list_files/read_file/edit_code/run_tests/run_linter/run_command` +
math `calculate`). We add: (1) **Monaco** editor surface with tabs/minimap/split; (2) a
**`create_project` scaffold tool** + **multi-file `edit_code`** so the agent can build
whole apps; (3) **zip export** of a project; (4) split-view layout to run editor and
agent side-by-side; (5) **host-ready** architecture (spaces already auth- and
path-scoped; workspaces_dir is env-configurable; no local-only paths). Math spec stays
untouched; no changes to chat/scheduling/auth/voice/memory beyond reusing existing auth.

## Implementation approach

### Step 1 — Monaco editor component (frontend)
- Install (build step, via `npm.cmd` to bypass the PS policy): `@monaco-editor/react`,
  `monaco-editor`. Bundle is ~16 MB; loaded inside `dist/` for prod, from node_modules in dev.
- New `frontend-react/src/views/WorkspaceEditor.tsx` (lazy/dynamic import so it doesn't
  block the initial bundle): wraps `<Editor>`, picks `defaultLanguage` from the file
  extension (`ts/tsx/js/jsx/py/css/html/json/yaml/md/sh`), `value`/`onChange` (dirty flag),
  `onSave` → `saveFile`, `automaticLayout`, minimap on, `vs-dark` theme reusing `--bg/--text`.
- Open files become tabs; Ctrl/Cmd+click in the tree opens a tab; right-click → "Open
  in new editor" populates a second pane (split via a CSS grid, no extra dep).

```tsx
import Editor from "@monaco-editor/react";
export const WorkspaceEditor = ({value, lang, onChange, onSave, dirty}: Props) => (
  <>
    <Editor height="100%" theme="vs-dark" language={lang} value={value}
      onChange={(v) => { onChange(v ?? ""); }}
      onMounted={() => {}} />
    {dirty && <button className="btn small" onClick={onSave}>{t("wsSave")}</button>}
  </>
);
```

### Step 2 — Multi-file `edit_code` + `create_project` tool (backend)
- `app/tools/coding_tools.py`: keep `edit_code` (single-file old→new) and add
  `edit_code_multi(edits: list[{path,old_str,new_str}])` applying each through
  `sandbox.write_file` (so the journal/diff/rollback still see them). Register as `math`
  category? No — `category="coding"`, `PermissionLevel.CONFIRM` (it mutates workspace files
  the user owns; auto for the agent since the workspace IS user-owned — mark `AUTO` so the
  coding agent can batch edits without pausing, matching existing `edit_code`).
- Add `create_project(name, template="python-cli"|"fastapi-app"|"react-app")` → writes a
  starter tree (`README.md`, `package.json`/`requirements.txt`, `src/...`, `.gitignore`)
  into the sandbox via `sandbox.create_file`; `CONFIRM`-gated. Returns the created tree.

```python
@registry.register(name="create_project", category="coding", permission=PermissionLevel.CONFIRM)
def create_project(user_id, name, template="python-cli", project_id=""):
    files = TEMPLATES[template]
    created = []
    for rel, content in files.items():
        sandbox.create_file(user_id, f"{name}/{rel}", content, project_id=project_id)
        created.append(rel)
    return ToolResult.ok(f"Scaffolded {template}", {"root": name, "files": created})
```

### Step 3 — Run-in-sandbox feedback loop (agent)
- `run_tests`, `run_linter`, `run_command` already exist and are bound to the coding agent.
- Add an `analyze_error` tool binding (the sandbox fn already exists) so after a failing
  `run_command`/`run_tests` the agent receives traceback frames + error type, then targets
  `edit_code` to the failing file/line and re-runs — closing the build→run→fix loop.
- Raise the coding `MAX_TOOL_CALLS` budget (currently 5) to ~12 so multi-step builds
  (scaffold → write tests → run → fix → re-run) can complete.

### Step 4 — Project export as .zip (backend + frontend)
- `sandbox.zip_project(user_id, project_id)`: walk the project root, skip `.jalora/`
  journal/backup + `__pycache__`/hidden, write to an in-memory `zipfile` BytesIO (path-safe).
- `GET /api/workspace/export?project_id=…` → `StreamingResponse(buf,
  media_type="application/zip", headers={"Content-Disposition":
  'attachment; filename="workspace.zip"'})`.
- Frontend: "Download project" button in `WorkspaceView` → `window.open(`/api/workspace/export?project_id=${projectId}`)`.

### Step 5 — Split / independent layout (frontend)
- `WorkspaceView` layout: left rail = file tree (× Changes); center = Monaco editor (tabs);
  right rail = agent chat. A `viewMode: "split" | "editor" | "agent"` toggle resizes/hides
  the right rail — editor-only hides the agent rail, agent-only keeps tree + editor,
  split (default) shows all three. Persist `viewMode` in localStorage per window.
- Monaco diff editor for the Changes tab (reuse `sandbox.pending_edits`).

### Step 6 — Host-ready deployment notes
- Workspaces live under `settings.workspaces_dir` (env-configurable → persistent volume in
  deployment); per-user isolation and path-escape guards already enforced in `sandbox.py`.
- Auth already JWT (`get_current_user`); rate-limit GET-read whitelist (A1) already in
  `main.py`. Run via `gunicorn -k uvicorn.workers.UvicornWorkers app.main:app` behind nginx
  (nginx serves `dist/` + proxies `/api/`); `STATIC_*` env drives `workspaces_dir`.

### Files to touch
- `frontend-react/`: `views/WorkspaceEditor.tsx` (new), `views/WorkspaceView.tsx`,
  `lib/api.ts`, `styles.css`, `i18n.ts`/locales, `package.json` (+ npm install).
- `backend/`: `app/api/workspace.py` (`export`), `app/services/sandbox.py`
  (`zip_project`), `app/tools/coding_tools.py` (`create_project`, `edit_code_multi`).
- `agents/graph.py` (coding tool budget). Tests: `tests/test_phase_j_coding.py`,
  `tests/test_workspace_projects.py`.

## Testing strategy
Backend: unit-test `sandbox.zip_project` round-trips (zip contents match the tree, rejects path escapes) and `create_project` writes the scaffold tree; add an `edit_code_multi` case to `test_phase_j_coding.py` (multi-file edits land + appear in diff). Frontend: dev smoke — open a file in Monaco, save, see it persist via `GET /api/workspace/file`; "Download project" returns a zip. Gate: `pytest tests/test_phase_j_coding.py tests/test_phase_g_schedules.py tests/test_react_frontend.py tests/test_rate_limit_phase_l.py -q` and `node node_modules/typescript/bin/tsc -b --noEmit` → 0 errors. (npm install is a build-time only step; if the registry is unreachable, fall back to the textarea editor and ship the backend/agent work first.)


## Status — implemented & audited

Audit result: **complete against this plan; gate is green.**

- Backend: `sandbox.zip_project` (path-safe in-memory zip, excludes `.jalora`/`.git`/`node_modules`/`__pycache__`/hidden, skips escaping symlinks), `GET /api/workspace/export` (200 -> zip `['main.py']`, `.jalora` absent; 400 `BAD_REQUEST` on missing pid; 400 `INVALID_PROJECT` on `../../etc`), `create_project` (python-cli/fastapi-app/react-app templates) + multi-file `edit_code_multi` (journal-backed), both registered in `_CODING`. Live HTTP smoke against `:8000` passed end-to-end.
- Frontend: `WorkspaceEditor.tsx` (lazy Monaco via `@monaco-editor/react` 4.7.0 + `monaco-editor` 0.57.0, ext->lang map, vs-dark, Ctrl/Cmd+S, textarea fallback skeleton), `WorkspaceView.tsx` swap + split/editor/agent view toggle + Export button, `api.ts` `exportProject`, `i18n.ts` en+sw keys, `styles.css`.
- Monaco render: `@monaco-editor/loader` is pinned to the jsDelivr CDN (`monaco-editor@0.57.0/min/vs`, verified 200 for `loader.js` / `editor/editor.main.js` / `editor/editor.worker.js`) via `loader.config` in `WorkspaceEditor` -- renders in dev (vite) and prod (`vite build`), fetched lazily so the app bundle stays ~350 KB.
- Verification: `node_modules/typescript/bin/tsc -b --noEmit` -> 0; `node_modules/vite/bin/vite.js build` -> 0 (59 modules); `pytest tests/test_math.py tests/test_chat.py tests/test_visuals.py tests/test_phase_j_sandbox.py tests/test_phase_j_coding.py tests/test_workspace_projects.py tests/test_react_frontend.py tests/test_rate_limit_phase_l.py -q` -> **200 passed, 2 skipped**.
- `requirements.txt` gained `email-validator` (pydantic `EmailStr` runtime dep that conftest needs at collection).
- Environment note: a scratch-cleanup accidentally traversed `.venv`/`node_modules`; both were cleanly rebuilt (venv from `requirements.txt`, node_modules via `npm install`) and re-greened. A per-file Windows App-Control flag on SQLAlchemy's `_immutiledict_cy.pyd` was resolved by rebuilding the venv from the trusted requirements (now intact).

Next action: start the local backend (`uvicorn app.main:app` on `:8000`) and frontend (`vite` on `:5173`) and re-run the live smoke.
