# 资源耗尽救援方案：讨论与汇报笔记

调研日期：2026-09-16  
调研方式：AnySearch 3.0.1 实时检索，结合官方文档、项目仓库和已采集的生产基线交叉验证  
适用背景：Ubuntu 22.04、systemd 249、cgroup v2、Docker 29.1.3；公司计划使用 Beszel

## 一页结论

### 推荐结论

不要在“高优先级常驻进程”和“95% 后自动杀进程”之间二选一。更可靠的方案是四层组合：

1. **事前限制**：先给 Docker/cgroup 设置 CPU、内存和 PID 边界，阻止单个工作负载拖垮宿主机。
2. **观测归因**：复用 Beszel 做主机、Docker、systemd 的实时监控和告警；用 atop 或有界快照补充进程级历史现场。
3. **保留救援能力**：把监控/救援组件放入受保护的 cgroup，但不能宣称仅靠高优先级就能保证 SSH 可用；真正兜底仍需带外控制台或独立受限控制通道。
4. **有限自动化**：CPU 失控优先限额和降权；内存危机可评估 systemd-oomd；只有明确登记的无状态、可重建对象才允许自动 SIGTERM/重启。

### 对 leader 两个想法的直接回答

| 想法 | 判断 | 需要修正的地方 |
| --- | --- | --- |
| 部署高优先级常驻进程，资源打满时还能快速进入 | **方向部分成立** | `nice`、`CPUWeight` 只是调度倾向或竞争时的相对份额，不是 CPU/内存绝对预留；常驻进程也不等于可交互入口。必须同时保护完整管理链路并做压力验证 |
| 资源达到 95%，回看历史，找出突增对象，不在白名单就杀掉 | **不能直接这样上线** | 95% CPU 不一定有故障，95% 已用内存可能包含可回收缓存；“不在白名单就杀”会误杀未知业务。正确语义应是：只有在**可处置白名单**中，且不在**保护名单**中，才允许动作 |

### 当前最值得讨论的技术路线

```text
短期：Beszel + Docker/cgroup 边界 + atop/快照 + 人工 runbook
                                |
                                v
中期：systemd-oomd（仅内存、按专用 cgroup 灰度）
      Monit/systemd（仅已知服务的确定性恢复）
                                |
                                v
后期：仍有缺口时，开发最小 Guardian 策略与执行层
```

## 1. 为什么“高优先级进程”不能直接等同于“SSH 一定能进去”

一次新的 SSH 登录至少依赖：网卡与内核网络栈、`sshd` 接受连接、创建新进程、PAM、systemd-logind、用户 session scope、shell，以及内存、PID、文件描述符和 I/O。只提高某个 Agent 或 `sshd` 主进程的优先级，不能保护整条链路。

Ubuntu 22.04 的 `pam_systemd` 会为登录会话创建 scope，并把它放在 `user.slice` 下。因此即使 `sshd.service` 位于高权重 cgroup，新登录后的 shell 也可能进入另一棵 cgroup。只改 `sshd.service` 不足以证明登录后的命令仍能执行。[E04]

### 各种“优先级”的真实含义

| 手段 | 能做什么 | 不能保证什么 |
| --- | --- | --- |
| `nice=-20` | 影响普通调度策略下进程之间的 CPU 调度倾向 | 不预留 CPU，不保护内存、I/O、PID、网络，也不保证登录链路完整 |
| `CPUWeight` / Docker `cpu_shares` | CPU 竞争时提高相对份额 | 官方明确属于软性权重，不保证固定 CPU 份额；不同 cgroup 层级配置不当还会削弱效果 [E01][E03] |
| `CPUQuota` / Docker `--cpus` | 给工作负载设置 CPU 上限 | 不是给管理进程预留 CPU；应主要用于限制业务故障域 |
| `MemoryLow` / `MemoryMin` | 对 cgroup 提供内存保护 | 依赖祖先 cgroup 正确分配；不能防止磁盘、PID、网络或内核级故障 [E01] |
| `IOWeight` | I/O 竞争时提高相对份额 | 不是绝对 IOPS/带宽预留，也不能解决设备完全卡死 |
| `OOMScoreAdjust` | 降低进程成为内核 OOM 受害者的概率 | 不能解决整个系统已无可分配内存，也不应设置成掩盖自身泄漏 |
| 实时调度 | 可获得很强的 CPU 调度优先级 | 配错可能反向饿死系统，Docker 官方也将其列为高级且可能使主机不可用的功能，不建议用于本项目 [E03] |

