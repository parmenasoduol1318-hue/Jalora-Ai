# JALORA capability gap audit and implementation plan

**Audit date:** 13 November 2026
**Audited checkout:** `fix/search-pairing`, commit `d62f330`
**Purpose:** compare the capabilities in the supplied ChatGPT benchmark with the
current JALORA source and recorded acceptance evidence, then provide an ordered
plan to close the highest-value gaps.

## Executive summary

JALORA is not a blank-slate chatbot. The codebase has a central LangGraph
assistant, a typed tool registry, persistent conversations and task records,
approval flows, model routing, search, memory, document handling, email and
calendar modules, schedules, voice/media code, a coding workspace, and Windows
and Android companion source.

That inventory is not equivalent to ChatGPT feature parity or production
readiness. The most important current gaps are:

1. **Proof across execution entry points:** durable task/approval/recovery
   behavior is strongest in the central chat path; cross-surface consistency and
   external-provider outcomes still need acceptance.
2. **Evidence-based research:** search and citation machinery exists, but live
   source quality, independent corroboration, exhaustive reports, and
   hundreds-source scale have not been demonstrated.
3. **Real integration and device acceptance:** code and mocked tests do not
   prove live Gmail/Google Workspace operation or physical Windows/Android
   control.
4. **Context and data scale:** document chunking and history compaction exist;
   very large codebases, six-figure-token coherence, large media, scanned
   documents, and large dataset analysis have no end-to-end capacity evidence.
5. **Product surfaces:** the coding Workspace is not a general-purpose Canvas;
   no user-facing general custom-assistant builder was found.
6. **Release readiness:** the master roadmap records stages 7, 10, 11, 12, and
   14 as partial and stage 15 as not started. This is the clearest summary of
   the remaining work.

**Recommendation:** follow the existing
[JALORA Master Implementation Roadmap](../plans/JALORA-MASTER-IMPLEMENTATION-ROADMAP.md)
as the authoritative execution sequence. This report is its capability-gap
crosswalk, not a competing implementation plan. Do not create a separate agent
for every feature: integrations and device companions remain capabilities of
the central planner and shared execution lifecycle.

## Scope, evidence rules, and limits

This source audit covered the active `backend/app` and backend tests,
`frontend-react`, Windows/Android companion source, and current project
documentation. Evidence status uses these meanings:

- **Verified, bounded:** code and cited automated/local acceptance support the
  stated scope. It does not imply every provider, account, OS version, or
  scenario works.
- **Implemented, not externally verified:** source and/or mock tests exist, but
  a live provider, production account, or real device was not tested.
- **Partial:** useful code exists but material requirements or cross-path
  evidence are missing.
- **Not evidenced:** no matching user-facing capability or supporting
  implementation was established in the inspected active application.

The audit did **not** run the complete backend or browser suites, connect to
live provider accounts, send communications, or operate physical devices.
Previously recorded test results are identified as historical evidence, not
new test runs. A plan, endpoint, mock, or UI alone is not treated as proof that a
feature works.

The submitted table is used as a **capability taxonomy**, not as a verified
inventory of every current ChatGPT feature. ChatGPT capabilities vary by plan,
region, product surface, and release; proprietary internals cannot be audited
from this repository. No independent product benchmarking was performed.

## Capability comparison

