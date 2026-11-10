# Video Reference Contract and Agent UI Hotfix

- Date: 2026-11-10
- Version: `2026.11.10.03`
- Scope: Volcano Ark video reference submission and AgentDock visual polish

## Reported Incident

Episode 11 segment 9 was expected to use Jia Fugui, but the generated video
showed the old accountant instead.

Production task inspected:

- provider task: `cgt-20261110214527-wkl2o`
- local task: `4wJ6o1iFpUcMmsqNgC7OId`
- project: `CC7nAazDAMK31EW9eJGw5b`
- node: `node-qj7p7nogef4i-ff6c73ab`

The task request contained both roles:

- reference image 2: old accountant
- reference image 3: Jia Fugui

It also contained two independent numbering namespaces:

- canvas asset codes in the prompt: `图16`, `图3`
- Ark provider slots in `content`: image 1, image 2, image 3...

The image URLs and `role=reference_image` values were correct. The defect was
the program sending both numbering systems to the model, leaving the model to
reconcile them. That is a program-side identity-contract bug.

## Program-Side Guarantee

The provider prompt now removes the canvas figure-code namespace before every
Ark request.

The final prompt:

- defines every reference once as `图片N` plus a stable subject tag;
- rewrites all body mentions to the exact defined tag;
- never sends `@角色｜...（图N）` to Ark;
- never guesses an identity from a figure code;
- rejects duplicate identities;
- rejects missing provider slots;
- rejects undefined body tags;
- rejects old canvas figure codes that failed normalization.

If any of these checks fail, the paid provider request is not sent.

The provider task audit now also records:

- `providerPromptSha256`
- ordered `referenceMapping` containing slot, `assetId`, label and `nodeId`

This makes every future paid request reconstructable and auditable.

## Actual Segment 9 Reconstruction

The current segment 9 request was reconstructed with the new contract:

```text
图片 1 -> 角色｜打手甲
图片 2 -> 角色｜老会计
图片 3 -> 角色｜贾富贵
图片 4 -> 角色｜赵铁柱
图片 5 -> 场景｜老会计家院门口·母版
图片 6 -> 道具｜厂里底账布包
```

Verification result:

```text
raw canvas asset references remaining: 0
unbound bare references remaining:     0
undefined subject tags:                0
provider slot count mismatch:          none
```

Official references:

- [创建视频生成任务](https://docs.volcengine.com/docs/82379/1520757)
- [Doubao Seedance 2.0 系列提示词指南](https://docs.volcengine.com/docs/82379/2222480)

## AgentDock UI

- Panel uses the active canvas theme surfaces, borders, text and accent colors.
- Outer panel radius is 14px; header and composer corners match.
- Resize handle is a narrow centered pill, becoming a theme-colored glowing
  line on hover and drag.
- Close uses a 160ms fade, translate and scale animation.
- `prefers-reduced-motion` disables the animation.
- Skill selection now lives inside the composer and can be opened with `/`.
- `@` opens a categorized, searchable asset menu; hovering a row shows a large
  thumbnail with type, episode, status and stable asset ID.
- Selected image references render as inline thumbnail tokens.
- Assistant messages use full-width mature chat layout; user messages use a
  compact right-aligned bubble.
- Message hover actions provide copy controls, timestamps are visible and
  completed runs end with a divider instead of repeated status cards.

## Verification

```text
Provider stack:                 82 passed, 4 subtests passed
Provider + explicit-reference:  90 passed, 4 subtests passed
Main backend full suite:        754 passed, 208 subtests passed
Frontend full suite:            708 passed
Frontend Agent theme/tests:     10 passed
TypeScript:                     0 errors
Browser close animation:        verified
Skill-inside-composer:          verified
@ hover image preview:          verified
Browser page/Agent errors:      0
```

No paid generation was submitted during verification.

## Production Release

- Release commit: `f55e36e`
- Version: `2026.11.10.03`
- Frontend entry: `assets/index-DV6uACSx.js`
- Main service: `drama-canvas-yj.service` active
- Agent service: `drama-canvas-agent.service` active
- Backup: `/www/wwwroot/yj.pduoduo.vip/app/backups/video-ref-agent-ui-f55e36e-20261111-005356`

Online browser smoke verified:

- skill selection renders inside the composer;
- draft recovery after full reload;
- `@` menu category/search rendering;
- hover reference preview displays an image and stable metadata;
- close animation;
- zero page errors;
- no generation or import action.