### 手动救援方案的正确表述

“高优先级 Agent”应改成“**受保护的管理面**”：

- Guardian/Beszel Agent/快照服务放在独立管理 slice，评估 `CPUWeight`、`IOWeight`、`MemoryLow`、自身 `MemoryHigh/MemoryMax`、`TasksMax`。
- 对普通业务容器设置上限，避免资源竞争先扩散到宿主机。
- 若目标是固定救援动作，使用已建立的出站长连接和枚举命令，不依赖临时建立新 SSH 会话。
- 若目标是交互式登录，需要专门设计受限 rescue 入口、专用账号和 session cgroup，并在 CPU、内存、I/O、PID 压力下验证。
- OS 完全失去响应时，只有 BMC/IPMI/iDRAC/iLO、云串口或虚拟化控制台属于真正的带外兜底。

结论是：受保护 Agent 能提高“危机时仍可观测、仍能执行固定动作”的概率，但不能单独承诺“SSH 一定能进去”。

## 2. 为什么不能只用 95% CPU/内存触发杀进程

### CPU 95% 不等于故障

批处理、编译、编码等任务可以长期使用全部 CPU，同时仍保持有效吞吐。应同时观察：

- 持续时间，而不是瞬时峰值。
- load、运行队列和 CPU PSI。
- SSH/管理心跳、业务延迟、错误率是否恶化。
- 消耗来源是否集中在某个 cgroup、容器或已知进程组。

### 内存“已用 95%”也不等于只剩 5%

Linux 会把空闲内存用于页缓存。判断内存危机应优先看：

- `MemAvailable`，而不是简单的 `used / total`。
- swap 剩余量与持续换入换出。
- memory PSI，尤其持续的 `full` 压力。
- cgroup `memory.events`、容器 OOM 和内核 OOM 记录。
- 业务健康及管理面是否同步恶化。

Linux PSI 官方定义正是量化 CPU、内存或 I/O 争用造成的停顿，并明确将 load shedding、暂停或终止低优先级可重建任务列为应用方式。[E02]

### 推荐状态机

```text
NORMAL
  -> WARNING：利用率/可用量异常持续 N 个窗口
  -> CRITICAL：PSI + 业务或管理面影响同时成立
  -> SNAPSHOT：保存有界现场
  -> RECOMMEND：给出责任容器/进程组和建议动作
  -> MITIGATE：仅对白名单对象执行预批准动作
  -> VERIFY：验证资源和业务是否恢复
  -> RECOVERED 或 ESCALATED
```

每次恢复也要有迟滞时间，避免在阈值附近反复停止和重启。

## 3. AnySearch 检索到的开源方案

### 3.1 监控与历史归因

