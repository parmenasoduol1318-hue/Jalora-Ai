# Jalora 11-Phase Improvement Plan

## Overview
Audit and harden the 10 priority Jalora agent systems across 11 phases (10 improvement areas + 1 final integration phase). Each phase: audit → implement → test 10 examples → verify → proceed.

**Test runner:** `.venv/Scripts/python -m pytest jalora/backend/tests/` (use `.venv` Python to avoid orjson DLL issues on system Python 3.12).

**Baseline state (from running tests):**
- `test_intent.py`: 17 tests PASS
- `test_routing.py`: 14 tests, 1 FAIL (`test_intent_sets_are_focused` — `send_payment` not in chat intent focused tools)
- `test_math.py`: extensive, 1 FAIL (`test_classify_science_intent[apply ohm's law...]` → classified as "research" not "science")

---

## Phase 1: Intent Detection (P0)
**Goal:** Distinguish chat, web search, email, math, coding, and other tasks reliably.

**Audit findings:**
- `classify_intent()` in `intent.py` uses regex priority ordering. Missing "web_search" as a distinct intent — web searches bleed into "research". Missing "email" as a distinct intent — only has helpers `extract_email_address` / `extract_email_parameters` but no intent classification.
- Job/career keywords short-circuit to "research" before coding is checked (e.g. "python job" → research, not coding).
- Science intent (Ohm's law, physics) misclassified as "research" because research keywords match first.
- `routing.py` has no "web_search" intent entry; search tools only accessible under "research" or full registry (None).
- `test_intent_sets_are_focused` expects `send_payment` in chat tools but it's not there.

**Plan:**
1. Add "web_search" intent: triggered by "what is", "latest", "current", "news", "how to" + absence of deep research markers.
2. Add "email" intent: triggered by "send email", "email", "compose", "write to".
3. Fix science classification: move science check before research, or add science patterns (ohm, voltage, physics, chemistry, equation).
4. Add "web_search" → search_web/open_url tool mapping in routing.py.
5. Add "email" → email tools in routing.py.
6. Fix the chat focused tool set to include money-movement tools per test expectations.
7. Write 10 examples + tests.

**Examples to test (10):**
1. "What is the latest news about AI?" → web_search
2. "Search the web for quantum computing" → web_search
3. "Send an email to john@example.com about the meeting" → email
4. "Email my boss about the quarterly report" → email
5. "What is 2+2?" → math
6. "Apply Ohm's law for 12V and 4 ohm" → science
7. "How do I fix a leaky faucet?" → web_search (how-to, not deep research)
8. "Research the causes of climate change" → research (deep)
9. "Write a Python function to sort a list" → coding
10. "Tell me a joke" → chat

---

## Phase 2: Tool Execution (P0)
Fix any tool returning text instructions instead of executing. 10 examples.

## Phase 3: Conversation Context (P0)
Multi-turn context persistence. 10 examples.

## Phase 4: Web Research (P0)
Search → extract → cite pipeline. 10 examples.

## Phase 5: Action Verification (P0)
Report success after external service confirms. 10 examples.

## Phase 6: Entity Recognition (P1)
People, orgs, dates, emails, phones. 10 examples.

## Phase 7: Error Recovery (P1)
Retry transient failures, explain permanent. 10 examples.

## Phase 8: Memory and Retrieval (P1)
Per-user memory isolation + retrieval. 10 examples.

## Phase 9: Model Routing (P1)
Math→engine, tools→executor. 10 examples.

## Phase 10: Testing and Monitoring (P1)
Track tool selection, args, failures, completion. 10 examples.

## Phase 11: Integration & End-to-End
All 10 systems together. 10 examples.

---

## Execution Strategy
- Work ~1 hour per phase.
- Each phase: audit (read files) → implement (edit files) → test (10 examples) → verify → document.
- Use `.venv\Scripts\python -m pytest` for running tests.
- Create a test file per phase.
