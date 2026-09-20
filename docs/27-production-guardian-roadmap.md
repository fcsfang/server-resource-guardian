# Guardian 生产化技术路线与执行手册

更新日期：2026-09-20

状态：`ACTIVE`

适用范围：Guardian 从当前 Python 本地原型进化为可在非生产和生产环境持续运行的服务。

> 本文是 2026-09-20 技术架构评审后的**后续实现唯一执行基线**。[`docs/14`](14-execution-roadmap.md) 和 [`docs/16`](16-autonomous-execution-roadmap.md) 保留历史路线和已完成 PoC 记录；凡是涉及“当前是否具备生产保护能力”、“下一步实现什么”、“何时允许进入生产”时，以本文为准。实验事实仍以 [`experiments/`](../experiments/README.md) 为唯一依据。

---

## 0. 给后续 Agent 的快速入口

接手后先执行：

1. 读取 `README.md` → `PROGRESS.md` → `goals/README.md` → `goals/resource-protection.md` 的 Goal 7 → 本文。
2. 读取本文「2. 当前事实基线」和「10. 当前状态板」，不要从旧 PPT 或已过期的“Goal 4 已完成”推断生产就绪状态。
3. 检查 `git status --short --branch`，不覆盖其他人未提交的变更。
4. 只选「10. 当前状态板」中第一个依赖已满足的 `READY` 任务。
5. 开始实验前创建 `EXP-###` 记录；开始代码前先把验收条件落到测试。
6. 完成后同时更新本文状态板、Goal 7、`PROGRESS.md` 和实验记录；不得只在对话中声称完成。

PG-P0-01 至 PG-P0-06 已完成；当前正在执行 **PG-P0-07：systemd 常驻服务与 Guardian 自身保护**。本文的状态板和 Goal 7 必须与代码、实验记录同步更新。

### 0.1 当前优先级调整（2026-09-20）

用户决定不继续等待 EXP-039 的 24 小时长跑：该实验已在 9,508 秒（约 2 小时 38 分钟）处安全结束，并以 `STOPPED` 记录提前结束的阶段性证据。24 小时门槛仍未通过，因此 PG-P0-07 不改为 `DONE`；但它不再阻塞当前最重要的核心问题验证。

当前优先级切换为：

1. 用当前代码复核 `observe/simulate` 的“内存危机 → 风险确认 → 唯一对象定位 → 受控动作计划”链路（已记录 EXP-053）。
2. 对照已有 EXP-021，区分“历史版本真实 `graceful_stop` 效果证据”和“当前生产化代码的可复核证据”。
3. 只有在当前代码的 observe/simulate 和安全门禁证据完整后，才申请本次本地 disposable `graceful_stop` 的单次明确授权。

这不是降低生产准入标准：watchdog 超时、真实 SIGKILL/OOM、真实磁盘耗尽和 24 小时长跑仍标记为未完成；只是先验证 Guardian 的核心止损价值。

---

## 1. 技术决策

### 1.1 主方案

采用**继续扩展 Python Guardian，但将它严格限定为 Beszel 的本机旁路决策与受控动作层**。

- **Beszel**：负责长周期指标、历史、可视化、常规告警和审计展示；不直接授权任何动作。
- **Guardian Observer / Decision Engine**：在本机采集高时效性信号，计算风险、归因和置信度，产生不可变的动作计划。
- **Guardian Action Broker**：在独立权限边界内重新校验身份、授权、时效、幂等键和保护策略，只执行有限动作。
- **Recovery Verifier**：将“风险已缓解”与“业务已恢复”分开验证。
- **systemd-oomd**：仅在业务 cgroup/slice 边界正确且完成独立对照后，作为内核 OOM 前的最后一道主机保护；不代替 Guardian 的业务策略。

### 1.2 不选的主路线

- 不将 Beszel 告警直接连到 Docker/systemd 动作。Beszel 的采集周期、传输和对象语义不足以承担低延迟处置授权。
- 不将 Guardian 做成通用 Docker/cgroup 调度器。本阶段只对明确登记、可丢弃/可恢复且有动作合同的对象处置。
- 不以 earlyoom/nohang 取代业务对象决策。它们可作独立对照或最后保险，但基于进程的选择不具备本项目要求的容器稳定身份、业务保护合同和恢复语义。
- 不默认给所有容器设置统一内存上限。资源边界只是每个业务策略的可选防线。

### 1.3 不可破坏的安全不变量

1. 默认模式永远为 `observe`；空配置、配置失效或升级后第一次启动均不得继承 `enforce`。
2. 数据缺失、过期、时钟回退、对象不唯一、低置信度或策略不可用时必须 fail-closed。
3. 任何告警都不是动作授权；任何 `critical` 也不是必须动作。
4. 身份必须在决策时和执行前各校验一次，不能只使用 PID、容器名或短 ID。
5. 保护名单优先于风险等级；未登记对象不可自动动作。
6. 动作意图必须先持久化，再执行；重启或崩溃后必须能够判定动作是未开始、进行中还是已完成。
7. 自动动作不得因为“恢复检查失败”继续升级到更破坏的动作；应当熄断并上报。
8. 不自动重启整机、不自动强制终止、不直接 kill 宿主机任意 PID、不自动修改业务资源上限。
9. 审计不可无界增长，但审计写入失败时不允许执行变更动作。
10. 开发、Multipass、非生产 x86_64 和生产结论必须分开记录。

