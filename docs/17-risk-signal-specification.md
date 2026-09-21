# Guardian 三资源风险信号规范

更新时间：2026-09-20

状态：`ACTIVE`

## 1. 统一规则

- CPU、内存、磁盘容量/inode、磁盘 I/O 使用独立输入、阈值、dwell、恢复和 reason code。
- 单点高值不产生可执行计划；至少需要持续性和第二类支持证据，硬事件除外。
- 每个样本带 monotonic time、wall time、source、quality、新鲜度和配置 digest。
- `missing`、`not_supported`、`stale`、`error` 与有效的零值分开；核心信号缺失进入 `degraded_observability`。
- 阈值是版本化配置，通过目标环境基线校准，不在代码或文档中声明通用生产数字。

## 2. 信号

| 通道 | 宿主信号 | 对象信号 | 硬边界 |
| --- | --- | --- | --- |
| CPU | `/proc/stat`、loadavg、CPU PSI、调度探针、持续时间 | cgroup `cpu.stat`、`cpu.max`、对象 PSI、throttling | 100% 利用率本身不是故障 |
| 内存 | MemAvailable、趋势、time-to-threshold、memory PSI、swap、root OOM 增量 | `memory.current/stat/events`、增量和宿主下降贡献 | OOM 是硬事件/滞后证据，不是提前预测 |
| 容量/inode | mount identity、free bytes/inodes、下降斜率、time-to-full、只读/写错误 | writer ownership/配额/已登记目录写速率 | 没有所有权证据不能归因 |
| I/O | I/O PSI、diskstats、设备利用率/延迟/队列/错误 | cgroup `io.stat` 增量、device mapping | 容量与 I/O 不共用阈值；writeback 可使归因失真 |

## 3. 状态机

```text
STARTING -> NORMAL -> WATCHING -> WARNING -> CRITICAL_CONFIRMED
CRITICAL_CONFIRMED -> PLAN_ONLY | AWAITING_AUTHORIZATION
ACTIONING -> VERIFYING -> MITIGATED -> BUSINESS_RECOVERED | BUSINESS_DEGRADED
Any -> DEGRADED_OBSERVABILITY | ESCALATED | CIRCUIT_OPEN
Recovered + cooldown -> NORMAL
```

进程启动先建立 counter baseline；不得从 STARTING 直接执行动作。enter/exit 阈值和最小停留分开，采样 gap 超限时旧窗口失效。

## 4. 混合风险仲裁

联合事件包含各通道独立结果，不把多个资源折成一个不可解释分数：

1. 任一 critical 可产生管理员告警。
2. 只有同一稳定对象在至少一个通道确认且其余 critical 通道不指向冲突对象，才允许形成单目标计划。
3. 多个资源指向不同对象、候选接近或任一关键证据过期时，输出 `MULTI_RESOURCE_AMBIGUOUS`，不执行动作。
4. 动作后每个 critical 通道分别验证恢复；不能用内存改善证明 CPU/I/O 恢复。

## 5. 事件结构

```json
{
  "event_id": "...",
  "observed_at": "...",
  "observed_monotonic_ns": 0,
  "state": "warning|critical|recovered|escalated",
  "resource_evaluations": {
    "cpu": {},
    "memory": {},
    "disk_capacity": {},
    "io": {}
  },
  "target_attribution": {},
  "decision": {"mode": "observe", "action": "none", "execution": "not_executed"},
  "quality_flags": [],
  "config_digest": "..."
}
```

## 6. 当前证据与缺口

- 内存：EXP-030/031 完成本地组合风险和容器/cgroup 归因。
- CPU：EXP-055 完成本地 observe/simulate、调度探针和 cgroup 归因。
- 磁盘：EXP-057 完成本地容量/inode 与 I/O fixture；容量 writer ownership 仍默认放弃。
- 联合仲裁代码已实现并通过同目标合并、冲突放弃、缺失归因和降级观测的负向测试；P0-14 证据包的本地代码/安全门禁已通过，EXP-062 已补部分同会话长窗口恢复，但真实 Docker、对象 churn、memory/I/O 稳定恢复、业务 health 和相关效果指标仍未完成；P0-16A 已有本地 fake sink 通知合同，P0-16B 耐久投递、P0-16C 真实通知渠道、x86_64 长期校准和生产 observe/simulate 仍未完成。
