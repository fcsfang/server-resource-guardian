# EXP-061：管理员通知 fake sink 合同闭环

- 实验 ID：`EXP-061`
- 状态：`PASSED`（仅限本地 fake sink 合同）
- 日期：2026-09-20
- 关联 Goal：`Goal 7 / PG-P0-16`
- 目的：在不连接任何真实通知渠道的前提下，验证 CPU、内存、容量/inode、I/O 和混合风险的版本化通知 payload、触发/恢复、去重、乱序/过期拒绝、限速、重试、死信、redrive、投递审计和动作授权不变。

## 1. 安全边界

- 只使用 Python 内存 `FakeNotificationSink`；不打开网络、不读取 token/URL/签名、不修改 Beszel、不发送真实消息。
- 输入事件为脱敏 synthetic Guardian event；固定 target ID 只能作为本地 hash 输入，不进入 payload。
- Dispatcher 没有 Docker、systemd、shell 或 action adapter 引用；所有结果固定为 `execution=not_executed`。
- 失败、限速和过期结果必须保留审计/死信；不得通过重试改变动作授权。

## 2. 预置验收条件

1. `guardian.notification.v1` 能表示四类资源和 mixed 事件的 warning/critical/escalated/recovered。
2. 同一事故键的同一状态只投递一次；恢复通知复用事故键但单独送达。
3. 乱序/过期拒绝；sink 不可用时有界重试并进入死信；sink 恢复后显式 redrive 不丢消息。
4. 限速命中进入死信；投递审计使用 `guardian.notification.delivery-audit.v1`。
5. payload 不含 raw signals、凭据或完整 target ID；通知成功/失败均不扩大动作权限。

## 3. 原始数据

运行脚本后写入 [`data/notification-fake-sink-v1.json`](data/notification-fake-sink-v1.json)。单元测试见 `tests/test_guardian_notifications.py`。

## 4. 实际结果

- 覆盖资源：`cpu`、`memory`、`disk_capacity`、`io` 和 mixed；状态覆盖 `warning`、`critical`、`recovered`、`escalated`。
- 结果计数：`delivered=16`、`duplicate_suppressed=4`、`rejected=2`（乱序/过期）、`rate_limited=1`、`dead_letter=1`；失败通知显式 redrive 后 `dead_letters_after_redrive=0`，redrive 结果为 `delivered`。
- 投递审计：`19` 条本地审计；所有审计保留 `action_authorization=unchanged`，所有结果保留 `execution=not_executed`。
- 安全字段：`fake_sink_only=true`、`network_connected=false`、`production_connected=false`、`action_adapter_invoked=false`、`credentials_read=false`。
- payload 只包含 hash 后的 host/target 引用、资源状态和有限 reason/quality 字段；不包含原始 signals、命令、凭据或完整 target ID。

## 5. 结论

本实验的本地 fake sink 合同通过，状态为 `PASSED`（仅限本地合同）：真实通知渠道、owner、凭据/签名管理、持久化队列、跨进程重放和真实值班接收端尚未授权或验证。该实验不改变任何动作权限，也不代表真实通知已送达。

## 6. 边界

本实验只证明本地事件/投递合同和 fake sink 行为，不证明公司通知渠道送达、凭据管理、持久化队列、跨进程重放或生产值班流程。