---

## 2. 当前事实基线

### 2.1 已有代码能力

| 能力 | 当前状态 | 生产缺口 |
| --- | --- | --- |
| `/proc`、PSI、root cgroup v2、Docker stats 采集 | 部分实现 | 缺采集新鲜度、错误隔离、背压、每对象 cgroup 数据和连续运行验证 |
| 宿主机可用内存风险判定 | 部分实现 | 当前主要是单阈值+去抖，未真正组合增长趋势、PSI、swap 和质量门禁 |
| OOM 事件 | 部分实现 | 当前使用累计计数的非零值，必须改为采样间增量并处理重置/启动基线 |
| 风险状态机 | 原型已实现 | 缺最小停留、恢复滞后、可用性状态、重启恢复和单调时钟 |
| 容器候选对象生成 | 部分实现 | 当前所有运行容器均是候选，没有资源贡献归因和排名 |
| 单对象 `simulate` | 已实现原型 | 策略和身份绑定仍不完整 |
| Docker `graceful_stop/restart/terminate` adapter | 已实现原型 | 只有 `graceful_stop` 有一次真实成功实验；`restart/terminate` 不具备自动生产准入 |
| 动作授权、冷却、失败计数 | 部分实现 | 授权无签名/一次性消费，ledger 非原子、无锁且粒度不足 |
| 动作后恢复检查 | 部分实现 | 对 stop/terminate 主要验证对象已停；未通用重采样宿主风险与业务 SLI |
| Beszel 只读 Adapter/Bridge/UI model | 已实现原型 | 无稳定 endpoint、运行管理、长期兼容性和身份映射验证 |
| systemd 常驻服务和自身保护 | 本地 MVP 部分实现 | 已有非 root observer unit/slice、readiness/watchdog、有界审计和自然失败重启；24h soak、watchdog 超时/SIGKILL/OOM 恢复、磁盘满边界、完整故障矩阵、P99 校准、日志轮换和升级/回滚仍未完成 |
| 宿主机 PID 动作 | 未实现 | 保持未实现；不作为自动生产路线 |

### 2.2 已有证据能说明什么

- [EXP-021](../experiments/EXP-021-2026-09-19-failure-prevention-comparison/report.md) 证明：在本地 ARM64、单个可丢弃泄漏容器、合成阈值和特定 OOM score 设置下，Guardian 可在该脚本场景内停止目标并避免对照组的 global OOM 结果。
- [EXP-028](../experiments/EXP-028-2026-09-20-beszel-guardian-alert-comparison/record.md) 证明：在有界内存运行中，Guardian 本地判定早于 Beszel 持久化告警被读到；它不是接近 OOM 的检测 SLA 验证。
- 现有 66 个单元/契约测试只证明被覆盖函数在当前 fixture 下通过，不证明常驻、抗压、权限隔离或生产安全。
- EXP-020 的“5 轮有效重复”与已提交计时证据的可重建性存在差异，必须在对外使用该统计前补齐或降级陈述。

### 2.3 当前不能声称

- 不能声称已使用趋势、memory PSI、swap 组合准确预测内存耗尽。
- 不能声称能在多容器场景中准确归因和自动选择风险对象。
- 不能声称 `restart`、`terminate` 已完成真实安全验证。
- 不能声称已作为 systemd 常驻服务可在资源危机中可靠工作。
- 不能声称动作后真实业务已恢复，或已在 x86_64/生产环境验证。

---

## 3. 目标架构

```text
Beszel Agent/Hub -----------------------+
  history / dashboard / alert evidence  |
                                         v (read-only, untrusted hint)
/proc + PSI + cgroup v2 + Docker API -> Collector -> Sample Store
                                                    |
                                                    v
                                             Risk Engine
                                                    |
                                                    v
                       Object Registry -> Attribution + Policy Engine
                                                    |
                                                    v
                                             Immutable Plan
                                                    |
                                   observe/simulate | enforce capability
                                                    v
                                         Privileged Action Broker
                                                    |
                                                    v
                                  Docker/systemd adapter (allowlisted)
                                                    |
                                                    v
                                 Host mitigation + business verification
                                                    |
                                                    v
                              Audit journal / Beszel read-only view model
```

### 3.1 进程和权限边界

| 组件 | 默认权限 | 可写内容 | 网络 |
| --- | --- | --- | --- |
| `guardian-observer` | 非 root；仅读必需的 proc/cgroup/runtime 视图 | 有界采样库/审计队列 | 不需公网 |
| `guardian-api` | 非 root | 不写运行时；只读查询 | 默认仅 Unix socket 或 localhost |
| `guardian-action-broker` | 最小必需特权 | 动作意图/结果和 Docker/systemd 允许操作 | 无公网；只接受本机 Unix socket |
| Beszel Adapter | 只读 | 仅更新脱敏 view model | 只允许到明确 Hub 地址 |

Docker socket 等价于高权限控制面。生产版 Observer/API 不得因为读取 Docker 状态而直接获得无限 Docker socket 权限；必须通过最小代理或明确评审的运行时读取通道。

### 3.2 数据时间语义

- dwell、TTL、冷却、操作超时使用单调时钟。
- RFC3339 UTC 墙上时间只用于审计和跨系统展示。
- 每个采样携带 `sample_id`、`boot_id`、`collector_version`、`observed_monotonic_ns`、`observed_at`和 `quality_flags`。
- 进程重启后不使用上一次运行的累计 OOM 数字直接触发风险；先建立新基线。

