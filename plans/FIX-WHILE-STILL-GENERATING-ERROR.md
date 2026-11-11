# Fix: "Something went wrong" Still Occurring After Prior Fixes

## Overview

The user is still seeing "Something went wrong. Please try again." after the
previous round of fixes. Root-cause analysis reveals a **two-layer problem**:

1. **Server-side: LoRA is "configured" but never running.** `is_configured("lora")`
   returns `True` even when `lora_enabled=False` because `ProviderSpec.requires_key=False`
   makes every keyless provider auto-configured. The router selects LoRA (which sits
   early in the patched `AUTO_PRIORITY`), tries to connect to `base_url=""`, fails,
   and the error cascades. Even with the improved exception handler, the frontend
   maps `INTERNAL_ERROR` → `errGeneric` ("Something went wrong").

2. **Frontend-side: missing LoRA error codes.** `FRIENDLY_CODE` in `errors.ts`
   has no `LORA_*` entries, so even a properly-mapped `LORA_OFFLINE` error falls
   through to `errGeneric` unless the raw details are short and non-technical.

3. **Stale process: the server may not have been restarted** with the prior fixes,
   so the old code (pre-`final_ai` None guard, pre-`_LOCAL_PROVIDERS` fix) is
   still live.

## Implementation Approach

### Step 1 — Fix `is_configured` to respect `lora_enabled` (model_router.py)

The patched `is_configured` in `provider_extensions.py` calls
`_orig_is_configured` which returns `True` for any `requires_key=False` provider.
LoRA should only be "configured" when `lora_enabled=True`.

**No source change needed.** Instead, gate LoRA in `build_provider_specs`:
when `lora_enabled=False`, set `requires_key=True` (or `base_url="*"`) so
`is_configured` returns `False` and the router skips it.

```python
# model_router.py line 126-130 — change requires_key to gate on lora_enabled
"lora": ProviderSpec(
    "lora", "openai", s.lora_local_url.rstrip("/"),
    None, s.lora_local_model_name, s.lora_local_model_name,
    requires_key=False) if s.lora_enabled else
    ProviderSpec(
        "lora", "openai", "", None, s.lora_local_model_name,
        s.lora_local_model_name, requires_key=True),  # True → is_configured() returns False
```

### Step 2 — Add LoRA error codes to frontend `errors.ts`

```typescript
// errors.ts — add after JAN_* entries
LORA_OUT_OF_MEMORY: "errOom",
LORA_MODEL_MISSING: "errModelMissing",
LORA_OFFLINE: "errLocalOffline",       // "The local server isn't running..."
LORA_ERROR: "errLocalGeneric",          // "The local model couldn't process..."
```

### Step 3 — Improve `_continue_from_state` exception handler (chat_service.py)

`_continue_from_state` (line 1077) has its own `except Exception` at line 1197
that already uses `_describe_model_error` — good. But verify the `final_ai` None
guard is present (already fixed in step 1 of previous round).

### Step 4 — Add LoRA healthcheck to `_healthcheck_patched` (provider_extensions.py)

The healthcheck at line 231 only probes LM Studio and Jan. Add a LoRA liveness
probe (hit `base_url/models`) so the Settings UI shows "LoRA not running"
instead of nothing.

```python
# provider_extensions.py line 237, extend the provider list
if provider in ("lmstudio", "jan", "lora") and provider in self.specs:
    ...
```

### Step 5 — Restart the server

User must restart the backend so all on-disk changes take effect.

## Testing Strategy

Run `python -c "..."` snippets from the project root: verify `is_configured("lora")`
returns `False` with `lora_enabled=False`, confirm intent classification still
maps "search everything about X" → `research`, and run `ast.parse` on every
modified file. Then restart the backend and send "hello" and "who is the
president of kenya" through the chat endpoint to confirm no "Something went
wrong" error.
