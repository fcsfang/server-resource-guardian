# 资源保护目标

本文件按“资源保护与故障救援”主题维护多个 Goal。目标编号遵循 [`goals/README.md`](README.md) 的全局规则：编号全局递增不复用；Goal 是执行计划，不是实验事实，实验结果只能进入 [`experiments/`](../experiments/README.md)。

执行统一按总蓝图 [`docs/14-execution-roadmap.md`](../docs/14-execution-roadmap.md) 和 [`docs/16-autonomous-execution-roadmap.md`](../docs/16-autonomous-execution-roadmap.md) 推进：以 Beszel 为监控基础，以 Guardian 的实时风险检测与自动处置为主线；Docker/systemd 资源限制只在具体业务明确允许时作为可选防线。Goal 1–3 为历史 WSL2 实验，Goal 4 起以 Mac 上的 Multipass Ubuntu 22.04 ARM64 为主测试环境。

## Goal 分工总览

| Goal | 主题 | 状态 | 一句话摘要 |
| --- | --- | --- | --- |
| [Goal 1](#goal-1只观测-poc-收尾) | 只观测 PoC 收尾 | `COMPLETED` | 本地 Beszel 指标核验、受控压测与首轮本地阈值 |
| [Goal 2](#goal-2第一层救援能力保障验证) | 第一层救援能力保障 | `COMPLETED` | 本地高压下 SSH/诊断/停止链路已验证，生产复核待授权 |
| [Goal 3](#goal-3第二层自动风险处置评估) | 第二层自动风险处置评估 | `COMPLETED` | 现成机制和检测-定位-处置-恢复管道已评估，Guardian 缺口已明确 |
| [Goal 4](#goal-4guardian最小风险检测与自动处置实现) | Guardian 最小风险检测与自动处置 | `COMPLETED` | 在可丢弃测试对象上实现并验证自动处置闭环，生产交接另列为外部依赖 |
| [Goal 5](#goal-5本地生产仿真性能报告) | 本地生产仿真性能报告 | `COMPLETED` | 在不接触生产的前提下复刻关键运行时、负载和故障模式，形成可交给 leader 的性能与有效性报告 |
| [Goal 6](#goal-6beszel-二次开发集成) | Beszel 二次开发集成 | `IN_PROGRESS` | 将 Beszel 监控基础与 Guardian 风险检测、策略和处置能力安全联调 |
| [Goal 7](#goal-7guardian-生产化实现) | Guardian 生产化实现 | `IN_PROGRESS` | 按 docs/27 补齐复合风险、对象归因、常驻运行、耐久安全和生产准入 |

依赖关系：Goal 1 的观测能力是 Goal 2/3 的共同前提；Goal 2 建立的压力场景复用给 Goal 3；Goal 3 的缺口分析是 Goal 4 的实现输入；Goal 5 将 Goal 4 的本地闭环扩展为生产仿真性能报告；Goal 6 将 Beszel 监控基础与 Guardian 联调。所有 Goal 都按 `experiments/` 的 `EXP-###` 规范记录，失败实验同样保留。

Goal 7 是 2026-09-20 架构评审后的生产化主线。Goal 4–6 的“完成”只代表对应原型/本地实验范围完成，不自动满足 Goal 7 的生产工程门。

## Goal 1：只观测 PoC 收尾

- 状态：`COMPLETED`
- 创建日期：2026-09-17
- 最近更新：2026-09-18（EXP-001 完成 CPU/内存压测与指标核验）
- 负责人：当前电脑 WSL2（PoC 运行环境）
- 来源：[分阶段实施计划](../docs/04-delivery-plan.md) 阶段 1、[本地 Beszel PoC](../docs/11-local-beszel-poc.md)

### 目标结果

Beszel 0.19.0 在本地 WSL2 完成指标完整性核验与受控压测：确认主机、Docker、systemd 指标可见且及时，得到负载下的 Hub/Agent 实测开销与首轮本地告警阈值，为 Goal 2/3/4 提供观测基础。所有阈值只作本地 PoC 校准值，不作为生产阈值。

### 任务清单

- [x] **G1-T01**：核验主机指标完整性：CPU、内存、swap、磁盘、网络、load 有数据且随负载变化。
- [x] **G1-T02**：核验 Docker 容器指标与历史和 systemd 服务列表（`docker`、`systemd-oomd`），确认无 Docker socket、D-Bus 或权限错误。（hello-world 容器历史待补充）
- [x] **G1-T03**：受控压测（有界 CPU/内存负载）验证指标及时性，重测负载下 Hub/Agent 开销。（磁盘 I/O 和网络压测待补充）
- [x] **G1-T04**：建立首轮本地告警阈值，明确标注“本地 PoC 校准值”，记录依据和适用边界。（见 EXP-001 record.md §5.9）
- [x] **G1-T05**：每个实验生成 `EXP-###` 记录，脱敏核心数据入 `experiments/<experiment-id>/data/`，结论回链 `PROGRESS.md`。（EXP-001 已完成）

### 接手入口

新 agent 先读 `README.md` → `PROGRESS.md` → 本文件，再按 [`deploy/`](../deploy/README.md) 和 `docs/11` 确认本地 Beszel 运行状态。本 Goal 只做只读观测和有界压测，不启用任何自动处置动作，不连接生产 Hub。

### 完成标准

- [x] 主机、Docker、systemd 指标均可见、及时，核验结果有记录。
- [x] 压测期间 Beszel 持续上报，负载开销有实测数据。
- [x] 首轮阈值已建立且标注本地校准属性。
- [x] 结论均有 `EXP-###` 记录支撑。

## Goal 2：第一层救援能力保障验证

- 状态：`COMPLETED`（本地 PoC 完成；生产复核仍受外部依赖阻塞）
- 创建日期：2026-09-18
- 最近更新：2026-09-18（EXP-002 完成）
- 负责人：当前电脑 WSL2 先行 PoC；生产事实与授权信息由外部补齐
- 来源：[Leader 测试交接](../docs/13-leader-test-handoff.md) 第一层

### 外部依赖（阻塞生产相关部分，不阻塞本地先行）

以下信息未确认前，不能把本地结果当生产结论，也不得连接生产服务器注入故障：

| 依赖 | 对应问题 |
| --- | --- |
| 非生产测试机、OS/内核/systemd/cgroup/Docker 版本、故障注入授权人 | Q-010 |
| SSH 失效真实表现（无法建连、认证慢，还是登录后命令无法执行） | Q-006 |
| 保护名单与资源预算、停止条件 | Q-009 |

### 目标结果

CPU 或内存高压下，验证“新建 SSH 连接 → 执行诊断命令 → 停止测试中的异常任务”链路是否可用：形成无保护基线、压力下救援结果和需在 Ubuntu 22.04 测试机复核的结论清单。资源边界只作为明确业务允许时的可选对照，不是统一配置要求。

### 任务清单

- [x] **G2-T01**：建立无保护基线：记录 SSH 建连、登录后 shell 响应、诊断命令、停止测试任务的正常表现（本地 WSL2 可用 sshd + 有界测试对象模拟；生产测试机待授权后复测）。
- [x] **G2-T02**：CPU 场景：在有界测试对象内逐步加压，对比“未配置 vs CPU 权重 vs CPU 配额”三组的救援链路表现。
- [x] **G2-T03**：内存场景：在可丢弃测试对象内逐步申请内存，对比无额外限制与业务明确允许的内存保护配置；不把 CPU 优先级当作内存不足的解决方案。
- [x] **G2-T04**：逐场景记录 SSH 建连时间、命令完成情况、停止任务耗时、压力指标、OOM/cgroup 事件和测试对象自身开销。
- [x] **G2-T05**：建立救援链路不稳定时立即停止扩大压力的原则；本地实验未触发扩大压力后的失稳，不进入自动终止测试。
- [x] **G2-T06**：整理需在 Ubuntu 22.04 测试机复核的结论清单（本地 systemd 259 vs 生产 249、内核 6.18 vs 6.8、swap 实现差异）。

### 接手入口

先读 `PROGRESS.md`、本文件、`docs/13`、[`docs/07-poc-blueprint.md`](../docs/07-poc-blueprint.md) 和 [`docs/06-safety-policy.md`](../docs/06-safety-policy.md)。实验前先分配 `EXP-###` 并写好授权、保护名单和停止条件；未确认外部依赖前只做本地有界实验。

### 完成标准

- [x] CPU、内存场景均有基线、配置对照、救援链路结果和资源开销数据。
- [x] 本地结论均标注"本地验证"属性；生产复核清单已产出。
- [x] `EXP-###` 记录齐全，失败/提前停止的实验保留。

## Goal 3：第二层自动风险处置评估

- 状态：`COMPLETED`
- 创建日期：2026-09-18
- 最近更新：2026-09-18
- 负责人：当前电脑 WSL2 负责实验；业务要求与授权信息由外部补齐
- 来源：[Leader 测试交接](../docs/13-leader-test-handoff.md) 第二层

### 目标结果

以只读/模拟方式明确 systemd-oomd、Monit、Docker API 和 systemd/cgroup 等现成机制的触发条件、对象选择、动作类型、排除规则和恢复行为，判定现成能力边界并输出 Guardian 的最小开发缺口清单。目标是自动风险处置，不是给所有容器统一设置资源上限。

### 任务清单

- [x] **G3-T01**：只读验证：压力观测、对象定位（容器/cgroup/进程组）、现场快照保存可复现（复用 Goal 2 压力场景）。
- [x] **G3-T02**：逐项记录 systemd-oomd 行为：实际监控对象（是否覆盖 docker scope）、触发条件、受害者选择逻辑、动作与恢复；只在非生产环境验证。
- [x] **G3-T03**：逐项记录 Monit 及其他现成工具的检查规则、动作能力和适用边界。
- [x] **G3-T04**：未获明确授权前，用模拟动作验证对象选择、审计、恢复和冷却流程；不执行真实 SIGKILL、容器重启或资源变更。
- [x] **G3-T05**：对照业务要求（近期资源增长 + 业务保护名单 + 允许动作），输出缺口分析结论：现成足够，或列出 Guardian 最小缺口。

### 接手入口

先读 `PROGRESS.md`、本文件、`docs/13` 和 [`docs/06-safety-policy.md`](../docs/06-safety-policy.md)。自动处置默认关闭：没有测试机、保护名单、允许动作和明确授权前，只做观测、模拟和受控测试。

### 完成标准

- [x] 至少完成一轮只读/模拟验证。
- [x] 每个现成策略都有行为记录（触发、对象、动作、排除、恢复）。
- [x] 缺口结论有实验或外部证据支撑，不把计划值写成实测值。
- [x] 没有未授权的真实终止、重启或资源变更。

## Goal 4：Guardian 最小风险检测与自动处置实现

- 状态：`COMPLETED`
- 创建日期：2026-09-18
- 最近更新：2026-09-19（EXP-018 完成本地闭环验收）
- 负责人：Mac Multipass Ubuntu 22.04 ARM64 先行实现；生产动作授权和业务策略由外部补齐
- 来源：[实验关键结论](../docs/15-experiment-findings.md)、[执行路线蓝图](../docs/14-execution-roadmap.md)

### 目标结果

在不统一修改 Docker 容器资源配置的前提下，完成一个默认安全的 Guardian 最小闭环：实时发现宕机风险，定位风险对象，核对保护名单和允许动作，按 `observe/simulate/enforce` 模式执行分级处置，并验证恢复或升级人工处理。

### 任务清单

- [x] **G4-T01**：定义风险信号和判定窗口：主机内存、swap、PSI、cgroup/OOM 事件、容器增长速率和持续时间。见 [`docs/17-risk-signal-specification.md`](../docs/17-risk-signal-specification.md)；具体数值仍需本地实验校准。
- [x] **G4-T02**：定义对象模型和策略配置：保护名单、可处理对象、业务动作级别、冷却、熔断和未知对象默认行为。见 [`docs/18-object-policy-specification.md`](../docs/18-object-policy-specification.md)。
- [x] **G4-T03**：实现第一版 `observe` 模式：实时采样、风险判定、对象定位、现场快照和 JSONL 审计记录，不执行变更。趋势窗口、快照和审计已在宿主机单元测试与 Ubuntu 实机 smoke test 中通过；更丰富的 Docker events 和业务对象解析留给后续增强。
- [x] **G4-T04**：实现 `simulate` 模式：生成动作计划，验证保护名单、动作分级和恢复判断，不执行真实终止或重启。7 个单元测试和 Ubuntu 实机验证通过，输出明确标记 `execution=not_executed`。
- [x] **G4-T05**：在可丢弃测试对象上实现并验证 `enforce` 的受控动作适配器，完成一次真实优雅停止闭环；授权校验、Docker 参数适配器、mock executor、`GuardianController`、单次运行桥接和 `ExitCode` fail-closed 判定已完成。EXP-014 的 exit 137 失败由 EXP-015 以 exit 0 修正验证；restart/terminate 仍需独立授权。
- [x] **G4-T06**：完成恢复验证、冷却、失败升级和误报测试；EXP-014 验证强制 kill 失败识别，EXP-015 验证真实恢复，EXP-016 验证跨进程冷却，EXP-017 验证连续失败升级和动作/恢复窗口超时，EXP-018 验证多对象竞争、无稳定身份和业务健康状态 fail-closed。

### 接手入口

先读 `PROGRESS.md`、本文件、[`docs/16-autonomous-execution-roadmap.md`](../docs/16-autonomous-execution-roadmap.md)、[`docs/14-execution-roadmap.md`](../docs/14-execution-roadmap.md)、[`docs/15-experiment-findings.md`](../docs/15-experiment-findings.md) 和 [`docs/06-safety-policy.md`](../docs/06-safety-policy.md)。先实现 `observe`/`simulate`，不要把 Docker 内存限制作为默认动作，也不要连接生产服务器。

### 完成标准

- [x] 能在秒级或明确的检测窗口内发现可复现的宕机风险信号。
- [x] 能正确定位风险对象，并在对象未知或命中保护名单时拒绝自动处置。
- [x] `observe`、`simulate`、`enforce` 三种模式边界清晰，默认模式不产生破坏性动作。
- [x] 每个动作都有快照、决策原因、审计记录和恢复验证结果。
- [x] 在可丢弃测试对象上完成自动处置闭环，失败时能够冷却并升级人工处理。
- [x] 没有把通用 Docker/systemd 资源限制写成所有业务的必选配置。

## Goal 5：本地生产仿真性能报告

- 状态：`COMPLETED`
- 创建日期：2026-09-19
- 最近更新：2026-09-19（EXP-021 补足真实故障预防对照）
- 负责人：当前 Agent；测试对象为 Mac Multipass Ubuntu 内的 disposable 容器
- 来源：[生产基线](../docs/10-production-baseline.md)、用户提出的生产前权限申请需求

### 目标结果

在无法进入生产服务器时，用与生产一致的 Ubuntu 22.04/systemd 249/cgroup v2/Docker 29.1.3 用户空间和代表性容器负载，完成一份可审阅的本地性能与有效性报告。报告必须明确区分：本地可证明结论、容量缩放推断和必须在生产/获授权 x86_64 测试机复核的结论。

### 任务清单

- [x] **G5-T01**：固化仿真参数、容器角色、资源预算、停止条件和可复现实验脚本。
- [x] **G5-T02**：建立无 Guardian、observe、simulate 和受控 enforce 的对照组，记录同一风险场景的结果差异。
- [x] **G5-T03**：完成基线、正常高负载、单对象内存增长、多对象竞争、CPU/IO/PID 退化和容器 churn 场景。
- [x] **G5-T04**：测量检测延迟、对象定位准确率、误报率、动作成功率、恢复/升级时间和重复动作拦截率。
- [x] **G5-T05**：测量 Guardian 的 CPU、RSS、Docker CLI 延迟、审计磁盘增长和压力下自身可用性。
- [x] **G5-T06**：形成老板可直接阅读的性能报告、结论分级、剩余风险和生产申请清单。

### 接手入口

先读 `README.md`、`PROGRESS.md`、本文件、`docs/10-production-baseline.md`、`docs/14-execution-roadmap.md`、`docs/16-autonomous-execution-roadmap.md` 和最近的 EXP-014～EXP-019。所有压测只在 `guardian-ubuntu` 的 disposable 容器中执行；任何生产连接、生产凭据、真实业务故障注入和不可逆动作都不在本 Goal 范围内。

### 完成标准

- [x] 至少完成一组无 Guardian 与 Guardian 对照实验，并保留原始核心数据。
- [x] 覆盖风险检测、对象定位、误报、受控处置、恢复和失败升级。
- [x] 报告给出 P50/P95 或等价的检测/处置时间、成功率、误报率和资源开销。
- [x] 报告明确 ARM64/2 vCPU/4GB 与生产 x86_64/32 CPU/31GiB 的差异，不把缩放推断写成实测事实。
- [x] 实验结束时无运行中的临时容器，仓库提交包含实验记录、核心数据和报告。

### 更新记录

- 2026-09-19：Goal 4 本地闭环完成；建立 Goal 5，准备在申请生产权限前完成生产仿真性能报告。
- 2026-09-19：EXP-020 完成 CPU/IO/PID、churn、无 Guardian/Guardian 对照和压力下自身开销测量；仓库可见 5 组跨文件计时，但重复证据不构成生产统计，性能报告已生成，生产交接仍需外部授权。
- 2026-09-19：根据对照证据复核，EXP-020 降级为安全性/性能基线；新增 EXP-021 真实故障预防对照，复现无 Guardian global OOM，并验证 Guardian 在 critical 阈值前后止损、恢复和健康探针保活。
- 2026-09-19：完成 EXP-022，补充多对象身份歧义、保护对象、CPU/IO 误报和恢复失败熔断边界；所有场景保持 observe/simulate 或纯 fixture，不执行真实动作。

## Goal 6：Beszel 二次开发集成

- 状态：`IN_PROGRESS`
- 创建日期：2026-09-19
- 最近更新：2026-09-19（Beszel Mac Multipass Hub/Agent 已部署并认证连接）
- 负责人：当前 Agent；主测试环境为 Mac Multipass `guardian-ubuntu`
- 来源：组长要求基于 Beszel 二次开发资源自动管理能力；当前项目路线收敛结果

### 目标结果

在不破坏 Beszel 原有监控能力、不过早修改上游核心的前提下，建立 Beszel 与 Guardian 的安全集成：Beszel 负责采集、历史、可视化和常规告警；Guardian 负责本机低延迟风险检测、对象定位、保护策略、分级处置和恢复验证。第一阶段只读，第二阶段 `observe/simulate`，最后才评估受控 `enforce`。

### 任务清单

- [x] **G6-T01**：在 `guardian-ubuntu` 的 Beszel 控制台逐项核验主机、Docker、systemd 指标；记录采集周期、缺失字段、延迟和 Hub/Agent 空载开销。（主机/Docker 可见；systemd_services 登录态 GET 为 `totalItems=0`，缺失项已证据化；更新间隔约 60 秒；EXP-023/025/026）
- [x] **G6-T02**：冻结 Beszel → Guardian 事件契约：事件 ID、来源、时间戳、风险信号、对象身份、置信度、过期时间和原始证据引用；文档见 [docs/21](../docs/21-beszel-guardian-event-contract.md)。
- [x] **G6-T03**：实现只读 `beszel_adapter`：获取或接收 Beszel 数据，标准化事件，处理认证失败、重复、乱序、过期和 Hub 不可用；`alerts_history` fixture、分页 GET、恢复边界和已登录空数据 fail-closed 均有证据。EXP-028 进一步从本地 `data.db` 确认实际 Memory 历史记录字段，但不保存或回显登录态原始 payload；不得调用 Docker/systemd 变更接口。
- [x] **G6-T04**：同一可丢弃故障场景下，对照 Beszel 告警路径与 Guardian 本机检测路径，测量检测延迟、漏报、误报、数据中断和降级行为。EXP-028 的两次有界内存运行均被 Guardian 约 6.15/6.18 秒发现，Beszel 通过本地 `data.db` 只读复核确认触发延迟约 16.378/33.159 秒并最终恢复；1% 空闲正向控制按预期触发，低于 12%/15% 的空闲样本无非预期历史事件，Hub 全程健康，Adapter 的 Hub 不可用 fail-closed 见 EXP-024。结论仅限本地 ARM64，详见 `live-alert-path-readback.json`。
- [x] **G6-T05**：将 Adapter 接入 Guardian `observe/simulate`；验证多对象、保护对象、未知对象、重复事件、过期事件和对象身份变化均 fail-closed。EXP-029 宿主机与 Multipass 全量 56/56 通过，bridge 7/7 通过；不执行真实动作。
- [ ] **G6-T06**：设计 Beszel UI 集成方案：风险等级、风险对象、策略原因、动作计划、动作结果、恢复状态和人工确认；设计稿 [docs/23](../docs/23-beszel-guardian-ui-integration-design.md)、[一页式效果对照](../docs/24-guardian-effectiveness-one-page.md)、[离线演示页](../demo/guardian-beszel-review/index.html) 和 [现场 Runbook](../docs/26-guardian-beszel-live-demo-runbook.md) 已形成并自检通过，当前状态 `DESIGN-READY-FOR-REVIEW`，待 leader/协作者评审后再实现旁路 view-model endpoint。
- [ ] **G6-T07**：在 G6-T01～T06 有完整证据后，才评估本地可丢弃对象上的受控 `enforce` 联调；restart/terminate 和生产接入必须单独授权。

### 接手入口

先读 `README.md` → `PROGRESS.md` → 本文件 → [`docs/16-autonomous-execution-roadmap.md`](../docs/16-autonomous-execution-roadmap.md) → [`docs/20-local-beszel-multipass-deployment.md`](../docs/20-local-beszel-multipass-deployment.md) → [`docs/17-risk-signal-specification.md`](../docs/17-risk-signal-specification.md) → [`docs/18-object-policy-specification.md`](../docs/18-object-policy-specification.md)。从 G6-T03 开始，不要把 Beszel 告警直接当成动作授权，不要连接生产。

### 完成标准

- [x] Beszel 主机、Docker、systemd 指标在当前本地环境逐项验收并有可复查证据；systemd_services 当前空记录作为缺失项保留。
- [x] 事件契约、对象映射、时间窗口和过期策略冻结并有测试。
- [x] 只读 Adapter 在 Hub 可用和不可用时均能安全运行，不产生资源变更；alerts_history 空数据保持 fail-closed。
- [x] Beszel 告警路径与 Guardian 本机检测路径完成同一故障场景对照；本地延迟、恢复、负向基线、正向控制和 Hub/Adapter 降级证据见 EXP-028/EXP-024。
- [x] `observe/simulate` 对重复、过期、多对象、保护对象和未知对象保持 fail-closed；EXP-029 覆盖重复、过期、乱序、低置信度、恢复和多候选对象，保护对象沿用 Observer/Controller 既有门禁。
- [ ] UI 集成方案通过 leader/协作者评审；当前已具备一页式材料、离线页面和现场 Runbook。任何 `enforce`、重启、终止和生产接入仍需独立授权记录。

### 更新记录

- 2026-09-19：Beszel 0.19.0 Hub/Agent 在 Mac Multipass `guardian-ubuntu` 部署并认证连接；建立 Goal 6，下一步从指标完整性验收开始。
- 2026-09-19：G6-T01 完成底层运行态核验，页面级字段验收保留为未完成；EXP-023 记录为 INCONCLUSIVE，不把单次空载快照当作稳定开销结论。
- 2026-09-19：完成 G6-T02 事件契约；G6-T03 形成 GET-only、白名单化、过期和身份校验的 fixture 版 Adapter，真实 Beszel 告警 payload 映射待补。
- 2026-09-19：完成 EXP-024；Adapter 增加重复/乱序窗口和传输失败降级，覆盖 alerts_history 活动/恢复/缺少映射字段 fixture，并增加分页 GET 入口；宿主机 49/49、Multipass Ubuntu 内 12/12 通过，真实 payload 映射仍待本地登录会话。
- 2026-09-19：补充 docs/22 字段清单和无凭据 API 边界；确认控制台字段基线，但登录态 UI 数值、真实数据可见性和动态延迟仍待验收。
- 2026-09-19：完成 EXP-025 有界动态探针；1 CPU worker + 128 MiB 内存 worker 持续 12 秒，Hub 健康全程 200、内存 PSI full 为 0；当时页面验收仍待登录态补充，后由 EXP-026 与 EXP-023 合并闭合 G6-T01。
- 2026-09-19：G6-T03 的只读获取和标准化边界已扩展到 alerts_history 分页 GET；后由 EXP-027 在登录态确认真实用户范围为空，Adapter 保持空数据 fail-closed。
- 2026-09-19：通过用户已登录的本地 Chrome 补充 EXP-023；确认主机在线概览、1 小时历史曲线、两个容器的 CPU/内存/网络/健康/镜像字段和告警类别可见，全部告警开关保持关闭；systemd 具体服务值、实际刷新延迟和真实告警时延仍未验证。
- 2026-09-19 23:51：补查 Beszel 登录态命令搜索和首页“服务”列；搜索 `service` 无结果，服务列无可读值。
- 2026-09-19 23:54：EXP-026 通过登录态 GET 核验 `systemd_services` 返回 `totalItems=0`；将“systemd 服务记录缺失”作为可复查验收结果，完成 G6-T01。真实告警时延仍留给 G6-T04。
- 2026-09-19 23:59：EXP-027 通过登录态 GET 核验 `alerts_history` 返回 `totalItems=0`；确认真实 payload 当前不可取得，Adapter 对空数据保持 fail-closed，完成 G6-T03。真实告警触发对照仍留给 G6-T04。
- 2026-09-20：建立 EXP-028 G6-T04 预检；确认告警开关全 off、告警历史为空，但通知投递字段已有配置。用户随后明确授权本地告警配置和有限测试通知范围。
- 2026-09-20：完成 EXP-028 两次本地有界内存对照；Guardian 两次约 6.1 秒进入 warning、约 77.2–77.3 秒恢复，Hub 全程 HTTP 200、Docker 只读采集可用、memory PSI full 为 0、worker 自然退出；Beszel `alerts_history` 两次均为 0 条，告警配置已恢复全 off。结论为 `INCONCLUSIVE`，需要已知 live payload 或更底层告警链路证据后再完成 G6-T04。
- 2026-09-20：追加 Beszel `v0.19.0` 上游源码只读诊断，记录默认 60 秒更新、Memory 使用 `Info.MemPct`、`alerts.triggered` 变化驱动 `alerts_history` 的路径；未执行上游 Go 测试，不把源码证据当作当前部署的 live 事件证明。下一步为同一时间窗的系统记录/alerts/Hub 日志三方只读核对。
- 2026-09-20：完成 EXP-028 最终只读复核：本地 `data.db` 中确认两次压力告警和一次 1% 空闲正向控制共 3 条已恢复历史；Guardian 约 6.15/6.18 秒发现，Beszel 约 16.378/33.159 秒触发，低阈值以下基线无非预期事件，G6-T04 完成并进入 G6-T05。
- 2026-09-20：完成 EXP-029/G6-T05：新增 `guardian_beszel_bridge.py`，将 Beszel 标准化事件接入 Guardian `observe/simulate`；宿主机和 Multipass 全量 56/56、bridge 7/7 通过，外部事件不获得动作授权，下一步进入 G6-T06 UI 集成设计。
- 2026-09-20：形成 G6-T06 设计稿 [docs/23](../docs/23-beszel-guardian-ui-integration-design.md)：冻结旁路 UI、`guardian.ui.v1` view model、降级状态和人工确认边界；不修改 Beszel 上游核心，状态为 `DESIGN-READY-FOR-REVIEW`。
- 2026-09-20：完成 G6-T06 本地契约自检：5 个 UI 纯函数测试覆盖字段冻结、模拟不执行、多对象和缺失身份 fail-closed；leader/协作者评审仍是完成门槛，未实现 endpoint。
- 2026-09-20：形成组长评审交付包：一页式 Guardian/Beszel 效果对照、离线可点击页面和现场演示 Runbook；页面只使用脱敏静态数据，正式评审和 G6-T07 仍未完成。
- 2026-09-20：新增交付材料离线/只读约束测试 `test_review_delivery.py`；宿主机与 Multipass 全量测试 66/66 通过，确认演示页不访问网络、不调用 Docker/systemd 执行器。

## Goal 7：Guardian 生产化实现

- 状态：`IN_PROGRESS`
- 创建日期：2026-09-20
- 最近更新：2026-09-20（建立生产化技术路线与 Agent 接手状态板）
- 负责人：按 [`docs/27`](../docs/27-production-guardian-roadmap.md) 状态板逐任务接手
- 来源：2026-09-20 独立技术架构评审

### 目标结果

将当前 Guardian 从“本地 ARM64、单对象、脚本化、单次 `graceful_stop` 有效性证据”推进为一个默认 fail-closed、可持续运行、可归因、可崩溃恢复、可审计、可回滚的 Guardian。只有完成本地 MVP、x86_64 非生产验证、生产 observe/simulate 和多方授权后，才可评估极小白名单的 `graceful_stop`。

### 任务清单

- [x] **G7-T00**：建立 [Guardian 生产化技术路线与执行手册](../docs/27-production-guardian-roadmap.md)，固定主方案、安全不变量、M0–M7 阶段门、P0/P1/P2 任务、实验矩阵和 Agent 交接协议。
- [x] **G7-T01 / PG-P0-01**：事实与状态重置；修正旧文档过度声称和 EXP-020/021/028 边界，统一口径见 [docs/28](../docs/28-claim-evidence-boundary.md)。
- [x] **G7-T02 / PG-P0-02**：实现严格 JSON 配置 schema、启动门禁、版本和 digest；证据见 [docs/29](../docs/29-guardian-config-schema.md)。
- [x] **G7-T03 / PG-P0-03**：实现 OOM 增量、趋势、PSI、swap、数据质量和滞后的组合风险引擎；证据见 [EXP-030](../experiments/EXP-030-2026-09-20-composite-risk-engine/record.md)。
- [x] **G7-T04 / PG-P0-04**：实现每容器/cgroup 归因、置信度、领先幅度和歧义放弃；证据见 [EXP-031](../experiments/EXP-031-2026-09-20-object-attribution/record.md)。
- [x] **G7-T05 / PG-P0-05**：实现对象策略、单次 capability、原子 intent/result、幂等、冷却、熄断和崩溃恢复；证据见 [EXP-032](../experiments/EXP-032-2026-09-20-durable-state-and-recovery/record.md) 和 [docs/30](../docs/30-guardian-durable-state.md)。
- [x] **G7-T06 / PG-P0-06**：实现宿主 `MITIGATED` 与业务 `BUSINESS_RECOVERED/BUSINESS_DEGRADED` 两层恢复；证据见 [EXP-033](../experiments/EXP-033-2026-09-20-two-layer-recovery/record.md) 和 [docs/31](../docs/31-two-layer-recovery.md)。
- [ ] **G7-T07 / PG-P0-07**：实现 systemd 常驻服务、watchdog、独立 slice、资源预留/上限和有界日志快照。（进行中：unit/slice、readiness/watchdog、静态校验、有界存储、依赖 fixture、transient notify/watchdog 通知接收和 45 秒/5 分钟局部 observe 已完成；EXP-039 运行中已观察到审计达到 1 MiB 有界门禁前停止增长且 Observer 继续存活；24 小时 soak、watchdog 超时/崩溃恢复、其他真实故障边界与 P99 校准未完成，见 [EXP-034](../experiments/EXP-034-2026-09-20-systemd-runtime-baseline/record.md)、[EXP-038](../experiments/EXP-038-2026-09-20-extended-observer-soak/record.md)、[EXP-039](../experiments/EXP-039-2026-09-20-24h-observer-soak/record.md)、[EXP-041](../experiments/EXP-041-2026-09-20-systemd-watchdog-notify/record.md) 和 [docs/32](../docs/32-guardian-systemd-runtime.md)。）
- [ ] **G7-T08 / PG-P0-08**：完成本地 observe → simulate → 单次授权 `graceful_stop` 对照实验。
- [ ] **G7-T09 / PG-P0-09**：在 docs/23 获得评审后实现 Beszel 只读旁路 endpoint，不为 UI 提供动作授权。
- [ ] **G7-T10**：按 docs/27 P1 完成底层机制对照、7 天 soak、x86_64 非生产 observe/simulate、单次灰度和运维交付。
- [ ] **G7-T11**：按 docs/27 P2 完成权限分离、安全审计、发布/回滚、生产 observe/simulate；生产 `enforce` 是单独多方审批项，不是 Goal 默认结果。

### 接手入口

必须先读 [`docs/27`](../docs/27-production-guardian-roadmap.md) 的第 0、2、7、10、11 节。从状态板第一个 `READY` 任务开始；当前是 PG-P0-07。当任务卡与旧 Goal 4–6 的“已完成”声称冲突时，以 docs/27 的生产化完成定义为准，以源码和实验记录核实事实。

未获得用户对当次 disposable 目标的单独明确授权前，只允许 observe/simulate。未获得外部书面授权前，不连接非生产/生产主机。

### 完成标准

- [ ] docs/27 M0–M6 全部有可复核证据，P0/P1/P2 没有未处理的硬阻断。
- [ ] 组合风险、对象归因、保护、授权、幂等、恢复和审计达到 docs/27 工程质量门。
- [ ] Guardian 在目标 x86_64 非生产和生产 observe/simulate 环境完成长跑、故障注入、禁用和回滚演练。
- [ ] 生产默认仍为 observe；restart、terminate、PID kill、整机重启和自动资源限额修改均未默认开启。
- [ ] 业务 owner、SRE、安全和变更管理的前置、授权和风险接受均有可追溯记录。

### 更新记录

- 2026-09-20：建立 Goal 7 和 docs/27；将主技术路线定为“Python Guardian 作为 Beszel 本机旁路决策/受控动作层”，当前进入 PG-P0-01。
- 2026-09-20：完成 G7-T01/PG-P0-01；建立 docs/28 声称—证据—边界对照，下一任务为 PG-P0-02 配置 Schema 与启动门禁。
- 2026-09-20：完成 G7-T02/PG-P0-02；建立严格 JSON schema、配置 digest、observe-only 默认和 fail-closed 启动门禁，下一任务为 PG-P0-03 组合风险引擎。
- 2026-09-20：完成 G7-T03/PG-P0-03；新增组合风险评估器、单调时钟、cgroup v2 当前路径解析和质量 fail-closed，证据见 EXP-030，下一任务为 PG-P0-04 对象归因。
- 2026-09-20：完成 G7-T04/PG-P0-04；新增 Docker full ID↔cgroup v2 registry、对象贡献度/置信度/领先幅度和歧义放弃，证据见 EXP-031，下一任务为 PG-P0-05 策略、授权和耐久状态。
- 2026-09-20：完成 G7-T05/PG-P0-05；新增 SQLite WAL 状态库、一次性 capability、原子 intent/result、并发 claim、审计失败前置和启动 reconciliation，证据见 EXP-032，下一任务为 PG-P0-06 两层恢复。
- 2026-09-20：完成 G7-T06/PG-P0-06；新增宿主 `MITIGATED`、业务恢复/降级和 Controller 分层输出，证据见 EXP-033，下一任务为 PG-P0-07 systemd 常驻与自身保护。
- 2026-09-20：PG-P0-07 完成第一轮本地运行基线；新增非 root Observer unit、独立 slice、readiness/watchdog 和有限日志边界，主机 111/111、VM 隔离 68/68、systemd verify 退出码 0、8 秒 observe smoke 通过；任务仍 IN_PROGRESS，长跑/故障注入/P99 校准待继续，证据见 EXP-034 和 docs/32。
- 2026-09-20：PG-P0-07 有界存储子项通过；快照/审计达到上限或写失败时拒写、保留历史并降级，主机 114/114、VM 隔离 71/71 和只读 smoke 通过，证据见 EXP-035。
- 2026-09-20：PG-P0-07 完成 45 秒局部 observe soak；最大 RSS 27,672 KiB、11 条采样/审计、stderr 0，readiness 修正为 `observe:ready`，证据见 EXP-036；24 小时 soak、真实 manager watchdog 和 P99 校准仍未完成。
- 2026-09-20：PG-P0-07 依赖故障 fixture 通过；Docker stats 超时和快照路径异常均保持降级、无未处理异常，主机 116/116、VM 73/73，证据见 EXP-037。
- 2026-09-20：PG-P0-07 5 分钟延长 soak 通过；300.02 秒、最大 RSS 27,672 KiB、43 条采样/审计、readiness `observe:ready`，证据见 EXP-038；24 小时 soak、真实 manager watchdog 和 P99 校准仍未完成。
- 2026-09-20：PG-P0-07 transient systemd readiness smoke 通过；systemd 249 user manager 对一次 `--once` Observer 返回 success 并回收 unit，证据见 EXP-040；watchdog 故障恢复和持久服务仍未验证。
- 2026-09-20：PG-P0-07 新增只读、有界资源采样器，主机 119/119、VM 76/76，当前 24 小时 soak 的 20 秒 sidecar 通过；证据回链 EXP-039，P99 统计待主 soak 完成。
- 2026-09-20：PG-P0-07 在真实 systemd 249 user manager 中完成 watchdog 通知接收验证；`WatchdogTimestampMonotonic` 非零、READY 后 unit 进入 active/running 并自然 success 退出，证据见 EXP-041。未触发超时，崩溃重启、磁盘满和 P99 仍待验证。
- 2026-09-20：PG-P0-07 新增有界资源时序汇总器，主机全量测试 121/121 通过；EXP-039 运行中 1,185 秒窗口的 119 个样本得到 RSS P99 17,492 KiB、CPU P99 0.0%、FD P99 5、线程 P99 1。该数值仅为中途 ARM64 本地校准，不改变 24 小时/P99 完成门。

## 全局边界（所有 Goal 共同遵守）

- 不承诺避免所有宕机或业务中断。
- 不在没有测试授权的情况下对生产服务器注入 CPU、内存、I/O 或网络故障。
- 不默认启用自动终止、重启容器或资源变更；`observe` 模式是默认模式。
- 不把 Docker/systemd 资源限制作为所有业务的必选方案；只有业务明确允许时才纳入对象策略。
- 本地 WSL2 结论只代表本地环境，生产兼容性必须在 Ubuntu 22.04 测试机复核。
- 环境边界：Goal 1–3 的 PoC 只代表历史 WSL2；Goal 4 起以 Mac Multipass Ubuntu 为主测试环境；生产原始报告不入库。

## 更新记录

- 2026-09-17：建立 Goal 1《两层资源保护方案测试》（原 G1-T01~T09）。
- 2026-09-18：按用户决定拆分为 Goal 1/2/3。任务映射：原 G1-T01/T02（测试机、SSH 表现确认）转为 Goal 2 外部依赖（Q-006/Q-009/Q-010）；原 G1-T03~T05（基线与压测）拆入 Goal 1/2；原 G1-T06/T07（只读/模拟验证）归入 Goal 3；原 G1-T08 实验记录要求适用于全部 Goal；原 G1-T09 缺口判定归入 Goal 3 的 G3-T05。
- 2026-09-18：根据实验结果修订路线：核心目标改为发现宕机风险并自动处置；资源限制降级为按业务选择的可选防线；新增 Goal 4 负责 Guardian 最小实现。
- 2026-09-19：建立自动化执行路线；Goal 4 进入 `IN_PROGRESS`，主测试环境切换为 Mac Multipass Ubuntu 22.04 ARM64，历史 WSL2 结果保持不变。
- 2026-09-19：完成 G4-T01 风险信号规范，确定 P0 内存风险、P1 对象定位、P2 辅助退化信号和 `normal/warning/critical/recovered/escalated` 状态边界。
- 2026-09-19：完成 G4-T02 对象策略规范，确定稳定身份、永久保护、L0–L5 动作等级、冷却、熔断和未知对象默认升级人工。
- 2026-09-19：完成 G4-T03 第一版只读 Observer，5 个单元测试和 Ubuntu 实机 snapshot/audit 验证通过；下一步进入 `simulate` 动作计划生成。
- 2026-09-19：完成 G4-T04 `simulate` 模式，7 个单元测试和 Ubuntu 实机验证通过；默认保护对象和未授权动作只升级计划，不执行任何动作。
- 2026-09-19：完成 G4-T05 第一版动作边界：授权、环境、目标 ID、保护名单、动作白名单和超时校验，以及 mock executor；12 个单元测试和 Ubuntu 实机 mock 验证通过，真实 enforce 未执行。
- 2026-09-19：完成 EXP-008 安全契约验证：恢复状态、冷却窗口和失败熔断的纯逻辑测试通过；真实动作后的恢复验证仍待明确授权。
- 2026-09-19：完成 EXP-009 控制层集成：`enforce` 事件、授权门禁、对象保护、冷却、失败熔断和恢复判断串联；宿主机与 Ubuntu 虚拟机 23/23 通过，真实 Docker 动作仍未执行。
- 2026-09-19：完成 EXP-010 运行桥接：事件快照、授权文件、mock/真实适配器门禁和只读恢复探测；宿主机与 Ubuntu 虚拟机 28/28 通过，Ubuntu 实机 `--mode enforce` CLI smoke test 生成 `pending_controller` 快照，真实 Docker 动作仍未执行。
- 2026-09-19：完成 EXP-011 本地测试镜像准备：Docker Hub 拉取超时后，使用 Ubuntu 自带静态 ARM64 BusyBox 构造 `guardian-test-base:local`；未创建、启动或停止容器，真实 enforce 仍待授权。
- 2026-09-19：完成 EXP-012 实机 observe/simulate：本地自动退出容器被稳定定位，合成风险阈值下生成 `graceful_stop` 计划并保持 `execution=not_executed`；容器自然退出，真实 enforce 仍待授权。
- 2026-09-19：完成 EXP-013 结构化审计记录：动作前事件与动作后执行、恢复、冷却和失败熔断结果统一输出为 `guardian.enforce.v1`；宿主机与 Ubuntu 虚拟机 29/29 通过，真实 Docker 动作仍待授权。
- 2026-09-19：执行 EXP-014 真实 `graceful_stop`：授权和 Docker 调用通过，但目标以 exit 137 结束；修正恢复判定为强制 kill 并 fail-closed，宿主机与 Ubuntu 测试 30/30 通过；成功复测仍待新的本地动作授权。
- 2026-09-19：完成 EXP-015 真实 `graceful_stop` 复测：目标 exit 0、OOMKilled=false，审计返回 `recovered / target_stopped`；G4-T05 标记完成，G4-T06 进入失败恢复/冷却/熔断验证。
- 2026-09-19：完成 EXP-016 真实跨进程冷却：第一次动作成功写入 ledger，第二次请求在 Docker executor 前被 `cooldown_active` 拒绝；G4-T06 的冷却子项完成。
- 2026-09-19：完成 EXP-017：真实 Docker 恢复失败连续两次后升级，第三次请求在执行器前被失败熔断；注入 runner 验证动作超时与恢复窗口超时；G4-T06 剩余多对象、误报和业务健康检查。
- 2026-09-19：完成 EXP-018：双 disposable 容器实机 observe/simulate 对多对象竞争升级，且验证无稳定身份和 unhealthy 健康状态 fail-closed；Goal 4 本地闭环完成，生产交接保留外部依赖。
- 2026-09-19：完成 EXP-019：测量 Mac Multipass Ubuntu 空载 Guardian 资源基线；单次峰值约 25.8 MiB，持续采样峰值约 26.5 MiB，生产多容器开销仍待复核。
