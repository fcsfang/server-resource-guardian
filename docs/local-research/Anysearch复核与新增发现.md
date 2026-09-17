# Anysearch 复核与新增发现

复核日期：2026-09-16。对象：当前三份调研材料，参考环境为用户提供的 Ubuntu 22.04.5、systemd 249、cgroup v2、Docker 29.1.3；共 5 台异构服务器。

补充说明：本轮核验聚焦已选技术的准确性，不代表已穷尽选型路线；后续覆盖审查见[组长决策摘要与覆盖范围](组长决策摘要与覆盖范围.md)。

## 1. 结论

**原报告的架构方向成立，但还不能当作可部署方案。** 本轮使用 Anysearch MCP 的 `batch_search` 和 `extract` 重新检索与核验，发现需要补足的版本与集成边界，未发现证据支持以某个通用工具完全替代业务策略。

保留“原生资源隔离 + 主机/容器观测 + 本地处置 + 逐机配置”的方向。调整为：Monit 是固定规则及脚本的承载候选，不是开箱即用的 Docker 多资源保护器；cAdvisor 必须先通过 Docker 29.1 的兼容性验证；磁盘治理扩展到镜像存储、构建缓存和日志背压。

这里只完成资料复核，没有连接服务器、复现 Issue 或测量性能。项目 Issue 是具体环境下的报告，不能直接当作公司服务器已受影响的证据。

## 2. 关键判断复核表

| 原判断 | 结论 | 核验依据及修订 |
| --- | --- | --- |
| systemd 249 已有 systemd-oomd | 成立 | Ubuntu Jammy 手册明确列出；仍需 PSI、统一 cgroup v2 和内存记账，不等于本机已启用。[A1] |
| systemd-oomd 只覆盖内存压力 | 成立 | 保留其内存专项定位，不能处理全部 CPU/磁盘风险。[A1] |
| 不能直接使用新版 oomd dry-run 命令 | 保留 | Jammy 249 手册无该接口；仍需以服务器实际包和帮助为准，不依赖最新教程。[A1] |
| Docker 限额应作为基础保护 | 成立，但补 swap 语义 | 内存限制和 swap 预算需一起核验，CPU 配额限制可用时间，不承诺业务延迟。[A2] |
| Prometheus/Alertmanager 还需要动作执行端 | 成立 | 官方架构定义告警规则、聚合与通知，不能将收到通知等同于处置成功。[A3] |
| Monit 可以快速验证简单规则 | 需收紧 | 支持 check program 与动作；本轮未核实原生 Docker API 自动发现和按容器汇总资源的完整机制，应按需要脚本/API 适配估算成本。[A4] |
| cAdvisor 可作为容器监控候选 | 成立，但有具体兼容风险 | Docker 29.1 最低 API 为 1.44；旧 cAdvisor 的失败已有上游报告。[A5–A6] |
| Docker 日志轮转是必要检查项 | 成立，需扩展 | 还需关注 local 驱动、日志阻塞与丢日志取舍。[A7] |
| 需要处理停止后的自动拉起 | 成立 | Docker 官方明确提示不要让重启策略与宿主机进程管理器重复管理生命周期。[A8] |
| I/O 限速需实测 | 成立 | cgroup writeback 依赖文件系统支持，不能笼统承诺全部缓冲写入都能按期望归属和限速。[A9] |

## 3. 新发现及其影响

### 3.1 Docker 29.1 对采集器有明确的 API 门槛

Docker 官方 API 矩阵列出：29.1 的最高 API 为 1.52、最低 API 为 1.44。cAdvisor Issue #3749 报告 v0.49.1 在 Docker 29.0.0 上使用 API 1.41，导致 Docker factory 注册失败、容器数据缺失。[API 矩阵][A5]、[上游失败报告][A6]

