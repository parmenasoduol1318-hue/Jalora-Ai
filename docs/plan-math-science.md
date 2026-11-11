# Plan — "Everything" Math, Physics & Chemistry for Jalora

> ## Status
- **Phase 0 implemented & tested** (68 → 72 math tests green).
- **Phase 1 implemented & tested** (pint unit algebra; 72 math tests green).
- **Phase 2 implemented & tested** (`math_numeric.py` SciPy engine; linear algebra,
  number theory, series, ODEs, complex numbers, distributions, regression;
  **110 math tests green**).
- **Phase 3 implemented & tested** (`physics_engine.py` + `data/physics_constants.json`;
  dimensional formula solver + physical-constants table; **126 math tests green**).
- **Phase 4 implemented & tested** (`chemistry_engine.py` + `data/periodic_table.json`;
  element lookup, formula parsing, molar mass, equation balancing, mole/mass conversions;
  **136 math tests green**).
- **Phase 5 implemented & tested** (`pedagogy.py`; `primary…professor` level ladder,
  level inference from the question, and deterministic (offline) level-aware derivations;
  **146 math tests green**).
- **Phase 6 implemented & tested** (`services/plotting.py` + `tools/plot_tool.py`;
  headless-matplotlib function/series plots rendered offline to PNG artifacts (shown
  inline) + inline SVG, with a bounded "plot …" request parser; **14 plotting tests,
  185 math+tools+chat+plot tests green**).
- **Phase 7 implemented & tested** (`science` intent + `SCIENCE_BRIEF`; science tool
  set incl. plot tools; per-request `math_level` + `show_working` (answer-only) toggle
  threaded request→fast-path; `math_engine.format_answer` + `pedagogy.render(show_working)`;
  frontend level dropdown + "show working" toggle (en/sw), persisted in localStorage;
  **19 new math tests, 165 math tests green; full suite 731 passed**).
- **Phase 8 implemented & tested** (`data/golden_problems.json` + `services/verification.py`;
  64 curated problems spanning 16 domains × all 6 levels, each checked against the
  engine's result *and* its level-aware offline render, with domain/level coverage
  assertions and self-detecting harness guards; **14 new tests, 179 math+verification
  tests green**).
- **Phase 9 implemented & tested** (professional, downloadable plots): `services/plotting.py`
  now renders high-resolution PNG **plus SVG and PDF vector exports** with a clean, modern
  style (whitegrid-like rc-context that never leaks into process defaults), and adds
  `plot_scatter` / `plot_bar` / `plot_histogram`; a `plot …` turn is answered **locally** by
  a chat fast-path (no model call) that saves all exports as owner-only artifacts
  (`data["artifact"]` + `data["artifacts"]` list), emits inline-SVG diagrams, and persists
  the run; `artifact_info` gains `file_name`/`download_url` and the artifacts endpoint forces
  an attachment (`?download=1`); the React UI loads images through the authenticated endpoint
  and adds Download buttons on artifacts and diagrams. **12 new tests
  (`test_plotting.py` +7, `test_plot_downloads.py` e2e), 26 plotting tests green; full suite
  758 passed** (same 9 pre-existing environment failures / 3 chromadb errors).

## Goal
Make Jalora compute — not guess — maths, physics and chemistry at **every level**
(primary school → high-school → university → STEM → postgraduate → professor),
**always with a step-by-step explanation**, and render the working (LaTeX) and
graphs inline.

## Locked decisions
1. **Answer style:** compute first (authoritative), then let the user's **active
   model** write the step-by-step explanation. The explanation may never change a
   computed number. If no model is available, fall back to the deterministic
   computed steps (keeps "math works offline", spec item 9).
2. **Answer block is always appended** from the engine, so a drifting model can
   never override a verified result (spec item 6).
3. **Dependencies:** add `pint`, `matplotlib`; optional `periodictable`/`chempy`
   with graceful fallback. Pin `scipy` (already installed).