---

## 4. 生产风险模型

### 4.1 输入信号

**宿主风险信号**

- `MemAvailable` 绝对值与占总内存比例。
- 30–60 秒滑动窗的 robust slope、样本数和 time-to-threshold；不使用单点两样本斜率执行动作。
- memory PSI `some/full` 的 `avg10/avg60`、增长方向和持续时间。
- swap 已用/可用、`pswpin/pswpout` 速率和变化方向。没有 swap 的机器使用显式 `not_applicable`，不写成 0 压力。
- root 与可处置 cgroup 的 `memory.events` **增量**；`oom/oom_kill` 是硬事件和滞后信号，不得冒充“耗尽前预测”。

**对象归因信号**

- 每容器/cgroup 的 `memory.current`、`memory.stat`、`memory.events`、增长斜率、增量占宿主内存下降的比例。
- 容器 full ID、image digest、created time、labels、cgroup path/inode、状态和 healthcheck。
- 仅作辅助的 CPU、I/O、PID 信号；它们不得单独触发内存动作。

### 4.2 输入质量门禁

计算风险前必须得到：

- 需要信号的新鲜度和连续样本数达标；
- 采集器没有超时、部分失败或计数重置未处理状态；
- 宿主与对象采样来自允许的最大时间偏差内；
- 最少能区分 `present_zero`、`missing`、`not_supported`、`stale` 和 `error`。

不达标时输出 `DEGRADED_OBSERVABILITY`，可告警、可快照，不得产生可执行计划。

### 4.3 组合规则

不在代码里写一套“所有服务器通用”的数字。阈值由版本化配置给出，并通过目标环境 observe 基线校准。结构固定为：

- `WATCHING`：单个余量/趋势信号持续越界，开始候选归因。
- `WARNING`：余量+趋势，或余量+压力，在 dwell 窗口内成立。
- `CRITICAL_CONFIRMED`：余量、time-to-threshold、PSI/swap 退化中至少两类一致，或发生新的 cgroup OOM 硬事件。
- `ESCALATED`：已发生 root OOM、无法归因、保护对象命中、动作或验证失败。

相同配置必须有独立的 enter/exit 阈值和最小停留时间，防止门限附近抖动。

### 4.4 状态机

```text
STARTING
  -> OBSERVE_ONLY
  -> NORMAL
  -> WATCHING
  -> WARNING
  -> CRITICAL_CONFIRMED
  -> PLAN_ONLY | AWAITING_AUTHORIZATION
  -> ACTIONING
  -> VERIFYING_MITIGATION
  -> MITIGATED
  -> VERIFYING_BUSINESS
  -> BUSINESS_RECOVERED | BUSINESS_DEGRADED
  -> COOLDOWN
  -> NORMAL

Any state
  -> DEGRADED_OBSERVABILITY
  -> ESCALATED
  -> CIRCUIT_OPEN
```

进程启动后必须先完成基线窗口，不允许从 `STARTING` 直接跳到 `ACTIONING`。

---

## 5. 对象选择、动作和恢复合同

### 5.1 对象选择

先做资格过滤，再排名：

1. 对象在版本化 registry 中，全部稳定身份字段一致。
2. 对象未命中永久保护或配置保护。
3. 对象动作合同显式允许当前 action，且健康检查和恢复责任方已定义。
4. 候选者按近期内存增量贡献、斜率、对象 PSI/OOM 事件和与宿主下降的时间相关性计分。
5. 只有第一名同时达到 `min_confidence`、`min_host_contribution`且领先第二名 `min_margin`时，才产生单目标计划。
6. 否则输出 `AMBIGUOUS_TARGET`并上报；不尝试逐个停止容器。

评分公式、阈值和特征权重必须版本化，在实验报告中保存配置 hash；不允许为了通过单个实验就修改默认分数。

### 5.2 保护名单

**永久保护**：Guardian 自身、action broker、PID 1、SSH/网络/DNS、Docker/containerd、日志/审计、Beszel Agent、存储和挂载关键服务。

**业务保护**：数据库、队列、quorum 成员、唯一副本、有状态任务、不可重放作业以及业务 owner 指定对象。

保护名单必须有 owner、原因、复核日期和策略版本。空白保护配置表示“无任何对象获得自动处置资格”，不表示“全部允许”。

### 5.3 动作阶梯

| 级别 | 动作 | 自动化边界 |
| --- | --- | --- |
| A0 | 采样、记录、有界快照 | 默认启用 |
| A1 | 告警、产生可解释计划 | `observe/simulate` 可用 |
| A2 | 应用自身 drain/降载钩子 | 只有应用 owner 提供幂等合同时可自动 |
| A3 | 一次 `graceful_stop` | 当前唯一个可进入本地授权实验的真实动作 |
| A4 | `restart` | 不是紧急止损的“更强一级”；只能在宿主压力已缓解、业务明确允许、独立防循环通过后作为另一条恢复分支 |
| A5 | `terminate`/SIGKILL | 自动路径永久禁止；仅保留人工 break-glass 可能性，且不纳入当前产品范围 |
| A6 | 宿主机 PID kill、整机重启、批量动作、自动修改资源限额 | 永久禁止自动执行 |

