# 03 · 技能包规范与上传

## 1. 目标

让用户像安装插件一样上传技能包，系统能：

- 校验它是否“能产出当前画布可导入的 feed”；
- 版本化、回滚、禁用；
- 让 Agent 安全地读取技能文档；
- 可选地运行技能自带的确定性导出器；
- 不让技能包越权读项目、联网、执行任意代码。

---

## 2. 包类型

| 类型 | 说明 | 例子 |
|---|---|---|
| `suite` | 一个总控 + 多个子技能 | 漫剧老李 V7.4.29（1 总控 + 12 子技能） |
| `single` | 单技能，一个 `SKILL.md` | 只做“打戏分镜”的单包 |

一个 suite 只产生一个入口；子技能是总控内部的规则文件，不单独对用户暴露（除非用户明确选择子技能）。

### 多技能并存与选择

- 技能库可以同时安装多个套件/单技能，互不覆盖（按 `id + version + packageRevision` 区分）。
- 一次运行默认只选**一个入口技能**（一个 suite 或一个 single），避免多个总控互相抢规则。
- suite 内部的子技能由总控路由；用户不需要手动切。
- 第一版**不做自动触发匹配**（“看到‘打戏’就自动挂某个技能”），避免不可预期；由用户在顶部选择器显式选择。
- 后续可加“推荐技能”提示，但最终仍以用户选择为准。

---

## 3. 推荐目录结构

```text
my-skill/
  skill.json                 # 必须
  README.md
  CHANGELOG.md
  SKILL.md                   # single 时的入口；suite 可只做说明
  skills/
    short-drama-director/
      SKILL.md
      references/
      scripts/               # 可选，默认不执行
    sd-asset-engine/
      SKILL.md
      references/
  references/                # 共享引用
  templates/
  examples/
    canvas-feed.example.json
```

也兼容现有套件的**平铺结构**（`short-drama-director/`、`sd-*-engine/` 与 `PACKAGE-MANIFEST.json` 同级）。上传时系统会做 legacy adapter（见第 5 节）。

---

## 4. `skill.json` 规范

机器可读 Schema：[schemas/skill-package.schema.json](schemas/skill-package.schema.json)。

```json
{
  "schema": "drama-canvas-skill/v1",
  "id": "manju-laoli-skill",
  "name": "漫剧老李 AIGC 创作全流程 Skill",
  "version": "7.4.29",
  "packageRevision": "20261105-r14",
  "kind": "suite",
  "entry": "short-drama-director/SKILL.md",
  "skills": [
    {"id": "short-drama-director", "name": "总控", "path": "short-drama-director/SKILL.md"},
    {"id": "sd-asset-engine", "name": "资产引擎", "path": "sd-asset-engine/SKILL.md"}
  ],
  "contract": {
    "schema": "drama-canvas-feed/v2",
    "schemaFile": "short-drama-director/references/canvas-feed.schema.json",
    "sha256": "…服务端 schema 的 sha256…"
  },
  "runtime": {
    "mode": "prompt",
    "maxContextTokens": 60000,
    "maxOutputTokens": 8000,
    "exporter": null,
    "stages": [
      {
        "id": "locks",
        "label": "锁题",
        "documents": ["short-drama-director/SKILL.md"],
        "maxTokens": 12000
      },
      {
        "id": "feed",
        "label": "投喂稿",
        "documents": ["sd-feed-engine/SKILL.md", "sd-feed-engine/references/*.md"],
        "maxTokens": 24000
      }
    ]
  },
  "permissions": {
    "readProject": true,
    "readAssets": true,
    "writeArtifacts": true,
    "executeScripts": false,
    "network": false
  },
  "locks": ["track", "source", "model", "aspect", "delivery", "transition", "style"],
  "triggers": ["短剧", "分镜", "资产", "投喂", "画布"],
  "publisher": {"name": "漫剧老李", "contact": ""},
  "files": {"count": 191, "bytes": 5217116}
}
```

字段说明：

