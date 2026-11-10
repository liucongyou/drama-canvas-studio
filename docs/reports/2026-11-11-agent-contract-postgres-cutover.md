# 2026-11-11 Agent 契约与 PostgreSQL 切换报告

## 结论

- 线上运行库已切换到 PostgreSQL `drama_canvas`，主服务和 Agent 侧车均 active。
- 线上 SQLite 迁移逐表行数与 SHA-256 全部一致，没有跳过实际业务租户。
- Agent 已使用 `manju-laoli-skill` V7.4.30；确定性 Feed 编译、共享契约预检和确认导入已接通。
- 测试与验收没有触发图片、视频或音频付费生成。

## 线上迁移

数据库：PostgreSQL 16.10，`postgresql://drama_canvas@127.0.0.1:5432/drama_canvas`。本地回环信任认证，应用使用独立低权限角色。

| Schema | 来源 | 行数 |
|---|---|---:|
| `control` | `data/control.db` | 263 |
| `tenant_owner` | `data/tenants/owner/canvas.db` | 6,377 |
| `tenant_u_a365879766` | `data/tenants/u-a365879766/canvas.db` | 1,312 |
| `tenant_u_liuchenyou` | `data/tenants/u-liuchenyou/canvas.db` | 9 |
| `tenant_u_zyh2018114` | `data/tenants/u-zyh2018114/canvas.db` | 7 |
| `tenant_u_aa520520` | `data/tenants/u-aa520520/canvas.db` | 8 |
| `agent` | `agent/data/agent.db` | 8 |
| `archive_legacy_root` | `data/canvas.db` | 3 |

合计迁移 `7,987` 行。每张表的源/目标行数、规范化 SHA-256 均为 `ok=true`。

备份：

```text
/www/wwwroot/yj.pduoduo.vip/app/backups/pg-cutover-20261111-030801
```

备份包含原代码、systemd 配置、控制库、Agent 库、owner 库和全部用户租户 SQLite 库。

## Agent 与技能

- 唯一 Feed：`drama-canvas-feed/v2`。
- 模型输出经服务端确定性编译器后固定写入 `generator.name`、`skillVersion`、`packageRevision`、`schemaSha256`、`feedSha256`。
- 预览前先跑共享 `canvas_feed_contract.analyze_declared_feed`，格式错误不再进入画布预检。
- `read_skill_section` 使用固定技能根目录、Markdown 扩展名、路径穿越拒绝和字符窗口上限。
- 运行时质量合同固定注入系统上下文，不依赖超长 `SKILL.md` 前 24k 字符是否恰好包含关键条款。
- 线上技能：`manju-laoli-skill` V7.4.30；包修订 `20261111-r15`。
- 技能包新增：规则冲突权威表、平台 3 秒/15 秒节奏、全局视听锁、空间/多人连续性、逐镜 15 项 fail-closed 自检、成片 canon、Retake 协议。

## 性能

本地 PostgreSQL 隔离库，36 个并发项目创建+读取+更新，12 工作线程：

```json
{"count":36,"medianMs":124.85,"p95Ms":1988.74,"maxMs":2025.81}
```

首次无预热运行 P95 约 14.2 秒；加入 schema 级连接复用后，预热运行 P95 约 1.99 秒。

## 验证

- Python 全量：`754 passed, 5 skipped, 208 subtests passed`。首轮曾出现一次 Windows 慢读性能断言 `2.188s > 1.5s`，单项连跑 3 次均通过；最终全量复跑全绿。
- 前端：`76` 个测试文件、`708` 项测试全绿。
- TypeScript：双工程 `tsc --noEmit` 通过。
- 生产构建：成功，主入口 `assets/index-BwA9xhR6.js`。
- Agent：全量测试通过；PG 专项测试通过。
- PostgreSQL 专项：兼容层、项目 CRUD/投影/事件/快照、控制面租户/账号/偏好/会话、并发写入通过。
- 技能包：`quick_validate` 通过；`check_package.py` 全部检查通过，版本 V7.4.30、references 51 份、12 子技能。
- 线上只读 Agent 冒烟：面板、输入框、技能 chip、草稿刷新恢复、素材悬停预览、关闭动画通过，`errors=[]`。
- 线上主 API：登录成功，owner 项目 9 个；当前项目 27 节点、77 连线读取正常。
- 线上 Agent：`/api/agent/health` 为 ok，技能列表包含 V7.4.30。
- 线上进程：`/proc/<pid>/fd` 未发现打开的 `.db`/`.sqlite`；PostgreSQL 连接池正常。

## 回滚

1. 停止 `drama-canvas-agent.service` 和 `drama-canvas-yj.service`。
2. 移除 `/etc/systemd/system/drama-canvas-yj.service.d/pg.conf` 和 Agent 的同名 drop-in。
3. 从备份恢复被覆盖的代码文件与 `web-vimax/dist`。
4. `systemctl daemon-reload` 后按 Agent → 主服务顺序重启。
5. PostgreSQL 数据和备份保留，不删除；回滚只恢复运行路径。

## 未纳入运行时

`drama-canvas-studio-pg/` 仍是独立重构副本，不参与线上运行；线上唯一运行服务仍是 `server.py` + PostgreSQL 兼容仓储。`db_slim.py`、迁移验证脚本和临时审计脚本中的 SQLite 只属于离线迁移/审计工具，不进入生产请求路径。