不得在一个事件中自动从 `graceful_stop` 递进到 `restart` 或 `terminate`。任何动作失败都应进入 `CIRCUIT_OPEN + ESCALATED`。

### 5.4 授权与幂等

一份可执行 capability 至少绑定：

- `authorization_id` 和一次性 nonce；
- host ID / boot ID / environment；
- 完整目标身份和允许 action；
- risk event ID、plan digest、policy version/config digest；
- 发行时间、单调时效和最大执行次数 1；
- 发行人/系统、审批依据和环境标识。

当前 JSON 授权文件只允许用于 `local-disposable`。非生产/生产必须使用 root 所有、受限权限、可验证来源并可一次性消费的 capability。

### 5.5 恢复验证

两个状态不得混用：

- `MITIGATED`：宿主 MemAvailable、趋势、memory PSI、swap 和 OOM 增量在恢复窗口内回到安全区间。
- `BUSINESS_RECOVERED`：业务 owner 定义的健康、依赖、流量、数据完整性和错误率检查通过。

`graceful_stop` 后目标停止只证明动作终态正确，不等于业务恢复。如果宿主已缓解但业务未恢复，必须输出 `BUSINESS_DEGRADED` 并上报，不自动 restart。

---

## 6. 交付阶段与准入门

| 里程碑 | 结果 | 进入下一阶段的硬门 |
| --- | --- | --- |
| M0 事实重置 | 文档与代码/证据一致 | 无“原型已完成=生产已就绪”类矛盾；EXP-020/021/028 边界被明示 |
| M1 本地持续 Observe | Guardian 作为 systemd 服务长跑，组合风险和自身保护可用 | 24h 本地 soak、采集失败注入、日志界限和重启恢复通过 |
| M2 本地 Simulate | 每对象归因、置信度、放弃和策略计划可解释 | 单泄漏命中、多泄漏放弃、保护对象拒绝、未知对象拒绝全部通过 |
| M3 本地单次 Enforce | 一次明确授权的 disposable 容器 `graceful_stop` 闭环 | 动作前意图落盘、单次消费、身份复验、宿主缓解和业务结果分开记录 |
| M4 x86_64 非生产 Observe | 完成版本兼容、基线和策略校准 | 获得书面授权，至少 7 天 observe soak，无动作权限 |
| M5 x86_64 非生产受控灰度 | 按对象先 simulate，再单次 `graceful_stop` | 业务 owner/SRE/安全审批，回滚、值班、带外通道和维护窗口可用 |
| M6 生产 Observe/Simulate | 生产只读长跑和决策对照 | 生产安全评审、变更单、隐私/审计策略、回滚演练通过 |
| M7 生产有限 Enforce | 只对极小白名单自动 `graceful_stop` | 单独的生产动作审批；任何一项未满足都保持 simulate |

M0–M3 仅在当前 Multipass `local-disposable` 环境执行。M4 及以后每个阶段都需新的外部授权，不因上一阶段通过自动继承。

---

## 7. 详细任务清单

### P0：本地可持续 Guardian MVP

#### PG-P0-01 事实与状态重置

- **目标**：消除文档过度声称，将 Goal 4 明确定位为“单对象脚本化 PoC”，将 Goal 7 设为生产化主线。
- **前置条件**：无。
- **产物**：更新 README、PROGRESS、Goal、docs/14/16/17/18/19/24 中与源码冲突的状态；建立一份“声称→证据→边界”对照表。
- **验收标准**：代码搜索和文档审查不再存在已知矛盾；EXP-020 重复次数、EXP-021 OOM score/单目标限制、EXP-028 非风险 SLA 均有明示。
- **外部授权**：否。

#### PG-P0-02 配置 Schema 与启动门禁

- **目标**：把风险、对象、授权、冷却、恢复、日志和运行模式从代码/CLI 默认值迁移到严格 schema。
- **前置条件**：PG-P0-01。
- **产物**：版本化 schema、本地样例、配置解析器、语义验证、配置 digest、迁移和回滚说明。
- **验收标准**：未知字段、单位错误、冲突阈值、空白白名单、危险默认和降级版本全部 fail-closed；无配置时只能 observe。
- **外部授权**：否；生产数值需业务/SRE 确认。

#### PG-P0-03 采集器与组合风险引擎

- **目标**：实现 OOM 增量、趋势、PSI、swap、数据质量和滞后状态机。
- **前置条件**：PG-P0-02。
- **产物**：类型化 sample model、滑动窗、单调时钟、计数重置处理、组合风险评估器、reason codes 和可解释输出。
- **验收标准**：历史 OOM 不会持续 critical；PSI/swap 真正参与判定；断样、计数回退、时钟跳变和部分采集失败进入 `DEGRADED_OBSERVABILITY`；单测+回放测试通过。
- **外部授权**：否。

#### PG-P0-04 每对象归因与放弃机制

- **目标**：使宿主风险与容器/cgroup 内存贡献建立可解释联系。
- **前置条件**：PG-P0-03；Multipass cgroup v2 层级已核对。
- **产物**：对象 registry、Docker↔cgroup 映射、对象时序、评分器、置信度/领先幅度和 `AMBIGUOUS_TARGET` 输出。
- **验收标准**：单泄漏对象可稳定命中；两个接近候选、对象信号缺失、容器重建、ID/cgroup 不一致全部放弃；不再以“只有一个运行容器”作为归因依据。
- **外部授权**：否。

