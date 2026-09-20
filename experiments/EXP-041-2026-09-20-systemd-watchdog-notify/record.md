# EXP-041：真实 systemd user manager watchdog 通知

- 实验 ID：`EXP-041`
- 日期：2026-09-20
- 关联目标：Goal 7 / `PG-P0-07`
- 状态：`PASSED`（watchdog 通知接收路径；不等同于 watchdog 超时恢复通过）
- 实验负责人：当前 Agent

## 1. 实验目的

在 `guardian-ubuntu` 的真实 systemd 249 user manager 中，以临时 transient unit 启动当前同步到 `/tmp` 的只读 Observer，验证 `WatchdogSec` 配置下 systemd 能收到 Guardian 的 `READY=1` 和 `WATCHDOG=1` 通知，并在进程自然退出后回收 unit。

## 2. 环境与安全边界

- VM：`guardian-ubuntu`，Ubuntu 22.04.5 LTS、ARM64、systemd 249、2 vCPU、约 3.8 GiB 内存。
- 运行目录：`/tmp/guardian-p0-03-check`；代码、配置、readiness 和 JSONL 审计均为临时文件。
- unit：`guardian-observer-watchdog-notify.service`，通过 `systemd-run --user --collect` 创建；`Type=notify`、`NotifyAccess=main`、`WatchdogSec=30s`。
- Observer：`observe` 模式、单轮采样后在同一进程内短暂自然等待并退出；Docker 只读采集。
- 未安装、enable 或 start 持久 systemd unit；未执行 Docker stop/restart/kill、容器重启、资源变更或生产连接。
- 24 小时 EXP-039 主进程在实验前后均保持运行。

## 3. 步骤与结果

最终通过的运行使用当前 VM 临时工作树作为 `WorkingDirectory`/`PYTHONPATH`，避免使用旧项目副本；systemd 状态在 unit 运行期间每 100 ms 只读轮询。

| 检查 | 结果 |
| --- | --- |
| `WatchdogSec` | `30s` |
| systemd 收到 watchdog 的证据 | `WatchdogTimestampMonotonic=54319804739`（非零） |
| READY 后 unit 状态 | `ActiveState=active`、`SubState=running`；连续轮询记录见 `data/verification.json` |
| readiness | `observe:ready` |
| 审计 | 1 条 JSONL，7,913 bytes |
| 结束方式 | 同一进程自然退出；systemd transient unit 结果为 `success`，`--collect` 后 `LoadState=not-found` |
| 长跑隔离性 | EXP-039 Observer PID `1076273` 仍存活，RSS 约 17,492 KiB，CPU 0.0% |

## 4. 预检失败与数据处理

前两次预检没有纳入通过统计，且没有影响 EXP-039：第一次误用了 VM 中旧项目副本，`--config` 参数未被识别；第二次 wrapper 将 `config` 作为字符串传入，触发类型错误。随后固定 `WorkingDirectory=/tmp/guardian-p0-03-check` 并使用 `pathlib.Path` 传参，才得到本记录的通过结果。失败日志仍保留在 VM user journal 中。

## 5. 结论与限制

本实验为 `PG-P0-07` 增加了真实 systemd manager 接收 `READY=1`/`WATCHDOG=1` 的正向证据：Guardian 发出的 watchdog 通知使 unit 从 activating 进入 active/running，随后以 success 自然结束。

本实验没有验证 watchdog 超时、进程崩溃后的自动重启、持久 unit 的完整 sandbox、非 root `guardian` 用户/Docker 只读代理、磁盘真正耗尽恢复或 24 小时 soak 完成。因此不能将 PG-P0-07 标记为 DONE，也不能把 transient user-manager 结果写成生产可用性结论。

## 6. 核心数据

- [`data/verification.json`](data/verification.json)：脱敏的状态采样、路径和未验证边界。
- VM 临时状态文件：`/tmp/guardian-p0-03-check/watchdog-notify-1789878126.status`；不进入 Git。
- VM user journal：`guardian-observer-watchdog-notify.service` 的启动与参数错误记录；不进入 Git。

## 7. 更新记录

- 2026-09-20：完成真实 systemd user-manager watchdog 通知验证；未触发 watchdog 超时或任何破坏性动作。