| Capability area | JALORA current position | Evidence and concrete gap |
|---|---|---|
| **Frontier models and complex reasoning** | **Partial; provider-dependent.** Provider-independent routing and complex-task mappings exist. | [`model_router.py`](../backend/app/services/model_router.py), [`provider_extensions.py`](../backend/app/services/provider_extensions.py), and [`test_model_router.py`](../backend/tests/test_model_router.py) configure and exercise model selection. The six mappings added at the audited commit are routing configuration, not proof that provider accounts expose those IDs or that their reasoning quality meets a target. “Gemini 4 Carbon” and “Gemini 4 Argon” are not configured or verified in this checkout; validate names and access against Google's current official catalog before promising them. Grad-level mathematics and scientific performance need benchmark evidence beyond deterministic math-tool tests. |
| **Very large context and multi-file/media ingestion** | **Partial; scale unverified.** History compaction and document chunking/retrieval exist. | [`chat_service.py`](../backend/app/services/chat_service.py) and [`document_service.py`](../backend/app/services/document_service.py) provide context/document handling. Tests for truncation or chunk retrieval do not prove coherent processing of hundreds of thousands of tokens, entire repositories, multi-hour recordings, or large collections. No measured file-size, token, latency, or retrieval-recall acceptance is recorded. |
| **Live web research and citations** | **Partial; provider-dependent.** Search, page opening, source collection, and citation-oriented report behavior exist in the agent/tool layer. | [`search_service.py`](../backend/app/services/search_service.py), [`graph.py`](../backend/app/agents/graph.py), [`web.py`](../backend/app/tools/web.py), and [`test_capability_acceptance.py`](../backend/tests/test_capability_acceptance.py) support and test bounded research behavior. A citation test proves a bounded mock scenario, not that sources are current, authoritative, independently corroborated, conflict-resolved, or sufficient for legal/academic reporting. Hundreds-source crawling and exhaustive multi-page reports are not verified. |
| **Software engineering and repository work** | **Partial to strong bounded local foundation; external/live acceptance incomplete.** Workspace editor, terminal, project files, and Copilot/Codex adapters exist. | [`WorkspaceView.tsx`](../frontend-react/src/views/WorkspaceView.tsx) and the workspace/coding services support development workflows. The master roadmap records mock-provider and isolated staging-copy tests for external coding adapters. This does not establish reliable operation over arbitrary large or multiple live repositories, every test toolchain, live SDKs, or a deploy workflow. Deployment must remain an explicit approval boundary. |
| **Browser and computer operation** | **Partial and carefully scoped.** Server-side browser automation, local Windows automation, and native Windows/Android companion source exist. | The browser tools operate an isolated browser context, not necessarily the user's logged-in browser profile. Windows uses allowlisted applications and supported accessibility/UIA operations; OCR requires Tesseract. Android uses explicitly granted accessibility capabilities and selected notifications; the companion does not implement screen-image capture or coordinate gestures. The companion implementations and protocol have automated tests, but Windows interactive acceptance and Android APK/device acceptance are not recorded. Neither OS provides unrestricted access to every application or private surface. See [`device-companion.md`](../docs/device-companion.md) and the [Windows](../companions/windows/README.md) and [Android](../companions/android/README.md) companion READMEs. |
| **WhatsApp and other personal communications** | **Partial; personal WhatsApp history is not verified.** Messaging send paths and provider adapters exist; consequential sending is approval-gated where supported. | No acceptance evidence demonstrates reading a personal account's full WhatsApp inbox, groups, unread state, or history. Supported official APIs, account type, OS permissions, and provider policy determine what is possible. Draft/approval/send/receipt must be verified for the exact recipient and message; API acceptance is not proof of delivery. |
| **Documents, spreadsheets, and data analysis** | **Partial.** Common document ingestion and generated artifacts exist; general-purpose analysis coverage is incomplete. | [`document_service.py`](../backend/app/services/document_service.py) and [`test_documents.py`](../backend/tests/test_documents.py) cover supported ingestion paths including PDF, text/Markdown, CSV, JSON, HTML, DOCX, XLSX, and PPTX, followed by chunking/indexing. The UI supports uploads and queries. This does not establish reliable OCR of scans, high-fidelity table extraction, very large collections, arbitrary ETL, statistical analysis, interactive charts, or end-to-end Word/Excel/PowerPoint quality. |
| **Gmail and Google Workspace** | **Partial; live account behavior unverified.** Gmail OAuth and email workflows exist; Calendar/Drive have provider and mock/local paths. | [`email.py`](../backend/app/services/email.py), [`calendar.py`](../backend/app/services/calendar.py), [`drive.py`](../backend/app/services/drive.py), and [`integration_registry.py`](../backend/app/services/integration_registry.py) show the current boundary. Registry descriptions identify mock-first behavior for some integrations, and the Drive service includes local per-user storage. No live acceptance proves the full Gmail/Drive/Docs/Sheets/Slides ecosystem. Do not describe a connector as Google Workspace integration solely because a view or mock exists. |
| **Memory and personal/project context** | **Partial with useful user-scoped memory controls.** Persistent memory, retrieval, user ownership, retention, filtering, and deletion are present. | [`memory_service.py`](../backend/app/services/memory_service.py), [`test_memory.py`](../backend/tests/test_memory.py), and [`MemoryView.tsx`](../frontend-react/src/views/MemoryView.tsx) provide memory APIs and user controls. The roadmap still marks context/memory quality as partial: consistent project, organization, task, and cross-entry-point context, as well as export/deletion and retrieval-quality acceptance, need completion. |
| **Voice, audio, image, and video** | **Partial; dependency/provider and real-device gates apply.** Voice, transcription, image input/analysis, creative generation, and media-processing code exist. | [`voice.py`](../backend/app/services/voice.py), [`transcribe.py`](../backend/app/services/creative/transcribe.py), [`media_engine.py`](../backend/app/services/creative/media_engine.py), and [`test_phase_d_media.py`](../backend/tests/test_phase_d_media.py) implement/test bounded functions. The model router carries image input for supported endpoints; this is not general video/audio reasoning. Media paths may require optional tools such as PyAV/FFmpeg; available tests include mock/offline cases and honest dependency failures. No live benchmark demonstrates cinematic video generation, speaker accuracy, multi-hour meeting summaries, or real-time audio quality. |
| **Scheduling and autonomous workflows** | **Partial-to-verified in bounded test scope.** Persistent schedules, leases/recovery, occurrence identity, and duplicate-fire safeguards exist. | [`scheduler.py`](../backend/app/services/scheduler.py) and [`test_phase_g_schedules.py`](../backend/tests/test_phase_g_schedules.py) cover focused concurrency/recovery cases. The roadmap records isolated schedule and UI tests. This is not evidence of a general event bus, broad always-on monitoring, all integrations triggering actions, or production multi-worker exactly-once side effects. |
| **Projects, Canvas, and custom assistants** | **Mixed.** A coding Workspace and project features exist; a general-purpose collaborative Canvas and general custom-assistant/Gem builder were not found in the active UI. | [`WorkspaceView.tsx`](../frontend-react/src/views/WorkspaceView.tsx) is a coding workspace with editor/terminal/project flows, not a generic side-by-side document Canvas. The UI navigation and views audited expose memory and saved workflows but not a general assistant persona builder with instructions/files/sharing. |
| **Professional and specialized work** | **Capability-dependent; not globally verified.** Tools cover selected domains such as mathematics, jobs, travel, communications, and creative output. | Specialized tool availability does not establish professional-grade advice, completeness, currentness, or domain-specific accuracy. There is no single measured evaluation suite covering legal/contract analysis, academic literature review, financial analysis, job discovery, and all specialized areas. |
| **Security, privacy, and enterprise operations** | **Substantial foundations; production readiness partial.** Authentication, user scoping, policy/approval, audit, rate limits, secure URL handling, and operational tooling exist. | [`policy.py`](../backend/app/services/policy.py), [`execution_lifecycle.py`](../backend/app/services/execution_lifecycle.py), and the master roadmap document meaningful controls. Remaining gates include independent security review, production deployment/rollback and backup rehearsal, privacy export/deletion policy, multi-tenant/load evidence, and live companion/provider security acceptance. |
| **Accessibility, localization, and product breadth** | **Not comprehensively verified.** React views cover major workflows; no full accessibility/localization acceptance was found in the reviewed evidence. | Keyboard/screen-reader, low-bandwidth, multilingual, responsive edge cases, and systematic translation quality should be measurable release criteria rather than assumptions from UI presence. |