这不是“所有 Docker 29 都不支持 cAdvisor”，也不能把旧 Issue 当作新版仍未修复的结论。新版本发布记录包含 Docker/containerd 适配及修复，但本轮没有在参考机确认任何版本组合通过。[cAdvisor releases](https://github.com/google/cadvisor/releases)

**修订**：PoC 先验收真实容器数、容器身份、CPU/内存/块 I/O/任务数、重建后的发现，以及失败日志；HTTP 200 或 Prometheus 显示 UP 不代表容器数据完整。锁定镜像版本和来源，不照搬旧教程中的镜像标签，也不优先降低 daemon 的 API 门槛来迁就旧客户端。

### 3.2 Monit 的版本与 Docker 适配成本被低估

Ubuntu Jammy 官方包页面列出的 Monit 为 `1:5.31.0-1`，这是软件源信息，不是对本机安装版本的判断。Monit 6.0.0 发布记录新增 PAGEIN/PAGEOUT 监控，并将 Linux 进程内存统计优先改为 PSS；不能混用新版手册、旧版安装和旧阈值。[Jammy 包][A10]、[Monit 发布记录][A11]

Jammy Monit 手册支持通过 `check program` 检查外部程序退出码；外部检查超时默认可达 300 秒。因此需要专门设计短超时、采样周期和失败分支，不能默认满足紧急响应要求。[Jammy 手册][A4]

**修订**：容器身份发现、Docker API 查询、聚合口径、候选选择和容器重建适配都计入脚本开发工作。若这些脚本逐渐承担完整状态机，应比较集中成一个轻量本地执行器的成本。Monit 仍是候选，但不再将其描述为“只配置就覆盖多容器保护”。

### 3.3 Docker memory reservation 不等于 memory.high

OpenContainers 的 cgroup v2 实现把 `MemoryReservation` 写入 `memory.low`，而不是 `memory.high`。前者属于尽力保护，后者用于超过边界后的回收与节流；不能用“软限制”这一名称把二者等同。[实现源码][A12]、[内核文档][A9]

该源码核验说明上游实现语义，参考机实际 runc/containerd 版本仍需核对。Docker 官方还明确：`--memory-swap` 为内存与 swap 的总预算；与 `--memory` 设为相同值时禁用该容器 swap；设定内存而未设 swap 参数时，在主机具备 swap 的前提下可能允许额外 swap。[Docker 内存配置][A2]

**修订**：需要 memory.high 时，应设计由谁管理和持久化，并读取有效 cgroup 文件确认。不得假设 Docker reservation 已完成节流，也不得让守护程序随意写入 Docker 管理的 cgroup，造成配置漂移。

### 3.4 Docker 29 的磁盘监控要覆盖实际存储后端

Docker 文档说明，新安装的 Engine 29.0 及以上默认使用 containerd image store；升级安装可能继续使用经典存储驱动。采用 containerd 存储时，镜像内容和容器快照通常位于 `/var/lib/containerd`，其他 Docker 数据仍可能位于 `/var/lib/docker`，`data-root` 不会同时迁移 containerd 数据。[Docker daemon 存储说明][A13]

**修订**：不能凭版本推定参考机存储模式。清单加入安装来源、实际 driver/snapshotter、Docker/containerd 数据目录、卷和绑定挂载；按底层挂载点监测容量及 inode，避免遗漏独立磁盘，也避免将同一文件系统重复计为多份容量。

### 3.5 日志之外还要检查构建缓存和日志背压

Docker 推荐在适用场景使用默认轮转的 `local` 日志驱动；若现有采集系统依赖 json-file，则保留该驱动并明确轮转预算。日志默认阻塞投递，非阻塞模式可减轻背压，但缓冲区满会丢消息；这需要业务选择。[日志配置][A7]

BuildKit 已有定期垃圾回收及保留策略。如果这些机器执行镜像构建，可以复用其缓存 GC；它只治理构建缓存，不替代卷、日志和全盘容量管理，也不能保证突发写入永不填满磁盘。[BuildKit GC][A14]

**修订**：不把 Docker 管理的内部日志直接交给通用 logrotate 操作；Docker stdout/stderr 日志优先使用驱动轮转，宿主机或应用自管文件另行设置 logrotate。Docker 官方也明确要求避免外部工具操作其管理的 JSON 日志文件。[json-file 说明](https://docs.docker.com/engine/logging/drivers/json-file/) 禁止把“自动执行全量 prune”作为默认磁盘保护策略。

### 3.6 新增取证候选 Below

Below 支持 cgroup v2、进程、硬件资源和 PSI 数据的 record/replay，以及可供脚本读取的 dump。对“目前不知道宕机前发生了什么”这一缺口，它比只观察当前时刻更有价值。[Below README][A15]

**定位**：本地历史取证候选，不是自动治理工具；无需替代已有集中监控。先验证 Ubuntu 安装方式、采集开销、记录空间预算与重启后的可读性；本地盘损坏或记录未落盘时仍可能丢失末尾数据。

### 3.7 新增验证参考 resctl-demo / resctl-bench

Meta 的项目提供资源争用演示和整机资源控制基准，涉及延迟敏感负载与后台负载的相互影响；与我们验证“牺牲后台任务后关键业务是否变好”的目标接近。[项目文档][A16]

**定位**：实验设计参考，不是五台服务器的现成守护服务。项目会检查乃至更新系统配置，应只在隔离环境评估，并先核对内核、设备等要求。当前不建议为采用这一工具而改变生产机器配置。

## 4. 更新后的验证优先级

1. **版本与观测可信度**：核实 Docker Server API、containerd/runc、Monit/cAdvisor 版本，以及全部预期容器是否有有效指标。
2. **磁盘范围**：确定实际存储目录与挂载点，检查日志、构建缓存、卷和容器可写层。
3. **内存语义**：核对 memory.max、memory.low、memory.high、memory.swap.max 的实际值与归属，避免把配置名称当作生效证据。
4. **生命周期协调**：选定一个自动恢复主责；测试 Docker restart、Monit 和本地保护之间不会形成反复拉起。
5. **取证与故障注入**：按正常峰值和隔离压力实验校准规则；有需要再引入 Below 或借鉴 resctl-bench。

本轮没有证据要求立即更换总体架构，也未完成能够决定“Monit 一定够用”或“必须自研”的生产验证。当前新增价值主要是提前识别集成风险，并把验收标准落到真实数据和实际控制效果。

## 5. 检索方法与证据范围

通过 Anysearch 分批检索 Docker 29 API/cAdvisor、Jammy systemd、Monit Docker 支持及版本、cgroup 内存/I/O 语义、Docker 存储与 GC、历史记录和资源控制工具，再用 extract 读取一手资料。

通用搜索结果含第三方教程和论坛，仅用于发现线索；报告技术结论优先采用官方文档、项目源码和项目上游 Issue。搜索摘要可能包含提问者的错误假设，例如某 Issue 将 memory-reservation 当作 memory.high，本报告使用实现源码核对，没有沿用该假设。

Anysearch 对 Docker 29 发布说明页面首次提取失败，改取官方仓库 Markdown 后又受到 50,000 字符截断，未取得 29.1.3 完整小节。因此 API 结论使用独立官方矩阵；本轮不声称完成 29.1.3 所有已知问题审计。长版内核和 Monit 手册也存在截断，未读取到的内容不作为已经全文核验处理。

## 6. 来源

- A1：[Ubuntu Jammy systemd-oomd 手册][A1]
- A2：[Docker Resource constraints][A2]
- A3：[Prometheus Alerting overview][A3]
- A4：[Ubuntu Jammy Monit 手册][A4]
- A5：[Docker Engine API 版本矩阵][A5]
- A6：[cAdvisor Issue #3749][A6]
- A7：[Docker 日志配置][A7]
- A8：[Docker restart policies][A8]
- A9：[Linux cgroup v2 文档][A9]
- A10：[Ubuntu Jammy Monit 软件包][A10]
- A11：[Monit 发布记录][A11]
- A12：[OpenContainers cgroup v2 memory 实现][A12]
- A13：[Docker daemon 数据目录][A13]
- A14：[BuildKit 垃圾回收][A14]
- A15：[Below 官方 README][A15]
- A16：[resctl-demo / resctl-bench][A16]

[A1]: https://manpages.ubuntu.com/manpages/jammy/man8/systemd-oomd.service.8.html
[A2]: https://docs.docker.com/engine/containers/resource_constraints/
[A3]: https://prometheus.io/docs/alerting/latest/overview/
[A4]: https://manpages.ubuntu.com/manpages/jammy/man1/monit.1.html
[A5]: https://docs.docker.com/reference/api/engine/
[A6]: https://github.com/google/cadvisor/issues/3749
[A7]: https://docs.docker.com/engine/logging/configure/
[A8]: https://docs.docker.com/engine/containers/start-containers-automatically/
[A9]: https://docs.kernel.org/admin-guide/cgroup-v2.html
[A10]: https://packages.ubuntu.com/jammy/monit
[A11]: https://mmonit.com/monit/changes/
[A12]: https://raw.githubusercontent.com/opencontainers/cgroups/main/fs2/memory.go
[A13]: https://docs.docker.com/engine/daemon/
[A14]: https://docs.docker.com/build/cache/garbage-collection/
[A15]: https://raw.githubusercontent.com/facebookincubator/below/main/README.md
[A16]: https://github.com/facebookexperimental/resctl-demo
