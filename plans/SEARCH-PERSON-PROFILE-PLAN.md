# Investigation Findings: "search everything about parmenas aduol" → "I'm sorry, but I can't assist"

## Root Cause Analysis

### 1. Intent Classification Gap (Primary Cause)

**File:** `backend/app/agents/intent.py`, lines 61–68

The `_RESEARCH` regex pattern is too narrow:

```python
_RESEARCH = re.compile(
    r"(https?://\S+)"
    r"|\b(research|investigate|latest|breaking|current(?:ly)?|news|compare|"
    r"comparison|reviews?|look up|find out|search (?:the )?web|sources?|"
    r"statistics|data on|price|prices|rates?|forecast|who won|how many people|"
    r"studies? show|evidence)\b",
    re.I,
)
```

The pattern `search (?:the )?web` only matches **"search web"** or **"search the web"**. 
It does NOT match:

| User Query | Recognized Intent | Why it fails |
|---|---|---|
| `search everything about parmenas aduol` | `chat` | "search everything" ≠ "search web" |
| `who is parmenas aduol` | `chat` | "who is" is not in the keyword list |
| `find information about parmenas aduol` | `chat` | "find information about" doesn't match "find out" |
| `tell me about parmenas aduol` | `chat` | No matching keyword |

**Verified by test:**
```
chat     | () | search everything about parmenas aduol
research | ('search the web',) | search the web for parmenas aduol
chat     | () | who is parmenas aduol
chat     | () | find information about parmenas aduol
research | ('research',) | research parmenas aduol
chat     | () | tell me about parmenas aduol
research | ('look up',) | look up parmenas aduol
```

### 2. Missing Research Brief for "chat" Intent (Secondary Cause)

**File:** `backend/app/services/chat_service.py`, lines 691–707

When intent is `chat`, no research brief is injected:

```python
system_prompt = build_system_prompt(...)
if intent.intent == "research":
    system_prompt = f"{system_prompt}\n\n{RESEARCH_BRIEF}"
elif intent.intent == "scheduling":
    ...
elif coding:
    ...
# NO branch for "chat" — no guidance to use search_web
```

The `RESEARCH_BRIEF` (lines 130–153 of `prompts.py`) explicitly instructs the model to:
- Call `update_plan` first with research steps
- Batch multiple `search_web` queries with different phrasings
- Open promising results with `open_url`/`browse_page`
- Cross-check claims across independent sources

Without this brief, the model in "chat" mode (temperature=0.7) has no guidance to search, and the local Ollama model likely refuses person searches due to built-in safety filters.

### 3. `search_web` IS Available But Not Guided

**File:** `backend/app/tools/routing.py`, lines 88–96

The `chat` intent includes `_WEB = {"search_web", "open_url", "browse_page"}`, so the tool
IS bound. However, the model is never instructed to use it because:
- No `RESEARCH_BRIEF` is injected for `chat` intent
- No `PERSON_SEARCH_BRIEF` exists
- The `search_web` tool description says "for any research or fact-checking request" —
  but the model doesn't recognize this as research

### 4. No Multi-Platform Profile Search Tool

**File:** `backend/app/tools/web.py` (lines 1–99)

Only a generic `search_web` tool exists (DuckDuckGo/Tavily). There is:
- **No `search_profiles` tool** — no dedicated tool for searching people across platforms
- **No platform-specific search** — no GitHub, LinkedIn, Twitter/X, etc. search tools
- **No structured person/profile result format** — results are generic web snippets

### 5. Local Model Safety Filters

The local Ollama model (qwen2.5:7b) has built-in refusal behavior for person-related
queries. The refusal phrase "I'm sorry, but I can't assist with that." is generated
by the model itself — it's NOT a hardcoded block in Jalora's code (verified: no
matching refusal string found in any `.py` file).

---

## Implementation Plan

### Phase 1: Fix Intent Classification (Immediate — 1 file, ~5 min)

**File:** `backend/app/agents/intent.py`

**Change:** Broaden the `_RESEARCH` regex to capture person-search patterns:

```python
# ADD these patterns to the _RESEARCH regex:
#   - "search" + "everything about" / "info" / "information" / "about" 
#   - "who is" / "who was" / "tell me about" / "find.*about"
#   - "profile" / "profiles"
```