| 字段 | 必填 | 说明 |
|---|---|---|
| `schema` | 是 | 固定 `drama-canvas-skill/v1` |
| `id` | 是 | 小写 slug，租户内唯一；建议 `^[a-z0-9][a-z0-9-]{1,63}$` |
| `version` | 是 | 技能版本；同 id + version 不可覆盖 |
| `packageRevision` | 是 | 包修订号；同一 version 的不同打包可区分 |
| `kind` | 是 | `suite` / `single` |
| `entry` | 是 | 入口 `SKILL.md` 相对路径 |
| `skills` | suite 必填 | 子技能列表 |
| `contract.schema` | 是 | 目前只支持 `drama-canvas-feed/v2` |
| `contract.schemaFile` | 是 | 包内 schema 路径 |
| `contract.sha256` | 是 | 与服务端 schema 完全一致 |
| `runtime.mode` | 是 | `prompt` / `exporter` / `hybrid` |
| `runtime.exporter` | 否 | 声明确定性导出器；执行受权限/沙箱约束 |
| `permissions` | 是 | 最小权限；默认全 false，只有需要才 true |
| `locks` | 否 | 需要向用户收集的锁；Agent 会转成选择卡 |
| `triggers` | 否 | 技能选择器的匹配词，不参与权限 |

---

## 5. 现有 V7.4.29 套件的兼容适配

现有包没有 `skill.json`，但有 `PACKAGE-MANIFEST.json` + `VERSION.json`。上传时按以下映射生成临时 manifest：

| 现有字段 | 映射 |
|---|---|
| `PACKAGE-MANIFEST.package` | `id` |
| `PACKAGE-MANIFEST.version` / `VERSION.version` | `version` |
| `PACKAGE-MANIFEST.packageRevision` | `packageRevision` |
| `PACKAGE-MANIFEST.entry` | `entry` |
| `PACKAGE-MANIFEST.canvasSchema` | `contract.schema` |
| `PACKAGE-MANIFEST.schemaFile` | `contract.schemaFile` |
| `PACKAGE-MANIFEST.skillCount` | `skills` 数量校验 |
| `PACKAGE-MANIFEST.fileCount` | 记录为声明值；以实际包扫描为准（现有目录可能因生成物多于清单声明） |
| `VERSION.jsonGenerator` | `runtime.exporter.entry`（仅声明，不自动授权执行） |
| `VERSION.jsonGeneratorSelfTest` 等 | 记录为包自检状态 |

**契约 hash 校验**：系统读取包内 `short-drama-director/references/canvas-feed.schema.json` 的 sha256，与 `schemas/canvas_feed.schema.json` 比对。不一致 = 上传失败（`SKILL_SCHEMA_HASH_MISMATCH`），不允许“先装上再说”。

现有套件第一版建议作为**系统内置信任包**安装（可在技能库中显示“官方/内置”），用户自己上传同名包则作为新版本，需显式激活。

---

## 6. 上传与激活生命周期

```text
POST /api/agent/skills/uploads          # 上传 zip / 分片
  → 解压到 staging（临时目录）
  → 解析 manifest / legacy adapter
  → 安全扫描 + 契约 hash + 入口校验
  → 返回 preview（不激活）

POST /api/agent/skills/uploads/{id}/activate
  → 复制到 tenants/<tenant>/agent/skills/<skillId>/<version>/
  → 写 agent_skill_versions (ready/active)
  → 旧 active 版本降为 ready
  → audit: agent.skill.activate

POST /api/agent/skills/{id}/versions/{version}/rollback
  → 仅切换 active 指针，不删除历史
```

规则：

- 同 `id + version + packageRevision` 重复上传：返回已存在，不重复写。
- 同 `id + version` 不同 `packageRevision`：允许作为新版本，但必须明确版本串。
- 删除：软删除；历史 run 仍可读；存储按保留策略清理。
- 激活：默认不自动激活新上传版本，需用户点“启用”。
- 运行固定版本：创建 run 时把 `skillVersionIds` 写死；运行中不跟随激活变化。

---

## 7. 上传安全校验（必须全部通过）

### 7.1 归档安全

- 仅接受 `.zip`；拒绝加密 zip、嵌套 zip（或只允许一层）。
- 拒绝绝对路径、`..`、盘符、UNC、符号链接、硬链接。
- 解压后总字节 ≤ 50 MB、文件数 ≤ 2000（现有套件当前约 191 个文件，清单声明 171，留余量）。
- 单文件 ≤ 10 MB；文本文件必须是 UTF-8（BOM 可容忍）。
- 拒绝可执行二进制（`.exe/.dll/.so/.dylib/.bat/.cmd/.ps1`），除非显式白名单。
- 解压必须在临时目录，文件名清洗，禁止覆盖。

### 7.2 包结构安全

- `entry` 必须存在且在包内；resolve 后仍在包根。
- 所有 `skills[].path` 必须在包内。
- 禁止 `skill.json` 指向包外路径。
- 拒绝隐藏目录/文件（`.git`、`.env`、`node_modules`）。
- 大小写重复路径、同名文件冲突直接拒绝。