### Strongest current evidence

- The central LangGraph graph discovers registered capabilities, validates
  plans and tool results, applies permissions, and has bounded execution. See
  [`graph.py`](../backend/app/agents/graph.py) and
  [`base.py`](../backend/app/tools/base.py).
- Durable task/run/step/attempt and shared approval services exist. The roadmap
  records stages 1–6 as verified within their stated boundaries, while stage 7
  remains partial.
- The frontend exposes chat/research/agent modes, citations/artifacts,
  approvals, Tasks, scheduling, integrations, memory, documents, and a coding
  Workspace. It does not prove that the linked backend/provider action
  completed.
- Windows and Android companion source implements scoped operations. Windows
  supports bounded allowlisted app/UI automation and optional OCR; Android
  supports selected-app launch, accessibility inspection, clicks, scroll,
  back, text entry, and selected notifications when the required permissions
  are granted. Device-level acceptance remains unverified.
- Recorded automated acceptance is strongest for isolated/mock workflows. The
  latest roadmap records focused test batches for scheduling, approvals,
  coding adapters, device protocol, and local UI, but also records unresolved
  live-provider/device/deployment gates.

### Claims that must not be made yet

- “ChatGPT parity,” “hundreds of sources,” “hundreds of thousands of tokens,”
  “all apps,” “unrestricted device access,” or “fully autonomous.”
