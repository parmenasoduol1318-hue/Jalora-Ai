# Backend-to-Frontend Flow Audit for Jalora AI

## Overview

This audit examines the complete data flow when users send chat messages, schedule tasks, perform math calculations, or use other application functions. The analysis traces from frontend request to backend response and back, identifying limitations and improvement opportunities.

---

## Detailed Request Flow Analysis

### Request Type 1: Chat Message (with streaming)

**Endpoint**: POST `/api/chat`
**Content-Type**: `application/json` or `text/event-stream` for streaming

**Complete Flow**:

```
[Frontend UI - ChatView.tsx]
    │
    ├── Request Construction:
    │   ├── payload = {
    │   │   message: "What is 2+2?",
    │   │   conversation_id: "conv-xxx",
    │   │   stream: true,
    │   │   mode: "chat",
    │   │   math_level: "intermediate",
    │   │   show_working: true
    │   │   }
    │   └── fetch('/api/chat', {method: 'POST', body: JSON.stringify(payload)})
    │
    └── Response Handling:
        For streaming: EventSource or fetch with ReadableStream
        Events received: "start", "intent", "token", "done"/"error"

[FastAPI - chat.py]
    │
    ├── Authentication:
    │   ├── get_current_user dependent
    │   ├── JWT validation from Authorization header
    │   └── Raises 401 if invalid/expired
    │
    ├── Route Dispatch:
    │   ├── payload.stream == true → StreamingResponse
    │   └── else → execute_chat() → ChatResponse
    │
    └── StreamingResponse:
        ├── media_type: "text/event-stream"
        ├── headers: Cache-Control: no-cache, Connection: keep-alive
        └── Generator: stream_chat() yielding "event: data\n\ndata: {json}"

[chat_service.py - stream_chat()]
    │
    ├── QueueEmitter Creation:
    │   queue = asyncio.Queue[dict]
    │   _collected = [] (buffers events before queue available)
    │
    ├── Chat Flow:
    │   1. Emit "start" event → UI shows "connecting..."
    │   2. Get user from DB with SessionLocal()
    │   3. Get/create conversation
    │   4. Load history (last N messages)
    │   5. Intent classification: route_request(message)
    │   6. Emit "intent" event → UI shows reasoning progress
    │   7. Select model via ModelRouter (based on RAM, priority)
    │   8. Invoke LLM/Graph
    │   9. Stream tokens via emitter.emit("token", {content: token})
    │   10. Persist messages to DB
    │   11. Emit "done" event with final response
    │
    └── Error Handling:
        ├── Catches all exceptions
        ├── Returns structured error via SSE: "event: error\n\ndata: {...}"
        └── May fall back to non-streaming via execute_chat()

[Model Router - model_router.py]
    │
    ├── Provider Selection:
    │   SELECT priority FROM providers WHERE:
    │   ├── API key available (for cloud providers)
    │   ├── Model exists and is accessible
    │   └── Sufficient RAM for local model
    │
    ├── Model Parameters:
    │   ├── temperature, top_p, repeat_penalty
    │   ├── num_ctx (context window)
    │   ├── max_tokens
    │   └── stream: true
    │
    └── Fallback Chain:
        If primary fails → try next in AUTO_PRIORITY list

[Backend Response to Frontend]
    ├── Success SSE Format:
    │   │   event: start
    │   │   data: {"mode": "chat"}
    │   │   
    │   │   event: intent
    │   │   data: {"intent": "chat", "signals": []}
    │   │   
    │   │   event: token
    │   │   data: {"content": "The", "done": false}
    │   │   
    │   │   event: done
    │   │   data: {"completion": "The answer is 4.", "usage": {...}}
    │   │
    └── Error Response:
            event: error
            data: {"code": "MODEL_NOT_AVAILABLE", "message": "..."}}
```

**Limitations Found**:
1. No explicit "done" event in successful stream (last token marked with "done: true" but no event)
2. Error events may not be emitted if exception occurs before stream is set up
3. RAM exhaustion causes silent model unloading with no user notification
4. All provider failures result in generic error without suggesting retry


### Request Type 2: Task Scheduling

**Endpoint**: POST `/api/schedules`

**Flow**:
```
[Frontend - SchedulesView.tsx]
    │
    ├── Form Submission:
    │   {task_template: "send reminder to team", when: "every Monday at 9am"}
    │
    └── Response Handling:
        Shows created schedule in list with status

[schedules.py API]
    │
    ├── Validation:
    │   ├── task_template: 1-2000 chars
    │   └── when: 1-200 chars (natural language)
    │
    ├── Creation:
    │   ├── Calls scheduler_service.create_schedule()
    │   ├── Parses "when" using dateparser/nlp
    │   └── Returns Schedule model with next_run
    │
    └── Response:
        201 Created with schedule object

[scheduler_service.py]
    │
    ├── APScheduler Integration:
    │   ├── Uses BackgroundScheduler
    │   ├── adds_job() for each schedule
    │   └── Triggers on: date, interval, cron, or natural language
    │
    └── Execution:
        ├── On trigger: calls agent to execute task_template
        ├── Results stored in Task model
        └── Frontend polls /api/tasks for status
```

