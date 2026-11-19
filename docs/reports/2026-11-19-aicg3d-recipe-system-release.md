# AICG3D Recipe System Release

Date: 2026-11-19
Status: Deployed and verified online

## Delivered

- Imported all 295 AICG3D recipes into an immutable catalog.
  - 292 creative recipes.
  - 3 tool recipes.
  - 12 categories.
  - 3,685,518-byte catalog with source and content hashes.
- Added Agent recipe search/detail APIs and a direct backend catalog endpoint.
- Added Agent tools `recipe_search` and `recipe_get`; selected contracts are
  loaded on demand and bounded before prompt injection.
- Added persistent run recipe bindings with recipe ID, version, SHA-256,
  segment number, role, slots and match reason.
- Added Agent composer recipe search, selection chip and runtime binding.
- Added video-node recipe controls, slot editing, badge display and removal.
- Kept the existing camera-template button as the single entry point: normal
  camera templates and AICG3D recipes now share one menu.
- Recipe selection uses a centered, viewport-bounded dialog so long slot forms
  cannot be clipped by the node editor or mobile viewport.
- Added read-only recipe display for shared projects.
- Added optional `segment.recipe` in `drama-canvas-feed/v2`.
- Re-import now preserves an existing recipe when the new JSON omits it and
  replaces it when a new recipe binding is explicitly supplied.
- Provider submission now validates recipe model support, version, hash and
  required slots before task creation.
- Provider prompts receive an invariant-level recipe execution lock. Anti-copy
  prose is removed first when prompt budget is tight; an impossible budget
  fails with `RECIPE_CONTRACT_TOO_LONG` before charging.
- Generation task requests and audit events retain recipe plan, slot mapping and
  prompt hash.
- Tenant audit supports filtering by recipe ID.

## Skill Package

- Version: `7.4.36`
- Revision: `20261119-r24`
- Skill package:
  `deliverables/manju-laoli-skill-V7.4.36-20261119-r24.zip`
- New references:
  - `agent-aicg3d-recipe-contract.md`
  - `aicg3d-recipes/catalog.v1.json`
- Package validator: all checks passed.
- Files: 181 manifest files plus manifest, 182 zip entries.
- Codex, 豆包 Profile 1 and the package source contain identical hashes for all
  181 files.

## Verification

### Agent

- Full suite with PostgreSQL:
  `57 passed`, 1 dependency deprecation warning, 0 skipped.

### Frontend

- `npm run typecheck:canvas`: passed.
- `npm test`: 90 files, 782 tests passed.
- `npm run build`: passed.

### Backend

- Full aggregate run: 84 modules.
- 83 modules passed on the first aggregate pass.
- One stale legacy fixture schema copy failed its hash assertion; the copy was
  updated to the current schema and the module reran with `7 passed`.
- Targeted recipe/backend execution tests: `18 passed`.
- PostgreSQL-focused tests: `30 passed`.

### Browser Acceptance

Verified with local isolated data and mocked Agent behavior:

- Agent searches `群演聚焦接力`.
- Selected recipe chip displays the correct title.
- Imported video node displays the linked recipe title and version.
- Read-only share displays the recipe but has no recipe editor.
- 390×844 mobile viewport has no picker overflow.
- Desktop and mobile screenshots:
  - `web-vimax/artifacts/recipe-ui-local-final.png`
  - `web-vimax/artifacts/recipe-ui-local-final-agent.png`
  - `web-vimax/artifacts/recipe-ui-local-final-mobile.png`

## Performance

- Pure in-process catalog search:
  - P50: 7.294 ms
  - P95: 10.813 ms
- Authenticated local HTTP, 100 sequential requests:
  - P50: 23.79 ms
  - P95: 32.24 ms
  - 100/100 HTTP 200.
- Authenticated local HTTP, 100 requests with 20 workers:
  - 100/100 HTTP 200
  - P50: 273.68 ms
  - P95: 1029.34 ms

The concurrent HTTP number includes the development server’s per-request
session lookup and Python HTTP proxy path. The catalog itself meets the
sub-150ms P95 target.

## Safety

- No paid image, audio or video generation task was submitted.
- All provider verification used mocks or unit-level request capture.
- Production deployment must remain a single window after commit and bundle
  validation.

## Production Release

- Release commit: `e851074`
- Backup:
  `/www/wwwroot/yj.pduoduo.vip/app/backups/aicg3d-recipe-e851074-20261119-102521`
- Services active:
  - `drama-canvas-yj.service`
  - `drama-canvas-agent.service`
  - `drama-canvas-worker.service`
- `/readyz`: database, migrations, media delivery and worker fleet all healthy;
  `activeTasks: 0`.
- Online authenticated API:
  - recipe catalog: 295;
  - query `群演聚焦接力` returns `handoff-ensemble-focus`;
  - active skill: `7.4.36`;
  - skill entry contains the AICG3D recipe contract.
- Online browser acceptance selected the recipe in the Agent composer without
  submitting any generation request.
- The online `index.html` references the same final JS asset as the local
  production build.
- The first release attempt was automatically rolled back by a release-marker
  assertion before completion. The marker and skill-directory ownership were
  corrected, then the deployment reran successfully.

## Follow-up Release

### Fixes

- `f7df762`: always mount the recipe listener container for video nodes, while
  keeping the empty row visually hidden. This fixes the “click recipe and
  nothing happens” case on nodes that do not yet have a recipe binding.
- `a1b7ec3`: localize recipe categories, purposes, slot names and fill examples;
  add category shortcuts and auto-fill the editable recipe draft from the
  current prompt.
- `f3568ef`: add and execute a frontend-only deployment path so the static
  update does not restart the API, Agent or Worker services.
- `2f48afb`: replace the recipe category strip's scroll container with
  automatic wrapping, removing the stray vertical scrollbar on Windows.

### Verification

- `npm run check:canvas`: passed.
- Frontend suite: `91` files, `786` tests passed.
- Production build: passed.
- Desktop and 390×844 mobile browser checks: passed without horizontal
  overflow; category chips have no vertical overflow and `overflowY: visible`.
- Online browser acceptance on project `CC7nAazDAMK31EW9eJGw5b`, `EP009`:
  - unbound video node opens the recipe dialog;
  - category filtering returns recipes;
  - selecting “刻意遮断省略” displays Chinese slot names and examples;
  - auto-fill extracts the current prompt roles
    `@角色｜赵铁柱（图1）`, `@角色｜陈大强（图4）`,
    `@角色｜工人甲（图5）` and scene
    `@场景｜厂办门口·母版（图10）`;
  - no recipe was applied and no generation task was submitted.

### Production

- Release commit: `2f48afb`
- Backup:
  `/www/wwwroot/yj.pduoduo.vip/app/backups/recipe-guidance-frontend-2f48afb-20261119-112508`
- Online JS asset: `assets/index-nHbk5H1T.js`
- `/readyz`: all checks healthy, `activeTasks: 0`.
- Services remained active without restart:
  `drama-canvas-yj.service`, `drama-canvas-agent.service`,
  `drama-canvas-worker.service`.
- No paid image, audio or video generation was submitted.
