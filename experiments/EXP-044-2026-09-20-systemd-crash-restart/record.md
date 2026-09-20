# EXP-044：transient systemd 自然失败后的 Observer 重启

- 实验 ID：`EXP-044`
- 日期：2026-09-20
- 关联目标：Goal 7 / `PG-P0-07`
- 状态：`PASSED`（自然失败后的 transient restart）
- 实验负责人：当前 Agent

## 1. 目的

在真实 systemd 249 user manager 中，以临时 transient unit 运行当前只读 Observer；让 wrapper 在第一轮 Observer 完成后自然返回一次非零码，再由 `Restart=on-failure` 重启并在第二轮自然返回 0，验证 Guardian 的安全重启路径和启动后重新建立 readiness 的能力。

## 2. 安全边界

- VM：`guardian-ubuntu`，Ubuntu 22.04.5 LTS ARM64，2 vCPU、约 3.8 GiB 内存。
- unit：仅使用 `systemd-run --user --collect` 创建，不安装、enable 或保留持久 unit。
- Observer：只读 `observe --once`；两轮均写入 `/tmp` 独立审计文件。
- “失败”由 wrapper 自然 `exit 1` 产生，不发送 SIGKILL/SIGTERM，不杀进程；systemd 自己负责按 `Restart=on-failure` 再启动。
- 禁止动作：不执行 Docker stop/restart/kill，不修改 cgroup/资源上限，不连接生产；EXP-039 主进程必须前后存活。

## 3. 预定步骤

1. 记录 EXP-039 主进程和 VM 状态。
2. 创建 `Type=notify`、`WatchdogSec=30s`、`Restart=on-failure` transient unit。
3. wrapper 首轮运行 Observer 后自然返回 1，第二轮运行 Observer 后自然返回 0。
4. 只读轮询 unit 的 `NRestarts`、`ActiveState`、`Result`、readiness、审计条数和主 Observer 存活状态。
5. `--collect` 回收 transient unit，保存结构化结果。

## 4. 验收口径

- `NRestarts=1`，最终结果 success；两轮均完成 readiness，审计记录可读。
- EXP-039 主进程未受影响；无 Docker/systemd 持久状态或资源变更。
- 该实验只证明自然退出后的 unit restart 机制，不证明 OOM kill、SIGKILL、真正 crash dump、持久 unit sandbox 或生产恢复。

## 5. 结果

| 检查 | 结果 |
| --- | --- |
| 首轮退出 | wrapper 在 Observer 完成一轮只读采样后自然返回 `status=1/FAILURE` |
| systemd 重启 | user journal 明确记录 `Scheduled restart job, restart counter is at 1` |
| 第二轮状态 | `MainPID=1125271`、`NRestarts=1`、`ExecMainStatus=0`，READY 后再次进入 active/running |
| readiness/审计 | readiness 为 `observe:ready`；两轮审计共 2 条、15,823 bytes |
| 最终 unit | transient unit 以 `Result=success` 结束并由 `--collect` 回收 |
| 隔离性 | EXP-039 PID `1076273` 仍存活，未发生 Docker 或资源变更 |

结构化摘要见 [`data/verification.json`](data/verification.json)。

结论：systemd 的 `Restart=on-failure` 能在本地 transient user manager 中承接一次自然非零退出，并重新启动 Observer、重新收到 readiness，最终以 success 结束。该结果支持 PG-P0-07 的“自然崩溃/退出重启”子项。

## 6. 限制

- 失败由 wrapper 自然返回非零码模拟；没有发送 SIGKILL/SIGTERM，没有触发 watchdog 超时或 OOM kill。
- 仅验证 transient user manager；未验证持久 unit、非 root `guardian` 用户、完整 sandbox、Docker 只读代理、磁盘满恢复或生产部署。
- 第二轮只读 Observer 仍在短窗口内因历史不足保持 fail-closed；这不是业务恢复证明。

## 7. 更新记录

- 2026-09-20：完成一次自然失败→systemd 重启→readiness 恢复→success 回收的本地 transient 验证。
