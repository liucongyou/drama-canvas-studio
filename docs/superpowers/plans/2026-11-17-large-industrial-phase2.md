# Large Industrial Phase 2 Implementation Plan

> Execute as one release gate. Do not mix unrelated user worktree changes.

**Goal:** Make tenant generation budgets correct across multiple API processes
and expose traceable reserved/committed spend.

**Spec:**
`docs/superpowers/specs/2026-11-17-large-industrial-phase2-cost-governance-design.md`

## Task 1: Cross-Process Budget Guard

**Status:** Complete locally

- Add `generation_budget_guard.py`.
- Use PostgreSQL session advisory locking keyed by tenant.
- Keep SQLite isolation-only behavior.
- Map guard timeout to a stable `BUDGET_GUARD_TIMEOUT` response.

## Task 2: Accurate Cost Classification

**Status:** Complete locally

- Split task spend into committed, reserved and released.
- Exclude known pre-provider validation failures and explicit queue cancellation.
- Keep uncertain post-submit outcomes conservative.
- Extend `/api/budget` without breaking existing fields.

## Task 3: Traceable Blocking

**Status:** Complete locally

- Record `generation.budget_blocked`.
- Include operation, entry, batch, session, IP, UA, node and estimate.
- Do not persist prompt text, cookies or authorization headers.

## Task 4: Multi-Process Pressure Test

**Status:** Complete locally

Run:

```powershell
.\.venv\Scripts\python.exe scripts\budget_guard_stress.py `
  --url postgresql://postgres@127.0.0.1:55432/drama_canvas_test `
  --processes 24 --limit 8 --unit-cost 1 `
  --report artifacts/budget-guard-stress.json
```

Acceptance:

- 8 accepted tasks from 24 simultaneous processes;
- amount and reserved amount exactly 8;
- no errors;
- no duplicate active tasks.

## Task 5: Release

**Status:** Pending

- Run backend, Agent, frontend, typecheck, build and multiprocess gates.
- Commit only Phase 2 files.
- Deploy once after production has zero active generation tasks.
- Verify release commit, readiness, frontend entry, budget fields and no paid
  provider calls.

## Remaining Phase 1 Acceptance

The 24-hour mock-provider soak still needs a passing report. Phase 2 does not
replace or imply completion of that soak.