- That a provider/model name in configuration is available to every account
  or produces frontier-quality results.
- That Gmail/Drive/Calendar/WhatsApp actions succeeded without provider-side
  evidence or a real authorized acceptance test.
- That a scheduled task, approval, click, accepted API call, queued message, or
  generated artifact is complete without the appropriate outcome evidence.
- That mocked tests establish live delivery, production security, or physical
  device compatibility.

## Prioritized implementation plan

This plan maps gaps onto the existing roadmap and preserves its order and
architecture. **The central agent remains the single planner/executor.** New
features should be tools, connectors, or UI surfaces using the shared task,
policy, approval, and evidence lifecycle.

### 0. Keep the capability inventory truthful

**Roadmap alignment:** stages 11, 12, 13, 15.
**Priority:** immediate documentation and release hygiene.

- Maintain a capability catalog with per-feature status: implemented,
  mock-tested, provider-tested, device-tested, unsupported, or blocked.
- List supported providers, account types, OS/app versions, required scopes,
  and known limits; distinguish API, browser automation, local-host, and
  paired-device behavior.
- Validate all model IDs and limits against provider catalogs before publishing
  them. Keep model access configurable and report unavailable models explicitly.
- Update product copy whenever evidence changes; never use a plan or roadmap as
  proof of implementation.

**Exit criteria:** every advertised capability links to a code path and
acceptance evidence; unsupported/unverified states are visible in the UI and
documentation.

### 1. Close shared task, approval, and recovery gaps

**Roadmap alignment:** complete stage 7, then stage 15.
**Priority:** highest; prerequisite for dependable multi-app workflows.

- Finish lifecycle parity for direct email/calendar/document/device actions,
  schedules, voice, saved workflows, and coding adapters.
- Preserve exact task/run/step/approval IDs and exact approved payloads across
  interfaces; unify cancellation, edit/revision, revoke, and status reporting.
- For each external side effect, persist intent before dispatch, use provider
  idempotency where supported, reconcile unknown outcomes, and never blindly
  retry.
- Add process-restart and failure-injection acceptance for action classes,
  including provider timeouts, permission denial, partial success, and user
  cancellation.
- Keep “submitted,” “provider accepted,” “delivered,” and “verified” distinct.

**Exit criteria:** tests demonstrate one action is not duplicated after
restart/timeout; every supported UI shows the same task and approval state; an
unknown result blocks unsafe replay and is reported honestly.

### 2. Establish evaluation and context/memory quality

**Roadmap alignment:** complete stage 10.

- Define privacy-scoped context layers: conversation, task, project, user
  preference, organization, and execution history. Retrieve only authorized,
  relevant records; do not dump all memory into every prompt.
- Finish inspect/correct/retain/export/delete and provenance controls; test
  deletion and isolation across users, tasks, connected accounts, and entry
  points.
- Build versioned evaluation sets for factuality, instruction constraints,
  ambiguity/clarification, math/science, multilingual behavior, tool choice,
  arguments, refusals, citations, latency/cost, and recovery.
- Set numeric acceptance thresholds from a measured baseline before claiming
  improvements. Record provider/model/version and test inputs for reproducible
  comparisons.

**Exit criteria:** context isolation and memory lifecycle tests pass; the
quality dashboard has reproducible baselines and regression thresholds; no
quality claim depends on anecdotal prompts alone.

### 3. Deliver source-grounded Deep Research

**Roadmap alignment:** stage 11 (web research) with stage 10 evaluations.

- Use the central planner to scope questions, identify subquestions, select
  sources, and define a bounded source/time/budget limit. Do not promise
  “hundreds” before a scale test.
- Store source URL, title, retrieval time, publisher/author, relevant excerpt,
  and stable citation ID. Re-open or otherwise verify sources before citing.
- Prefer primary/official sources where appropriate; independently corroborate
  important claims and surface conflicts, stale pages, inaccessible content,
  and uncertainty.