| 项目 | 能力 | 适合本项目的角色 | 明确缺口 |
| --- | --- | --- | --- |
| [Beszel](https://beszel.dev/guide/what-is-beszel) | 主机、Docker、systemd、历史和告警；Generic Webhook 可发 JSON | 现有监控主干；已经完成本地 0.19.0 PoC | 不能执行命令；REST API 在小版本间可能变化 [E11][E12] |
| [atop](https://github.com/Atoptool/atop) | 记录 CPU、内存、磁盘、网络、进程、cgroup；包含区间内已经退出的进程；默认长期 raw log | 回答“事故前几分钟是谁突然增长”；补足瞬时进程现场 | 不是告警或自动处置引擎；需要磁盘配额和保留策略 [E10] |
| 有界快照脚本 | `ps`、`pidstat`、PSI、cgroup、Docker stats/events、journal 摘要 | 告警时冻结轻量证据，方便人工确认 | 需自行控制超时、输出大小和并发 |

结论：Beszel 适合容器与服务趋势，atop/快照适合进程级事后归因。二者互补，不需要因此另建一套 Prometheus/Grafana 主干。

### 3.2 内存危机自动回收

| 项目 | 触发与选择方式 | 优点 | 风险与结论 |
| --- | --- | --- | --- |
| [systemd-oomd](https://manpages.ubuntu.com/manpages/jammy/man8/systemd-oomd.service.8.html) | cgroup v2 + PSI；监控明确启用的 cgroup，选择后代 cgroup并整体 SIGKILL | Ubuntu 22.04/systemd 249 自带；业务身份可落在 cgroup；支持 `ManagedOOMPreference=avoid/omit` | 只解决内存；杀整个 cgroup；必须先设计 cgroup 层级。生产当前虽然服务已运行，但没有覆盖 Docker，不能直接认为已受保护 [E05][E06] |
| [earlyoom](https://github.com/rfjakob/earlyoom) | `MemAvailable` 和 free swap 低于阈值时按 `oom_score` 选进程；先 SIGTERM 后 SIGKILL | 很轻，锁住自身内存；支持 `--prefer`、`--avoid`、`--ignore` 和 dry-run | 选择粒度是进程，名称正则不等于业务身份；不看 CPU。Ubuntu 22.04 仓库为 1.6.2，而上游当前为 1.9.0，功能需按实际版本核对 [E07] |
| [nohang](https://github.com/hakavlad/nohang) | 内存阈值/PSI；可按名称、cgroup、exe、cmdline、UID 等调整受害者和动作 | 比 earlyoom 灵活，能按 cgroup 和多种身份匹配，也能运行自定义动作 | Python root daemon，策略复杂；官方明确警告错误配置会误杀且不存在通用参数。适合实验比较，不建议直接作为生产默认 [E08] |
| [Meta oomd](https://github.com/facebookincubator/oomd) | PSI + cgroup v2 + 插件规则 | 数据中心级、策略可扩展、支持 pre-OOM hook | 部署和调优复杂；当前环境已有 systemd-oomd，除非后者验证不足，否则不值得同时引入第二套 oomd [E09] |

### 3.3 已知服务的自动恢复

| 项目 | 能力 | 适用边界 |
| --- | --- | --- |
| [Monit](https://mmonit.com/monit/documentation/monit.html) | 对已知 PID/服务检查 CPU、总 CPU、内存和健康；支持 `for N cycles then restart` | 适合少量明确、无状态、可重启的服务；不适合扫描任意未知进程并决定谁该被杀 [E13] |
| systemd | `Restart=`、`WatchdogSec=`、`OOMPolicy=`、资源控制 | 首选用于本来就由 systemd 管理的服务；规则简单、责任边界明确 |
| Docker restart policy | 容器退出后重启 | 只解决退出后的拉起，不防止资源耗尽；必须配合 CPU/内存/PID 限制 |

### 3.4 未找到可直接采用的一体化方案

本轮 AnySearch 的通用检索和 GitHub 代码检索没有找到一个成熟、通用且安全的一体化项目，能够同时完成：

1. CPU 和内存复合危机判断。
2. 回看时间窗口并识别突增来源。
3. 将 PID、systemd unit、Docker 容器映射到稳定业务身份。
4. 保护名单、可处置白名单、审批、冷却、熔断和防重放。
5. SIGTERM/SIGKILL/容器重启后的业务恢复验证。
6. 在宿主机严重压力下仍保持可用。

检索到的项目通常只覆盖监控、内存 OOM 或已知服务重启中的一个部分。因此最现实的方向是复用成熟组件，自己只补“策略编排与受控执行”的最小缺口，而不是从头开发监控平台或照搬网上的 kill 脚本。

## 4. 对当前生产基线的含义

已采集环境中有 68 个容器，其中 55 个运行；只有 1 个设置了 CPU、内存和 PID 约束，其余 67 个无边界。这是当前最直接的宿主机耗尽风险。

因此优先级应为：

1. 先给一个低风险、无状态容器灰度设置 CPU、内存 reservation/max 和 PID 限制。
2. 用 Beszel 对比限制前后的峰值、PSI、业务健康和告警。
3. 部署 atop 或等价的有界进程历史记录，补齐“过去一段时间是谁突增”的证据。
4. 验证管理 Agent/快照服务在独立 cgroup 中的生存能力。
5. 重组一小组测试 workload slice，验证 systemd-oomd，而不是直接让它面向全部 Docker scope。
6. 自动 CPU 处置优先改为限额；只有已知可重建对象才评估自动停止或重启。

## 5. 建议拿去讨论的三条路线

### 路线 A：受控人工救援，推荐先做

组成：

- Beszel：实时监控、Docker/systemd 归因、告警。
- Docker/cgroup：资源边界，防止故障扩散。
- atop + 告警快照：保留进程级历史与现场。
- 受保护 Agent：心跳、快照和固定建议，不执行自动杀进程。
- 人工 runbook：确认责任对象后限流、SIGTERM、重启或升级带外处理。

优点：风险最低、最快形成可演示闭环，也能收集自动化所需的真实数据。  
不足：严重故障仍依赖人工确认；若没有独立控制通道，SSH 完全不可用时只能走带外控制台。

### 路线 B：成熟组件的有限自动化，第二阶段

组成：

- 内存：专用业务 slice + systemd-oomd。
- CPU：容器 `--cpus`/quota 与权重，不以 CPU 95% 自动杀任意对象。
- 已知服务：systemd/Monit 根据持续周期执行固定重启策略。
- 告警：Beszel Generic Webhook 进入审批或事件系统。

优点：自研少，行为边界较清晰。  
不足：无法覆盖任意进程；systemd-oomd 可能整体杀 cgroup，必须先做好业务分组和演练。

### 路线 C：最小 Guardian，确认缺口后再做

只补成熟工具没有的部分：

- 组合 PSI、MemAvailable、持续时间、业务健康和 Beszel 告警。
- 将进程/容器映射为稳定业务身份。
- 输出建议和有界快照。
- 执行枚举动作，不接受任意 shell。
- 本地保护名单 + 可处置白名单、审批、冷却、熔断、审计、结果验证。

不要让 Guardian 重复存储全部监控时序，也不要把 Beszel 内部 REST collection 直接作为长期稳定业务接口。若接入 Beszel，优先使用 Generic Webhook；必须使用 REST 时固定 Beszel 版本并隔离适配器。[E11][E12]

## 6. 推荐决策

建议在讨论中提出以下选择：

> 先批准路线 A 的只读和人工处置 PoC，同时验证路线 B 的资源限制与 systemd-oomd。PoC 数据证明仍需要独立处置通道后，再批准最小 Guardian。暂不批准“95% 后杀所有非保护对象”的通用自动化。

建议 PoC 验收指标：

- 受控压力开始后 60 秒内告警。
- 能定位到责任容器、cgroup 或明确进程组。
- 管理 Agent、Hub 和快照流程在压力期间保持心跳。
- 施加边界后，故障不再扩散到宿主机管理面。
- 首阶段自动变更次数为 0。
- 第二阶段动作只发生在测试白名单对象，且每次均有快照、审计和恢复验证。

## 7. 讨论前必须确认的问题

1. leader 所说“快速进去”是必须获得交互式 shell，还是能执行固定诊断/恢复动作即可？
2. 服务器是否有云控制台、BMC、串口或虚拟化控制台？
3. 是否允许逐个修改现有容器的 CPU、内存和 PID 限制？
4. 哪些业务绝对不能停，哪些无状态服务可以自动重建？
5. 谁批准停止进程或重启容器，是否需要双人审批？
6. 最近事故主要是 CPU、内存、I/O、磁盘满还是 PID 耗尽？
7. 249 服务器是否能作为非生产 PoC，允许进行有界故障注入？
8. 是否允许安装 atop，以及进程历史数据的保留周期和合规边界？

## 8. 三分钟口头汇报稿

> 我把方案分成预防、发现、救援和自动化四层。现有 Beszel 适合负责发现和容器、systemd 归因，但它不能执行命令，所以不需要换监控平台，也不能单靠它解决 SSH 失效后的处置。
>
> 高优先级常驻进程这个方向可以提高救援组件存活概率，但不能直接保证 SSH 可进。因为一次 SSH 登录还依赖网络、sshd、PAM、systemd session、内存、PID 和 I/O。CPUWeight 和 nice 只是相对优先级，MemoryLow 也需要正确的 cgroup 层级。所以更准确的方案是保护整个管理面，同时先限制业务容器。目前采集结果里 68 个容器只有 1 个有 CPU、内存和 PID 限制，这是首要风险。
>
> 自动方案也不建议只看 CPU 或内存 95%。CPU 满可能是正常计算，Linux 已用内存还包含缓存。应该组合持续时间、PSI、MemAvailable、swap、业务健康和责任对象。处置规则不是“非白名单就杀”，而是“只有明确进入可处置白名单、且不在保护名单才动”。
>
> 开源方案方面，内存有 systemd-oomd、earlyoom、nohang 和 Meta oomd；已知服务重启有 Monit；历史进程归因可用 atop。但没有一个成熟项目同时覆盖历史窗口、Docker/进程身份、CPU和内存判断、审批白名单、审计和恢复验证。因此我建议先做 Beszel + 资源限制 + atop/快照 + 人工 runbook 的 PoC；再灰度 systemd-oomd 和已知服务自动恢复；只有确认还有缺口时才开发一个很小的 Guardian 策略与执行层。

## 9. 可能追问与回答

**为什么不直接把 Agent nice 设成 -20？**  
它只影响普通 CPU 调度倾向，不保护内存、I/O、PID、网络和登录 session；可以作为参数之一，但不能作为可用性承诺。

**为什么 CPU 95% 不直接杀最大进程？**  
CPU 100% 可能仍是健康高吞吐。杀最大进程可能终止核心业务。CPU 故障更适合提前用 quota/weight 限制，再结合 PSI 和业务健康判断。

**哪个开源项目最接近自动方案？**  
内存场景是 systemd-oomd/nohang，已知服务是 Monit；没有一个候选完整覆盖任意 Docker/进程的安全自动处置。

**现有 systemd-oomd 不是已经运行了吗？**  
服务运行不等于策略生效。当前 `oomctl` 只看到部分 user slice，没有覆盖 Docker 工作负载，需要先重构 cgroup 组织并在测试 slice 验证。

**为什么还需要 atop，Beszel 没有历史吗？**  
Beszel适合主机、容器和服务趋势；atop 能记录区间内所有活跃进程，包括已经退出的短命进程，更适合回答事故前几分钟的进程级变化。

**能不能完全不自研？**  
可以先不自研。若资源边界、systemd-oomd、Monit 和人工 runbook 已满足恢复目标，就没有开发 Guardian 的必要。

## 10. 证据索引

| 编号 | 资料 | 本次采用的事实 |
| --- | --- | --- |
| E01 | [systemd.resource-control（Ubuntu 22.04/systemd 249）](https://manpages.ubuntu.com/manpages/jammy/man5/systemd.resource-control.5.html) | CPU/IO 权重、MemoryLow/Min/High/Max、ManagedOOM 行为及 cgroup 层级约束 |
| E02 | [Linux PSI](https://docs.kernel.org/accounting/psi.html) | PSI 衡量 CPU/内存/I/O 争用造成的停顿，支持阈值触发与按 cgroup 观察 |
| E03 | [Docker Resource Constraints](https://docs.docker.com/engine/containers/resource_constraints/) | 容器默认无资源限制；CPU shares 不保证预留；OOM 可能影响 Docker 和关键进程 |
| E04 | [pam_systemd（Ubuntu 22.04/systemd 249）](https://manpages.ubuntu.com/manpages/jammy/man8/pam_systemd.8.html) | 登录会话被创建为 scope 并放入 `user.slice`；session 可应用资源参数 |
| E05 | [systemd-oomd（Ubuntu 22.04）](https://manpages.ubuntu.com/manpages/jammy/man8/systemd-oomd.service.8.html) | 使用 cgroup v2 与 PSI，在内核 OOM 前选择并终止 cgroup |
| E06 | [systemd ManagedOOM](https://manpages.ubuntu.com/manpages/jammy/man5/systemd.resource-control.5.html#OPTIONS) | `ManagedOOMMemoryPressure`、`ManagedOOMPreference=avoid/omit` 及非递归注意事项 |
| E07 | [earlyoom](https://github.com/rfjakob/earlyoom) | MemAvailable + swap 阈值、oom_score、SIGTERM/SIGKILL、prefer/avoid/ignore、mlockall |
| E08 | [nohang](https://github.com/hakavlad/nohang) | PSI、可配置受害者身份与动作；root 权限和错误配置误杀警告 |
| E09 | [Meta oomd](https://github.com/facebookincubator/oomd) | PSI+cgroup v2、插件体系和 pre-OOM hook |
| E10 | [atop](https://github.com/Atoptool/atop) | 进程/cgroup 区间活动、已退出进程和长期压缩 raw log |
| E11 | [Beszel Generic Webhook](https://beszel.dev/guide/notifications/generic) | 支持通用 webhook 与 JSON payload，可作为事件集成入口 |
| E12 | [Beszel REST API](https://beszel.dev/guide/rest-api) | 返回结构在小版本间可能变化，集成必须固定版本并隔离适配层 |
| E13 | [Monit Manual](https://mmonit.com/monit/documentation/monit.html) | 可对已知进程按 CPU/内存持续 cycles 执行告警、停止或重启 |

## 11. 调研范围说明

AnySearch 本轮覆盖了通用网页检索、代码领域检索和 URL 正文提取。重点查询了 cgroup/systemd 资源保护、SSH session 归属、PSI、Docker 限制、OOM daemon、Monit、atop、Beszel webhook/API，以及“按 CPU/内存阈值和名单自动杀进程/容器”的开源实现。

“未找到一体化方案”是本轮检索结论，不等于证明互联网上绝对不存在类似项目。任何候选进入生产前仍需核对目标版本、许可证、维护状态、漏洞、发行版包差异和公司安全政策。
