# 调研证据与官方资料

访问日期：2026-09-16

本文件记录选型结论所依据的官方文档或项目官方仓库。网页内容和版本会变化，上线前应按目标版本再次复核。

## 1. 证据索引

| 编号 | 官方资料 | 已核实事实 | 对项目的影响 |
| --- | --- | --- | --- |
| S01 | [Prometheus Node Exporter Guide](https://prometheus.io/docs/guides/node-exporter/) | node_exporter 暴露硬件和内核相关指标 | 可直接承担主机基础指标采集 |
| S02 | [node_exporter README](https://github.com/prometheus/node_exporter/blob/master/README.md) | PSI collector 可采集 `/proc/pressure`；额外 collector 可能产生高基数、超时或资源开销 | PSI 不必由 Guardian 重复采集；collector 必须逐项灰度 |
| S03 | [Alertmanager](https://prometheus.io/docs/alerting/latest/alertmanager/) | 支持去重、分组、路由、静默、抑制和高可用 | 负责通知治理，不负责主机处置 |
| S04 | [Prometheus Security Model](https://prometheus.io/docs/operating/security/) | HTTP/metrics 接口不应直接暴露公网；Alertmanager 无认证时接口可被操作 | 部署必须限制网络并配置认证 |
| S05 | [Alertmanager HTTPS and Authentication](https://prometheus.io/docs/alerting/latest/https/) | 支持 TLS、客户端证书策略和 basic auth | 可保护告警管理入口 |
| S06 | [cAdvisor README](https://github.com/google/cadvisor/blob/master/README.md) | 采集并导出容器资源隔离、历史资源和网络统计 | Docker 场景的主要归因组件 |
| S07 | [process-exporter README](https://github.com/ncabatoff/process-exporter/blob/master/README.md) | 从 `/proc` 选择并分组进程；不建议用 PID/启动时间作为标签 | 只监控有限业务进程组，控制基数 |
| S08 | [Docker Resource Constraints](https://docs.docker.com/engine/containers/resource_constraints/) | 容器默认没有资源限制；错误的 OOM 受害者可能使整机失效 | 资源限制是事前预防的第一优先级 |
| S09 | [Docker Runtime Metrics](https://docs.docker.com/engine/containers/runmetrics/) | `docker stats` 和 cgroup 提供运行指标；v1/v2 文件布局不同 | 实施前确认 cgroup 版本 |
| S10 | [Linux PSI](https://docs.kernel.org/accounting/psi.html) | PSI 描述 CPU、内存、I/O 竞争导致的停顿，提供 `some/full` 与阈值触发 | 作为危机判断核心信号，而非只看利用率 |
| S11 | [systemd.resource-control 源文件](https://github.com/systemd/systemd/blob/main/man/systemd.resource-control.xml) | CPU/IO 权重为相对分配；内存保护依赖 cgroup 层级；推荐 MemoryHigh 主控、MemoryMax 兜底 | Guardian 和业务必须正确组织到 cgroup 层级 |
| S12 | [systemd-oomd man page 源文件](https://github.com/systemd/systemd/blob/main/man/systemd-oomd.service.xml) | 依赖 cgroup v2 和 PSI，选择 cgroup 后 SIGKILL 其中所有进程，推荐启用 swap | 只能在明确 cgroup 边界后评估启用 |
| S13 | [oomd.conf 源文件](https://github.com/systemd/systemd/blob/main/man/oomd.conf.xml) | 支持压力阈值持续时间等配置 | 自动处置必须包含持续时间而非瞬时触发 |
| S14 | [earlyoom README](https://github.com/rfjakob/earlyoom/blob/master/README.md) | 按 MemAvailable、SwapFree 与 `oom_score` 选进程，先 SIGTERM 后 SIGKILL | 轻量但对混合业务机粒度过粗 |
| S15 | [Monit Manual](https://mmonit.com/monit/documentation/monit.html) | 可监控进程资源并自动启动、停止、重启或执行程序 | 可覆盖少量已知服务的确定性恢复 |
| S16 | [Grafana Fundamentals](https://grafana.com/docs/grafana/latest/fundamentals/) | 可查询、可视化、告警和探索多类可观测数据 | 作为诊断界面，不承担资源隔离 |
| S17 | [Netdata Alerts and Notifications](https://learn.netdata.cloud/docs/alerts-&-notifications/) | Agent/Parent 在边缘评估告警，动作可调用脚本或程序 | 快速 PoC 可用，动作安全需额外治理 |
| S18 | [Zabbix Agent](https://www.zabbix.com/documentation/current/en/manual/config/items/itemtypes/zabbix_agent) | Zabbix 具备 agent 和标准监控项体系 | 公司已有时优先复用 |
| S19 | [Zabbix Remote Commands](https://www.zabbix.com/documentation/current/en/manual/config/notifications/action/operation/remote_command) | Zabbix 动作体系包含远程命令；agent 的 `system.run[,nowait]` 后台命令没有超时且不检查结果 | 必须额外实现超时、结果验证和审计闭环，不能直接当成可靠处置器 |
| S20 | [What is Beszel](https://beszel.dev/guide/what-is-beszel) | Beszel 提供主机与 Docker/Podman 指标、历史数据和资源告警 | 直接作为公司现有监控主干复用 |
| S21 | [Beszel Agent Installation](https://beszel.dev/guide/agent-installation) | Agent 可用容器或二进制部署；Docker 统计依赖只读 Docker socket；默认监听 45876 | 生产采集需确认部署模式、端口和 socket 权限 |
| S22 | [Beszel Security](https://beszel.dev/guide/security) | 支持 SSH 或 Agent 主动 WebSocket；SSH 不提供终端且不接受输入，无法执行命令 | Beszel 可保留监控通路，但不能承担危机处置 |
| S23 | [Beszel Systemd Services](https://beszel.dev/guide/systemd) | 展示 systemd 服务状态、CPU、内存和重启次数；完整枚举需要 systemd 243+；容器 Agent 需 D-Bus 访问 | 采集 systemd 版本与 Agent 挂载，避免直接 privileged |
| S24 | [Beszel Healthchecks](https://beszel.dev/guide/healthchecks) | Hub/Agent 提供健康检查，但官方建议间隔至少 60 秒；Agent health 不代表已连接 Hub | 本机存活和端到端连接状态必须分别判断 |

## 2. 证据推导出的设计约束

### 约束 A：监控和处置必须解耦

Prometheus、Alertmanager、Grafana、node_exporter 和 cAdvisor 已覆盖采集、规则、告警与展示。把 root 处置能力塞入 exporter 会扩大攻击面，也破坏组件职责。因此 Guardian 即使开发，也应是独立服务。

### 约束 B：危机判断不能只依赖利用率

CPU 满载可能仍在完成有效工作；PSI `some/full`、业务延迟、运行队列和持续时间才能说明竞争是否造成停顿。内存同理，应使用 MemAvailable、swap 活动、memory PSI 与 OOM 事件组合判断。

### 约束 C：资源边界优先于事后终止

Docker 默认无限制，意味着单个容器可以与系统服务竞争全部资源。先设置 cgroup/Docker 的 `MemoryHigh/MemoryMax`、CPU/IO 分配和服务边界，通常比危机后猜测应该杀谁更可靠。

### 约束 D：OOM 自动化必须以 cgroup 业务身份为基础

systemd-oomd 终止整个候选 cgroup，earlyoom 按进程 `oom_score` 选择受害者。两者都要求提前定义业务边界和保护策略，否则“成功释放内存”仍可能等于“终止核心业务”。

### 约束 E：高优先级不等于保证可用

CPU/IO 权重只在竞争时分配相对份额；内存保护还依赖祖先 cgroup 的有效配置。Guardian 生存设计必须通过压测验证，并配合 swap、磁盘配额、有界队列和带外控制，不能只设置 nice 或 `OOMScoreAdjust`。

## 3. 尚未完成的环境验证

官方资料证明组件具备相应能力，但以下问题只能在公司环境确认：

- 公司是否已部署 Zabbix、Prometheus、云监控或统一日志平台。
- 目标内核是否启用 PSI，systemd 和 cgroup v2 能力是否完整。
- Docker/Kubernetes 的运行方式与现有资源限制覆盖率。
- 公司网络、证书、通知渠道、账号和审批平台如何接入。
- 历史故障是 CPU、内存、I/O、磁盘满还是应用死锁为主。
- 现有 agent 和 exporter 在生产规格下的真实资源开销。

这些答案将决定是否需要 Guardian，以及 Guardian 的最小功能范围。
