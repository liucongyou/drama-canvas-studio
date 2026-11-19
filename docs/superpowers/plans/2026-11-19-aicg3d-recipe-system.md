# AICG3D Recipe System Implementation Plan

**Goal:** Add a versioned, tenant-auditable camera recipe registry that connects
the Agent, canvas nodes and backend generation request without loading the full
recipe library into model context.

**Spec:** `docs/superpowers/specs/2026-11-19-aicg3d-recipe-system-design.md`

## Task 1: Recipe Catalog and Import

**Status:** Complete locally

- Add a deterministic importer for the 295 source recipes.
- Normalize 292 creative recipes and 3 tool recipes.
- Extract mechanism, invariants, slots, anti-copy boundaries and model templates.
- Generate a versioned catalog and fail closed on source drift.
- Add catalog validation tests.

## Task 2: Agent Registry, Search and Bindings

**Status:** Complete locally

- Add recipe catalog and version tables.
- Add run recipe binding persistence with tenant scope.
- Add catalog listing, search and detail APIs.
- Add `recipe_search` and `recipe_get` Agent tools.
- Inject only the selected recipe contract into the Agent prompt.
- Add API, runtime and tenant-isolation tests.

## Task 3: Canvas and Agent UI

**Status:** Complete locally

- Add frontend recipe API types.
- Add searchable recipe picker and inline chip to the Agent composer.
- Display selected recipe details and slot readiness.
- Add recipe application to video node dialogs.
- Keep read-only shares display-only.
- Add frontend unit and browser verification.

## Task 4: Feed and Node Persistence

**Status:** Complete locally

- Extend `drama-canvas-feed/v2` segment input with optional recipe bindings.
- Preserve bindings through normalization, compilation, preview and import.
- Persist `params.recipe` on video nodes.
- Preserve recipe data during re-import and historical media switching.
- Test old and new JSON round trips.

## Task 5: Backend Execution and Audit

**Status:** Complete locally

- Add model-native recipe execution compilation.
- Validate recipe hash, version, required slots and model support before submit.
- Append invariant-level execution locks to the provider prompt.
- Persist recipe identity and mapping in the generation task audit.
- Add targeted backend tests with mocked providers only.

## Task 6: Verification, Commit and Release

**Status:** In progress

- Run all Agent, frontend, backend and PostgreSQL contract tests.
- Run 100 concurrent recipe searches and large-canvas import stress.
- Update the skill package and package manifest.
- Verify no paid provider calls occurred.
- Commit implementation, deploy once after gates pass, and verify production
  with read-only recipe search and mocked compile checks.

## Stop Conditions

- Any source recipe is silently skipped or duplicated.
- A recipe binding can cross tenant boundaries.
- An invalid or stale recipe can reach provider submission.
- Re-import removes existing node IDs, references or media versions.
- The full recipe library enters the system prompt.
- Any test or verification step requires paid generation.
