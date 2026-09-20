# EXP-040：systemd transient `Type=notify` readiness smoke

- 实验 ID：`EXP-040`
- 日期：2026-09-20
- 关联目标：Goal 7 / `PG-P0-07`
- 状态：`PASSED`
- 实验负责人：当前 Agent

## 1. 实验目的

在 Multipass Ubuntu 22.04 的真实 systemd 249 user manager 中，临时启动一次只读 Observer，验证 `Type=notify` 能收到 Guardian 的 `READY=1`，服务能正常退出，transient unit 能被回收；不把 transient smoke 扩大为持久部署或真实动作授权。

## 2. 环境与边界

- VM：`guardian-ubuntu`，Ubuntu 22.04.5 LTS ARM64，systemd 249，Docker 29.1.3。
- unit：`guardian-observer-transient-smoke.service`，使用 `systemd-run --user --collect --wait --pipe` 创建，运行一次 `--once` Observer。
- 文件：配置、readiness 和审计均位于 `/tmp/guardian-p0-03-check`。
- 未使用 root/sudo；未安装、enable 或修改持久 unit；未执行 Docker stop/restart/kill 或资源变更。
- 24 小时 soak 的 Observer PID `1076273` 在 smoke 前后均保持存活。

## 3. 结果

| 检查 | 结果 |
| --- | --- |
| `systemd-run` 返回 | `0` |
| systemd unit 结果 | `success`，服务运行约 2.121 秒，CPU 53 ms |
| readiness 文件 | `observe:ready` |
| 审计记录 | 1 条 |
| 回收后状态 | `ActiveState=inactive`、`Result=success`、`LoadState=not-found` |
| 持久化 systemd 状态 | 未安装、未 enable、未保留 |

结构化摘要见 [`data/verification.json`](data/verification.json)。

## 4. 结论与限制

本实验证明当前 runtime notify/readiness 适配能在真实 systemd manager 中完成一次启动通知和安全退出，transient unit 回收也符合预期。

本实验没有验证 watchdog 超时后的 manager 重启、崩溃重启、非 root `guardian` 服务用户、Docker 组权限和持久 unit 的完整 sandbox 组合；这些仍需在不改变生产状态的本地验证或安装授权窗口中单独验证。PG-P0-07 仍保持 `IN_PROGRESS`。

## 5. 更新记录

- 2026-09-20：完成一次真实 user-manager transient `Type=notify` smoke；readiness 和回收通过，24 小时 Observer soak 未被中断。
