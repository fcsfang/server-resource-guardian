# Guardian v2 当前架构

更新时间：2026-09-21

状态：`ACTIVE`

## 1. 架构目标

系统采用“双层三资源”架构：Rescue Plane 保障最小人工维护链路，Guardian 在故障前检测 CPU、内存和磁盘风险并执行受控止损。Beszel 负责中心侧历史、展示和通知，不直接控制本机对象。

```text
                 Beszel Hub / approved notifier
                  history · UI · alert delivery
                              ^
                              | read-only evidence/view
                              |
Docker -> filtered read-only collector ┐
procfs/cgroup/PSI ----------------------┴-> resource engines -> attribution -> policy -> immutable plan
                                           CPU/memory/disk       |             |
                                                | simulate    | capability
                                                v             v
                                          audit/view     Action Broker
                                                              |
                                                              v
                                                allowlisted Docker/systemd adapter
                                                              |
                                                mitigation + business verification

Rescue Plane: ssh/network/auth + Guardian + broker + runtime control + minimal audit
Workload Plane: registered containers/systemd units/cgroups
Out-of-band console: final recovery when the host plane itself is unavailable
```

## 2. 组件职责

| 组件 | 职责 | 明确不做 |
| --- | --- | --- |
| Beszel Agent/Hub | 长周期监控、历史、可视化、常规告警、批准渠道通知 | 不选择动作目标，不签发本地动作权限 |
| Container Collector | 通过窄化 Unix Socket 提供容器身份、状态、一次性 stats 和 cgroup 映射 | 不提供通用 Docker API，不执行 stop/restart/kill/exec |
| Observer | 只读采集、质量/新鲜度判断、产生资源事件 | 不持有写动作权限 |
| Resource Engines | CPU、内存、容量/inode、I/O 独立状态机 | 不跨资源借用阈值，不直接执行动作 |
| Attribution | 使用稳定容器或 unit/cgroup 身份计算贡献与置信度 | 不以 PID、容器名、Top 排名作为自动身份 |
| Policy | 保护/可处置资格、允许动作、频率、环境、健康合同 | 不把“非保护”解释为“可杀” |
| Action Broker | 复验 capability、身份、时效、幂等、审计和熔断 | 不接受 shell 文本和任意命令 |
| Recovery Verifier | 区分宿主缓解和业务恢复 | 不把目标停止当作业务恢复 |
| Rescue Plane | 在资源竞争中提高维护链路获得 CPU/内存/I/O 的机会 | 不承诺覆盖内核、网络、供电、硬盘硬故障 |

## 3. 三条运行通路

### 3.1 告警通路

Guardian 产生本机事件和审计；Beszel 或独立批准的 notifier 负责发送 warning、critical、recovered、escalated 通知。投递失败不授权动作，但必须可重试、去重并记录。告警时间与本机风险时间分开保存。

### 3.2 救援通路

`rescue.slice` 候选包含 SSH/必要认证网络、Guardian、未来 Action Broker、Docker/containerd 控制面和最小审计；登录 session 可能仍位于 `user.slice`，因此必须作为并行资源域验证。CPU/IO weight 是相对竞争，memory.low/min 受层级影响，所有值需要目标机实测。

### 3.3 自动修复通路

只有 `critical_confirmed + target_confirmed + actionable + fresh capability + durable intent` 同时成立时，Action Broker 才能执行一次有限动作。当前产品唯一候选真实动作是 `graceful_stop`；失败、超时或恢复未确认后熔断并升级人工。

## 4. 资源与混合风险

- CPU、内存、磁盘容量/inode、磁盘 I/O 分别采样、判定和恢复。
- 多资源同时异常形成一个联合事件，不并发执行多个动作。
- 同一稳定对象获得多资源一致证据时可提高解释置信度，但不能绕过每个通道的质量门。
- 多个对象接近、资源指向不同对象或容量 writer 不明时输出 `AMBIGUOUS_TARGET`/`DEGRADED_OBSERVABILITY`，只告警。

## 5. 进程与权限边界

- Observer/Runtime 非 root，只读 procfs/cgroup 和 Collector view，写有界本地审计；不加入 `docker` 组，不直接访问 Docker socket。
- Container Collector 与 Action Broker 使用不同账户、socket 和协议。Collector 的应用层只读接口不是 Docker socket 的权限降级；进入非生产前必须有过滤代理、Docker AuthZ、rootless 或等价守护进程侧限制。
- Action Broker 独立进程/服务，通过本机受限协议接收不可变计划；执行前重新读取稳定身份。
- Docker socket 等价高权限。当前 Collector 尚未实现；缺少容器事实时 Runtime 只能继续宿主观测和告警，目标选择/动作必须 fail-closed。
- Beszel/UI 不共享 Action Broker capability；中心侧失联时 Guardian 保持本地 observe/fail-closed。

## 6. 当前实现映射

| 层 | 当前模块/证据 | 状态 |
| --- | --- | --- |
| 内存风险 | `guardian_risk.py`、EXP-030 | 本地实现 |
| 内存归因 | `guardian_attribution.py`、EXP-031 | 本地实现 |
| CPU | `guardian_cpu.py`、EXP-055 | 本地 observe/simulate |
| 磁盘 | `guardian_disk.py`、EXP-057 | 本地 observe/simulate |
| 耐久策略/动作 | `guardian_state.py`、`guardian_controller.py`、独立 Broker、EXP-032 | 本地实现，生产权限尚未完成准入 |
| 恢复 | `guardian_recovery.py`、EXP-033 | 内存主线本地实现 |
| Rescue Plane | `deploy/guardian/rescue.slice`、本地综合验证 | 本地安装、重启和回滚已通过；生产尚未验证 |
| Container Collector | 里程碑二 | 尚未实现；Runtime 已移出 `docker` 组 |
| 告警投递 | Beszel memory 告警、Guardian 事件 | 三资源端到端管理员通知未完成 |

运行顺序和完成标准只看根目录 [`ROADMAP.md`](../ROADMAP.md)。
