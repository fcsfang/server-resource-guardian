# Guardian 风险信号规范（G4-T01）

更新时间：2026-09-19

> 本文定义 Guardian 的第一版风险信号接口和判定原则。数值阈值是待本地实验校准的配置，不是生产阈值。

## 1. 设计原则

- 以宿主机内存风险为 P0 主线；历史实验表明 CPU 饱和通常不会直接导致 SSH 失效，宿主机内存耗尽才是整机崩溃的主要风险。
- 单点瞬时值不能直接触发处置；必须结合持续时间、增长趋势、压力指标或内核事件。
- “信号采集”和“动作决策”分离：信号只说明风险，策略层决定是否允许动作。
- 指标缺失、对象身份不稳定或时间窗口不完整时，风险可以升级告警，但默认不能升级为自动破坏性动作。
- 初始采样周期目标为 1–5 秒；实际检测时延和开销必须通过新的 EXP 实测。

## 2. 信号分层

### P0：宿主机内存风险

| 信号 | 来源 | 作用 | 备注 |
| --- | --- | --- | --- |
| 内存可用比例 | `/proc/meminfo` | 判断可分配余量 | 记录总量、可用量、缓存和回收变化 |
| 内存增长速率 | 连续采样计算 | 判断泄漏或突增 | 至少记录窗口长度、斜率和样本数 |
| swap 使用比例与增长速率 | `/proc/meminfo`、`swapon` | 判断是否进入换页退化 | swap 本身不是故障，需结合 PSI 和趋势 |
| memory PSI | `/proc/pressure/memory` | 判断任务等待内存的压力 | 保存 `some/full` 的 `avg10/avg60/avg300` |
| OOM/cgroup 事件增量 | cgroup v2 `memory.events`、内核事件 | 判断是否已发生硬风险 | 记录采样前后计数，不能只读当前布尔状态 |

### P1：对象定位与局部风险

| 信号 | 来源 | 作用 |
| --- | --- | --- |
| 容器内存使用量、限制和增长速率 | `docker stats`、Docker inspect | 排序候选肇事容器 |
| 容器 OOM、die、restart 事件 | Docker events | 关联对象生命周期和历史事件 |
| cgroup `memory.current`、`memory.events` | `/sys/fs/cgroup` | 避免只依赖 Docker 展示层 |
| 进程启动时间、PID、cgroup 路径 | `/proc`、cgroup 文件 | 防止 PID 重用导致误处理 |
| 容器健康状态和重启次数 | Docker inspect | 判断动作后是否恢复 |

### P2：辅助退化信号

| 信号 | 来源 | 作用 |
| --- | --- | --- |
| CPU 使用率、load、CPU PSI | `/proc/stat`、`/proc/loadavg`、`/proc/pressure/cpu` | 判断调度争用和响应退化 |
| I/O PSI、磁盘可用空间 | `/proc/pressure/io`、文件系统统计 | 判断 I/O 堵塞或日志/快照风险 |
| PID 使用率和 fork 失败 | cgroup `pids.current/pids.max`、进程事件 | 识别 PID 耗尽导致的服务异常 |

P2 信号不会单独触发高风险动作；它们主要用于解释、排序和恢复验证。

## 3. 风险状态

| 状态 | 进入条件 | 允许输出 |
| --- | --- | --- |
| `normal` | 无持续风险或风险已恢复 | 采样记录 |
| `warning` | 低余量、持续增长或压力超过校准阈值，但尚有恢复窗口 | 告警、对象排序、快照建议 |
| `critical` | 硬事件、临界趋势或多信号组合确认风险接近失效 | 生成动作计划；是否执行由策略层决定 |
| `recovered` | 资源和服务健康在验证窗口内恢复 | 关闭事件或进入冷却 |
| `escalated` | 无法定位、动作失败、保护对象命中或重复恶化 | 停止自动升级并通知人工 |

## 4. 第一版判定规则

第一版只固化结构，不固化生产数字：

1. 每次采样保存原始信号和采样时间，所有派生值保存计算窗口和样本数。
2. `warning` 至少需要一个趋势/余量信号持续超过配置窗口，或一个可解释的局部风险信号持续出现。
3. `critical` 需要硬事件，或“宿主机余量/增长趋势 + memory PSI/swap 退化”的组合确认；单次瞬时高值不能直接进入 `enforce`。
4. 同一事件必须有去抖和冷却 ID，避免每个采样周期重复触发动作。
5. 只有对象身份、保护策略和动作白名单均确认后，风险状态才允许生成可执行动作计划。
6. 任何信号缺失、采样中断或时钟异常都要写入审计；不得把缺失当作安全。

## 5. 事件输出字段

最小事件结构应包含：

```yaml
event_id: <stable-id>
observed_at: <RFC3339>
state: warning|critical|recovered|escalated
host_id: <stable-host-id>
signals:
  memory_available_ratio: <value>
  memory_growth_rate: <value>
  swap_used_ratio: <value>
  memory_psi: <snapshot>
  oom_events_delta: <value>
object_candidates:
  - kind: container|cgroup|process|host
    id: <stable-id>
    confidence: <value>
decision:
  mode: observe|simulate|enforce
  action: none|notify|snapshot|graceful_stop|restart|terminate|escalate
  reason_codes: []
  protected: true|false
evidence:
  snapshot_path: <path>
  sample_window: <duration>
```

该结构用于设计讨论，正式 schema 需要在实现前通过测试和配置校验固化。

## 6. 验收与下一步

- [x] 明确 P0/P1/P2 信号及其来源。
- [x] 明确状态机、去抖、冷却和缺失数据边界。
- [x] 明确风险信号与动作决策分离。
- [ ] 在 Mac Ubuntu 上用可丢弃对象校准采样周期、增长速率和 PSI 窗口。
- [ ] 将对象策略和保护名单形成 G4-T02 配置规范。
- [x] 用 `observe` 实现第一版事件输出，不执行自动动作；验证记录见 EXP-007 和 G4-T03。
- [x] 在 `simulate` 中复用风险事件和对象策略，只生成动作计划；默认输出 `execution=not_executed`。
