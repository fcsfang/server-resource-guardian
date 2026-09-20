# EXP-050：watchdog 通知状态可审计与失败熔断

- 实验 ID：`EXP-050`
- 日期：2026-09-20
- 关联目标：Goal 7 / `PG-P0-07`
- 状态：`PASSED`（通知状态分类、失败熔断和当前代码 VM smoke；未触发真实 watchdog 超时）
- 实验负责人：当前 Agent

## 1. 目的

验证 Observer 不再静默丢弃 systemd watchdog 通知结果：没有 `NOTIFY_SOCKET` 时明确记录 `not_configured`；通知成功时记录 `sent`；socket 已配置但发送失败时记录 `failed`，并将当前事件保持为不可执行的 fail-closed 状态。

## 2. 环境与安全边界

- 主机：macOS，本地 Python 标准库测试。
- VM：`guardian-ubuntu`，只在 `/tmp/guardian-exp050` 运行测试代码。
- 范围：通知状态分类和 Observer 事件构造；不安装或启用持久 unit。
- 禁止动作：不触发真实 watchdog 超时，不发送 SIGKILL，不停止/重启进程或容器，不修改资源限制，不连接生产；不触碰 EXP-039。

## 3. 实现

新增 `systemd_notification_status()`，将 systemd 通知结果区分为 `sent`、`failed` 和 `not_configured`。Observer 在写快照/审计前记录 watchdog 状态；当 socket 已配置但发送失败时，事件追加 `watchdog_notify_failed`，动作改为 `escalate`、执行改为 `not_executed`。没有 systemd socket 的普通本地运行保持 `not_configured`，不把测试环境误判为故障。

## 4. 验证

1. 主机 `tests.test_guardian_runtime` + `tests.test_guardian_observer`：`28/28` 通过。
2. 主机完整回归：`132/132` 通过；`compileall` 退出码 `0`。
3. Multipass 临时目录回归：`28/28` 通过。第一次运行因临时目录漏拷贝 unit/slice 模板失败，补齐模板后同一代码重跑通过；夹具错误保留且不计入通过统计。
4. `systemd-analyze verify`：退出码 `0`；仅有 VM 的 netplan 权限和系统 snapd `RestartMode` 无关警告。
5. 当前代码 VM `observe --once`：`watchdog_status=not_configured`、`audit_status=written`、`action=none`、`execution=not_applicable`，审计 7,879 bytes。

6. 负向单元测试确认：socket 已配置但通知失败会被审计，并阻止执行；无 socket 时不升级为失败。

## 5. 安全边界与限制

- 全部验证均在本机和 `guardian-ubuntu` 临时目录完成；没有安装、enable 或 start 持久 unit。
- 没有触发真实 watchdog 超时、SIGKILL、OOM、Docker 动作或资源变更；没有触碰 EXP-039。
- `not_configured` smoke 只证明无 systemd 环境不会误报；`failed` 由注入的通知返回值负向测试证明，不等同于真实 systemd socket 故障恢复。
- 该实验不改变 PG-P0-07 的 24 小时 soak、磁盘满、真实 watchdog 超时/SIGKILL/OOM 恢复和最终 P99 完成门。

结构化结果见 [`data/verification.json`](data/verification.json)。

## 6. 更新记录

- 2026-09-20：建立实验并加入通知状态分类与 fail-closed 事件门禁。
- 2026-09-20：Multipass 首次夹具漏拷贝 unit/slice，补齐后 `28/28` 通过；失败输出保留为夹具问题。
- 2026-09-20：当前代码 VM smoke 和 systemd 静态校验通过；未触发真实 watchdog 超时。
