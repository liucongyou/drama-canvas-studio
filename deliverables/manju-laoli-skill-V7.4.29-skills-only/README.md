# 漫剧老李 AIGC 创作全流程 Skill

版本：`V7.4.30`（包修订 `20261105-r11`）

主入口：`short-drama-director/SKILL.md`

> **r11 变更（2026-11-05）**：`### 素材引用` 身份契约的位置由分段末尾改为 **`【场景资产】` 之前**。段末是最低注意力区，模型读不到身份绑定，等于没写。同时修正 `build_board_lite.py` / `provider_client.py` 的契约重建逻辑——旧实现按 `### 素材引用` 处 split 截断，契约一旦不在末尾就会把它后面的全部正文（场景资产／核心人物／站位声明／时间轴分镜）整段删除，现改为按块删除再插回。

本包包含 1 个总控技能和 12 个子技能。总控目录与全部 `sd-*-engine/` 目录必须保持同级。

## 目录

- `short-drama-director/`：总控入口
- `sd-asset-engine/`
- `sd-combat-engine/`
- `sd-decision-engine/`
- `sd-feed-engine/`
- `sd-jieshuo-engine/`
- `sd-overseas-engine/`
- `sd-qc-engine/`
- `sd-real-engine/`
- `sd-recon-engine/`
- `sd-script-engine/`
- `sd-storyboard-engine/`
- `sd-xuanhuan-engine/`

## 安装

支持多技能目录的平台，把包内 13 个技能目录整体放入技能目录，并从 `short-drama-director` 进入。

只能导入单个 `SKILL.md` 的平台，以 `short-drama-director/SKILL.md` 为主入口，同时补齐其它 12 个同级子技能目录。只导入总控会缺少资产、分镜、投喂和质检规则。

## 画布 JSON

生成 `drama-canvas-feed/v2` 画布载荷：

```bash
python short-drama-director/scripts/build_board_lite.py \
  项目输入.md \
  --out 分镜看板.html \
  --export-json 第01集_画布载荷.json
```

脚本会自动读取输出目录内既往画布载荷作为系列资产总账。跨集导出时可显式传入：

```bash
--asset-ledger 既有画布载荷.json
```

复用角色不会重复导出 `asset_prompts`，但完整英文音色会保存在顶层 `voice_prompts`，确保跨集导入后仍能生成详细音色提示词。

### 两种通用工作模式

- **独立新剧模式（默认）**：不提供历史台账。技能只读取当前剧本与当前资产提示词，可处理任意剧名、人物和题材。
- **系列连续模式（可选）**：传入 `--asset-ledger` 或把既往画布 JSON 放在输出目录。相同 `seriesId + variantKey` 才复用旧角色身份、音色和年龄。

资产台账是跨集一致性数据，不是技能运行前提，也不包含任何固定剧本或固定人物。

每次导出 JSON 后必须执行通用校验：

```bash
python short-drama-director/scripts/validate_canvas_feed.py 第01集_画布载荷.json
```

缺 `voice_prompts`、对白角色音色缺失、年龄格式混用、缺固定音频结尾或音色引用不一致都会直接失败，不能交付或导入。

导出规则见 `CANVAS-JSON-CONTRACT.md`。

## 自检

```bash
python short-drama-director/scripts/build_board_lite.py --self-test
python short-drama-director/scripts/validate_canvas_feed.py --self-test
python short-drama-director/scripts/validate_prompt.py --self-test
python short-drama-director/scripts/slice_context.py --self-test
```

发布包已在 Windows + Python 3.14 下通过 JSON 生成器、提示词机检和动态规则切片器自检。

