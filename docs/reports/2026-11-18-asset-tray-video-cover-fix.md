# Asset Tray Video Cover Fix

Date: 2026-11-18
Code commit: `ff06d6a75ba831aee6ac9d3d30be6d84d73f5959`
Frontend version: `2026.11.18.02`

## Problem

The video rows in the left asset tray rendered a `<video preload="none">`
element directly. This does not load metadata or paint a frame, so the fixed
thumbnail slot appeared blank. The tray also ignored the persisted
`params.thumbUrl` poster already used by media cards.

## Change

- Video tray thumbnails now use the persisted video poster first.
- If no poster exists, the tray loads video metadata and seeks to `0.01s` to
  paint the first frame.
- A loading shimmer is shown until the poster/frame is ready.
- A real load failure falls back to the existing video placeholder icon.
- No video, provider, or generation request is made by the fix.

## Verification

- Local fixture with two video assets:
  - persisted poster: image loaded, `naturalWidth > 0`;
  - no poster: video first frame ready, `readyState=4`, `currentTime=0.01`;
  - no placeholder or page errors.
- Full frontend gate: 88 test files, 778 tests passed.
- Canvas typecheck and production build passed.
- Production project `CC7nAazDAMK31EW9eJGw5b`: after scrolling the asset tray
  to the video group, all 8 visible video entries had loaded poster images;
  zero visible placeholders and zero page errors.
- Online entry: `/assets/index-DLmhdoFv.js`.
- Production backup:
  `/www/wwwroot/yj.pduoduo.vip/app/backups/canvas-ux-stability-ff06d6a75ba831aee6ac9d3d30be6d84d73f5959-20261119-000615`

No paid image, audio, or video generation task was submitted during this
verification.
