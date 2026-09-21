# EXP-070：PG-P0-16B 本地耐久通知 outbox

- 实验 ID：`EXP-070`
- 日期：2026-09-21
- 关联任务：`PG-P0-16B`
- 状态：`PASSED_LOCAL`
- 代码/安全 gate：`PASS`
- 原始汇总：[`p016b-durable-outbox-v1.json`](data/p016b-durable-outbox-v1.json)
- SQLite 证据：同目录下 5 个 `notification-outbox-*.db` 文件

## 目的

在不连接任何真实通知渠道的前提下，把 P0-16A 的内存去重/死信边界升级为本地
SQLite WAL 耐久 outbox，验证入队先落盘、进程重启后的去重与待投递恢复、有限重试、
死信、限速 redrive、in-flight 中断处理和审计完整性。

## 实现

- 新增 [`guardian_notification_outbox.py`](../../src/guardian_notification_outbox.py)，
  版本化为 `guardian.notification.outbox.v1`。
- `notification_outbox`、dedup state、attempt log 和 hash-chain audit 均存入 SQLite
  WAL；`max_pending` 和 payload 字节数有界。
- 投递前状态为 `IN_FLIGHT`。若进程在 sink 调用附近崩溃，下一次启动将其转为
  `DEAD_LETTER/delivery_interrupted`，必须显式 redrive，不自动重试含歧义的投递。
- sink 仍是注入式 `FakeNotificationSink`；任何结果都固定为
  `execution=not_executed`、`action_authorization=unchanged`。

## 场景与结果

运行命令：

```bash
python3 scripts/run_p016_durable_outbox.py \
  --output experiments/EXP-070-2026-09-21-p016b-durable-outbox/data/p016b-durable-outbox-v1.json
```

8 个 code-gate 场景全部通过：

| 场景 | 结果 |
| --- | --- |
| 入队先落盘、重启后投递 | 通过，重启时先看到 `PENDING` |
| 重启后去重 | 通过，同一通知只投递一次 |
| warning/critical/recovered/恢复后新 warning | 通过 |
| sink 失败、持久死信、显式 redrive | 通过 |
| in-flight 中断、重启 reconciliation、显式 redrive | 通过 |
| 限速死信与窗口后 redrive | 通过 |
| outbox 容量和动作边界拒绝 | 通过 |
| 审计 hash chain 篡改检测 | 通过 |

汇总为 `code_gate=PASS`、`status=PASSED_LOCAL`；主流程、失败重放、in-flight
重放和限速重放均最终 `DELIVERED`，执行动作数为 0，全部 audit 保留
`action_authorization=unchanged`。

## 后续合同加固

在本地 fixture 完成后，通知合同与 durable outbox 共用严格的有界整数和有限正时长校验，
明确拒绝 `NaN/Infinity`、bool、分数值和溢出时长；`drain_pending`/`redrive_dead_letters`
的 `max_items` 也保持同样的 fail-closed 边界。新增 2 个负向测试并纳入仓库统一检查，
不改变上述 8 个场景、fake sink、执行动作 0 和真实渠道未连接的结论。

## 结论与边界

P0-16B 的本地 fake sink 耐久队列、死信、去重、重启恢复、显式 redrive 和审计完整性
已完成本地合同验证。它不证明真实通知渠道可用，不读取凭据、不发送真实消息，也不
包含 Beszel 原生通知/公司渠道 ADR、owner 收件确认、签名/凭据管理或生产部署；这些
仍属于 P0-16C/外部授权，并且父任务继续保持 `IN_PROGRESS`。