#### PG-P0-05 策略、授权和耐久状态

- **目标**：将对象登记、保护、动作合同、单次授权、冷却和熄断改为可崩溃恢复的一致性流程。
- **前置条件**：PG-P0-02、PG-P0-04。
- **产物**：按 host/object/action 粒度的本地状态库（推荐 SQLite WAL）、唯一幂等键、原子 intent/result 记录、一次性 capability 消费和启动 reconciliation。
- **验收标准**：在 intent 前、intent 后/执行前、执行后/结果前三个崩溃点恢复时不会重复动作；并发请求只有一个胜者；审计写失败在 adapter 之前拒绝。
- **外部授权**：本地否；生产 capability 发行方式需安全审批。

#### PG-P0-06 恢复、冷却和失败熄断

- **目标**：实现宿主缓解和业务恢复的两层验证，消除“容器停了=恢复了”的语义错误。
- **前置条件**：PG-P0-03、PG-P0-05。
- **产物**：恢复检查协调器、主机信号重采样、可插拔业务 probe 契约、`MITIGATED/BUSINESS_RECOVERED/BUSINESS_DEGRADED` 输出。
- **验收标准**：验证超时、probe 异常、资源二次恶化、业务不健康和新 OOM 事件均熄断并上报；不执行自动升级动作。
- **外部授权**：否；真实业务 probe 需 owner 提供。

#### PG-P0-07 systemd 服务与 Guardian 自身保护

- **目标**：让 Guardian 在资源紧张时仍能进行最小判断、审计和单个受控动作。
- **前置条件**：PG-P0-03、PG-P0-05、PG-P0-06。
- **产物**：安装路径、systemd unit/slice、非 root 服务用户、watchdog、有界队列和快照、journald 限制、资源预留/上限、健康和 readiness、安全重启。
- **验收标准**：24h soak 无泄漏；进程崩溃、磁盘满、Docker 超时、Hub 不可用、日志写失败、高压采样均有预期降级；资源参数来自实测 P99+余量，不凭空写死。
- **外部授权**：本地否；安装到外部主机需授权。

> 优先级说明：EXP-039 的 24 小时长跑已按用户决定提前结束，当前快照只作为阶段性稳定性证据；PG-P0-07 仍未完成。当前主线先推进核心内存危机有效性对照，不能把阶段性 soak 结果写成生产就绪。

#### PG-P0-08 本地对照实验和唯一允许的真实动作

- **目标**：在 Multipass 上完成 observe → simulate → 一次明确授权 `graceful_stop` 的新闭环。
- **前置条件**：PG-P0-01–07 全部通过；容器是新建 disposable 对象；停止条件已写入 EXP 记录。
- **产物**：本文「8.1 本地必做实验」的全部数据、报告和失败记录。
- **验收标准**：保护对象误动作 0；歧义/未知对象放弃率 100%；授权只消费一次；重启后无重复动作；资源和业务结果分开报告。
- **外部授权**：需用户对该次本地 disposable `graceful_stop` 单独明确授权。

#### PG-P0-09 Beszel 旁路展示接口

- **目标**：让 Beszel/UI 可以查看 Guardian 事件，不扩大控制面。
- **前置条件**：PG-P0-05–07；docs/23 设计评审通过。
- **产物**：版本化只读 view-model endpoint/Unix socket、分页、脱敏、认证、缓存和降级语义。
- **验收标准**：UI 不能创建或扩大 capability；Beszel/endpoint 不可用不影响 Guardian 本地判定；敏感身份和原始凭据不进入输出。
- **外部授权**：需 leader 对 UI 方案评审；本地实现不需生产授权。

### P1：本地 MVP 后，非生产前准备

#### PG-P1-01 systemd-oomd / earlyoom / nohang 对照

- **目标**：用同一组场景确认哪些底层能力值得作为独立最后防线。
- **前置条件**：M3 通过；独立 disposable slice/cgroup。
- **产物**：同一压力模型下的检测、选择、误杀、恢复、开销和失败安全对照报告；ADR。
- **验收标准**：明确是否启用 systemd-oomd、覆盖哪些 slice、与 Guardian 同时命中时谁优先；未得出结论前不变更默认。
- **外部授权**：Multipass 否；外部主机是。

#### PG-P1-02 长时间稳定性与容量基线

- **目标**：验证连续运行、日志增长、数据库维护、容器 churn 和依赖不可用。
- **前置条件**：M3 通过。
- **产物**：至少 7 天本地 soak 报告，CPU/RSS/FD/线程/队列/磁盘时序，日志轮换和 DB 恢复实验。
- **验收标准**：达到「9. 工程质量门」；资源增长无单调泄漏；任意依赖故障不导致执行未授权动作。
- **外部授权**：否。

#### PG-P1-03 x86_64 非生产 Observe/Simulate

- **目标**：在与生产相同版本/架构的非生产主机校准并发现环境差异。
- **前置条件**：M3、PG-P1-02；书面授权；只读账号/安装窗口。
- **产物**：环境差异、配置包、7 天 observe、对象 registry 和 simulate 对照报告。
- **验收标准**：全程无动作权限；真实对象映射覆盖率和不可归因率可量化；配置阈值有数据依据和 owner 签字。
- **外部授权**：是。

#### PG-P1-04 非生产故障注入与单次灰度

