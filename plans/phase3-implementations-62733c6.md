# Phase 3 Implementation Plan for Jalora AI

## Overview

This plan covers the remaining Phase 3 improvements to make Jalora more robust and production-ready:

1. **Job Persistence** - ✅ ALREADY IMPLEMENTED via SQLAlchemyJobStore
2. **Document Progress Tracking** - Add WebSocket endpoint for progress updates
3. **Math Fast-Path Feedback** - Emit explicit SSE event to inform users when fast-path is used

---

## Status Summary

### ✅ Already Implemented

1. **Job Persistence** (`scheduler.py`)
   - APScheduler uses `SQLAlchemyJobStore` which persists jobs to `apscheduler_jobs` table
   - Jobs survive server restarts automatically
   - `sync_schedules()` function mirrors enabled Schedule rows into APScheduler jobs

2. **Circuit Breaker Pattern** (`model_router.py`)
   - Functions: `record_provider_failure()`, `is_provider_blacklisted()`, `reset_provider_failures()`, `get_provider_failure_count()`
   - `MAX_FAILURES = 3`, `BACKOFF_SECONDS = 300` (5 minutes)

3. **SSE Terminal Events** (`chat_service.py`)
   - `TERMINAL_EVENTS = {"done", "error"}` ensures proper stream completion

4. **Enhanced Error Responses** (`errors.py`)
   - Error responses include `retry_after_seconds` and `error_code` fields
   - `LowMemoryError` class for memory-related failures

5. **Health Endpoint** (`system.py`)
   - `/api/health` includes `ram_available_gb`, `ram_health` ("healthy"/"low"/"critical")
   - Includes `cpu_cores`, `gpu`, `disk_free_gb`

6. **Token Refresh** (`auth.py`)
   - `/api/auth/refresh` endpoint already implemented

### 🚧 Needs Implementation

1. **Document Progress Tracking** - Add WebSocket endpoint for real-time progress updates
2. **Math Fast-Path Feedback** - Emit SSE event when fast-path math is used

---

## Implementation Details

### 1. Document Progress Tracking (WebSocket Endpoint)

**Problem**: Users have no real-time feedback during document upload/processing.

**Solution**: Add a WebSocket endpoint that streams progress updates from the Document model's `progress` and `status` fields.

**Files to modify**:
- `backend/app/api/documents.py` - Add WebSocket endpoint

**Code changes**:

```python
# Add WebSocket endpoint in documents.py
from fastapi import WebSocket, WebSocketDisconnect
from fastapi import APIRouter, Depends, File, UploadFile
import asyncio

@router.websocket("/progress/{document_id}")
async def document_progress(
    websocket: WebSocket,
    document_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db):
):
    """WebSocket endpoint for real-time document processing progress."""
    doc = db.get(Document, document_id)
    if doc is None or doc.user_id != user.id:
        await websocket.close()
        return
    
    await websocket.accept()
    try:
        last_progress = -1
        while True:
            db.refresh(doc)
            if doc.progress != last_progress:
                last_progress = doc.progress
                await websocket.send_json({
                    "document_id": document_id,
                    "progress": doc.progress,
                    "status": doc.status,
                    "filename": doc.filename,
                })
            if doc.status in ("ready", "failed"):
                break
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
```

### 2. Math Fast-Path Feedback (SSE Event)

**Problem**: Users can't tell if their calculation used fast-path (SymPy) or LLM.

**Solution**: Emit explicit SSE event `"math_fast_path"` when a math expression is classified as `direct=True` and computed via SymPy.

**Files to modify**:
- `backend/app/services/chat_service.py` - Add fast-path detection and SSE event emission

**Code changes**:

```python
# In chat_service.py - Add in execute_chat before graph execution:
# After intent classification, check for math fast-path
if routing.intent == "math":
    from app.services import math_engine as _math_engine
    signal = _math_engine.classify_math(request_message)
    if signal and signal.direct:
        # Emit fast-path event
        emitter.emit("math_fast_path", {
            "expression": request_message,
            "hint": "Computed directly without LLM for faster results.",
            "method": "sympy",
        })
```

---

## Testing Strategy

1. **Document Progress**: Upload a PDF and monitor WebSocket for progress updates (5%, 40%, 75%, 100%)
2. **Math Fast-Path**: Send math expressions like "2+2" and verify SSE event is emitted with `math_fast_path` type
3. **Job Persistence**: Restart server and verify schedules reload from database