- Keep retrieved content isolated as untrusted evidence. It must not alter
  system instructions, authorize tools, or bypass approvals.
- Generate structured reports with inline citations, bibliography, method,
  limitations, and downloadable artifact; support report review and continuation.

**Exit criteria:** mock and live read-only tests cover freshness, conflicting
sources, inaccessible pages, citation-to-claim correctness, source deduplication,
and bounded failure. A source-count/latency/cost benchmark defines supported
scale; no live browsing is claimed from mock-only results.

### 4. Complete document intelligence and safe data analysis

**Roadmap alignment:** stage 11 (files/documents) and stage 14 (sandbox/security).

- Add measurable limits for upload size, page count, token count, OCR, workbook
  sheets, and execution time; provide clear errors when exceeded.
- Test PDFs, scans/OCR, tables, DOCX, XLSX, CSV, PPTX, HTML, and multi-document
  comparison against known expected outputs.
- Add isolated, resource-limited Python/data analysis for CSV/XLSX with
  deterministic file access, no implicit network/secrets, reproducible
  statistics, and chart artifacts.
- Validate generated DOCX/XLSX/PPTX/PDF files structurally and visually where
  practical; show previews and ownership-safe downloads.
- Require explicit approval for destructive file changes, external uploads,
  sharing, or overwriting important content.

**Exit criteria:** format-specific regression corpora, size-boundary tests,
malicious-file tests, sandbox escape tests, and generated-file validation pass;
the UI reports which formats and limits are actually supported.

### 5. Turn integrations into verified connectors

**Roadmap alignment:** stage 7 and stage 11.

- Audit OAuth scopes, token lifecycle, provider error handling, ownership, and
  mock-vs-live mode for Gmail, Calendar, Drive, and other listed integrations.
- For Google Workspace, implement only supported official APIs and the scopes
  needed for each capability. Verify Drive search/read, Docs create/edit,
  Sheets read/write, and Slides create/edit separately; do not infer them from
  Gmail or a generic “Google connected” state.
- Keep email approvals exact and per-message, display all recipients/content/
  attachments/account, and reconcile send status through provider evidence.
- Define WhatsApp product scope by supported API/account type. If personal chat
  history is unavailable through an authorized supported interface, report
  that limitation rather than simulating access.
- Use a common connector manifest for authentication, capabilities, scopes,
  sensitivity, rate limits, result provenance, and action-verification method.

**Exit criteria:** provider contract tests and authorized sandbox tests verify
read/search/draft/edit/approve/send/reconcile paths independently for each
connector; each write is separately approved and audited.

### 6. Complete Windows/Android device acceptance

**Roadmap alignment:** stage 12, then stages 13–15.

- Build and sign distributable companion packages; document version support,
  install/update/revocation, permissions, and recovery.
- Validate pairing, identity, encrypted transport, heartbeat, reconnect,
  inventory, permission denial/revocation, and target-side approval on physical
  Windows and Android test devices.
- Publish per-app capabilities rather than promising every installed app.
  Windows support should disclose UI Automation/OCR limits; Android should
  disclose accessibility, secure-window, notification, and background limits.
- Require observe → identify → act → verify behavior; stop and reassess on
  unexpected screens. Return evidence without leaking secrets or password
  values.
- Test task continuation after a device reconnect without repeating completed
  external actions; report uncertain outcomes explicitly.

**Exit criteria:** a named OS/version/app/permission matrix passes on real
devices, including disconnect/restart and revocation tests. Until then, label
the companion as source-implemented/mock-tested, not device-verified.

### 7. Finish product surfaces: task center, Canvas, and customization

**Roadmap alignment:** stage 13; extension of stage 11.

- Preserve the existing coding Workspace; add a separate general-purpose
  side-by-side Canvas only if it meets a distinct need for editable documents,
  plans, research reports, and structured content.
- Add project-level instructions/files/context with explicit scope, ownership,
  retrieval, and deletion; avoid automatically injecting every project file.
- If custom assistants/personas are in scope, build a user-controlled
  configuration surface for instructions, allowed tools, reference files,
  sharing, and version history. A saved workflow is not a custom assistant.
- Provide consistent approval, evidence, progress, pause/cancel, blocked-state,
  and recovery details across Chat, Tasks, Workspace, Integrations, and
  notifications.