### 7.3 契约安全

- `contract.schema` 必须是 `drama-canvas-feed/v2`。
- 包内 schema 与服务端 schema 的 sha256 必须一致。
- 若声明 `runtime.exporter`：入口必须存在、扩展名 `.py`、在包内；是否允许执行由权限决定。

### 7.4 内容安全

- Markdown 渲染时禁用原始 HTML（或白名单），防 XSS。
- 不把包内 `scripts` 自动加入 PATH。
- 不读取包外的任何文件。
- 记录包 sha256、文件清单、上传者、时间。

---

## 8. 技能运行模式

### 8.1 `prompt`（默认，MVP）

Agent：

1. 读 `entry` + 当前阶段声明的文档（或按需检索）；
2. 按技能规则生成阶段产物；
3. 直接产出 feed JSON；
4. 服务端预检 + 修复。

适合大多数“规则/模板”型技能，也最安全。

### 8.2 `exporter`（可选）

技能提供一个确定性导出器，把“已生成的项目 Markdown”转成 feed JSON。流程：

```text
Agent 产出 project.md（按技能模板）
  → 沙箱执行 exporter
  → 产出 feed.json
  → 服务端预检
```

现有 V7.4.29 的 `build_board_lite.py` 就是这种模式：

```bash
python build_board_lite.py 项目输入.md \
  --export-json feed.json \
  --asset-ledger 现有资产台账.json
```

它还会产出看板 HTML；Agent 场景只需要 `--export-json`，不要生成无关文件。

### 8.3 `hybrid`

Agent 负责创意/阶段内容，exporter 负责最终 JSON 结构。推荐给“技能很大、JSON 很严格”的套件。

---

## 9. 导出器沙箱（Phase 3，默认关闭）

只有同时满足以下条件才允许执行：

- manifest `permissions.executeScripts=true`；
- 部署方在 `agent.exporter.allowlist` 中允许该 skill/版本；
- 脚本 hash 与 manifest 记录一致；
- 运行在独立子进程/容器，不在主进程。

限制：

- 只读挂载技能目录；工作目录为临时目录；
- 无网络（或仅 localhost 禁用）；
- CPU 时间 ≤ 120s、内存 ≤ 512MB、输出文件 ≤ 8MB；
- 环境变量清空，不传任何密钥/租户数据；
- 只允许声明的一个入口 + 固定参数；
- 超时 kill，退出码非 0 视为失败；
- 产物必须再由服务端 schema/两闸校验。

> 第一版可以不实现完整容器，只做“内置信任包”的受控 subprocess + 超时；用户上传包禁止执行。

---

## 10. 上下文策略（防止“技能包太大”）

现有套件约 141 份 md、127 万字符，不可能整包进上下文。manifest 用 `runtime.stages` 声明：

- 每个阶段只加载 `documents` 指定的入口/参考；
- 每阶段 `maxTokens` 上限；
- Agent 通过 `skill.search` / `skill.read` 按需取片段；
- 阶段产物落盘，用“已完成清单 + 状态台账”接力，不重复全文。

推荐技能作者：

- 把“总控路由”和“阶段细则”拆开；
- 每个阶段一个入口文件；
- references 用清晰标题，便于检索；
- 不要把全部规则塞进一个 SKILL.md。

---

## 11. 技能作者检查清单

- [ ] `skill.json` 通过 [schema](schemas/skill-package.schema.json)
- [ ] `entry` 可读，能独立说明“何时触发/产出什么”
- [ ] `contract.schema` = `drama-canvas-feed/v2`
- [ ] 包内 schema 与服务端 schema hash 一致
- [ ] 至少一个 `examples/` feed（或生成命令）
- [ ] 明确列出锁题（如果技能需要）
- [ ] 明确产出物的阶段和文件
- [ ] 不含密钥、不联网、不依赖包外路径
- [ ] 若声明 exporter：有 `--help`、超时可控、输出稳定、无交互
- [ ] 通过 `validate_canvas_feed.py` 和现有导入预检

---

## 12. 现有套件落地建议

- 先把它作为**内置技能包**接入，不执行用户上传脚本；
- 用 `build_board_lite.py` 做 Phase 0 的确定性基线；
- Agent MVP 先用 `prompt` 模式：读总控 + 投喂引擎，产出 feed；
- Phase 2 再加“Agent 产出 project.md → 内置 exporter → feed”的混合模式；
- 任何情况下，最终都以 `prepare_import()` 为准。