# Guardian 持久状态、授权与崩溃恢复

## 1. 解决的问题

旧版 Guardian 的冷却和失败熔断主要在内存对象或 JSON ledger 中。进程崩溃、两个执行进程并发或动作前后审计写失败时，无法可靠回答：

- 这个一次性授权是否已经消费？
- 这个对象/动作是否已有执行意图？
- 动作是否已经进入执行阶段但尚未写回结果？
- 本机是否仍处于冷却或连续失败熔断状态？

这些状态保存在 [`src/guardian_state.py`](../src/guardian_state.py) 的 SQLite WAL 本地状态库中。它是动作前安全门，不是动作执行器。

## 2. 数据模型

| 表 | 作用 | 关键约束 |
| --- | --- | --- |
| `capabilities` | 短时授权登记和一次性消费 | `approval_id` 唯一；消费使用 `BEGIN IMMEDIATE`；过期、重复和字段不一致拒绝 |
| `intents` | `host/object/action/event` 级意图 | `idempotency_key` 唯一；活动意图阻止并发第二次执行 |
| `audit_records` | 授权、意图、执行开始、结果和恢复审计 | 意图写入与审计写入在同一事务；审计 payload 无法序列化时事务回滚 |
| `action_ledger` | 持久冷却、动作次数和连续失败 | host/object/action 复合主键；失败熔断跨进程保留 |

SQLite 连接启用 WAL、外键和 5 秒 busy timeout；关键 claim/consume/reconcile 操作使用 `BEGIN IMMEDIATE` 串行化。

## 3. 状态机与崩溃语义

```text
INTENT_RECORDED → EXECUTION_STARTED → SUCCEEDED / FAILED / PLANNED
       │                    │
       └────进程崩溃─────────┘
                    ↓
        RECONCILIATION_REQUIRED
```

`reconcile_pending()` 只把未完成意图标记为人工核对状态，不自动重试动作。这样在以下三个崩溃点都不会静默重复动作：

1. intent 已写入、执行器尚未调用；
2. execution started 已写入、执行器结果尚未返回；
3. 执行器可能已改变运行态、结果尚未落库。

真实 Docker executor 现在必须同时提供持久 `--ledger-file` 和 `--state-db`；缺少任一项在调用执行器前拒绝。`mock` 仍用于纯计划测试。

## 4. 安全边界

- Production capability issuance, key protection, and multi-party approval are not defined.
- SQLite is local to one host; it is not a multi-host coordinator.
- A stored intent or audit record does not prove a workload or business service recovered.

## 5. 恢复

不传 `--state-db` 时，`mock` 计划路径仍可运行；真实 Docker executor 会安全拒绝启动。删除或移走状态库不会触发动作，但会丢失冷却/意图历史，因此生产不得用删除数据库作为恢复手段。
