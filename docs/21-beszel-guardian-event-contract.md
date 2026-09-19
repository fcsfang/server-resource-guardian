# Beszel → Guardian 事件契约

更新时间：2026-09-19

状态：DRAFT-FROZEN-FOR-LOCAL-POC

本文件冻结 Goal 6 的第一版本地联调边界。它描述 Beszel 观测事件如何进入 Guardian，不代表 Beszel 0.19.0 已经提供完全一致的原始 payload；实际 Adapter 接入前必须用脱敏实测 payload 补齐字段映射。

## 1. 设计原则

- Beszel 是观测、历史、可视化和常规告警来源；事件不得直接获得动作授权。
- Guardian 重新执行本机安全核验，独立判断对象身份、保护状态、风险持续时间和允许动作。
- Hub/Agent 不可用、事件过期、对象身份不明确、字段不完整时，默认 fail-closed，只能记录或升级人工。
- 事件不得包含管理员密码、Agent Token、SSH Key、完整环境变量或生产原始日志。
- 所有时间使用 UTC ISO 8601；事件接收时间与 Beszel 观测时间必须分开保存。

## 2. 事件类型

当前只定义三类输入：

| source_kind | 含义 | 本地动作权限 |
| --- | --- | --- |
| hub_snapshot | Hub/API 查询到的系统或容器观测快照 | 只读 |
| hub_alert | Hub 产生的阈值/状态告警 | 只读，需 Guardian 二次确认 |
| agent_probe | Agent 或本机采集链路提供的辅助状态 | 只读 |

事件统一使用 schema：guardian.beszel.v1。

## 3. 最小事件结构

~~~json
{
  "schema": "guardian.beszel.v1",
  "event_id": "beszel:<source-record-id>:<observed-at>:<signal>",
  "source": {
    "kind": "hub_alert",
    "hub_id": "local-poc",
    "system_id": "beszel-system-id",
    "record_id": "source-record-id"
  },
  "observed_at": "2026-09-19T14:00:00Z",
  "received_at": "2026-09-19T14:00:02Z",
  "expires_at": "2026-09-19T14:00:32Z",
  "system": {
    "name": "guardian-ubuntu",
    "host": "local-vm",
    "architecture": "aarch64"
  },
  "object": {
    "kind": "container",
    "stable_id": "docker-container-id",
    "name": "discardable-test-target",
    "identity_source": "docker_id",
    "identity_confidence": "high"
  },
  "signal": {
    "resource": "memory",
    "metric": "available_ratio_percent",
    "value": 8.2,
    "unit": "percent",
    "severity": "critical",
    "reason_codes": ["host_memory_available_critical"]
  },
  "evidence": {
    "source_url": "http://local-hub/api/...",
    "snapshot_ref": "local-audit/2026-09-19T14:00:00Z.json"
  },
  "integrity": {
    "stale": false,
    "raw_payload_digest": "sha256:...",
    "redacted": true
  }
}
~~~

evidence.source_url 只允许保存脱敏后的本地相对引用或不含凭据的 URL；示例中的省略号不是可直接调用的地址。

## 4. 必填与校验规则

### 4.1 事件级

- schema 必须精确为 guardian.beszel.v1。
- event_id 必须非空；相同 event_id 在去重窗口内只能处理一次。
- observed_at、received_at 必须存在且可解析；received_at 早于 observed_at 时拒绝。
- expires_at 缺失时按 Adapter 默认 TTL 计算；超过 TTL 后标记 stale=true，不得进入 enforce。
- integrity.redacted 不为 true 时只允许进入本地隔离日志，不得进入事件总线。

### 4.2 对象级

- kind 当前只支持 container、systemd_unit、host。
- container 必须有稳定 Docker ID；仅有名称时 identity_confidence=low，只能 observe/simulate。
- 多个候选对象、对象 ID 变化或对象归属无法确认时，Guardian 输出 ambiguous_object_identity，不得执行动作。
- systemd_unit 必须保留完整 unit 名称，例如 docker.service；展示层是否去掉后缀不能改变策略 ID。

### 4.3 信号级

- resource 当前允许 cpu、memory、swap、disk、io、network、load、status。
- severity 只允许 normal、warning、critical、recovered。
- Beszel 的阈值告警只作为触发线索；Guardian 必须结合本机信号、连续窗口和对象策略重新评估。

## 5. 去重、过期和降级

| 条件 | Guardian 结果 |
| --- | --- |
| 同一 event_id 重复 | 写入 duplicate_event，不重复决策或动作 |
| 当前时间超过 expires_at | 写入 stale_event，只允许 observe |
| Hub/API 超时或 5xx | 写入 source_unavailable；本机 Guardian 继续独立 observe |
| 对象不存在 | 写入 object_disappeared，进入恢复/人工判断，不重建对象 |
| 多对象或无稳定 ID | 写入 ambiguous_object_identity，升级人工 |
| 保护名单命中 | 写入 protected_object，不执行动作 |

## 6. 与现有 Guardian 事件的边界

Adapter 输出的是“外部观测输入”，不能直接填充 Guardian 的 decision.action。Guardian 必须重新生成自己的事件和决策：

~~~text
Beszel event
  → schema / freshness / identity validation
  → local Guardian observation
  → risk evaluator
  → protection and allow-list policy
  → observe or simulate plan
  → separately authorized enforce
~~~

## 7. 未决项

- Beszel 0.19.0 实际 API/告警 payload 的字段名和分页方式，需在 G6-T03 使用脱敏 fixture 验证。
- Hub 告警到达方式（轮询、通知或 API）尚未选定；第一版优先使用可重放的只读 API/fixture。
- Beszel UI 是否承载 Guardian 自定义状态，留到 G6-T06 设计，不在 Adapter 中硬编码。
