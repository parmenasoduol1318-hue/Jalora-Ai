@echo off
REM ============================================================
REM  Jalora Creative Studio — dev runner (Phase 0)
REM  * Frees port 8000 if it is already held (avoids the
REM    "backend momentarily down" recurrence that made the
REM    Creative Studio's images/video "disappear").
REM  * Starts the FastAPI backend (uvicorn, reload) +
REM    the React frontend (vite dev server).
REM ============================================================
setlocal
set "REPO_ROOT=%~dp0..\.."
set "BACKEND=%~dp0..\backend"
set "FRONTEND=%~dp0..\frontend-react"
set "VENV_PY=%REPO_ROOT%\.venv\Scripts\python.exe"

echo [dev_run] Freeing port 8000 ...
for /f "tokens=5" %%A in ('netstat -ano 2^>nul ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    echo [dev_run] Killing PID %%A on :8000
    taskkill /PID %%A /F >nul 2>&1
)

if not exist "%VENV_PY%" (
    echo [dev_run] ERROR: venv python not found at "%VENV_PY%"
    exit /b 1
)

echo [dev_run] Backend  -> http://127.0.0.1:8000  (uvicorn --reload)
start "jalora-backend" cmd /c "cd /d \"%BACKEND%\" && \"%VENV_PY%\" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"

echo [dev_run] Frontend -> http://localhost:5173  (vite)
start "jalora-frontend" cmd /c "cd /d \"%FRONTEND%\" && npx vite"

echo [dev_run] Ready. Open http://localhost:5173  (Vite proxies /api to :8000)
exit /b 0
