# Guardian 声称—证据—边界对照表

更新时间：2026-09-20

状态：`ACTIVE`

用途：为 Goal 7 的生产化工作提供统一事实口径。任何汇报、路线或新 Agent 交接都必须区分“源码已实现”“实验已证明”“工程设计”和“尚待验证”，不能把 PoC 结果扩大为生产能力。

## 1. 结论分级

| 级别 | 含义 | 使用规则 |
| --- | --- | --- |
| `IMPLEMENTED` | 源码或测试已经实现某个局部能力 | 只能说明代码路径存在，不能代替实机、长期运行或生产验证 |
| `LOCAL-EVIDENCE` | 本地实验在明确条件下观察到结果 | 必须同时写明环境、对象、参数、样本和停止条件 |
| `ENGINEERING-DESIGN` | 路线或规范提出的目标行为 | 不得写成当前已经具备的能力 |
| `PENDING` | 依赖外部授权、业务信息或新实验 | 只能作为下一步，不得作为当前结果 |

## 2. 当前关键声称

| 声称 | 证据 | 当前允许的表述 | 不能扩大的边界 |
| --- | --- | --- | --- |
| Guardian 能在宿主机级 OOM 前止损 | [EXP-021](../experiments/EXP-021-2026-09-19-failure-prevention-comparison/report.md) | 在 Multipass Ubuntu 22.04 ARM64、约 4GB、无 swap、单个无界 disposable 泄漏容器和本地健康探针条件下，无 Guardian 组发生 global OOM，Guardian 组完成一次授权 `graceful_stop` 并保住探针 | 不是生产预测 SLA；不是多容器归因证明；不是业务自动恢复证明 |
| EXP-021 的风险对象定位有效 | EXP-021 Guardian 事件和审计 | 在该次实验中唯一候选容器被稳定 ID 识别 | 该实验刻意保持单一运行对象；不能推断复杂 fleet 中能准确选出肇事对象 |
| EXP-021 的健康探针被保住 | EXP-021 探针日志和内核日志 | 本地健康探针保持 `health-ok`，且无同轮 global OOM | 健康探针不是生产业务 health check；`oom_score_adj=1000` 是故障 fixture，不是生产配置 |
| EXP-020 有 5 轮有效重复 | `data/timings.csv` 与 `data/timings-repeated.csv` | 当前仓库可见 1 个基础 timing 文件和 r2–r5 四个重复标识，共可见 5 组计时记录 | 计时文件分散，未形成每轮完整事件/审计/恢复结果的单一可重算汇总；不能把“5 轮”当作稳健统计或生产 SLA |
| EXP-020 证明 Guardian 有效 | EXP-020 报告/记录 | EXP-020 证明正常压力下不误动作、局部动作链路可运行并提供开销基线 | 无 Guardian 组没有真实系统失败；有效性主证据必须引用 EXP-021 |
| EXP-028 证明 Guardian 比 Beszel 更快 | EXP-028 `live-alert-path-readback.json` 和记录 | 在两次本地 bounded memory pressure 中，Guardian 本地判定早于 Beszel 持久化告警被读回 | 不是接近 OOM 的通用预测 SLA；不是通知投递 SLA；仅代表本地 ARM64、特定告警配置和采样相位 |
| Beszel 可以触发 Guardian 动作 | [docs/21](21-beszel-guardian-event-contract.md)、[docs/23](23-beszel-guardian-ui-integration-design.md) | Beszel 事件只能作为经过校验的外部证据，进入 Guardian 的 observe/simulate 边界 | Beszel 告警不能直接获得动作授权；旁路 endpoint 仍待 UI 评审后实现 |
| Guardian 已经是生产服务 | 当前 `src/` 原型和 [docs/27](27-production-guardian-roadmap.md) | 已有单对象脚本化 observe/simulate/enforce 原型 | 尚无组合风险、复杂归因、耐久状态、systemd 常驻、自保护和 x86_64 非生产验证 |
| Guardian 能自动杀死任意进程 | 当前动作适配器 | 仅存在受控 Docker `terminate` 适配器代码，且默认不启用 | 没有宿主机 PID kill 生产路线；`terminate` 不属于当前自动动作；真实 `restart/terminate` 未完成安全准入 |

## 3. 当前源码与设计的差异

- `guardian_observer.py` 当前实际主要依据宿主 `MemAvailable` 比例和 cgroup memory 事件判定候选风险；增长速率、PSI 和 swap 尚未组成完整可执行风险模型。
- 当前对象生成会把运行中的 Docker 容器列为候选；真正的贡献评分、置信度和领先幅度仍属于 Goal 7 PG-P0-04。
- `config/guardian.example.yaml` 是配置样例，不是当前运行时已经加载的生产配置。
- `observe` 默认不执行动作；`simulate` 只生成计划；真实 Docker adapter 还需要显式的本地 disposable 授权和执行门禁。
- 容器退出、Docker 返回码为 0 或内存回升，只能证明局部动作/宿主缓解；不能自动写成业务恢复。

## 4. 交接时的强制口径

1. 讲 Guardian 有效性时引用 EXP-021，并同时说明单对象、ARM64、测试 fixture 和一次动作条件。
2. 讲性能时引用 EXP-020，但将 5 组计时视为分散的本地基线，不写成生产 P95/SLA。
3. 讲 Beszel 集成时说明它负责历史、展示和告警证据；动作授权仍由 Guardian 本机策略控制。
4. 讲 Goal 4 时使用“本地脚本化 PoC 已完成”；讲 Goal 7 时才讨论生产化任务，不把两者合并。
5. 任何未知、歧义、保护对象、数据过期或证据不足的情况都必须写成 `fail-closed` 或 `PENDING`，不能用“未发现问题”替代。

## 5. 下一步证据要求

Goal 7 PG-P0-01 之后，所有新增声称必须至少回链到源码/测试、实验记录、结构化数据和明确边界。若一个数字不能从提交的数据重算，就只能作为带限制的人工复核结果，不能作为质量门或生产承诺。
