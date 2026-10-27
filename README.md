# 戏剧画布工作流系统（drama-canvas-studio）

独立本地画布工作流系统：消费**投喂载荷 JSON** → 自动生成五区节点画布（角色 / 场景 / 道具 / 音频 / 视频）→ 内置挂接机检 → 一键调火山方舟生成（Seedream 1.5K 资产生图 / Seedance 2.0 视频 / 4.5S 音色参考）。

与 ViMax 解耦：本系统只读投喂载荷（`铁西硬汉_第01集_投喂载荷.json` 格式），不参与剧本创作；ViMax/技能产出的载荷直接拖进来即可。

## 启动

```powershell
cd C:\Users\liuch\Doubao\chats\2026-10-15\new-chat\drama-canvas-studio
python server.py                       # 后端 8890
npm --prefix web-vimax run dev         # 画布前端 8891（ViMax 画布内核 + 自研参数面板）
```

浏览器打开：http://localhost:8891

## 使用流程

1. **配置模型接口**：顶部「模型接口」填写供应商地址与 API Key；密钥保存到本机 `config.local.json`（已加入 `.gitignore`）
2. **新建项目**：顶部「新建」
3. **导入载荷**：输入框填投喂载荷 JSON 路径 → 「导入载荷 → 画布」→ 自动生成五区卡片画布并连线（角色 / 场景 / 道具 / 音色 / 段）
4. **挂接机检**：顶部「创作质量自评」→ 全部 PASS 才建议生成
5. **模型接口**：顶部「模型接口」管理供应商、API Key、模型 ID、适配器与参数 Schema。供应商密钥只在后端读取，画布节点不保存密钥。
6. **生成**：点击卡片节点**坑位** → 下方弹出提示词输入框 + ⚙ 设置参数（供应商 / 模型 / 清晰度 / 比例 / 推理强度 / 引用资产 / 音色引用）→ 「生成」→ 结果回填卡片（图 / 视频 / 音频可预览）

## 文件说明

| 文件 | 职责 |
|---|---|
| `server.py` | 本地 HTTP 后端（标准库，零依赖，端口 8890；节点 PUT / catalog / notes / refs / voice / 生成） |
| `canvas_store.py` | SQLite 存取（payload 整包 + 自动快照） |
| `import_feed.py` | 投喂载荷 → 画布 payload（五区布局 + 对白音色过滤 + 自动连线 + 标题=投喂名） |
| `gate_check.py` | 挂接机检（稿 @//图 ↔ 实连线图 双向核对） |
| `provider_catalog.py` | 供应商与模型目录、参数 Schema、密钥脱敏与连接测试 |
| `provider_client.py` | 方舟图片/视频、OpenAI 兼容文本/图片、阿里云百炼 TTS 适配器 |
| `ark_client.py` | 旧版方舟客户端（保留兼容，主生成链路已切到 provider_client） |
| `config.json` / `config.local.json` | 非敏感默认配置 / 本机供应商密钥与自定义模型配置 |
| `web/index.html` | v1 简易前端（8890 直开，保留） |
| `web-vimax/` | **v2 画布前端（正式）**：ViMax 画布内核复制 + 自研适配层 `api.ts` + 卡片式媒体节点 + 详细生成参数面板；vite dev 8891，proxy `/api` → 8890 |
| `e2e_test.py` | 端到端自检（`python e2e_test.py`） |

## v2 卡片式节点（图片 / 音色 / 视频）

- 媒体节点 = **卡片式节点**：标题 + 预览坑位（虚线框，未生成时显示类型图标 + 「点击编辑」）
- **点击卡片坑位** → 节点下方弹出编辑区：提示词输入框 + 「⚙ 设置参数」折叠面板 + 「生成」按钮
- **图片节点参数**：模型（Seedream Pro / Flash / 4.5）、清晰度（1K / 1.5K / 2K / 4K，1.5K=长边1536≈0.3元/张）、比例（1:1 / 16:9 / 9:16 / 4:3 / 3:4 / 21:9，自动换算 size）、推理强度（轻度→最高）、@ 添加资产图引用（refs）
- **视频节点参数**：Seedance 2.0 / 2.0 mini / 2.0 fast / 2.5 独立能力范围；比例、时长、分辨率由模型目录限制，支持参考图、参考视频、文本与多个角色音色
- **音频节点参数**：阿里云百炼 CosyVoice；支持「音色设计 / 指定音色合成」、voiceId、wav/mp3、采样率
- 参数持久化到节点 `metadata.params`；生成时后端 `_generate` 自动读取 params（size/model/guidance/ratio/duration/resolution/refs/voiceRef）