- **目标**：在可回滚环境验证真实应用动作合同。
- **前置条件**：PG-P1-03；业务副本/状态/依赖已确认；值班、带外、回滚可用。
- **产物**：按对象的 runbook、健康合同、一次 simulate 和一次明确审批 `graceful_stop` 证据。
- **验收标准**：无保护对象动作；无重复动作；宿主和业务结果均可证明；任何意外结果阻断后续阶段。
- **外部授权**：是，且每次动作单独授权。

#### PG-P1-05 运维交付

- **目标**：使值班人员可安全安装、观测、禁用、回滚和取证。
- **前置条件**：PG-P1-02–04。
- **产物**：安装/升级/回滚/禁用/故障处理 runbook，监控与告警规则，值班联系与 RACI，证据保留策略。
- **验收标准**：由未参与开发的操作者按 runbook 完成一次安装、禁用、回滚和事件导出演练。
- **外部授权**：需运维/值班 owner 参与。

### P2：生产化前必须完成

#### PG-P2-01 权限分离与安全审计

- **目标**：将观测/决策与 Docker/systemd 动作权限分离，收缩 root-equivalent 暴露面。
- **前置条件**：M3；组织安全要求已确认。
- **产物**：独立 action broker、Unix socket peer credential 校验、有限协议、输入上限、身份复验、文件权限、威胁模型和安全测试。
- **验收标准**：Observer/API 不持有 Docker 写权；任意字符串/命令注入无路径；重放、篡改、TOCTOU、过大输入和未授权本机客户端全部拒绝。
- **外部授权**：需安全评审。

#### PG-P2-02 发布、升级和回滚工程

- **目标**：产出可复现、可验证、可回滚的发布单元。
- **前置条件**：PG-P2-01。
- **产物**：锁定依赖、离线安装包/内部制品、SBOM、签名/校验、数据迁移、配置兼容矩阵和一键禁用/回滚。
- **验收标准**：全新主机可离线安装；升级失败自动回到 observe 或旧版；旧事件不使用新策略执行。
- **外部授权**：需发布/制品仓规范确认。

#### PG-P2-03 生产 Observe/Simulate 灰度

- **目标**：在不具备动作权限的前提下确认真实负载下的信号、归因和误报。
- **前置条件**：M5、PG-P2-01/02；生产变更单。
- **产物**：分批 host 清单、基线报告、告警调优、误报/漏检/不可归因报表和回滚演练。
- **验收标准**：达到「9. 工程质量门」；全程无 Docker/systemd 写权；任何异常可在指定时间内禁用并保留证据。
- **外部授权**：是。

#### PG-P2-04 生产有限 Enforce 准入

- **目标**：如果组织最终批准，只对极小白名单开启 `graceful_stop`。
- **前置条件**：M6 达标；安全、SRE、业务 owner 联合批准；带外/回滚/值班齐备。
- **产物**：每对象 policy pack、变更单、灰度计划、自动停用条件、每日审计和事后复盘。
- **验收标准**：先 1 host/1 object/1 action；首次动作需人工在线；任何误选、验证失败、审计中断或 SLO 超标立即全局回到 simulate。
- **外部授权**：是，且不可由项目代码或 Agent 自行推定。

### 暂不做

- 自动 `restart`、自动 `terminate`、宿主机 PID kill、整机重启。
- 任意 cgroup 的通用调度/限额调整。
- 直接 fork Beszel 核心执行逻辑，或让 Beszel 告警 webhook 获得动作权限。
- 面向所有业务的通用编排、自动迁移、弹性伸缩或容量调度。
- 在没有对象 registry 和业务 owner 合同时开发“智能选择”或 ML 模型。

---

## 8. 实验与验收矩阵

### 8.1 P0 本地必做实验

| 类别 | 最少场景 | 必须记录 | 通过条件 |
| --- | --- | --- | --- |
| 无 Guardian 对照 | 单容器无界增长，有明确停止条件 | 失效时间、MemAvailable、PSI、swap、OOM 增量、健康探针 | 确实产生目标风险，否则不能用于有效性对照 |
| 提前检测 | 相同负载、多种增长速率 | 首次 watching/warning/critical、剩余时间、信号组合 | P95 剩余时间大于实测动作 P99 + 验证 P99 + 批准安全余量 |
| 目标选择 | 单泄漏、两个同等泄漏、一主一次、保护对象、容器重建 | 候选特征、分数、置信度、领先幅度、放弃原因 | 单目标命中率达工程门；歧义、未知、保护场景自动动作 0 |
| `graceful_stop` | 正常 TERM、忽略 TERM、动作超时、身份变更 | exit code、OOMKilled、adapter 返回、intent/result、capability 消费 | 只有正常 TERM 场景可记为成功；其他均熄断，不升级 |
| 恢复 | 风险缓解+业务恢复、只缓解但业务不健康、未缓解 | 主机信号时序、业务 probe、新 OOM 增量 | 三种结果正确分类，不把 target stopped 写成 business recovered |
| 自身开销 | 空载、容器 churn、高压、Hub/Docker 超时、24h soak | CPU、RSS、FD、线程、采样耗时、丢样、日志/DB 增长 | 达「9. 工程质量门」，无无界增长 |
| 误触发/漏检 | CPU/IO/PID 高但内存安全，短时突发，慢泄漏，断样 | 每个状态和 reason code、真值标签 | 辅助信号不单独触发内存动作；断样 fail-closed |
| Beszel 时差 | 同一负载下 Guardian/Beszel/宿主原始时钟对照 | 原始采样时间、持久化告警时间、时钟误差 | 能解释延迟构成；Beszel 事件不改变动作授权 |
| 崩溃一致性 | intent 前/后、adapter 前/后、result 前杀进程 | 恢复后的 ledger/audit 和 adapter 调用次数 | 无重复动作，未知中间态一律需人工对账 |

