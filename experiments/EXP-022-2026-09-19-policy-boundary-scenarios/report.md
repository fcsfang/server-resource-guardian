# Guardian 策略边界补充报告

实验：EXP-022
日期：2026-09-19

## 结论

EXP-022 补充证明 Guardian 的安全边界有效：它不会在多对象场景中盲选，不会处置保护对象，不会把普通 CPU/IO 忙碌误判成内存危机；恢复失败会升级并触发 failure breaker。

| 场景 | 结果 | 是否执行动作 |
| --- | --- | --- |
| 2 个风险对象同时存在 | `ambiguous_object_identity` | 否 |
| 1 个受保护风险对象 | `protected_object` | 否 |
| CPU/IO 压力但无内存风险 | `normal / none` | 否 |
| 恢复失败连续发生 | `failed` → `failure_breaker_tripped` | 否，纯 fixture |

## 与 EXP-021 的关系

EXP-021 证明 Guardian 在明确唯一对象时能够止损；EXP-022 证明 Guardian 在对象不明确、对象受保护或恢复不可信时会拒绝动作。两者共同构成“能处理，也知道什么时候不能处理”的证据。

本实验没有调用真实 `enforce`，因此不改变 EXP-021 的真实动作证据，也没有新增生产授权需求。

详细记录见 [`record.md`](record.md)，原始数据见 [`data/`](data/)。
