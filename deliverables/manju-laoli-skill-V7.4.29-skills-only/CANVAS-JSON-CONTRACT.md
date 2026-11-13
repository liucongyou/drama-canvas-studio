# 画布 JSON 兼容契约

适用版本：`drama-canvas-feed/v2`

结构 Schema：`short-drama-director/references/canvas-feed.schema.json`

Schema 只负责字段类型、必填和格式；跨数组、复用和派生后接线规则由 `short-drama-director/scripts/canvas_feed_contract.py` 负责。

## 必填结构

- 顶层 `title`：项目展示名
- 顶层 `episode_title`：当前集完整名称；画布项目名以此为准
- 顶层 `asset_prompts`：资产提示词数组
- 顶层 `voice_prompts`：所有对白角色的音色定义数组；复用角色也必须提供
- 顶层 `segments`：分段数组
- 顶层 `canvas_contract.schema`：`drama-canvas-feed/v2`

## 资产规则

- 资产有两个明确身份，不得混用：
  - `semanticCode`：稳定语义码，如 `CHR-001` / `SCN-001` / `PRP-001`，只用于跨集身份追溯。
  - `code`：画布槽位码，必须是 `图N`；正文引用、连线、音频绑定只认这个字段。
- 旧载荷若把语义码写进 `code`，以顶层 `assets[].code → assets[].img` 为权威映射，在导入入口一次性迁移；源文件不被改写。
- 迁移必须拒绝语义码重复、槽位码重复、缺 `img` 的资产，不能静默纠正。
- 同一角色不同形态必须使用不同的 `seriesId + variantKey`，并分配不同图号。
- 只有 `seriesId + variantKey` 完全一致时才允许复用旧图号。
- 场景只登记母版，`variantKey=master`；九宫格不得成为独立场景资产。
- 正文引用必须使用完整标题：
  - `@角色｜钱万金（图2）`
  - `@场景｜街口饭馆·母版（图29）`
  - `@道具｜账本（图18）`

禁止：

- `钱万金（图2）`
- `@钱万金`
- `@SCN-街口饭馆`
- `@PRP-账本`
- `@//chr/ -`

## 音色规则

有对白的段落在 `【核心人物】` 下方、`【站位声明】` 上方写入：

```text
【音色引用】@音频｜钱万金（图2）；@音频｜赵铁柱（图1）
```

音色图号必须与对应角色资产图号一致。提示词音色引用、画布 `voiceRefs` 和音色连线必须一致。

复用角色即使不重复导出 `asset_prompts`，也必须在 `voice_prompts` 中保留完整英文音色定义：

```json
{
  "code": "图1",
  "name": "赵铁柱",
  "title": "角色｜赵铁柱（图1）",
  "assetId": "character:赵铁柱:default",
  "format": "audio-prompt-v1",
  "prompt": "low, steady, northeastern-accented baritone. A male voice, low and steady ... A male aged 38-42, speaking northeastern-accented Mandarin, short drama dubbing reference clip, 4.5 seconds clean voice, no background noise.",
  "label": "low, steady, northeastern-accented baritone",
  "description": "A male voice, low and steady ...",
  "age": "38-42",
  "gender": "male",
  "dialect": "northeastern-accented"
}
```

`prompt` 必须是可直接投喂 TTS 的最终 Audio Prompt，包含音色标签、完整描述、年龄/性别（原资产提供时）、口音以及固定结尾：

```text
short drama dubbing reference clip, 4.5 seconds clean voice, no background noise.
```

不得只输出 `Voice Description 完整版` 正文，不得用“请补充音色描述”等占位词替代。

年龄统一使用同一种句式：区间写 `A male aged 48-54`，单一年龄写 `A male aged 42`；禁止混用 `A 42-year-old male`，禁止 `A male in his 48~54`。

旧字段别名 `voicePrompt / voiceLabel / voiceDescription / voiceAge / voiceGender / voiceDialect` 仍可读取，但会产生 `WARN_DEPRECATED_VOICE_FIELD`。缺 `dialect` 时，只有文本中存在 `Northeastern` 或 `standard Mandarin` 明确证据才能派生，并产生 `WARN_DERIVED_DIALECT`；否则直接失败。

## 素材引用身份契约

`segments[].raw` 必须带唯一一份 `### 素材引用` 块，**位置在 `【画幅风格】` 之后、`【场景资产】` 之前**。位置是硬要求：段末属于最低注意力区，模型展开资产时读不到绑定关系，等于没写；挂在 `【场景资产】` 之后会报 `MATERIAL_CONTRACT_UNBOUND`。

