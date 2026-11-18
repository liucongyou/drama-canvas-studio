# Cinematic Grammar Skill and Agent Upgrade

Date: 2026-11-19
Skill version: `7.4.35`
Package revision: `20261119-r23`

## Sources

- `认识电影-可编辑版.docx`: extracted approximately 368k characters, covering photography, mise-en-scène, movement, editing, sound, performance, story, viewpoint, ideology, and film theory.
- `镜头语言判定表_全书版.docx`: extracted all 33 tables and the executable decision rules derived from *Film Directing Shot by Shot*.
- `电影镜头设计：从构思到银幕（插图第2版）扫描版.pdf`: read the readable table of contents and key structural pages. The PDF is an encrypted image scan without extractable text; no copy/print restriction was bypassed.

## Skill Upgrade

- Added `short-drama-director/references/agent-cinematic-grammar-contract.md` as the compact Agent-facing contract.
- The contract covers:
  - four questions before every shot;
  - information vs. reaction shot sizing;
  - story-driven camera angle instead of fixed “low angle = power” formulas;
  - explicit screen left/right, facing direction, eyeline, and 180-degree axis;
  - I/A/L dialogue blocking and ten two-person positions;
  - foreground/middle/background depth;
  - cut relations, question-answer editing, and cut-on-action;
  - movement capability boundaries;
  - seven transition classes;
  - 9:16 vertical composition compensation;
  - twelve pre-delivery shot checks.
- `runtime-quality-contract.md` now mounts the cinematic-grammar hard gate.
- The main skill authoritative table, P3 trigger ledger, CHANGELOG, README, all 13 SKILL frontmatters, scripts, `VERSION.json`, and `PACKAGE-MANIFEST.json` were synchronized to `7.4.35`.
- Source, `.codex`, and Doubao `.user_skills` copies were synchronized and hash-checked.

## Agent Upgrade

- `agent/app/runtime.py` now reads the compact cinematic contract from the active skill package and injects it into the model system prompt after the runtime-quality contract.
- The Agent is instructed to complete shot decisions before producing `drama-canvas-feed/v2`.
- Added a regression asserting the cinematic contract is present in the generated system prompt.

## Verification

- `check_package.py`: all checks passed.
- Skill package: `7.4.35`, revision `20261119-r23`, 179 files, 56 total references.
- Agent tests: 42 passed, 1 skipped because `TEST_POSTGRES_URL` is not configured.
- Actual Agent system prompt: approximately 35k characters and contains the cinematic contract, four-question decision chain, and twelve-question self-check.
- Generated package:
  `deliverables/manju-laoli-skill-V7.4.35-20261119-r23.zip`
- ZIP integrity: 180 entries, all manifest sizes and SHA-256 hashes matched.

No paid media generation was submitted during this upgrade.

## Production Verification

- Release commit: `381b5cd0a7b1a387e7e2c7bd77642f818d27ee94`.
- Production Agent active skill: `manju-laoli-skill` `7.4.35`.
- Installed package root:
  `/www/wwwroot/yj.pduoduo.vip/app/agent/data/skills/manju-laoli-skill/7.4.35-b3cfff95d93d`.
- Production system prompt length: `35077` characters.
- Production prompt contains `Agent 镜头语法合同`,
  `Agent 输出前十二问`, and explicit screen left/right rules.
- Production `check_package.py`: all checks passed, 56 references,
  12 subskills, 150 Markdown files scanned.
- Agent, main API, Worker, and Nginx are active.
- `/readyz`: database, migrations, media delivery, and worker fleet are healthy;
  active tasks are zero.
- Backup:
  `/www/wwwroot/yj.pduoduo.vip/app/backups/cinematic-grammar-agent-381b5cd0a7b1a387e7e2c7bd77642f818d27ee94-20261119-013920`

No model generation or paid media task was submitted during production
verification.
