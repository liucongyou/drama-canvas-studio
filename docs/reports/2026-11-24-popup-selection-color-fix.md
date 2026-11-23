# Popup Selection Color Unification

Date: 2026-11-24
Status: Deployed and verified online

## Reported Issue

The clarity and watermark popup still showed blue selected buttons even though
earlier parameter-panel rules already tried to use neutral colors.

## Root Cause

The neutral rules existed in `ui-refinements.css`, but later-loaded theme rules
contained stronger `!important` selectors:

```css
.jim-ratio-item.is-active,
.jim-chip.is-active,
.jim-tool.is-active {
  color: var(--v-accent-mid) !important;
  background: var(--v-accent-soft) !important;
  border-color: var(--v-accent-line) !important;
}
```

Those later rules therefore won and restored the blue state.

## Fix

Added a final popup-selection contract in `theme-tokens.css` covering:

- clarity and other parameter chips;
- ratio options;
- radio options;
- model options.

Popup selections now use a neutral elevated surface mixed from the active
theme text and sunken surface. Action buttons keep their separate emphasis
color, so “change a parameter” and “execute an action” no longer look the same.

## Verification

- Full frontend gate: `92` files, `789` tests passed.
- Production build: passed.
- Online browser verification on the real clarity/watermark popup:
  - clarity selected background:
    `color(srgb 0.197961 0.197961 0.198902)`;
  - watermark selected background is identical;
  - borders and foreground colors are identical;
  - no page or console errors.
- Screenshot:
  `web-vimax/artifacts/chip-color-current.png`.

## Production

- Release commit: `8dc6397`
- Backup:
  `/www/wwwroot/yj.pduoduo.vip/app/backups/recipe-guidance-frontend-8dc6397-20261124-000428`
- Online JS asset: `assets/index-C_QFZodt.js`
- Frontend-only deployment; API, Agent and Worker services were not restarted.
- `/readyz`: healthy, `activeTasks: 0`.
- No paid image, audio or video generation was submitted.
