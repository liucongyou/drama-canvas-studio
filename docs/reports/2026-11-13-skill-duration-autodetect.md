# 技能分段时长自动识别修复

日期：2026-11-13

## 问题

画布载荷中的分段正文已经包含完整时间轴，例如段1：

`00:00-00:04 / 00:04-00:07 / 00:07-00:10`

但旧导出或旧技能产物可能缺少 `durationSec` / `canvas.duration`，导入器随后回退到 4 秒默认值，导致画布视频节点时长与正文不一致。

## 修复

- `canvas_feed_contract.py` 新增 `segment_timeline_duration_seconds()`，从分段 `raw` 最后一条镜头结束时间码计算时长。
- `build_board_lite.py` 导出时强制双写：
  - `segments[].durationSec`
  - `segments[].canvas.duration`
- 共享契约在导入前归一化分段时长，不再因缺少顶层 `assets` 提前返回。
- 旧 JSON 缺时长但正文时间码完整时自动补齐，并产生 `WARN_SEGMENT_DURATION_DERIVED`。
- 声明时长与时间轴冲突时以时间轴为准。
- `import_feed.py` 的兜底也直接解析 raw 时间轴，不再只依赖 `shots` 对象。
- 根 Schema 和技能包 Schema 同步加入 `durationSec`、`canvas.duration` 字段。

## 验证

- 使用用户附件《婆媳拼图》第01集 JSON 删除所有时长字段后重新导入：
  - 段1：10 秒
  - 段2：14 秒
  - 段3：15 秒
  - 段4：14 秒
  - 段5：14 秒
  - 段6：14 秒
  - 段7：12 秒
- `test_canvas_feed_generator_contract`、`test_canvas_feed_schema_runtime`、`test_canvas_feed_negative_contract`：46 项通过。
- `build_board_lite.py --self-test`：通过。
- 技能包 `check_package.py`：全部通过。
- 发布包清单：172 个文件哈希一致。

## 发布版本

- 技能版本：`7.4.32`
- 包修订：`20261113-r19`
- 代码提交：`c38fe74 fix: derive canvas segment duration from timecodes`
- 发布 ZIP：`deliverables/manju-laoli-skill-V7.4.32-20261113-r19.zip`
- ZIP SHA-256：`43171e7e4883410f145bd408aa04ed6986f7433de502dc6af3d01999fdd77086`
- 线上激活版本：`7.4.32`
- 线上安装目录：`/www/wwwroot/yj.pduoduo.vip/app/agent/data/skills/manju-laoli-skill/7.4.32-43171e7e4883`
- 回滚版本：`7.4.31`、`7.4.30` 仍保留且未激活。
- 本地与线上 `VERSION.json`、`PACKAGE-MANIFEST.json`、导出器、共享契约和 Schema 的 SHA-256 一致。
- 线上 `build_board_lite.py --self-test` 和 `check_package.py` 均通过。

本次不调用图片、音频或视频供应商生成接口。
