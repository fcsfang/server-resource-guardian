# Beszel × Guardian UI 集成设计

更新时间：2026-09-20

状态：`DESIGN-READY-FOR-REVIEW`

关联里程碑：里程碑二和里程碑三

本设计描述如何在 Beszel 现有监控和告警页面旁边呈现 Guardian 的风险判断与受控处置状态。第一版只定义接口、页面信息层级和安全边界，不直接修改 Beszel 上游核心代码，也不把 UI 按钮当作动作授权。

## 1. 设计目标与非目标

### 目标

- 让使用者在 Beszel 的主机/容器上下文中同时看到 Guardian 的风险等级、证据、对象判断和当前处置状态。
- 明确区分“Beszel 观测到了什么”“Guardian 本机重新确认了什么”“策略允许计划什么”“动作实际发生了什么”。
- 在 Hub、Agent、Adapter 或本机数据不完整时展示降级状态，而不是把空白解释为安全。
- 让人工可以复核和确认计划，但保留现有 `observe → simulate → enforce` 安全门禁。

### 非目标

- 不在 UI 中统一设置 Docker 内存上限。
- 不让 Beszel 告警直接触发 Docker/systemd 操作。
- 当前不修改 Beszel 上游核心、现有告警规则或通知凭据。
- 不把本地 ARM64 的阈值和延迟写成生产承诺。

## 2. 信息来源和权威关系

```text
Beszel metrics / alerts_history
          │ read-only Adapter
          ▼
guardian.beszel.v1 external observation
          │ freshness + ordering + identity
          ▼
Guardian local observation and risk evaluator
          │ local object/policy confirmation
          ▼
UI view model: risk → target → plan → result → recovery
```

权威关系：

| 信息 | UI 展示 | 决策权 |
| --- | --- | --- |
| Beszel 指标、告警、历史 | 外部观测卡片、时间线 | 只作为证据和触发线索 |
| Guardian 本机信号 | 本机确认卡片、信号详情 | 风险状态和对象复核的主要输入 |
| 对象身份与保护状态 | 对象卡片、策略原因 | 决定是否允许生成动作计划 |
| `observe/simulate/enforce` | 模式标签、计划状态 | 由运行配置和授权门禁决定 |
| 动作结果与恢复验证 | 结果卡片、审计时间线 | 由控制层和恢复探测写入，UI 不推断 |

## 3. 页面结构

### 3.1 系统总览页

保留 Beszel 原有系统名称、在线状态和资源曲线，在右侧增加 Guardian 面板：

1. **当前风险**：`normal / warning / critical / recovered / escalated`，附状态进入时间和有效期。
2. **风险摘要**：内存可用率、增长速率、swap、memory PSI、OOM/cgroup 事件和 Beszel 信号的最近值。
3. **对象候选**：主机、容器、systemd unit 或进程组；展示稳定 ID、名称、身份来源和置信度。
4. **策略结论**：保护命中、动作白名单、冷却、熔断、未知对象或多对象歧义等原因码。
5. **处置状态**：当前模式、计划动作、人工确认状态、动作结果和恢复状态。

风险卡片必须在视觉上区别外部告警和本机确认：Beszel 告警使用“外部观测”标签，Guardian 判断使用“本机确认”标签。

### 3.2 对象详情页

对象详情按照“身份 → 证据 → 策略 → 计划 → 结果”顺序展示：

- 身份：稳定 ID、创建时间、image digest/标签、所属系统或 unit、最近一次确认时间。
- 证据：内存/CPU/I/O/PSI/cgroup/Docker 健康数据，显示采样窗口而非单点值。
- 策略：命中的保护规则、允许动作、策略版本、冷却剩余时间和拒绝原因。
- 计划：动作类型、目标 ID、生成时间、过期时间、生成来源和 `execution` 状态。
- 结果：返回码、状态变化、恢复探测、失败次数和人工升级原因。

### 3.3 事件时间线

时间线按 `observed_at`、`received_at`、`planned_at`、`executed_at`、`recovered_at` 分列，避免把告警到达时间误当成风险发生时间。每条记录至少显示：

- 事件 ID 和来源（Beszel/Guardian/Controller）；
- 风险等级和资源类型；
- 对象稳定 ID 及身份置信度；
- 关键原因码；
- 当前状态和下一步；
- 证据快照引用，不内嵌凭据、token、完整环境变量或生产原始日志。

## 4. UI View Model

UI 只消费脱敏后的 `guardian.ui.v1` 视图模型，不直接消费 Beszel 原始记录或 Docker API：

```json
{
  "schema": "guardian.ui.v1",
  "system": {"id": "beszel-system-id", "name": "guardian-ubuntu", "status": "up"},
  "risk": {
    "state": "critical",
    "confidence": "high",
    "entered_at": "2026-09-20T00:00:00Z",
    "expires_at": "2026-09-20T00:00:30Z",
    "signals": [
      {"source": "guardian", "resource": "memory", "metric": "available_ratio_percent", "value": 8.2, "unit": "%", "window_seconds": 30},
      {"source": "beszel", "resource": "memory", "metric": "Memory", "value": 92, "unit": "%", "window_seconds": 60}
    ]
  },
  "object": {"kind": "container", "stable_id": "docker-id", "display_name": "discardable-test-target", "identity_confidence": "high", "protected": false, "candidates": 1},
  "policy": {"mode": "simulate", "decision": "plan_generated", "reason_codes": ["host_memory_available_critical", "beszel_event_accepted", "simulate_only"], "allowed_actions": ["snapshot", "graceful_stop"], "policy_version": "local-poc-1"},
  "plan": {"action": "graceful_stop", "execution": "not_executed", "confirmation": "not_required_for_simulate", "created_at": "2026-09-20T00:00:02Z"},
  "result": null,
  "recovery": {"state": "pending", "checks": [], "cooldown_until": null}
}
```

