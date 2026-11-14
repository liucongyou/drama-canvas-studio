# 并发生成终态冲突与本地结果恢复记录

日期：2026-11-15 00:45 +08:00

## 现象

四个视频逐个提交后都进入生成中。段11、段12获得供应商任务号并完成下载，
但卡片持续显示“结果待确认”，任务中心也显示任务异常结束。

## 根因

- 并发提交本身正常，四条任务各自拥有独立节点和供应商任务。
- 段11、段12的供应商结果都已成功下载：
  - `local_result_url` 存在；
  - `provider_result_url` 存在；
  - 本地 MP4 文件存在。
- 两个任务几乎同时写同一项目终态时，PostgreSQL 项目 revision 更新发生冲突，
  两条终态事务回滚。
- 任务执行线程随后把仍为 running 的记录标成“未形成终态”，但已下载的本地结果
  没有进入自动恢复队列，所以卡片一直停在旧的不确定投影。

## 修复

- `commit_generation_terminal` 在 PostgreSQL 中对项目行使用 `FOR UPDATE`：
  - 同一项目的并发终态提交串行执行；
  - 不同项目仍可并行；
  - 不再通过 revision 竞争互相打断。
- 自动投递恢复队列新增：
  - `failed + delivery_status=failed + local_result_url` 任务；
  - 只要本地文件仍存在，就复用原文件补写节点和任务终态；
  - 不重新下载、不重新提交供应商任务。
- `claim_local_delivery` 允许原子认领 `pending` 或 `failed` 的本地结果。
- 新增两项回归测试，覆盖有本地结果可恢复、无本地结果不可恢复。

提交：`63ceb70`

## 验证

- 后端测试：12 项通过。
- Python 编译检查：`canvas_store.py`、`server.py` 通过。
- 线上部署文件 SHA-256 与本地提交一致。
- 段11：
  - 任务：`succeeded / ready`
  - 节点：`done / completed`
  - 视频：HTTP 206，`video/mp4`
- 段12：
  - 任务：`succeeded / ready`
  - 节点：`done / completed`
  - 视频：HTTP 206，`video/mp4`
- `drama-canvas-yj.service`：active。
- `drama-canvas-agent.service`：active。

## 结论

系统支持并发生成。并发任务可以同时调用供应商；真正需要串行的是同一项目内的
终态数据库提交。现在终态提交已串行化，并保留本地结果自动恢复兜底。

回滚目录：

`/www/wwwroot/yj.pduoduo.vip/app/backups/generation-terminal-recovery-63ceb70-20261115-003755`

本次恢复未触发任何新的图片、音频或视频付费生成。
