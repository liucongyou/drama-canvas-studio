# 02 · 统一 Canvas Feed 契约

## 1. 结论：只有一份契约

**Agent 产出的 JSON 必须就是现有 `drama-canvas-feed/v2`。**

不新增 `agent-feed/v1`，不新增画布节点直写格式，不允许“Agent 版 JSON”。原因：

- 现有 schema + `prepare_import()` 已经覆盖资产、音色、引用、接线、集级合并、revision、溯源；
- 两套格式必然漂移，最终一定在导入时出错；
- 现有手动导入、技能脚本、Agent 产物可以共用同一套校验和测试。

权威文件：

- Schema：[schemas/canvas_feed.schema.json](../../schemas/canvas_feed.schema.json)
- 语义契约：[CANVAS-JSON-CONTRACT.md](../../deliverables/manju-laoli-skill-V7.4.29/CANVAS-JSON-CONTRACT.md)
- 校验/规划：[canvas_feed_contract.py](../../canvas_feed_contract.py)
- 构建/合并：[import_feed.py](../../import_feed.py)

> 本文件是“Agent 生产侧”的摘要与纪律；凡与 `CANVAS-JSON-CONTRACT.md` 冲突，以服务端代码和该契约为准。

## 2. 必填结构（v2）

```json
{
  "title": "项目名",
  "episode_title": "第01集 · 集名",
  "canvas_contract": {
    "schema": "drama-canvas-feed/v2",
    "video_model": "doubao-seedance-2-0",
    "video_aspect": "9:16",
    "asset_aspect": "16:9",
    "generator": {
      "name": "canvas-agent",
      "skillVersion": "7.4.29",
      "packageRevision": "20261105-r14"
    }
  },
  "asset_prompts": [],
  "voice_prompts": [],
  "segments": []
}
```

- `title` / `episode_title`：必填，决定项目名与目标集。
- `canvas_contract.schema`：必须是 `drama-canvas-feed/v2`。
- `asset_prompts`：资产提示词数组；角色/场景/道具/群像。
- `voice_prompts`：所有有对白角色的完整英文音色定义。
- `segments`：至少 1 段，每段含 `raw` 投喂正文。

## 3. Agent 可以追加、但不能改语义的字段

现有 schema 允许 `additionalProperties`。建议只加一个命名空间：

```json
"canvas_contract": {
  "schema": "drama-canvas-feed/v2",
  "agent": {
    "runtime": "canvas-agent/v1",
    "runId": "arun_...",
    "model": "doubao-...",
    "generatedAt": "2026-11-09T00:00:00Z",
    "contractSha256": "…",
    "skills": [
      {"id": "manju-laoli-skill", "version": "7.4.29", "packageRevision": "20261105-r14"}
    ]
  }
}
```

规则：

- `agent` 块只做溯源，**导入器不依赖它**；缺了也能导入。
- 不要改 `asset_prompts` / `voice_prompts` / `segments` 的既有语义。
- 不要把画布节点 ID、坐标、连线 ID 写进 feed；这些由导入器生成。
- 不要写 `mediaUrl` / 已生成图片 URL；feed 只描述“要生成什么”。

## 4. 资产与引用规则（Agent 最容易出错的 8 条）

1. `code` 必须是 `图N`（N 为正整数），是画布槽位码；`semanticCode` 是稳定语义码（`CHR-001` / `SCN-001` / `PRP-001`），不得混用。
2. 正文引用必须写完整标题：`@角色｜钱万金（图2）`、`@场景｜街口饭馆·母版（图29）`、`@道具｜账本（图18）`。
3. 禁止 `@钱万金`、`@SCN-街口饭馆`、`@//chr/ -`、`@//scn/ -` 作为最终投喂引用。
4. 同一角色不同形态必须使用不同 `seriesId + variantKey`，并分配不同图号；只有两者完全一致才允许复用旧图号。
5. 场景只登记母版（`variantKey=master`）；九宫格不能作为独立场景资产。
6. 有对白的段必须在 `【核心人物】` 下方、`【站位声明】` 上方写 `【音色引用】@音频｜角色（图N）`。
7. `voice_prompts` 必须覆盖每个有对白角色，且 `prompt` 是可直接投喂 TTS 的完整英文 Audio Prompt，结尾固定为：
   `short drama dubbing reference clip, 4.5 seconds clean voice, no background noise.`
8. `segments[].raw` 中只能有一份 `### 素材引用`，位置在 `【画幅风格】` 之后、`【场景资产】` 之前；左侧尖括号只放纯资产别名，图号只出现在右侧 `对应@类型｜名称（图N）`。

> 完整规则、示例、禁止项：见 `CANVAS-JSON-CONTRACT.md`。Agent 系统提示里必须逐条引用，不得只给“参考该文件”。

## 5. 两道路由闸（服务端已经实现，Agent 不重复实现）

### Gate 1：声明层

`analyze_declared_feed()` 校验：

- schema 版本、必填结构；
- 图号唯一性、语义码/槽位码是否混用；
- 对白角色是否在 `voice_prompts` 中；
- 音色引用与角色图号一致；
- `### 素材引用` 位置与绑定；
- `segments[].canvas.asset_codes/asset_refs` 与正文是否一致。

### Gate 2：产物层

