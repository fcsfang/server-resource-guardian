# 成熟方案调研与选型

文档状态：首轮调研完成  
调研日期：2026-09-16  
证据索引：[调研证据与官方资料](08-research-evidence.md)

## 1. 结论摘要

推荐方案是“成熟监控栈 + 实时风险检测 + 受控本机处置”，并按业务需要叠加预防性隔离，而不是单独开发一个无策略的自动杀进程程序：

1. 对明确允许的业务进程和容器，可选设置 cgroup/Docker 资源边界以缩小故障影响范围；不能把它作为所有应用的统一要求。
2. 公司已经使用 Beszel，因此以 Beszel Hub/Agent 作为监控主干，不再重复部署 Prometheus/Grafana；只有 Beszel 明确缺失的指标才补充组件。
3. Docker 场景加入 cAdvisor；宿主进程只监控明确配置的进程组，避免为每个 PID 创建高基数指标。
4. 使用 PSI、MemAvailable、swap、OOM 事件和业务健康联合判断危机，不以单个利用率阈值触发终止动作。
5. 第一阶段不开发自动处置：先完成监控、告警、现场快照和人工 runbook。
6. 只有现有工具无法满足“实时风险检测、对象定位、审批、保护名单、防重放、审计、结果验证”时，才开发轻量 Guardian。
7. systemd-oomd、earlyoom、Monit 和 Netdata 的动作能力只能覆盖部分场景，不能直接替代完整的安全处置层。

## 2. 官方资料确认的关键事实

### 2.1 监控与告警

- Beszel 已覆盖主机 CPU、内存、swap、磁盘、I/O、网络、load、Docker/Podman 容器统计、历史数据和资源告警；也能展示 systemd 服务状态、CPU、内存和重启次数。[S20][S23]
- Beszel Hub 与 Agent 可使用 Hub 主动连接的 SSH 或 Agent 主动连接的 WebSocket。其 SSH 服务不提供伪终端且不接受输入，不能用于执行恢复命令；这证明监控通路与处置通路需要解耦。[S22]
- node_exporter 面向类 Unix 系统暴露硬件和内核指标，并已包含 Linux PSI collector；官方提醒额外 collector 可能带来高基数、长采集时间或显著主机开销，必须逐项灰度验证。[S01][S02]
- Alertmanager 负责告警去重、分组、路由、静默和抑制，也支持高可用集群；它不是远程处置引擎。[S03]
- Grafana 可查询、可视化、告警和探索指标、日志、链路数据，适合作为统一诊断入口，但不是资源隔离组件。[S16]
- Prometheus 官方明确不应把组件 HTTP 接口直接暴露到公网；Alertmanager 未配置认证时，能访问接口的用户可创建、修改静默或操作告警。Alertmanager 可配置 TLS、客户端证书校验和 basic auth。[S04][S05]

### 2.2 进程与容器定位

- cAdvisor 以守护进程方式采集、聚合并导出容器资源隔离参数、历史使用量和网络统计，适合回答“哪个容器消耗资源”。[S06]
- process-exporter 从 `/proc` 采集经过配置选择和分组的进程。官方文档明确不建议把 PID 或启动时间放入分组名，因为会产生 Prometheus 难以承受的高基数。[S07]
- Docker 的 `docker stats` 和 cgroup 文件可提供 CPU、内存、网络和块 I/O 指标；cgroup v1 与 v2 文件布局明显不同，实施前必须确认版本。[S09]

### 2.3 资源预防与压力判断

- Docker 容器默认没有 CPU 或内存资源约束，可以使用宿主机内核调度器允许的全部资源。Docker 官方也警告 OOM 可能杀死 Docker daemon 或其他关键进程并拖垮整机。[S08]
- Linux PSI 从 `/proc/pressure/cpu|memory|io` 暴露 `some` 和 `full` 压力，并支持阈值触发；它直接描述任务因资源竞争而停顿的时间，比“CPU 100%”更接近业务受损。[S10]
- systemd 的 `CPUWeight` 和 `IOWeight` 是竞争时的相对份额，不是绝对预留；`MemoryMin`/`MemoryLow` 提供内存保护且需要在祖先 cgroup 正确分配；官方建议以 `MemoryHigh` 作为主要控制、`MemoryMax` 作为最后防线。[S11]

### 2.4 自动回收工具的边界

- systemd-oomd 依赖完整 cgroup v2 和内核 PSI。触发后会选择一个合格的后代 cgroup，并向其中所有进程发送 SIGKILL；官方强烈建议启用 swap，以便在内存压力下留出反应时间。[S12][S13]
- earlyoom 默认观察 MemAvailable 与空闲 swap，达到阈值时根据 `oom_score` 选择进程，先 SIGTERM、再 SIGKILL。它轻量但选择粒度仍然是进程，混合业务服务器上存在误杀风险。[S14]
- Monit 可按进程 CPU、内存、存活和响应状态自动启动、停止或重启服务，也可执行脚本，适合少量已知服务的确定性恢复，不适合作为开放的通用 root 远程执行器。[S15]
- Netdata 在 Agent/Parent 边缘侧实时评估告警，并允许通过可执行程序执行重启、扩缩容等动作。这降低了中心失联时的依赖，但自定义脚本的权限、白名单和审计仍需自行治理。[S17]
- Zabbix 具备 agent、触发器、通知和远程命令能力。如果公司已经部署 Zabbix，应优先做能力验证，而不是同时引入第二套监控平台。其文档同时说明，agent 的 `system.run[,nowait]` 后台命令没有执行超时且不检查执行结果，因此不能直接视为本项目要求的可靠处置闭环。[S18][S19]