## 多供应商模型接口

内置三条独立协议通道：

- `火山方舟`：`/images/generations`、`/contents/generations/tasks`
- `TeamRouter`：OpenAI 兼容 `/chat/completions`
- `阿里云百炼`：`/services/audio/tts/customization`、`/services/audio/tts/SpeechSynthesizer`

模型目录、参数范围和适配器由后端 `/api/model-catalog` 返回。新增同协议供应商只需在「模型接口」中增加 provider + model；新增私有协议时在 `provider_client.py` 增加适配器，节点 UI 不需要改。

本地素材会由服务端在调用供应商前转换为可传输的数据地址，避免把 `127.0.0.1 /uploads/...` 发给远端模型。缺失素材、超出模型的参考数量、提示词为空、参数越界都会在提交前明确报错。

## 生成队列与任务恢复

- 不同节点不会直接同时打到供应商。默认全局最多 2 路、同一供应商最多 1 路，其余任务持久化为“排队中”。
- 排队阶段可在任务中心取消；已经提交到供应商的运行中任务不会被伪造为已取消。
- 后端每 30 秒按节点终态对账任务表，完成后自动把遗留的 `running` 记录收敛为完成、失败或中断。
- 可通过环境变量调整：`CANVAS_GENERATION_GLOBAL_CONCURRENCY`、`CANVAS_GENERATION_PROVIDER_CONCURRENCY`、`CANVAS_GENERATION_QUEUE_TIMEOUT`。

## 模型计费规则

画布价格提示只读取当前模型的 `pricing` 配置，不再按供应商写死单价。没有配置可靠价格的模型会显示“按供应商账单”，不会显示猜测金额。

视频按秒计价示例：

```json
{
  "currency": "CNY",
  "video": {
    "unit": "second",
    "rates": {"480P": 0.462, "720P": 0.994, "1080P": 2.478},
    "ratesWithVideo": {"480P": 0.506, "720P": 1.088, "1080P": 2.712},
    "note": "最终费用以供应商 usage 和账单为准"
  }
}
```

视频按条计价可使用 `{"unit":"video","fixed":1.25}`；图片按张可在 `image.rates` 中按清晰度或比例配置。计费规则可在「模型接口 → 计费规则 JSON」中直接维护。

## 统一主题

顶栏「主题」按钮提供五套统一主题：石墨蓝、松石绿、暖铜灰、AIGC 暗色、白色主题。主题变量集中在 `web-vimax/src/theme-tokens.css`，画布、面板、弹窗、任务状态和小地图共用同一套令牌；选择结果保存在浏览器本地。

## 多租户部署（自带密钥、自行付费）

默认不设置 `CANVAS_MULTI_TENANT` 时仍按单机模式运行。启用多租户后，每个用户对应一个独立子域名、独立租户数据库、独立上传目录、独立供应商配置和独立生成队列额度。主域名只返回 `404`，用户通过管理员生成的一次性邀请进入。

```powershell
$env:CANVAS_MULTI_TENANT="1"
$env:CANVAS_BASE_DOMAIN="example.com"
$env:CANVAS_TENANT_DATA_ROOT="C:\drama-canvas-data"
$env:CANVAS_MASTER_KEY="base64:<32字节URL-safe Base64>"
py server.py
```

租户管理：

```powershell
py tenant_admin.py create alpha --name "Alpha Studio"
py tenant_admin.py invite alpha --ttl-hours 24
py tenant_admin.py list
py tenant_admin.py rotate-invite alpha
py tenant_admin.py disable alpha
```

首次启用会把现有项目和素材迁移到 `owner` 租户；原始文件保留不动。供应商和 TOS 密钥会加密写入各租户自己的 `config.local.json`，接口只返回掩码。完整的反向代理、泛域名 TLS 和部署约束见 `deploy/README.md`。

## 铁律（已内置）

- **只有有对白的角色才生成音色**（音频节点）——解析各段台词行自动判定，无对白角色自动跳过
- **挂接机检通过才建议生成**——生成前自动核对
- **方舟统一**：资产生图 1.5K（0.3 元/张）、视频 Seedance 2.0、音色 4.5S 参考音色
- **生成只在点击生成按钮后提交**——配置、引用和参数编辑不会自动调用付费接口
- **`@` 资产可视化**——提示词内插入 `@角色/场景/道具` 时显示对应图片；插入 `@音色` 时优先显示同角色立绘，并单独写入 `voiceRefs`