```text
【画幅风格】9:16 竖屏，…
### 素材引用
【人物】
<钱万金> 对应@角色｜钱万金（图2），仅采用该人物的外貌、发型和服装。人物身份必须按角色名锁定，不得按画面左右位置替换。
【场景】
<厂办门口小广场·母版> 对应@场景｜厂办门口小广场·母版（图15），仅采用空间布局、建筑、陈设和光线，不采用图中人物。
【道具】
<买断协议> 对应@道具｜买断协议（图7），仅采用造型、材质与状态，不作为人物身份参考。
【声音】
<钱万金>的音色对应@音频｜钱万金（图2），仅用于该角色发声，不得分配给其他角色。

【场景资产】@场景｜厂办门口小广场·母版（图15）；…
```

规则：

- 每行是「别名 → 素材」两段式。**左侧尖括号里只放别名（纯资产名）**，禁放类型前缀（`角色｜`）、禁放图号。
- 图号只能出现在右侧 `对应@类型｜资产名（图N）` 的素材位上。图号写在左边 = 平台挂不上图 = `MATERIAL_CONTRACT_UNBOUND`。
- 块内只允许 `【人物】/【场景】/【道具】/【声音】` 四个分组标题，不许嵌其他 `【】` 栏位。
- 同一段只允许一份契约，重复追加同样报错。
- 正文引用的每个图号都必须出现在契约里，漏绑一律报错。
- 生成器 `build_board_lite.py` 按块删除旧契约后再插到 `【场景资产】` 上方；提交器 `provider_client.py` 用同一套块删除逻辑，禁止在 `### 素材引用` 处 split 截断（截断会把它后面的正文全部丢掉）。

## 导出前检查

- 生成器自检通过。
- 生成器真实导出的 JSON 已通过同一版 `canvas_feed.schema.json`。
- 生成器真实导出的 JSON 已由画布服务端 `prepare_import()` 实际导入，节点、音色、图片槽位和连线断言全部通过。
- `short-drama-director/references/canvas-feed.schema.json` 与服务端 `schemas/canvas_feed.schema.json` 哈希一致。
- 同名多形态没有被错误合并。
- 同形态跨集图号保持一致。
- 没有九宫格独立场景资产。
- 所有画面引用均为完整 `@类型｜名称（图N）`。
- 所有对白段落都有音色引用。
- 每个对白角色都能在 `voice_prompts` 中按图号或角色名查到完整英文音色定义。
- `segments[].canvas.asset_codes`、`asset_refs` 与正文引用双向一致。

## 导入准入链

画布导入分两道闸，任一道失败都不会写库：

1. **载荷闸**：schema、结构、图号唯一性、对白角色与 `voice_prompts`、`【音色引用】` 的声明一致性。
2. **产物闸**：`build_payload → merge_payload_by_episode` 后，校验对白角色、`voiceRefs`、audio 入边、图片入边的实际接线一致性。

服务端使用深拷贝构建候选项目。闸 2 失败时原 payload 与 revision 均不得变化。

## Dry Run 与 Revision

预览接口：

```text
POST /api/projects/{id}/import/preview
```

正式导入：

```text
POST /api/projects/{id}/import
{
  "feed_json": {...},
  "if_revision": 12
}
```

预览和提交共用同一个 `prepare_import()`。项目在预览后被其他操作更新时，系统自动按当前 revision 重新预检一次，并返回 `WARN_RETRIED_AFTER_REVISION`。

## 集级溯源

每个 `episodeImports[i]` 保存：

- `sourceFeedName`
- `sourceFeedPath`（文件导入时可为空）
- `feedSha256`
- `schemaVersion`
- `skillVersion`
- `packageRevision`
- `firstImportedAt`
- `lastImportedAt`
- `importedAt`（兼容字段，等于最后一次导入时间）
- `importWarnings`

溯源不得挂在项目根，否则后续集导入会覆盖历史集来源。

`segments[].canvas.asset_codes` 与 `asset_refs` 如存在，必须与正文全部 `图N` 双向一致；导入器会拒绝漂移声明。

`segments[].durationSec` 与 `segments[].canvas.duration` 必须由该段 `raw` 的最后一条镜头结束时间码自动计算并双写，不得由模型手填或回退到前端默认 4 秒。例如 `00:00-00:04 / 00:04-00:07 / 00:07-00:10` 必须得到 10 秒。旧 JSON 缺这两个字段时，导入器会按时间轴自动补齐；声明值与时间轴冲突时以时间轴为准并记录 `WARN_SEGMENT_DURATION_DERIVED`。

## 资产记录导出

```text
GET /api/projects/{id}/assets/export
```

导出 `drama-canvas-assets/v1`，包含资产图号、URL、状态、版本、复用信息、所属集与集级 provenance。