4. **Plots/graphs are in scope** → matplotlib PNG artifacts served by
   `/api/artifacts/...` + inline `svg`.
5. **Depth: every level** — same computed answer, tiered explanation.

## Root cause fixed in Phase 0
`integrate sin(x) dx from 0 to pi` returned
`Integration failed: invalid syntax (<string>, line 1)` because:
- `_do_integral` only handled indefinite integrals (leftover `from … to …` broke parsing);
- the fast-path ignored `MathSignal.direct` and hard-failed narrative science
  questions instead of letting the model answer;
- `integrate the velocity` fabricated `c*e*i*l*o*t*v*x*y`;
- raw SymPy/Python exceptions leaked into the user reply.

## Architecture
```
message → intent/router ─┬─► compute core (SymPy · SciPy · pint · domain engines)
                         │        pure, offline, verified → locked Result
                         └─► explain layer (active model, numbers pinned)
                                  └ offline fallback = deterministic steps
                         → format_reply + plots → MathJax UI
```

## Phases
- **Phase 0 (done):** definite/indefinite integrals + numeric fallback; honor
  `direct`; prose guard; systems of equations; friendly errors; no fabrication;
  `MATH_BRIEF`; `science_explain` (compute → model explanation → deterministic
  fallback). Files: `math_engine.py`, `science_explain.py`, `chat_service.py`,
  `agents/prompts.py`, `config.py`, `tests/test_math.py`.
- **Phase 1 (done):** `services/math_units.py` (pint) — compound/derived units
  (`km/h`, `kg*m/s^2` → `N`), affine temperature scales, dimensional checks;
  wired into `solve()` ahead of the manual table. Config/flags:
  `JALORA_MATH_EXPLAIN`, `JALORA_MATH_LEVEL`, `JALORA_MATH_TIMEOUT_SECONDS`,
  `JALORA_PLOTS_ENABLED`.
- **Phase 1:** `services/math_units.py` (pint) — compound units + dimensional checks.
- **Phase 2:** `services/math_numeric.py` (SciPy) + broaden math (matrices, ODEs,
  series, complex, number theory, distributions, tests, regression). *DONE.*
- **Phase 3:** `services/physics_engine.py` + `data/physics_constants.json`. *DONE.*
- **Phase 4:** `services/chemistry_engine.py` + `data/periodic_table.json`. *DONE.*
- **Phase 5:** derivations + pedagogical levels (primary…professor). *DONE.*
- **Phase 6:** `services/plotting.py` + `tools/plot_tool.py` + frontend rendering. *DONE.*
- **Phase 7:** `science` intent + briefs + i18n + "show working"/level selector. *DONE.*
- **Phase 8:** `data/golden_problems.json` (64 curated problems, 16 domains × 6 levels)
  + `services/verification.py` — runs each problem through `math_engine.solve`, checks
  the `MathResult` (ok/intent/exact/numeric/units/verified) and the level-aware
  `pedagogy.render` + `format_answer`, and reports pass/fail by domain/level. Usable as
  a library and a CLI (`python -m app.services.verification`, exits non-zero on failure).
  `tests/test_verification.py` asserts the whole set passes, coverage holds, and the
  comparator actually detects wrong values/missing computation/crashes. *DONE.*
- **Phase 9:** professional downloadable plots — PNG/SVG/PDF exports, scatter/bar/histogram
  charts, a local plot fast-path (inline figure + downloadable vector files), `?download=1`
  attachment on `/api/artifacts`, and frontend authenticated image loading + download
  buttons. *DONE.*

## Config flags (planned)
`JALORA_MATH_EXPLAIN=true` · `JALORA_MATH_LEVEL=auto` ·
`JALORA_MATH_TIMEOUT_SECONDS=15` · `JALORA_PLOTS_ENABLED=true` ·
`JALORA_MATH_SHOW_WORKING=true` (Phase 7; per-request `show_working` overrides).
