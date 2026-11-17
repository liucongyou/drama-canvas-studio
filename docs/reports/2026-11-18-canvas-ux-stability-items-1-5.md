# Canvas UX and Stability Items 1-5

Date: 2026-11-18
Code commit: `ceabc3eb90153e92e01c48dafd82d2f34e59a668`
Frontend version: `2026.11.18.01`

## Released Scope

1. Prompt editor input responsiveness
2. Image and video history switching
3. Video cover, playback, pause, and fullscreen layout
4. Explicit `@` reference and unique asset binding
5. Read-only sharing and upload/download recovery

## Changes

- The contenteditable prompt DOM is now authoritative during typing. Normal
  characters update the live counter without forcing the full media modal to
  render. React state updates only when empty/non-empty, over-limit, reference,
  template, clear, blur, or commit state changes.
- Node switching remounts the rich editor and prevents a stale editor value
  from resetting the caret.
- Media version navigation preserves the user's local cursor while a selection
  is in flight. It only synchronizes from the server after the selection lock is
  cleared.
- Object-storage upload failure no longer discards an upload that already
  exists locally. Drag/drop continues creating the node and reports that the
  asset is local-only.
- Added no-charge browser regressions for prompt typing, image/video version
  switching, video playback, fullscreen portrait/landscape geometry, explicit
  reference deletion, drag upload recovery, and anonymous read-only sharing.

## Local Verification

- Frontend gate: 88 test files, 778 tests passed.
- Canvas typecheck passed.
- Production build passed.
- Prompt regression on a 120-node board with a 1,208-character prompt:
  no lost characters, Chinese input order correct, caret remained at the end,
  and the text counter updated from 1208 to 1211.
- Image version switch: `2/3 -> 3/3`, persisted to the backend, survived reload.
- Video version switch: desktop/mobile menus remained inside the viewport;
  refresh persistence and rapid navigation passed.
- Video playback: single click opened the modal without playing; playback after
  refresh worked; pause retained the current frame; eight play/pause rounds did
  not produce a false missing-media state.
- Fullscreen: intrinsic portrait ratio `0.5625` and landscape ratio `1.7778`;
  pause and playback geometry matched and controls stayed inside the video.
- Explicit references: 16 backend tests passed; deleting an inline `@` removed
  the prompt mention and left `params.refs` empty.
- Upload recovery: object storage returned `502`, but the local file completed
  node creation and showed `1 个仅保存本地`.
- Read-only share: prompt editor was non-editable, toolbar/ref editor/edit
  buttons/visible file inputs/connect controls were absent.
- Backend project-share and security tests: 22 tests passed.

## Production Verification

- Production release commit:
  `ceabc3eb90153e92e01c48dafd82d2f34e59a668`.
- Frontend entry: `/assets/index-aIqHLorR.js`.
- Bundle reports version `2026.11.18.01`.
- `drama-canvas-yj`, Worker, Agent, and Nginx are active.
- `/readyz`: database, migrations, media delivery, and worker fleet are healthy;
  active tasks are zero.
- Database pool has no waiting or stale connections at verification.
- Public admin login succeeds.
- Real production project `OWcYcABggfuG0bFM1vL5hl` opened with a fully rendered
  prompt, scrollable overflow, no hidden content, and no page errors.

## Backup

```text
/www/wwwroot/yj.pduoduo.vip/app/backups/
  canvas-ux-stability-ceabc3eb90153e92e01c48dafd82d2f34e59a668-20261118-010709
```

No paid image, audio, or video generation task was submitted during local or
production verification.
