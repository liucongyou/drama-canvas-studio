# Provider and Model ID Auto-Fill

Date: 2026-11-25
Status: Deployed and verified online

## Reported Issue

Creating a provider or model required users to invent a stable ID before they
had entered the display name or remote model ID.

## Behavior

- New provider IDs are derived from the display name first and fall back to the
  API hostname when the name contains no ASCII characters.
- New model IDs are derived from the remote model ID first, then from capability
  plus display name.
- Generated IDs are checked against the current catalog and receive an
  incrementing `-2`, `-3` suffix on collision.
- Once the user edits the ID manually the field stops auto-syncing.
- Existing saved provider and model IDs stay read-only, so historical references
  and generation records keep pointing at the same catalog entry.
- Provider IDs follow the backend `normalize_provider_id` character set; model
  IDs follow `normalize_model_id`, which also allows dots.

## Files

- `web-vimax/src/ModelSettings.tsx`
- `web-vimax/src/modelSettings.test.ts`
- `web-vimax/src/ui-refinements.css`
- `web-vimax/src/changelog.ts`
- `web-vimax/src/changelogEntries.ts`

## Verification

- Focused test: `src/modelSettings.test.ts` (9 tests).
- Full frontend gate: `92` files, `793` tests passed.
- TypeScript canvas check: passed.
- Production build: passed.
- Real browser verification on production:
  - new provider with display name `Sand AI` produced provider ID `sand-ai`;
  - manually changing the ID to `my-custom-id` and then renaming the provider
    kept `my-custom-id`;
  - new model with remote model ID `sand-model-x1` produced model ID
    `sand-model-x1`;
  - selecting the existing model `deepseek-flash-1017` left the model ID field
    read-only;
  - page errors `0`, console errors `0`.

## Production

- Release commit: `9dea49d`
- Backup:
  `/www/wwwroot/yj.pduoduo.vip/app/backups/recipe-guidance-frontend-9dea49d-20261125-205016`
- Online assets: `assets/index-0DtjKPk-.js`, `assets/index-Ye8TLtmS.css`
- Frontend-only deployment; API, Agent and Worker services were not restarted.
- `/readyz`: healthy, `activeTasks: 0`.

No paid image, audio or video generation was submitted.
