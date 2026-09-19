# EXP-022：多对象、保护名单、误报与恢复失败边界

- 实验 ID：`EXP-022`
- 状态：`PASSED`
- 创建日期：2026-09-19
- 最近更新：2026-09-19
- 关联 Goal：`Goal 5 / G5-T03~G5-T06`
- 环境：Mac Multipass `guardian-ubuntu`，Ubuntu 22.04.5 ARM64，2 vCPU、4GB、无 swap、Docker 29.1.3。

## 1. 实验目的

补充验证真实故障预防之外的安全边界：

1. 多个对象同时处于风险时，Guardian 是否拒绝盲选对象。
2. 命中保护策略时，Guardian 是否拒绝自动动作。
3. CPU/IO 忙但没有内存危机时，是否出现误报或破坏性动作。
4. 恢复失败时，是否进入失败状态并触发 failure breaker。

## 2. 授权和停止条件

- 只使用本地 disposable BusyBox 容器和纯内存 fixture。
- 全部运行 `observe` 或 `simulate`；没有调用 `guardian_enforce`，没有 Docker 重启、停止、kill 或资源变更动作。
- 每个内存 fixture 只分配 160MiB，两个同时运行总量有界；CPU fixture 限制为 0.5 CPU；IO fixture 只写入 16MiB。
- 每个场景结束立即清理实验容器；最终运行中容器为 `0`，虚拟机 systemd 为 `running`。

## 3. 场景和结果

| 场景 | 输入 | 期望 | 实测结果 |
| --- | --- | --- | --- |
| 多对象 simulate | 2 个同时存在的内存 fixture，合成 critical | 不选对象，升级歧义 | `critical / escalate / not_executed`，原因 `ambiguous_object_identity`，候选数 2 |
| protected object simulate | 1 个 critical fixture，默认 protected | 不执行动作 | `critical / escalate / not_executed`，原因 `protected_object`，候选数 1 |
| CPU/IO observe | 0.5 CPU burner + 16MiB 有界 IO | 正常状态，不产生动作 | `normal / none / not_applicable`，候选数 2 |
| recovery failure fixture | 纯 Python 执行器和 unhealthy recovery observation | 失败并熔断 | 前两次 `failed`，第三次 `failure_breaker_tripped` 升级 |

## 4. 结论

- Guardian 不会因为多个风险对象存在就随意选择一个对象执行动作。
- 保护对象默认保持人工升级，不会因为模拟风险就绕过保护策略。
- CPU/IO 压力在没有内存风险信号时不会触发破坏性动作。
- 恢复验证失败会被记录，并在连续失败后进入 failure breaker；本场景未调用真实执行器。

## 5. 证据和复现

- 汇总数据：[`data/summary.csv`](data/summary.csv)
- 多对象结果：[`data/multi-object-simulate.json`](data/multi-object-simulate.json)
- 保护对象结果：[`data/protected-object-simulate.json`](data/protected-object-simulate.json)
- 正常压力结果：[`data/normal-pressure-observe.json`](data/normal-pressure-observe.json)
- 恢复失败 fixture：[`data/recovery-failure-fixture.json`](data/recovery-failure-fixture.json)
- 可复现实验脚本：[`scripts/run-policy-boundary-scenarios.sh`](../../scripts/run-policy-boundary-scenarios.sh)

## 6. 更新记录

- 2026-09-19：建立并完成多对象、保护策略、误报和恢复失败边界验证；所有场景均保持非变更模式。