### 8.2 实验设计规则

- 对照组和 Guardian 组必须使用同一镜像、负载参数、初始资源和采样方法。
- 每个统计结论必须能从提交的结构化数据重算；失败轮次不删除，但需标明是否纳入主分析及原因。
- OOM score、资源限额、缓存、swap、内核和架构必须作为实验条件报告，不隐藏对结果的人为偏置。
- 多对象评估不能通过强制主机只运行一个容器来完成。
- 任何可能导致 Multipass 整机失联的实验必须先保存外部时间线、写明停止条件并确认不影响宿主数据。

---

## 9. 工程质量门

以下是项目默认工程门；生产正式 SLO 还需 SRE/业务 owner 签字。放宽任何门限必须通过 ADR，不得为让实验通过而静默修改。

| 类别 | 默认门限 |
| --- | --- |
| 安全 | 保护、未知、歧义对象的自动动作次数必须为 0 |
| 幂等 | 重放、并发、崩溃恢复导致的重复 adapter 调用必须为 0 |
| 审计 | 每次决策和动作都能追溯 sample、policy、identity、authorization、intent、result 和 verification；完整率 100% |
| 归因 | 本地已标注单故障集精度≥95%；歧义数据集放弃率 100%；真实生产目标由 M4/M6 重新批准 |
| 检测 | 不用固定“6 秒”作 SLA；每个场景 P95 lead time 必须大于动作 P99 + 验证 P99 + 安全余量 |
| 误报 | 7 天 observe soak 中不允许任何“本会自动动作”的无效计划；warning 告警预算由 owner 批准 |
| 运行开销 | 默认工程目标：P95 CPU ≤1 个核的 1%、RSS ≤64 MiB、持久写入 ≤100 MiB/天；必须在目标机重测并按批准预算调整 |
| 可用性 | 7 天 observe soak 可用性≥99.9%；任意依赖不可用不影响 fail-closed |
| 快照/日志 | 大小、单事件数量、保留时间和总额度全部有上限；到达高水位时不执行动作 |
| 恢复 | 宿主缓解、业务恢复、业务退化和未缓解的分类正确率 100% |

单元测试、fixture 测试、实机集成、故障注入和 soak 不可互相替代。所有 P0 代码变更需同时给出负向测试，特别是数据缺失、过期、并发、身份变更和崩溃点。

---

## 10. 当前状态板

状态值：`BACKLOG` / `READY` / `IN_PROGRESS` / `BLOCKED` / `DONE`。一个任务只能由一个主责任人/Agent 改为 `IN_PROGRESS`。

| 任务 | 状态 | 依赖 | 当前证据/阻塞 | 下一动作 |
| --- | --- | --- | --- | --- |
| PG-P0-01 事实与状态重置 | `DONE` | 无 | docs/28 已建立；旧 PoC 文档、EXP-020/021/028 边界已核对 | 进入 PG-P0-02 |
| PG-P0-02 配置 Schema | `DONE` | P0-01 | docs/29、JSON schema、校验器和 74 个测试已通过 | 已进入 PG-P0-03 |
| PG-P0-03 组合风险引擎 | `DONE` | P0-02 | EXP-030；宿主 84/84、Multipass 隔离 20/20；cgroup v2 当前路径已解析 | 进入 PG-P0-04；长跑/阈值/x86_64 仍待后续阶段 |
| PG-P0-04 对象归因 | `DONE` | P0-03 | EXP-031；宿主 91/91、Multipass 隔离 27/27；真实 Docker full ID↔cgroup 映射、无压力 NO_TARGET 已验证 | 进入 PG-P0-05；贡献阈值仍是本地校准值 |
| PG-P0-05 策略/授权/耐久性 | `DONE` | P0-02,P0-04 | docs/30、EXP-032；101/101 宿主、52/52 Multipass 隔离；SQLite WAL/capability/intent/reconciliation 已接入 Docker enforce 门 | 进入 PG-P0-06；生产 capability 发行仍待审批 |
| PG-P0-06 两层恢复 | `DONE` | P0-03,P0-05 | docs/31、EXP-033；宿主/业务分层和 Controller 集成已验证，宿主端 107/107、Multipass 相关测试 64/64 | 进入 PG-P0-07；真实业务 probe 仍待 owner |
| PG-P0-07 systemd/自身保护 | `IN_PROGRESS` | P0-03,P0-05,P0-06 | EXP-034/035/036/037/038/040/041/043/044/045/046/047/048/049/050/051/052：主机全量 `134/134`，审计 `flush/fsync`、完整性校验、watchdog 状态和 ENOSPC 正负向契约通过；当前 Observer/Runtime 相关 VM 回归 `30/30`；systemd verify 退出码 0，8 秒 smoke、有界存储、45 秒/5 分钟局部 soak、依赖 fixture、transient readiness/watchdog 通知接收、watchdog 余量、间隔 fail-closed、有限高压采样、自然失败重启、审计完整性、ENOSPC fixture 和异常退出码重启已完成；EXP-042 预检失败保留。EXP-039 已由用户决定在 9,508 秒提前结束：sidecar 933 样本/9,346 秒，RSS P99/最大值 17,752 KiB、均值 17,557.796 KiB，CPU/FD/线程 P99 为 0.0%/5/1，审计 1,046,557 bytes 且 136/136 行合法 JSON、事件 ID 无重复；该结果是阶段性证据，不是 24h 通过。24h soak、watchdog 超时、真实 SIGKILL/OOM 恢复、真实磁盘满边界和完整场景 P99 未完成 | 先复核当前代码的核心内存危机 observe/simulate 链路；随后再处理剩余自身保护边界，真实 disposable `graceful_stop` 仍需单次明确授权 |
| PG-P0-08 本地新闭环 | `BACKLOG` | P0-01–07 | 需重新授权 | 等待依赖和用户单次授权 |
| PG-P0-09 Beszel 旁路 endpoint | `BLOCKED` | P0-05–07, UI 评审 | docs/23 尚未经 leader 评审 | 评审后实现 |
| P1/P2 任务 | `BACKLOG` | M3 及外部条件 | 未进入 | 不提前开始 |

