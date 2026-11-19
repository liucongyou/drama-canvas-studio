# AICG3D Recipe System Design

Date: 2026-11-19
Status: Design approved in chat; implementation pending

## Objective

Integrate the 295 AICG3D camera recipes into the existing skill, Agent and
canvas workflows without injecting the full library into model context.

The system must let a user discover and apply a recipe to a segment, preserve
the recipe version through Agent generation, canvas import and provider
submission, and audit exactly which recipe and slot values produced each
generation request.

## Verified Source Facts

- The source contains 295 `SKILL.md` files in 12 categories.
- 292 entries are creative recipes with both `references/summary.md` and
  `references/template.md`.
- `character-refs`, `h3-video-remake` and
  `minimax-h3-short-drama-director` are tool skills without that pair.
- Recipe text is too large for system-prompt injection. The extracted recipe
  bodies, summaries and templates total roughly 2.29 million characters.
- Recipe IDs are unique. All entries have frontmatter name and description.

## Non-Goals

- Do not expose all 295 recipes as top-level skill routes.
- Do not inject the complete recipe library into an Agent prompt.
- Do not let recipes add reference images automatically. Explicit `@` mentions
  remain the authority for subjects appearing in a segment.
- Do not trigger paid image, audio or video generation during development,
  migration, testing or release verification.
- Do not replace the source recipe text. The normalized catalog is an index and
  execution contract backed by the immutable source files.

## Architecture

```text
AICG3D source
  -> recipe importer
  -> immutable recipe catalog
  -> Agent search/get tools
  -> run recipe bindings
  -> drama-canvas-feed/v2
  -> canvas node params.recipe
  -> backend recipe execution compiler
  -> provider request + generation audit
```

### 1. Recipe Registry

Add a recipe registry owned by the skill package and imported into the Agent
database.

The registry has two namespaces:

- `creative`: 292 mechanism recipes grouped in categories 01 through 10 and 12.
- `tool`: the three special entries in category 11 that require tools, assets or
  a different interaction contract.

Each normalized recipe stores:

```json
{
  "id": "handoff-ensemble-focus",
  "version": "1.0.0",
  "category": "12_综合镜头配方",
  "title": "群演聚焦接力｜全场到主位再回合奏",
  "description": "short routing description",
  "kind": "creative",
  "useWhen": ["ensemble", "focus handoff", "shared payoff"],
  "mechanism": "one paragraph",
  "invariants": [
    {
      "id": "inv-01",
      "rule": "mandatory structure",
      "failureIfRemoved": "what breaks"
    }
  ],
  "slots": [
    {
      "key": "lead_subject",
      "constraint": "original stable subject",
      "required": true
    }
  ],
  "antiCopy": ["source boundary"],
  "models": ["seedance-2.0", "minimax-h3"],
  "templates": {
    "seedance": "model-native compact template",
    "minimaxH3": "model-native compact template"
  },
  "sourcePath": "12_综合镜头配方/handoff-ensemble-focus",
  "sourceSha256": "sha256",
  "contentSha256": "sha256"
}
```

The importer must fail closed on duplicate IDs, missing templates for creative
recipes, unsafe paths or unsupported frontmatter. Tool recipes are validated as
tool entries rather than forced into the creative schema.

The registry is global, not tenant-owned. Tenant isolation applies to run
bindings and project usage records.

### 2. Database

Add forward migrations for:

```text
recipe_catalog
  id, kind, category, title, description, active_version, manifest_json,
  updated_at

recipe_versions
  recipe_id, version, content_sha256, contract_json, source_path, created_at

run_recipe_bindings
  id, run_id, segment_no, recipe_id, recipe_version, recipe_sha256, role,
  slots_json, match_reason, created_at
```

Constraints:

- `recipe_versions` is append-only for a given version and hash.
- `role` is `primary` or `modifier`.
- Each run segment may have at most one primary recipe.
- A modifier may contribute only anchor-level guidance, not a second full
  mechanism.
- Existing run rows remain valid with no bindings.

### 3. Agent Retrieval

Add Agent API and tool contracts:

```text
GET /api/agent/recipes
  query, category, kind, model, cursor, limit

GET /api/agent/recipes/{recipe_id}
  optional version
```

Search returns at most 8 compact results to model tools and paginated summaries
to the UI. Results include ID, title, category, one-line use case, supported
models and relevance reason. Full invariants and templates are returned only by
the detail endpoint.

Agent tool sequence:

1. Classify segment intent and model.
2. Search recipes.
3. Select no recipe or exactly one primary recipe.
4. Optionally select up to two modifier anchors.
5. Fill required slots and report unresolved slots.
6. Persist bindings before producing the final feed.

If no recipe clearly matches, the Agent must continue without forcing one.

### 4. Skill Upgrade

Upgrade `short-drama-director` to the next package version and add:

- a compact recipe-routing contract;
- rules for one primary recipe plus anchor-only modifiers;
- model-specific compilation rules;
- unresolved-slot and anti-copy gates;
- a statement that recipes never override explicit `@` asset authority.

The full registry lives in the skill package as generated reference data. The
Agent system prompt contains only the routing contract and the currently
selected recipe contract.

### 5. Canvas Data Contract

Video nodes support an optional parameter:

```json
{
  "params": {
    "recipe": {
      "id": "handoff-ensemble-focus",
      "version": "1.0.0",
      "sha256": "sha256",
      "role": "primary",
      "slots": {
        "lead_subject": "@角色｜贾富贵（图3）",
        "setting": "长桌南沿",
        "payoff": "群体同步停顿"
      }
    }
  }
}
```

Compatibility rules:

