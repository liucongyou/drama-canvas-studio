# Video Identity Release

- Date: 2026-11-11
- Version: `2026.11.11.10`
- Local commit: `e288b00`
- Production commit: `e288b00`

## Fixed

The canvas and imported JSON keep canonical labels such as
`@角色｜贾富贵（图3）`. They are not rewritten in storage or the editor.

At the provider boundary, the server resolves each explicit mention through
its stable `assetId` (falling back to `nodeId`), then maps the asset to the
actual Ark request slot as `@图像N`. Reference ordering, card sorting and
canvas position therefore cannot change role identity.

`params.refs` is treated as the set of reference slots carried by the request.
Only an explicit body `@` selects which referenced identity appears and acts.
Characters that exist only in the background remain referenced but are not
promoted to action subjects.

Video generation reads direct incoming canvas edges on the server in addition
to the stored `params.refs` snapshot. A linked background character cannot be
dropped merely because the browser did not add it to a prompt mention.

The provider task receives an auditable reference plan containing the final
prompt SHA-256, ordered image slots and ordered audio slots. The plan is
persisted in the generation task and projected to the node response.

The Agent tool loop now stops repeated identical tool calls after two repeats
instead of exhausting the full step allowance on a no-progress loop.

## Production Incident

The `/www` volume was at 100% because PostgreSQL had produced a 26GB daily log.
PostgreSQL failed to extend relation files with `No space left on device`.

Old application backups were moved intact to
`/root/yj-old-app-backups-20261111`. The active PostgreSQL log was archived to
`/root/yj-pg-log-archive-20261111/postgresql-2026-11-11.full.log` and truncated
in place, preserving the open file handle. `/www` recovered from approximately
11MB to approximately 27GB free.

## Verification

- Provider/reference regression: `147 passed, 4 subtests passed`
- Frontend focused suite: `95 passed`
- Agent tool-loop tests: `6 passed`
- TypeScript: passed
- Production build: passed
- Production frontend: `2026.11.11.10`
- Production entry: `assets/index-616E_opR.js`
- `drama-canvas-yj.service`: active
- `drama-canvas-agent.service`: active
- Production `RELEASE_COMMIT`: `e288b00`

No paid image, audio or video generation was submitted during verification.
