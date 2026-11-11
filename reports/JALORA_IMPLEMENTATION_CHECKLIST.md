# Jalora milestone checklist

This checklist is intentionally incremental. A gate is not marked complete until
its focused tests and shared-localhost verification pass.

- [x] Gate 0: identify active backend, frontend, database, local model services, and startup paths.
- [x] Gate 0: verify backend, frontend, Ollama, and LM Studio are reachable locally.
- [x] Gate 0: finish and record the complete backend baseline suite (1239 passed, 1 pre-fix routing failure, 4 skipped).
- [x] Gate 1: repair the shared execution lifecycle, dependency enforcement, bounded recovery, verification evidence, and truthful final review (59 focused tests passed).
- [x] Gate 2: finish the verified persistent chat management slice: rename, archive, search, refresh/reopen, and ownership acceptance (browser-tested).
- [x] Gate 3: default to `Africa/Nairobi`, preserve UTC storage, render message timestamps/date separators, and test user timezone overrides.
- [x] Gate 4: verify user-controlled memory across conversations, provenance, corrections/deletion, source-conversation policy, and isolation (48 focused tests passed; live model extraction remains provider-dependent).
- [ ] Gate 5: verify mocked Google Calendar end to end and document live OAuth/provider blockers (10 focused tests and 2 browser tests pass; live Google remains unverified).
- [ ] Gate 6: verify shared text/voice continuity, interruption, cancellation, duplicate prevention, and browser behavior.
- [x] Gate 7: harden provider-independent telephony readiness and mocked status transitions (covered by the 92 security/provider tests; live calling remains disabled/unverified).
- [x] Gate 8: harden normalized travel/booking readiness and approval/verification boundaries (covered by mocked contract tests; live inventory/payment remains unverified).
- [x] Gate 9: run full regression, frontend build/E2E, security checks, and update the audit report with exact counts (backend 1246 passed/4 skipped; browser 22/22; build passed).

No real email, call, booking, payment, job application, or other consequential
external action is authorized by this checklist.