- Missing `params.recipe` means manual prompting remains unchanged.
- Unknown recipe IDs do not silently disappear. The canvas shows a stale or
  unavailable state and blocks recipe-assisted submission until resolved.
- Re-import merges recipe bindings by stable segment identity and preserves
  existing node IDs, media versions and references unless the imported recipe
  binding explicitly replaces them.
- Existing JSON without recipe fields imports without generated warnings.

`drama-canvas-feed/v2` receives an optional segment recipe binding. It must not
change existing required fields.

### 6. Canvas UI

Agent composer:

- Add a searchable `镜头配方` selector separate from the main skill selector.
- Browse by category, model and use case.
- Show an inline recipe chip with remove/replace actions.
- Show a compact detail card with required slots and warnings.

Video node:

- Show the applied recipe as a small metadata tag.
- Add a recipe section in the node dialog.
- Allow applying or replacing a recipe for one node.
- Support batch apply only when every selected node is writable.
- Display unresolved slots and stale versions before generation.

Read-only shares:

- Recipe metadata is visible.
- Recipe browsing, apply, replace and submission are disabled.

The UI must remain usable at narrow and wide panel sizes. Recipe controls must
not resize the node card, overlap existing controls or replace the existing
prompt editor.

### 7. Backend Compilation

The main backend remains authoritative for provider requests.

At submission:

1. Resolve recipe ID and exact version from the registry.
2. Verify content hash and node binding.
3. Resolve explicit `@` assets by stable asset identity.
4. Compile model-native execution guidance from invariant and slot data.
5. Append only the compiled execution lock to the final provider prompt.
6. Reject the request before task creation if required slots, model support or
   reference authority are invalid.
7. Persist recipe ID, version, hash, slots, prompt hash and reference map in the
   generation task audit.

The compiler must not duplicate a full recipe template that the Agent already
used to draft the segment prompt. It appends invariant-level execution locks and
resolved slot values.

Prompt length handling:

- Preserve user intent, explicit references, required invariants and physical
  continuity first.
- Drop optional descriptive enrichment and anti-copy prose last.
- If the minimum valid recipe contract cannot fit, fail with a typed error and
  do not create a provider task.

### 8. Audit

Run, generation and provider-task records expose:

- recipe ID, version and hash;
- primary/modifier role;
- segment number;
- resolved slot values;
- recipe search/selection reason;
- final prompt hash;
- actual image/video/audio reference mapping.

The existing audit query gains recipe filters without changing tenant
boundaries. Recipe definitions are public system data, but their use is visible
only inside the owning tenant.

## Error Handling

Typed, Chinese user-facing errors:

- `RECIPE_NOT_FOUND`
- `RECIPE_VERSION_MISMATCH`
- `RECIPE_HASH_MISMATCH`
- `RECIPE_MODEL_UNSUPPORTED`
- `RECIPE_REQUIRED_SLOT_MISSING`
- `RECIPE_CONTRACT_TOO_LONG`
- `RECIPE_BINDING_STALE`

No error path may submit a paid task. Manual prompt generation remains available
when the user removes an invalid recipe binding.

## Performance

- Startup loads only compact index rows, not full recipe bodies.
- Search operates over at most 295 records and returns compact fields.
- Only selected recipe details are loaded per Agent run.
- Target local search P95 is under 150 ms for 295 recipes.
- Adding the index must not materially delay Agent bootstrap or canvas loads.
- Concurrent recipe searches must not share mutable run selection state.

## Testing

### Registry

- Import all 295 source entries.
- Assert 292 creative plus 3 tool entries.
- Assert unique IDs, source hashes and template presence by kind.
- Test malformed, duplicate and path-traversal inputs fail closed.

### Agent

- Search by category, intent and model.
- Select no recipe, one recipe and one primary plus modifiers.
- Reject two primary recipes in one segment.
- Persist and read back bindings with tenant isolation.
- Verify the system prompt contains only the selected recipe contract.

### Feed and Canvas

- JSON round-trip with and without recipe data.
- Re-import preserves node IDs, references and media versions.
- Stale and unknown recipes show actionable errors.
- Batch apply is atomic for writable nodes and read-only shares cannot mutate.
- Existing prompt editor and explicit `@` behavior remain unchanged.

### Backend

- Golden compilation tests for representative recipes from all creative
  categories and both supported model families.
- Required slot and hash mismatch fail before task creation.
- Provider request contains the resolved recipe audit fields.
- Prompt-length fallback never removes required invariants silently.

### Stress and Release Gates

- 100 concurrent catalog searches without errors or connection growth.
- Concurrent Agent selection tests with isolated run bindings.
- Large canvas import with recipe-bearing video nodes.
- Existing frontend, Agent, backend, PostgreSQL and build suites.
- No paid provider calls.
- Release only after local all-green, Git commit, backup, single deployment and
  live read-only acceptance.

## Versioning and Release

- Start from skill version `7.4.35`.
- Publish the next immutable migration version and package revision.
- Keep old recipe versions available for historical runs.
- Deployment order: migrate registry, restart Agent, deploy backend/frontend,
  verify read-only search and one mocked compilation path.
- Rollback keeps existing recipe bindings readable and disables new selection
  only if the frontend must be rolled back.

## Acceptance Criteria

- A user can find a recipe by Chinese name, category, intent or model.
- A segment can carry exactly one primary recipe and trace it end to end.
- The final provider request is rejected before charging if recipe authority is
  invalid.
- Old projects, old JSON and manual prompts continue to work.
- `@` mentions remain the only authority for subject appearance and reference
  image selection.
- All 295 source entries are accounted for.
- No recipe library content is blindly injected into the system prompt.
- Local tests, concurrency checks and production read-only verification pass.