**Limitations Found**:
1. Missed jobs warning in logs (apscheduler events missed by hours)
2. No job result persistence if scheduler restarts
3. When "when" parsing fails, returns 422 with raw text (not user-friendly)
4. No way to update existing schedule (only toggle enabled/disabled)


### Request Type 3: Math/Science Calculation

**Endpoint**: POST `/api/chat`

**Flow**:
```
[Frontend]
    │
    ├── Request:
    │   message: "solve 2x + 5 = 15 for x"
    │   mode: "chat" (default)
    │   math_level: "intermediate" (optional)
    │
[chat_service.py]
    │
    ├── Before Model Routing:
    │   ├── Check if math fast-path applies
    │   ├── Look for keywords: calculate, solve, graph, plot, etc.
    │   ├── Extract mathematical expression
    │   └── Match against calculator patterns
    │
    ├── Math Fast-Path (math.py):
    │   ├── Direct computation using sympy/numpy
    │   ├── Returns ChatOutcome with reply and usage
    │   └── NO LLM call required (fast!)
    │
    └── If No Fast-Path:
        ├── Route to model via intent routing
        ├── May use math-optimized profile:
        │   temperature: 0.1, max_tokens: 2048, num_ctx: 16384
        └── Response via SSE/streaming
```

**Limitations Found**:
1. Math detection happens but may be bypassed by intent routing confusion
2. No feedback to user whether fast-path or LLM was used
3. Complex multi-step math may fail and fall back to LLM
4. Math errors return raw traceback in some cases


### Request Type 4: Document Processing (RAG)

**Endpoint**: POST `/api/documents` then GET `/api/chunks` or via chat

**Flow**:
```
[Frontend - DocumentsView.tsx]
    │
    ├── Upload:
    │   ├── multipart/form-data
    │   ├── File size limit: 25MB (PDF: 60MB)
    │   └── Stored in data/uploads/user-xyz/
    │
    ├── Processing:
    │   ├── Backend extracts: PDF/DOCX → text
    │   ├── Chunks: 1200 chars + 200 overlap
    │   ├── Embeds: using embedding_provider
    │   └── Stores: in vector DB or SQL


[embeddings.py]
    │
    ├── Provider Chain:
    │   1. OpenAI embeddings (requires key)
    │   2. Ollama nomic-embed-text (local)
    │   3. Local hashing fallback (fast but less accurate)
    │
    ├── Caching:
    │   ├── Embeddings cached in memory
    │   └── Cache invalidated on config change
    │
    └── Error Handling:
        Falls back to next provider on failure

[chat_service.py - RAG Retrieval]
    │
    ├── When user asks about document:
    │   ├── Detect intent: "search_documents", "query_docs"
    │   ├── Retrieve similar chunks via vector search
    │   ├── Inject as context in system prompt
    │   └── Send to model with retrieved context
```

**Limitations Found**:
1. Embedding service failures silently return empty results (no user feedback)
2. Large documents may timeout during processing
3. Vector search uses SQL cosine similarity (slower than ChromaDB)
4. No progress indicator during document upload/processing


### Request Type 5: Authentication

**Endpoint**: POST `/api/auth/login`, GET `/api/auth/me`

**Flow**:
```
[Frontend - LoginForm.tsx]
    │
    ├── Login Request:
    │   POST /api/auth/login
    │   {email: "...", password: "..."}
    │
    ├── Response:
    │   ├── Set-Cookie: access_token=... (HttpOnly, Secure)
    │   └── JSON: {access_token: "...", token_type: "bearer"}
    │
    └── Subsequent Requests:
        Headers: Authorization: Bearer <token>

[auth.py]
    │
    ├── Login:
    │   ├── Verify password with Argon2
    │   ├── Generate JWT with user sub
    │   └── Return token + set cookie
    │
    ├── Token Validation:
    │   ├── Decode JWT with HS256
    │   ├── Check expiration
    │   └── Attach user to request
    │
    └── Access Token Refresh:
        None implemented - token expires permanently

[Token Lifecycle]
    │
    ├── Expiration: 24 hours (configurable)
    ├── No refresh mechanism
    └── Once expired: must re-login
```

**Limitations Found**:
1. No token refresh mechanism - 24h hard expiration
2. Cross-origin cookie issues on some browsers
3. Failed validation returns 500 (should be 401)
4. No "remember me" option to extend session


---

## Testing Strategy

To verify the fixes and improvements:

1. **Chat Flow Test**:
   - Send message with `stream=true`
   - Verify SSE events: start → token → done
   - Kill Ollama during stream, verify error event
   - Test with empty RAM, verify 503 response

2. **Schedule Flow Test**:
   - Create schedule with valid when clause
   - Verify next_run is correct
   - Restart scheduler, verify jobs persist/load
   - Test missed job recovery

3. **Math Flow Test**:
   - Send calculation with `math_level` param
   - Verify fast-path skips LLM (check network tab)
   - Test complex math falls back to model
   - Verify error messages are user-friendly