- Test keyboard-only, screen-reader, mobile, localization, and low-bandwidth
  interactions.

**Exit criteria:** browser tests cover each supported task state and entry point;
accessibility and project-scope tests pass; product copy distinguishes Canvas,
coding Workspace, workflow templates, and assistants.

### 8. Close multimedia and voice quality gaps

**Roadmap alignment:** stage 11 and stages 14–15.

- Use separate capability contracts for image input, image generation/editing,
  audio transcription/understanding, video transcription/summarization, and
  video generation; do not imply one is available because another exists.
- Add provider/tool availability checks, size/duration limits, progress,
  cancellation, transcript provenance, speaker-attribution confidence, and
  artifact validation.
- Verify real microphone/speaker, interruption, reconnect, and voice-to-central
  task continuity on supported browsers/devices; keep telephony tests separate
  from in-app voice tests.
- Integrate only model providers and media services that are explicitly
  configured and tested; report missing native dependencies such as FFmpeg
  clearly.

**Exit criteria:** synthetic and consented test media have expected
transcription/summary/output checks; real-device/browser tests are reported
separately from mocks and provider contract tests.

### 9. Complete production security, reliability, and release gate

**Roadmap alignment:** complete stage 14 before stage 15.

- Finish independent security review, threat modeling, user-data export/
  deletion, key rotation, retention, audit integrity, tenant isolation, and
  incident response.
- Rehearse production database migration, backup/restore, rollback, worker
  concurrency, queue/provider outages, and cost/rate limits.
- Establish SLOs for availability, task completion, safe aborts, duplicate
  actions, recovery time, citation correctness, and support response.
- Run stage 15 end-to-end scenarios across chat, voice, workflows, direct
  integrations, schedules, workspace, and physical devices; include negative
  cases and authorized provider sandboxes.
- Publish a release report with supported capability matrix, exact test
  results, known limitations, provider/device gates, and next milestone.

**Exit criteria:** all required release tests pass or have an explicit
owner-approved block; no capability is marked verified without evidence at its
required boundary. A skipped live-provider/device test remains unverified.

## Mapping to the current master roadmap

The existing roadmap is newer and more authoritative than older audit files.
At this audit snapshot it records:

| Roadmap stage | Status | Relevance to this gap audit |
|---:|---|---|
| 1–6 | Verified within stated local/test boundaries | Architecture, shared lifecycle foundations, workflows, and voice integration; not blanket live-provider proof. |
| 7 | Partially verified | Direct email/calendar/document/device paths and provider sandbox acceptance remain incomplete. |
| 8 | Verified in isolated test boundary | Scheduling is implemented/tested; shared production instance/provider acceptance remains separate. |
| 9 | Verified in isolated mock-provider/API boundary | Coding adapters have bounded central-lifecycle coverage; live SDK acceptance is unverified. |
| 10 | Partially verified | Memory/context governance and measured answer quality. |
| 11 | Partially verified | Capability breadth exists, with uneven live provider and end-to-end evidence. |
| 12 | Partially verified; native device gates blocked | Windows/Android source and scoped protocol exist; signing, toolchain, and physical-device acceptance remain. |
| 13 | Verified in isolated localhost scope | Task/approval/evidence UX has local acceptance; broader regressions and provider behavior are separate. |
| 14 | Partially verified; deployment gates blocked | Reliability/operations foundations exist; security review, production rehearsal, scale, and external gates remain. |
| 15 | Not started | Release-level cross-entrypoint and supported-capability acceptance. |

Use the roadmap's existing stage exit gates and progress ledger; do not mark
stages complete based on this audit.

## Documentation inconsistency found

The root [`README.md`](../README.md) still labels native Windows/Android
companions “Not implemented,” while the current roadmap and companion source
document bounded source implementations with physical acceptance blocked. This
audit updates that summary to distinguish implementation from device
verification. Older dated audit reports remain historical snapshots; consult
the master roadmap for current stage status.

## Bottom line

JALORA already has useful foundations and some tested bounded workflows. The
next objective is not to claim every ChatGPT feature or create disconnected
agents. It is to close the partial lifecycle and verification gates, add
grounded research and useful document/data workflows through existing tools,
verify actual providers and devices, and publish an honest capability matrix.
