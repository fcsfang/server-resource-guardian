# Guardian 对象与策略规范

更新时间：2026-09-20

状态：`ACTIVE`

## 1. 决策公式

```text
action_eligible =
  risk_confirmed
  AND target_confirmed
  AND target in actionable_set
  AND target not in protected_set
  AND action in target.allowed_actions
  AND identity_unchanged
  AND capability_valid
  AND audit_and_state_writable
  AND circuit_closed
```

任何一项为 false/unknown 都不得执行。

## 2. 对象类型与身份

| 类型 | 最低稳定身份 | 自动边界 |
| --- | --- | --- |
| Docker 容器 | full ID、image digest、created time、labels、cgroup path/inode | v2 首个可评估对象 |
| systemd unit | 完整 unit、cgroup path、主进程 starttime、配置版本 | 只做 observe/simulate，是否 stop 待 owner 明确 |
| cgroup | 规范路径/inode、owner、父层级、控制器 | 只用于归因；不对任意 cgroup 动作 |
| 裸进程 | PID、starttime、exe、父/cgroup | 只用于诊断；永久不自动 kill |
| 主机 | host ID、boot ID、环境 | 只告警/救援；不自动重启 |

## 3. `protected_set`

永久保护包含 Guardian/Broker、PID 1、SSH/认证/网络/DNS、Docker/containerd、日志审计、Beszel、文件系统/挂载关键服务。业务保护包含数据库、队列、quorum、唯一副本、有状态任务和 owner 指定对象。

每条记录必须有 owner、原因、环境、创建/复核/失效时间和策略版本。

## 4. `actionable_set`

每条记录至少包含：

- stable selector 与期望身份字段；
- owner、业务重要级、状态/副本语义；
- 允许资源类型和允许动作；
- grace timeout、最大频率、冷却、失效日期；
- host mitigation 与 business probe 合同；
- 回滚、值班和升级联系人。

空集合表示没有任何对象可自动处置。禁止 wildcard “all non-protected”、短 ID、容器名、进程名或 Top N 规则。

## 5. 目标选择

先资格过滤，再按资源贡献、时间相关性、置信度和领先幅度排名。只有第一名达到 `min_confidence`、`min_contribution` 且领先第二名 `min_margin` 才确认。多资源事件最多选择一个共同目标；证据冲突时放弃。

## 6. 结果与原因码

- `TARGET_CONFIRMED`
- `NO_TARGET`
- `AMBIGUOUS_TARGET`
- `PROTECTED_TARGET`
- `NOT_ACTIONABLE`
- `IDENTITY_CHANGED`
- `DEGRADED_OBSERVABILITY`
- `MULTI_RESOURCE_AMBIGUOUS`

所有放弃结果必须包含可解释 reason codes 并进入审计/告警，不允许静默降级。

## 7. 当前实现

内存与 CPU/I/O cgroup 归因已有本地实现；容量 writer ownership 尚未建立，因此容量风险固定不可自动归因。SQLite 状态层已实现 capability、intent/result、冷却、熔断和 reconciliation。本规范的生产清单、业务 owner 和 systemd unit 动作权限仍需外部确认。