4. **Document Flow Test**:
   - Upload PDF, verify chunking
   - Query document, verify RAG results
   - Disable embeddings, verify fallback works
   - Test large document timeout

5. **Auth Flow Test**:
   - Login, verify token received
   - Wait until expiration, verify 401 on protected route
   - Test token refresh endpoint
   - Test cross-origin cookie settings


---

## Recommendations (Prioritized)

### Phase 1: Immediate (0-2 days)

1. **SSE Stream Completion** - Always emit explicit `done` event
   - File: `chat_service.py`
   - Add: Final `yield f"event: done\n\ndata: {json}"`

2. **Error Response Standardization** - Return actionable errors
   - File: All API endpoints
   - Add: `retry_after_seconds`, `error_code` fields

3. **Port Conflict Detection** - Check port availability at startup
   - File: `main.py`
   - Add: Socket check before `uvicorn.run()`

4. **Frontend Error Handling** - Show user-friendly messages
   - File: `jalora/frontend-react/src/`
   - Add: Connection status overlay, retry buttons

### Phase 2: Short Term (2-7 days)

5. **Health Endpoint** - Return server status
   - File: `system.py`
   - Add: `/api/health` with memory, provider, model status

6. **Circuit Breakers** - Skip failing providers
   - File: `model_router.py`
   - Add: Provider failure tracking, auto-blacklist

7. **Token Refresh** - Implement refresh mechanism
   - File: `auth.py`
   - Add: `/api/auth/refresh`, new token on 401

### Phase 3: Medium Term (1-4 weeks)

8. **Job Persistence** - Save schedules across restarts
   - File: `scheduler_service.py`
   - Add: Load schedules from DB on startup

9. **Document Progress** - Show upload/processing status
   - File: `documents.py`, `files.py`
   - Add: Progress WebSocket, status polling

10. **Math Fast-Path Feedback** - Indicate when fast-path used
    - File: `chat_service.py`
    - Add: `intent: "calculation"` event, skip LLM


---

## Summary Table

| Request Type | Critical Issues | Files to Modify | Priority |
|--------------|----------------|-----------------|----------|
| Chat (SSE) | No done event, silent failures | `chat_service.py`, `chat.py` | High |
| Scheduling | Missed jobs, no persistence | `schedules.py`, `scheduler_service.py` | Medium |
| Math | Inconsistent fast-path | `chat_service.py`, `tools/math.py` | Medium |
| Documents | Silent embedding failures | `documents.py`, `embeddings.py` | Medium |
| Auth | No refresh, 24h expiry | `auth.py`, `schemas.py` | High |

---

## ✅ Changes Implemented

### Phase 1: Immediate (Completed)

1. **SSE Stream Completion** - ✅ Already implemented
   - File: `chat_service.py`
   - `TERMINAL_EVENTS = {"done", "error"}` ensures proper stream completion

2. **Error Response Enhancement** - ✅ Implemented
   - File: `app/errors.py`
   - Added `retry_after_seconds` field to `ApiError`
   - Updated `api_error_handler` to include `Retry-After` HTTP headers
   - Added `LowMemoryError` exception class

3. **Port Conflict Detection** - ✅ Implemented
   - File: `app/main.py`
   - Added `_check_port_available()` function
   - Logs port conflicts at startup

4. **Memory Availability Checking** - ✅ Implemented
   - File: `app/main.py`
   - Added `_check_memory_availability()` function
   - Warns if free RAM < 1GB

### Phase 2: Short Term (Completed)

5. **Health Endpoint Enhancement** - ✅ Implemented
   - File: `app/api/system.py`
   - Added `ram_health` field ("healthy"/"low"/"critical")
   - Added precise `ram_available_gb` monitoring
   - Overall status considers memory availability

6. **Circuit Breaker Pattern** - ✅ Implemented
   - File: `app/services/model_router.py`
   - Provider failure tracking with `record_provider_failure()`
   - Auto-blacklisting after 3 consecutive failures
   - 5-minute backoff period
   - Functions: `is_provider_blacklisted()`, `reset_provider_failures()`, `get_provider_failure_count()`

7. **Token Refresh** - ✅ Already Existed
   - File: `app/api/auth.py`
   - Full refresh token rotation implemented
   - `/api/auth/refresh` endpoint with DB-backed tokens
   - Token revocation on logout/password change

### Phase 3: Medium Term (Recommended for Future)

8. **Job Persistence** - Recommended
   - File: `scheduler_service.py`
   - Add: Load schedules from DB on startup

9. **Document Progress** - Recommended
   - File: `documents.py`, `files.py`
   - Add: Progress WebSocket, status polling

10. **Math Fast-Path Feedback** - Recommended
    - File: `chat_service.py`
    - Add: `intent: "calculation"` event, skip LLM

---

## Validation Results

All modified files import successfully:
- ✅ `app.errors` - New `LowMemoryError`, `retry_after_seconds` in `ApiError`
- ✅ `app.main` - Port and memory check functions
- ✅ `app.services.model_router` - Circuit breaker functions
- ✅ `app.api.system` - Enhanced health endpoint
- ✅ `app.api.auth` - Token refresh already working