字段约束：

- `risk.state` 必须来自 Guardian 状态机，不能由前端根据某个数值自行推断。
- `object.stable_id` 缺失或 `candidates != 1` 时，`policy.decision` 只能是 `observation_only` 或 `escalated`。
- `plan.execution` 只能由控制层更新；UI 不得把 `not_executed` 改成 `executed`。
- `result` 为空表示尚未执行，不表示执行成功。
- `recovery.state=verified` 必须有具体检查结果和时间戳。

## 5. 人工确认与动作门禁

### `observe`

- 显示风险、对象、策略原因和建议动作。
- 所有动作按钮隐藏或显示为“仅生成计划”。
- 不产生 Docker/systemd 变更。

### `simulate`

- 显示“模拟计划”徽标和 `execution=not_executed`。
- 可展开查看动作参数摘要，但不显示可复制的危险命令串。
- 不需要动作授权，也不调用真实执行器。

### `enforce`

- 只有授权文件、环境、对象保护检查、动作白名单、冷却和恢复策略全部通过时才显示可确认按钮。
- 二次确认必须明确显示目标稳定 ID、动作、策略版本、过期时间和失败升级路径。
- 确认后 UI 只提交一个不可变的计划 ID 给控制层；不直接调用 Docker/systemd。
- 任何保护对象、未知对象、多候选对象、过期事件、审计失败或 Hub/Adapter 不可用都隐藏确认按钮并显示人工升级。

当前不实现上述 `enforce` UI，只冻结状态和接口语义；真实动作属于里程碑三，并且仍需独立授权。

## 6. 降级和错误展示

| 状态 | UI 表现 | 动作边界 |
| --- | --- | --- |
| Beszel Hub 不可用 | 外部观测显示 `source_unavailable` 和最近成功时间 | Guardian 可继续本机 observe；不使用过期外部告警 |
| Adapter 认证失败/字段不全 | 显示 `observation_only`，列出脱敏原因码 | 不生成动作计划 |
| 事件 stale/duplicate/out-of-order | 时间线保留拒绝记录 | 不重复决策或动作 |
| 对象消失 | 显示 `object_disappeared` | 不重建、不强杀、不重启 |
| 多对象/身份不明 | 显示 `ambiguous_object_identity` | 只能升级人工 |
| Guardian 本机采样中断 | 显示 `local_observation_unavailable` | 不把 Beszel 告警升级为动作 |
| 动作失败/恢复超时 | 显示 `escalated` 和失败次数 | 熔断后停止继续升级 |

空白、零值和无数据必须有不同视觉状态，避免把采集缺失误解成“资源正常”。

## 7. 推荐落地方式

第一阶段采用旁路集成：Guardian 提供一个只读 view-model endpoint 或静态 JSONL/事件流，Beszel 原页面不改；通过系统详情页入口或独立侧栏打开 Guardian 面板。这样可以先验证数据模型、审计和降级表现，再决定是否开发 Beszel 插件/上游 UI。

不建议在当前阶段把 Guardian 字段直接写入 Beszel 的 `systems.info` 或修改 Beszel 告警核心，原因是：

- 会混淆 Beszel 采集事实与 Guardian 策略判断；
- 上游升级时容易丢失自定义字段和安全边界；
- 真实动作授权不应依赖展示层数据；
- 当前 ARM64 PoC 还没有生产字段、权限和 x86_64 兼容性结论。

## 8. 验收清单

- [x] 风险等级、证据来源和时间字段有明确分层。
- [x] 对象、保护状态、策略原因和动作计划有稳定字段。
- [x] 动作结果与恢复验证不由 UI 推断。
- [x] `observe/simulate/enforce` 的展示和授权边界明确。
- [x] Hub/Adapter/本机采样/对象身份/动作失败的降级状态明确。
- [x] 不修改 Beszel 上游核心，不读取或保存通知凭据。
- [x] `guardian_ui_model.py` 已用 5 个纯函数测试验证冻结字段、模拟计划、拒绝事件、多对象和缺失身份降级；宿主机与 Multipass 全量测试均通过。
- [x] 已形成离线静态展示页 [demo/guardian-beszel-review](../demo/guardian-beszel-review/index.html)，覆盖效果对照、Beszel/Guardian 分工和现场回放；页面不连接 Hub、不读取凭据、不调用执行器。
- [ ] 由 leader 或项目协作者评审字段、页面入口和人工确认流程。
- [ ] 在里程碑二实现旁路只读 view-model endpoint；在此之前不开发 UI 动作入口。

## 9. 与当前证据的对应关系

- Beszel 字段和 `alerts_history`：[`docs/22-beszel-dashboard-field-inventory.md`](22-beszel-dashboard-field-inventory.md)、EXP-028。
- 事件 schema、身份和过期规则：[`docs/21-beszel-guardian-event-contract.md`](21-beszel-guardian-event-contract.md)。
- 本机风险状态：[`docs/17-risk-signal-specification.md`](17-risk-signal-specification.md)。
- 对象保护和动作门禁：[`docs/18-object-policy-specification.md`](18-object-policy-specification.md)、[`docs/19-action-adapter-contract.md`](19-action-adapter-contract.md)。