`build_payload()` + `merge_payload_by_episode()` 之后，`validate_final_payload()` 校验：

- 每个视频节点实际引用的图片/音频是否接上；
- 对白角色、`voiceRefs`、audio 入边、图片入边是否一致；
- 集级成员关系是否正确。

两闸任一失败：**原 payload 与 revision 不变**。

### 错误码（Agent 修复循环要按 code/segment/role 定位）

```text
FEED_PARSE_ERROR
SCHEMA_VALIDATION_FAILED
SCHEMA_VERSION_UNSUPPORTED
SEGMENT_INVALID / SEGMENT_NO_DUPLICATE
ASSET_CODE_INVALID / ASSET_CODE_DUPLICATE
ASSET_DECLARED_MISSING / ASSET_EDGE_MISMATCH
VOICE_PROMPT_MISSING / VOICE_PROMPT_INCOMPLETE / VOICE_DIALECT_MISSING
VOICE_FIELD_CONFLICT / VOICE_CODE_CONFLICT / VOICE_ROLE_DUPLICATE
VOICE_DECLARED_MISSING / VOICE_REF_MISMATCH / VOICE_EDGE_MISMATCH
REVISION_CONFLICT
```

警告码：`WARN_DEPRECATED_VOICE_FIELD`、`WARN_DERIVED_DIALECT`、`WARN_GENERATOR_METADATA_MISSING`、`WARN_CODE_CANONICALIZED`、`WARN_RETRIED_AFTER_REVISION`、`WARN_ASSET_IDENTITY_AUTO_CORRECTED`。

## 6. Agent 的校验 / 修复循环

```text
生成 feed
  → 服务端 prepare_import（只读）
    → errors 为空：进入预览
    → errors 非空：
        把 {code, message, path, segment, role, nodeCode} 原样回喂
        Agent 只修复对应位置，禁止重写全文
        最多 2 轮
        仍失败：停止，展示 issues + 下载 JSON，不再自动改
```

硬规则：

- **不允许 Agent 通过“删掉出问题的段/角色”来让校验通过**；必须保留用户确认过的内容。
- 修复轮次、每轮 issues、最终 feed sha256 都要落库。
- 第 2 轮仍失败的 feed 只能作为草稿展示，不能进入“确认导入”。

## 7. 溯源与版本

导入后，现有 `_attach_provenance()` 会写：

- `sourceFeedName`
- `sourceFeedPath`
- `feedSha256`
- `schemaVersion`
- `skillVersion`
- `packageRevision`
- `firstImportedAt` / `lastImportedAt` / `importedAt`
- `importWarnings`

Agent 建议额外在 `agent_imports` 表记录：

- `runId`、`artifactId`、`projectId`、`episodeId`
- `revisionBefore/After`
- `skillVersionIds`
- `model`
- `importSummary` / `warnings`

如果希望画布集级 provenance 也带 `agentRunId`，可做**向后兼容的小扩展**：在 `_source_provenance()` 中读取 `source["agentRunId"]`，写入集级字段；不改变现有字段语义。

## 8. 契约版本策略

| 情况 | 处理 |
|---|---|
| 新增可选字段 | 保持 `drama-canvas-feed/v2`，schema 加 optional |
| 修正校验规则 | v2 内升级；skill 包声明 `packageRevision`，服务端记录 |
| 破坏性变更 | 新开 `v3`；服务端同时支持 v2/v3；提供 v2→v3 转换器；禁止静默升级 |
| 技能包 schema 与服务端不一致 | 上传时拒绝；错误码 `SKILL_SCHEMA_HASH_MISMATCH` |
| 旧技能包 | 只读保留；新运行必须选兼容版本 |

**同步机制**：

- 服务端 schema 的 `sha256` 是唯一权威；
- 技能包 manifest 必须声明 `contract.sha256`；
- 上传/激活时比对；CI 中也比对；
- Agent 产出的 `canvas_contract.agent.contractSha256` 记录运行时使用的 hash。

## 9. Agent 输出纪律

- 产物文件必须是**纯 JSON**，不要 Markdown 代码围栏、不要“以下是 JSON”前缀。
- 不要在 JSON 里写注释（JSON 不支持；导入了会解析失败）。
- 不要输出多个 JSON 文档；一次运行一个候选 feed。
- 大段生成要分阶段落盘（brief → assets → segments → feed），不要在一次模型调用里硬憋完整 JSON。
- 最终 feed 必须再走一次 `validate_canvas_feed.py` 或服务端预检，不能只依赖模型自检。

## 10. 最小验收清单（每次 Agent 产出都要过）

- [ ] `canvas_contract.schema === "drama-canvas-feed/v2"`
- [ ] `title` / `episode_title` 非空
- [ ] 每个 `asset_prompts[].code` 唯一且为 `图N`
- [ ] 每个 `voice_prompts` 有完整英文 prompt + 固定结尾
- [ ] 每个有对白角色都有音色引用
- [ ] 每个 `segments[].raw` 有且仅有一份 `### 素材引用`，位置正确
- [ ] 正文引用全部为完整 `@类型｜名称（图N）`
- [ ] `segments[].canvas.asset_codes/asset_refs` 与正文双向一致
- [ ] `prepare_import` 无 error（warning 可接受但必须展示）
- [ ] feed sha256 已记录