# EXP-053：当前代码核心内存危机 `observe/simulate` 门禁复核

- 实验 ID：`EXP-053`
- 日期：2026-09-20
- 关联目标：Goal 7 / 核心内存危机有效性复核（PG-P0-07 → PG-P0-08 前置）
- 状态：`PASSED`（仅指当前代码的只读/模拟决策门；不等同于真实动作有效性通过）
- 实验负责人：当前 Agent

## 1. 目的

响应“核心问题优先”的调整，在不等待 24 小时 soak、也不执行新的 Docker 变更动作的前提下，复核当前代码是否能把内存危机正确转换为：

```text
critical 风险 → 唯一对象判断 → graceful_stop 模拟计划 → not_executed
```

同时复核多对象、保护对象和归因未确认时是否保持 fail-closed。

## 2. 环境与安全边界

- 宿主：macOS 工作区，当前仓库代码。
- 执行内容：Python 纯 fixture、当前单元测试和 JSON 决策输出。
- 未连接生产或外部主机；未读取凭据。
- 未创建、停止、重启或 kill 容器；未修改 Docker/systemd/资源限制。
- `simulate` 的 `graceful_stop` 只生成计划，必须保持 `execution=not_executed`。

## 3. 验收条件

1. 单一、未保护、稳定身份对象在 critical 条件下生成 `graceful_stop` 模拟计划。
2. 多对象竞争、保护对象和归因未确认均输出 `escalate`，不执行动作。
3. 所有场景的执行字段都必须是 `not_executed`。
4. 当前仓库全量回归通过。

## 4. 执行结果

### 4.1 当前代码 fixture 输出

| 场景 | 风险 | action | execution | 结果 |
| --- | --- | --- | --- | --- |
| 单一未保护对象 | `critical` | `graceful_stop` | `not_executed` | 生成模拟止损计划 |
| 两个竞争对象 | `critical` | `escalate` | `not_executed` | `ambiguous_object_identity` |
| 保护对象 | `critical` | `escalate` | `not_executed` | `protected_object` |
| 归因未确认 | `critical` | `escalate` | `not_executed` | `object_attribution_not_confirmed` |

单一对象场景同时观察到 `cgroup_memory_oom_event` 和 `host_memory_available_critical`，说明事件具备明确的内存危机信号；本实验没有把历史 OOM 计数直接当作真实生产预测 SLA。

### 4.2 回归结果

- 当前仓库全量测试：`134/134` 通过。
- 核心模拟门禁定向测试：`4/4` 通过。
- `verification.json` JSON 校验和 `git diff --check`：通过。

## 5. 结论与限制

本实验确认当前代码的安全决策门没有回退：在唯一对象场景中能够生成受控 `graceful_stop` 计划，在不确定场景中能够放弃动作；没有任何 Docker 变更被调用。

本实验不能证明“Guardian 已经成功保护业务”，因为没有执行新的真实 `graceful_stop`，也没有制造新的无 Guardian/Guardian 故障对照。该结论仍以历史 [EXP-021](../EXP-021-2026-09-19-failure-prevention-comparison/report.md) 为主要真实效果证据，但 EXP-021 使用的是此前版本和特定 ARM64/单对象 fixture，不能直接替代当前生产化代码复测。

## 6. 下一步

先保留当前 `observe/simulate` 证据，随后准备同一可丢弃内存泄漏对象的两组复测：无 Guardian 组必须产生目标错误，Guardian 组必须在错误前止损并保住健康探针。Guardian 组新的真实 `graceful_stop` 仍需针对本次对象、环境和动作单独明确授权；未获授权前只继续 observe/simulate 和报告准备。

## 7. 更新记录

- 2026-09-20：完成当前代码核心内存危机模拟门禁复核；无外部连接、无容器/systemd 变更。
