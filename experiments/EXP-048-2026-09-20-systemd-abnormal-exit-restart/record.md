# EXP-048：systemd transient 异常退出码 137 后重启

- 实验 ID：`EXP-048`
- 日期：2026-09-20
- 关联目标：Goal 7 / `PG-P0-07`
- 状态：`PASSED`（本地 disposable、自然退出码模拟；不等同于 SIGKILL/OOM 恢复）
- 实验负责人：当前 Agent

## 1. 目的

在真实 systemd 249 user manager 中，让只读 Observer wrapper 首轮自然返回退出码 `137`，验证 transient `Restart=on-failure` 能重新启动 Observer、恢复 readiness 并最终正常结束。退出码由 wrapper 自己返回，不发送 SIGKILL/SIGTERM，不制造 OOM 或内存压力。

## 2. 环境与安全边界

- VM：`guardian-ubuntu`，Ubuntu 22.04.5 LTS ARM64，2 vCPU，约 3.8 GiB 内存。
- unit：`systemd-run --user --collect` 创建的临时 unit；没有安装、enable 或保留持久 unit。
- Observer：当前代码临时工作树 `/tmp/guardian-exp047`，两轮均为 `--once`、observe-only，审计和 readiness 写入 `/tmp/guardian-exp048`。
- wrapper：[`crash-wrapper.sh`](crash-wrapper.sh) 首轮采样后自然 `exit 137`，第二轮采样后 `exit 0`。
- 禁止动作：不执行 Docker stop/restart/kill，不修改资源限制，不连接生产；EXP-039 主进程和 Beszel 容器必须前后存活/healthy。

## 3. 首次夹具失败与修正

第一次使用 `NotifyAccess=main` 启动 shell wrapper。Observer 是 wrapper 的子进程，其 `READY=1`/`WATCHDOG=1` 通知被 systemd 拒绝，journal 记录 `reception only permitted for main PID`，unit 最终以 `Result=protocol` 失败并触发启动速率限制。这是 wrapper 配置错误，不纳入通过统计，失败输出保留在 VM user journal 中。

修正版仅对该 transient wrapper 使用 `NotifyAccess=all`，以便验证重启控制流；生产模板仍保持 `NotifyAccess=main`，并已由 EXP-040/041 验证 Observer 作为主进程的通知路径。

## 4. 通过步骤与结果

1. 创建 `Type=notify`、`WatchdogSec=30s`、`Restart=on-failure`、`RestartSec=100ms` 的 transient unit。
2. 启动 wrapper；首轮 Observer 完成只读采样后自然退出码 `137`。
3. 只读轮询 systemd 状态和 user journal；观察到 `NRestarts=1`，第二轮再次启动并完成 Observer 采样。
4. transient unit 最终 `Result=success`，随后由 `--collect` 回收为 `LoadState=not-found`。

| 检查 | 结果 |
| --- | --- |
| 首轮失败 | `status=137/FAILURE`，由 wrapper 自然返回，无外部信号 |
| 重启 | journal 记录 `Scheduled restart job, restart counter is at 1`；轮询时 `NRestarts=1` |
| readiness | `observe:ready` |
| 审计 | 两轮共 2 条，15,775 bytes；两轮 Observer 事件均写入 |
| 最终状态 | 第二轮启动后 success，transient unit 被 `--collect` 回收 |
| 隔离性 | EXP-039 PID `1076273` 仍存活；Beszel Hub/Agent 容器仍 healthy；无 Docker/资源变更 |

## 5. 证据与限制

结构化摘要见 [`data/verification.json`](data/verification.json)。本实验只证明“自然退出码 137 的模拟失败 → systemd 自动重启 → readiness/审计恢复”的控制流，不证明：

- 真实 SIGKILL 或 OOM kill 后的恢复；
- watchdog 超时后的恢复；
- 持久 systemd unit、非 root sandbox、磁盘满恢复或生产 readiness。

因此 PG-P0-07 仍保持 `IN_PROGRESS`，P0-08 仍不启动。

## 6. 更新记录

- 2026-09-20：首次 `NotifyAccess=main` wrapper 夹具因子进程通知被拒而失败，标记为夹具问题并保留 journal 证据。
- 2026-09-20：修正为 transient-only `NotifyAccess=all` 后通过；未触碰 EXP-039 或任何容器动作。
