# Core reliability validation progress — 2026-11-25

## Scope

This follow-up contains focused test reliability fixes on `main`. It does not claim that every Jalora feature or production acceptance requirement is complete.

## Changes

- Restart tests now set subprocess working directories from their test-file location, so they work whether pytest is launched from the repository root or backend directory.
- The email approval browser test supplies a deterministic approval stream instead of depending on a live model response.
- Workspace approval coverage waits for the asynchronous `202 Accepted` response before checking the approval flow.
- The device-access browser assertion now matches the Android limitation copy currently rendered by the frontend.

## Validation

- Four backend restart regressions passed when run from the repository root.
- Three targeted Playwright regressions passed: email approval editing/rejection, device-access copy, and workspace approval.
- The browser tests were run from an isolated worktree based on `origin/main`.

## Remaining verification

The broader feature-completion request remains open. Production/Render persistence and authentication acceptance, transactional email delivery, full-suite reruns, and physical Android acceptance were not verified as part of this focused test-fix push. No production data or device registrations were changed.