## 3. 方案能力矩阵

| 方案 | 主机指标 | 进程/容器归因 | 告警 | 本机动作 | 审批与安全处置 | 结论 |
| --- | --- | --- | --- | --- | --- | --- |
| Beszel | 强，已在公司使用 | Docker 与 systemd 服务 | 内置资源/状态告警 | 无命令执行能力 | 需独立处置层 | 本项目监控主干，优先复用 |
| Prometheus + exporters + Alertmanager + Grafana | 强 | 需 cAdvisor/process-exporter | 强 | 无 | 需外部系统 | 推荐作为无现有平台时的监控主干 |
| Zabbix | 强 | 模板/agent 扩展 | 强 | 支持远程命令 | 需核实现有 RBAC、审计和动作约束 | 公司已有时优先复用 |
| Netdata | 强，实时性高 | 容器/进程可见 | 强，边缘评估 | 可执行动作程序 | 自定义动作治理需补足 | 快速单机 PoC 候选 |
| Monit | 中，偏本机 | 面向已知进程/服务 | 基础 | 强，规则化重启 | 集中审批与全局审计弱 | 少量确定性服务恢复候选 |
| systemd-oomd | 仅内存压力 | cgroup 级 | 非完整平台 | 自动 SIGKILL cgroup | 依赖正确 cgroup 组织 | 仅用于严格划分后的内存保命 |
| earlyoom | 仅内存/swap | 进程 `oom_score` | 非完整平台 | SIGTERM/SIGKILL | 粒度较粗 | 不作为混合生产机默认方案 |
| Guardian | 可按需补充 | 可绑定业务身份 | 接入现有告警 | 固定受控动作 | 可实现审批、保护、审计和验证 | 仅开发成熟工具缺失的部分 |

## 4. 推荐架构的具体组成

### 4.1 当前公司环境：Beszel

这是当前优先方案。先验证 Beszel 的实际版本和部署模式能否覆盖：

- 主机与 PSI 指标。
- Docker/cgroup 指标。
- 告警去重、抑制、通知和恢复通知。
- Top 进程/容器的现场快照。
- 动作审批、RBAC、审计和接口安全。

只为缺失部分增加采集、快照服务或 Guardian，不同时维护两套完整监控。Beszel 支持 Agent 主动连接 Hub 的 WebSocket 模式，可作为普通 SSH 失效时仍保留监控心跳的一种路径；但它不能执行处置命令。

### 4.2 公司没有统一监控平台

建议基线：

- **node_exporter**：主机、内核和 PSI 指标。
- **cAdvisor**：Docker/cgroup 资源归因；如果只有少量容器，也可先使用 Docker runtime metrics。
- **process-exporter（可选）**：只为已知业务进程组持续采集指标，不按 PID 生成标签。
- **Prometheus**：指标存储、记录规则和告警规则。
- **Alertmanager**：告警聚合、抑制、路由和通知。
- **Grafana**：主机、容器、进程组和事件时间线。
- **有界快照脚本/服务**：告警时保存 `ps`、`pidstat`、cgroup、Docker、PSI 和内核日志摘要。
- **Guardian（后续可选）**：只提供经过审批的固定动作与审计。

### 4.3 只有一台测试机，需要快速展示

Netdata 可以快速展示实时指标和边缘告警，Monit 可以验证“已知服务异常后自动重启”。但演示成功不代表适合全公司推广，PoC 仍需评估集中管理、权限、审计、数据保留和运维成本。

## 5. 是否需要自研 Guardian

满足以下全部条件前，不启动自研：

1. 现有监控已能稳定发现和定位资源危机。
2. 公司确实需要 SSH 不可用时的独立控制路径。
3. 现有 Zabbix/运维平台/云平台无法满足固定动作审批和主机本地保护。
4. 已明确不可处置对象、可自动处置对象和责任人。
5. 有非生产环境可做 CPU、内存、I/O 和失联演练。

若需求仅是“某个已知无状态服务超限后重启”，优先尝试 systemd/Monit/容器 restart policy 和资源限制，不必先开发 Guardian。

## 6. 不建议直接采用的设计

- 看到 CPU 100% 就终止占用最高的进程。
- 只根据“已用内存百分比”判断危机，忽略 MemAvailable、swap、PSI 和 OOM 事件。
- 为每个 PID 持续生成 Prometheus 时序数据。
- 对所有容器保持默认无限制，再依赖事后杀容器保命。
- 直接在混合业务主机按 earlyoom 默认规则选择最大 `oom_score` 进程。
- 在没有 cgroup 业务边界时开启 systemd-oomd 的广泛自动 SIGKILL。
- 允许中心系统向 root agent 下发任意 shell 文本。
- 把 Prometheus、Alertmanager、exporter 或 cAdvisor 接口直接暴露到不可信网络。

## 7. 最终建议

本轮 EXP-001～EXP-006 已完成本地监控、救援韧性和现成机制评估。当前下一步应依据 [执行路线蓝图](14-execution-roadmap.md) 建立 Guardian 的最小风险检测与自动处置闭环；生产兼容性、业务动作策略和授权仍需单独确认。资源限制只能作为业务明确允许时的可选防线。
