# 生产日志与磁盘审计

审计日期：2026-11-13

## 事实

- systemd journal 审计前约 4.0G；过去 7 天本系统后端约 147,003 行、Agent 约 58,495 行，主要是成功请求轮询。
- `/www/server/pgsql/logs` 审计前约 29G。
- PostgreSQL `log_statement = all`，每日日志 464M～12G，正常 SQL 及其完整绑定参数全部落盘。
- 2026-11-13 日志在约 9 小时内达到 3.3G，`statement:` 约 128 万行，`ERROR:` 为 0。

## 修复策略

- 应用成功访问默认不写 journal；4xx/5xx 和业务审计记录保留。
- Agent Uvicorn 关闭成功访问日志。
- journald 限制 1G、14 天，单文件 64M。
- PostgreSQL 不再记录所有 SQL；仅记录超过 3 秒的慢查询和错误。
- 慢查询/错误参数最多记录 2048 字节，避免整份画布 JSON 写入日志。
- PostgreSQL 按天或 256MB 轮转，保留 3 天且总量不超过 1G。

## 回滚

- 日志策略修改前会自动备份 journald、Agent unit 和 PostgreSQL 配置。
- PostgreSQL 配置通过 `ALTER SYSTEM` 管理，回滚可用 `ALTER SYSTEM RESET` 后执行 `pg_reload_conf()`。
