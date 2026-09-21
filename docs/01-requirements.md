# 项目章程与需求基线

更新时间：2026-09-20

状态：`ACTIVE`
需求来源：leader 最新确认 + 当前源码/实验事实

## 1. 项目目的

当 CPU、内存、磁盘容量/inode、磁盘 I/O 中任一资源或多个资源同时接近耗尽并威胁服务可用性时，系统必须形成以下闭环：

1. **告警**：及时生成可解释事件并通知管理员；告警包含主机、资源、严重级别、责任候选、证据时间和处置状态。
2. **救援**：尽可能保留 SSH、必要网络/认证、Guardian、Docker/containerd 控制面和最小审计能力，支撑管理员进入服务器快速释放资源。
3. **自动修复**：在风险真正耗尽前，Guardian 基于本机新鲜证据和正向可处置策略执行最多一个有限动作，验证宿主缓解和业务状态，并保存完整日志供复核。

三项目标互相补充，不能互相替代：有告警不等于可救援，Guardian 进程存活不等于 SSH 可用，目标停止不等于系统和业务恢复。

## 2. 方案边界

- **Beszel**：负责长周期监控、历史、展示、常规告警和通知集成；Beszel 告警是外部证据，不是动作授权。
- **Rescue Plane**：负责资源竞争下的最小维护链路；它提高可进入概率，但不能对内核失效、网络/供电故障、严重 I/O hang 或根盘不可写作绝对保证。
- **Guardian**：负责本机高时效采集、风险判断、对象归因、策略、有限动作、恢复验证和审计。
- **带外通道**：BMC/IPMI/iDRAC/iLO、云串口或救援控制台是同主机能力失效后的最终兜底，不能由 Guardian 替代。

## 3. 当前验收对象

| 资源域 | 检测要求 | 归因要求 | v2 MVP 动作边界 |
| --- | --- | --- | --- |
| CPU | 利用率、load、CPU PSI、调度延迟、持续时间 | 登记容器/systemd cgroup 的 CPU 时间贡献；接近候选放弃 | 默认只告警/计划；后续只评估已登记对象的 graceful stop |
| 内存 | MemAvailable、趋势、memory PSI、swap、OOM 增量 | 容器/cgroup 内存增量、贡献、置信度和领先幅度 | 一次性授权的 disposable 容器 graceful stop |
| 磁盘容量/inode | 挂载点余量、下降趋势、time-to-full、只读/写错误 | 仅接受明确 writer ownership/配额证据；否则放弃 | 默认只告警/计划；不得删除未知文件 |
| 磁盘 I/O | I/O PSI、设备延迟/利用率/错误、cgroup io.stat | 登记 cgroup 的 I/O 增量；设备映射不清时放弃 | 默认只告警/计划；不得对未知设备/cgroup 限速 |
| 混合风险 | 每个资源独立状态和新鲜度，形成一个联合事件 | 同一目标证据一致或明确放弃 | 一次事件最多一个目标、一个动作；不得并行动作 |

## 4. 功能需求

| 编号 | 需求 | 完成判据 |
| --- | --- | --- |
| FR-001 | 三资源本机采集 | CPU、内存、容量/inode、I/O 均有类型化样本、新鲜度和质量状态 |
| FR-002 | 风险判断 | 每个资源有独立 enter/exit、dwell、恢复和 fail-closed；混合风险有仲裁 |
| FR-003 | 稳定对象归因 | 只使用完整容器身份或登记 unit/cgroup；未知、歧义、重建、映射变化全部放弃 |
| FR-004 | 管理员告警 | warning/critical/recovered/escalated 可投递到批准渠道；去重、重试、恢复通知和投递审计可验证 |
| FR-005 | 救援接口 | 批准压力模型下 SSH、诊断、Guardian 状态和容器控制面达到签字 SLO |
| FR-006 | 正向对象策略 | `protected_set` 与 `actionable_set` 独立；不在保护名单不等于可动作 |
| FR-007 | 受控动作 | 动作前持久化 intent，执行前复验身份/授权；一次事件最多一个动作 |
| FR-008 | 恢复验证 | 宿主缓解与业务恢复分层；证据缺失或未恢复时熔断并升级人工 |
| FR-009 | 审计 | sample、policy、identity、authorization、intent、result、verification 和通知结果可追溯 |
| FR-010 | Beszel 集成 | Beszel 保持只读旁路；Hub/通知失败不扩大动作权限，也不阻塞本机 fail-closed |

## 5. 非功能需求

| 编号 | 需求 | 工程门 |
| --- | --- | --- |
| NFR-001 | 默认安全 | 无配置、配置错误、数据缺失或升级首次启动只能 observe |
| NFR-002 | 自身有界 | CPU/RSS/FD/线程/队列/日志/快照/数据库均有预算和上限 |
| NFR-003 | 抗压 | Guardian 与救援链路使用独立资源域；参数由目标机 P99+余量校准 |
| NFR-004 | 最小权限 | Observer 不持有写动作权限；Action Broker 使用有限协议和最小权限 |
| NFR-005 | 一致性 | capability 单次消费、原子 intent/result、幂等、冷却、熔断和崩溃对账 |
| NFR-006 | 可运维 | systemd 常驻、watchdog、安装/禁用/升级/回滚/取证 runbook 可演练 |
| NFR-007 | 可移植 | ARM64 本地结果必须在 x86_64 Ubuntu 22.04/systemd 249/cgroup v2 非生产环境复核 |
| NFR-008 | 证据可重算 | 质量门数字来自结构化数据；失败实验保留且不能被成功轮次覆盖 |

## 6. 永久禁止自动执行

- 宿主机任意裸 PID kill、按 Top 排名杀进程。
- 自动 restart、terminate/SIGKILL、批量 stop 或整机重启。
- 自动删除未知文件/日志、清空目录、修改任意挂载点。
- 自动修改任意生产 cgroup/Docker 资源上限。
- 由 Beszel webhook、UI 按钮或外部告警直接获得动作授权。

## 7. 当前事实状态

| 目标 | 当前状态 | 已有证据 | 主要缺口 |
| --- | --- | --- | --- |
| 管理员告警 | `PARTIAL` | Beszel memory 告警链路和 Guardian 结构化事件已有本地证据 | CPU/磁盘/混合 Guardian 事件到批准通知渠道的端到端投递、去重、恢复通知未完成 |
| 维护接口 | `LOCAL-EVIDENCE` | EXP-054 在 disposable VM 验证候选 Rescue Plane 和管理型 SSH 探针 | 生产真实客户端、x86_64、带外通道、任意耗尽和签字 SLO 未验证 |
| 自动修复 | `PARTIAL` | 内存历史单对象 graceful_stop；内存/CPU/磁盘已有 observe/simulate 与 fail-closed | 三资源联合对照、混合仲裁、当前代码一次动作、生产 action broker/权限均未完成 |

## 8. 权威关系

- 本文回答“为什么做、必须实现什么”。
- [`docs/03-architecture.md`](03-architecture.md) 回答“系统如何分层”。
- [`docs/06-safety-policy.md`](06-safety-policy.md) 回答“哪些事情绝对不能做”。
- 根目录 [`ROADMAP.md`](../ROADMAP.md) 是唯一执行路线，[`PROGRESS.md`](../PROGRESS.md) 是当前状态。旧 Goal 和旧路线已从工作区移除。
- [`PROGRESS.md`](../PROGRESS.md) 是进度总账；[`experiments/`](../experiments/README.md) 是实验事实源。