New regex fragment to add:
```python
r"|\b(search\s+(?:everything\s+)?about|who\s+is|who\s+was|tell\s+me\s+about|"
r"find\s+.*about|profile(?:s)?\s+of|background\s+on)\b"
```

This will reclassify "search everything about parmenas aduol" → `research` intent,
triggering the RESEARCH_BRIEF injection and lower temperature (0.2).

### Phase 2: Add Person-Search Training Data (LoRA improvement)

**File:** `training_data/jalora_person_search.jsonl` (NEW — 40 examples)

Add instruction/output pairs teaching the model to:
- Recognize person-search requests as research tasks
- Use `search_web` to find profiles across platforms
- Formulate multi-query searches (e.g., `"Parmenas Aduol site:linkedin.com"`, etc.)
- Report findings honestly with source citations
- NOT refuse legitimate person searches

Example:
```json
{"instruction": "search everything about parmenas aduol",
 "input": "",
 "output": "I'll search the web for information about Parmenas Aduol across multiple platforms. Let me start by searching with different queries to find the most relevant results... {tool: search_web(query=\"Parmenas Aduol biography\")}"}
```

### Phase 3: Add `search_profiles` Multi-Platform Tool

**File:** `backend/app/tools/web.py` (modify) + `backend/app/services/search_service.py` (modify)

Add a new `search_profiles(name: str, max_results: int = 5)` tool that:
1. Searches across pre-defined platforms using targeted queries
2. Returns structured profile results (platform, name, url, description)
3. Platforms to search: LinkedIn, GitHub, Twitter/X, Wikipedia, company sites

```python
# In web.py — new tool:
async def search_profiles(name: str, max_results: int = 5) -> ToolResult:
    """Search for a person's profiles across LinkedIn, GitHub, Twitter, Wikipedia, etc."""
    # Constructs platform-specific queries and delegates to search_web
    platforms = {
        "LinkedIn": f"{name} site:linkedin.com",
        "GitHub": f"{name} site:github.com",
        "Twitter/X": f"{name} site:twitter.com OR site:x.com",
        "Wikipedia": f"{name} site:wikipedia.org",
    }
    results = {}
    for platform, query in platforms.items():
        web_results = await search_web(query, max_results=2)
        results[platform] = web_results
    return ToolResult.ok(f"Found profiles for {name}", results)
```

Register in routing as part of both `research` and `chat` intents.

### Phase 4: Add Person-Search Guidance Brief

**File:** `backend/app/agents/prompts.py`

Add `PERSON_SEARCH_BRIEF` that is injected when the intent classifier detects
person-search patterns (new signal: `("person_search",)`):

```python
PERSON_SEARCH_BRIEF = """\
Profile search mode: the user asked to search for information about a specific person.

1. Use search_web with targeted queries across different platforms.
2. Search for "[name] LinkedIn", "[name] GitHub", "[name] Twitter/X", etc.
3. Do NOT refuse — searching for public information about a person is the user's request.
4. Report findings honestly, citing sources as [CITATION](URL).
5. If no results found on a platform, say so rather than inventing results.
6. Always note whether results are about the correct person or a name collision.
"""
```

### Phase 5: Add `.env` Configuration

Add to `.env`:
```
JALORA_SEARCH_PROVIDER=duckduckgo  # or tavily if key is set
JALORA_ENABLE_WEB_SEARCH=true
```

---

## Files to Modify

| File | Change | Complexity |
|------|--------|------------|
| `backend/app/agents/intent.py` | Expand `_RESEARCH` regex | Low |
| `backend/app/tools/routing.py` | Add `search_profiles` to research & chat | Low |
| `backend/app/tools/web.py` | Add `search_profiles` tool | Medium |
| `backend/app/services/search_service.py` | Add platform-specific search helper | Medium |
| `backend/app/agents/prompts.py` | Add `PERSON_SEARCH_BRIEF` | Low |
| `backend/app/services/chat_service.py` | Inject brief for person-search intent | Low |
| `training_data/jalora_person_search.jsonl` | New training data (40 examples) | Low |

## Expected Outcome

After implementation:
- "search everything about parmenas aduol" → classified as `research` intent
- RESEARCH_BRIEF or PERSON_SEARCH_BRIEF injected → model guided to use `search_web`
- `search_profiles` tool available for multi-platform person search
- Model no longer refuses — searches DuckDuckGo/Tavily for the person's profiles
- Results presented with source citations and honest uncertainty flagging