---

## 11. Agent 执行和交接协议

### 11.1 开始前

- 通过 `git status` 确认工作区，通过本状态板确认任务未被另一 Agent 接手。
- 将任务状态改为 `IN_PROGRESS`，在 Goal 7 记录责任人/分支/开始日期。
- 把任务卡的验收条件转成测试计划；如果要做实验，先建 `EXP-###/record.md`。
- 检查任务是否涉及本地真实动作、外部主机、生产、凭据、故障注入或不可逆操作；涉及则必须停下获取对应授权。

### 11.2 实现中

- 优先小型、可审查的提交；不在一个改动里同时更换风险算法、持久化和动作 adapter。
- 任何新的宽松路径都必须有对应拒绝路径测试。
- 不把 mock 返回、Docker CLI exit 0、容器已退出或一次测试通过写成业务恢复。
- 配置、schema、审计事件、数据库和 endpoint 必须显式版本化。
- 发现本文与代码/证据冲突时，先记录冲突和证据，再修正路线；不为维持旧说法而修改实验结果。

### 11.3 完成时

任务只有同时满足以下条件才能改为 `DONE`：

1. 任务卡的产物已提交；
2. 正向、负向、失败注入和环境相关验证均完成；
3. 需要实验的任务有完整 `EXP-###` 记录和可重算数据；
4. 本文状态板、Goal 7 和 `PROGRESS.md` 已更新；
5. 已说明验证了什么、没有验证什么、剩余风险和回滚方法；
6. Git 工作区中没有未解释的临时文件、凭据、原始生产数据或运行时废料。

### 11.4 必须暂停的情况

- 要求连接生产、使用真实凭据、修改生产配置或注入故障。
- 要求执行 `restart`、`terminate`、PID kill、整机重启、资源限额修改。
- 保护名单、对象 owner、健康合同或回滚责任不明确。
- 实验可能超出 Multipass/disposable 范围，或停止条件不可实施。
- 要求绕过 fail-closed、降低审计、放宽身份匹配或删除失败证据。

---

## 12. 从 Multipass 到生产的外部前置和授权

进入 M4 前必须获得：

- 指定的 x86_64 Ubuntu 22.04 非生产测试机和环境 owner。
- 可安装 observe-only 服务的变更窗口、范围、日期和回滚责任人。
- 真实容器清单的脱敏对象 ID、owner、副本/有状态性质、依赖和保护分类。
- Guardian/Beszel 的 CPU、RSS、磁盘、日志和网络预算。
- 审计数据的保留时间、脱敏规则、访问者和导出流程。
- 运维告警接收人、值班窗口和带外救援通道。

进入 M5/M7 前还必须获得：

- 每个对象签字的允许动作、业务健康合同、回滚方式和最大恢复时间。
- 每次故障注入和真实动作的独立审批号、维护窗口和在场负责人。
- 安全对 action broker、capability、Docker/systemd 权限和依赖供应链的审批。
- SRE 对阈值、SLO、熄断、全局 kill switch、升级联系和事故响应的批准。

这些授权没有到位时，Agent 可继续做不需外部条件的文档、单元测试、fixture、本地 observe/simulate 和不含动作的耐久性工作；不得自行模拟审批或把旧授权重用到新对象/新环境。

---

## 13. 最终生产完成定义

Guardian 只有在以下条件全部成立时，才能被称为“已具备生产有限资源保护能力”：

- M0–M6 均有可复核证据，P0/P1/P2 任务不存在未接受高风险项。
- 生产默认 observe，且 kill switch 和回滚已演练。
- 风险检测、对象归因、保护、授权、幂等、动作、恢复和审计全链路都通过目标环境验证。
- Guardian 自身在依赖故障、资源压力、重启、磁盘高水位和部分数据下保持有界、可观测和 fail-closed。
- 产品范围仍只包含白名单对象的有限 `graceful_stop`；没有因生产准入而默认开启 restart、terminate 或 PID kill。
- 业务 owner、SRE、安全和变更管理者已对自己负责的配置、SLO、风险接受和操作手册签字。

在此之前，对项目最准确的描述是：**Guardian 是一个已有本地单对象止损证据、但正在补齐复合风险、因果归因、常驻运行和生产安全边界的原型。**
