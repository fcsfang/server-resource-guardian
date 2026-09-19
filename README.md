# Server Resource Guardian

服务器资源监控、危机预警与受控处置项目。

> 本文件是项目总入口：用于让第一次接触项目的人、leader、协作者和新的 agent 快速理解项目内容，并知道下一步如何安全行动。细节以 [文档总目录](docs/README.md)、[执行蓝图](docs/14-execution-roadmap.md)、[项目进度](PROGRESS.md)、当前 [Goal](goals/README.md) 和 [实验记录](experiments/README.md) 为准。

更新时间：2026-09-20

## 30 秒理解项目

公司服务器曾出现 CPU、内存或其他资源占用过高，导致服务器响应变慢、SSH 无法登录，最后只能重启并影响生产的问题。

本项目要解决的是：**在资源危机前发现风险，在危机发生时保留人工救援能力，并在得到明确授权后通过受控策略缓解风险。**

项目优先复用成熟能力，不默认重新开发一套完整监控平台：

```text
成熟监控能力 → 保存历史、展示状态并提供常规告警
实时检测能力 → 发现主机和容器正在接近宕机风险
对象与策略判断 → 定位风险对象，核对保护名单和允许动作
受控处置能力 → 自动执行分级动作并验证恢复结果
```

## 项目目标与边界

### 当前需要验证的两个层次

| 层次 | 目标 | 优先技术路线 | 验收重点 |
| --- | --- | --- | --- |
| 第一层：救援能力保障 | CPU 或内存高压时，管理员仍能进入服务器并完成救援 | 验证 Linux、systemd/cgroup 和 Docker 隔离对救援链路的影响；资源限制只作为业务允许时的可选防线 | 新建 SSH 连接 → 执行诊断命令 → 停止测试中的异常任务 |
| 第二层：自动风险处置 | 没有人及时操作时，系统发现宕机风险、定位对象、执行允许动作并验证恢复 | Guardian 负责实时检测、策略判断和处置；优先复用 systemd-oomd、Monit、Docker API 等机制，不统一给容器加资源上限 | 检测 → 定位 → 保护判断 → 分级处置 → 验证/升级 |

第一层和第二层可以叠加，但自动处置不能替代人工救援保障。Docker/systemd 资源限制可能改变应用行为，只能按具体业务明确允许后使用，不能作为本项目的统一验收目标。

### 不在当前承诺范围内

- 不承诺避免所有类型的宕机或业务中断。
- 不把本地 WSL2 PoC 结果直接当作生产兼容性结论。
- 不在没有测试授权的情况下对生产服务器注入 CPU、内存、I/O 或网络故障。
- 不默认启用自动终止进程、重启容器、限制资源或删除业务数据。
- 不因为存在五台服务器就假定它们有可互换副本、冗余容量或统一配置。
- 不把“能监控”写成“能自动定位、处置并保证业务恢复”。

## 当前状态

当前项目已完成本地监控、救援韧性、现成自动保护机制评估、Guardian 最小闭环和本地生产仿真报告，尚未进入生产部署。

- 阶段 0：需求与环境确认，已完成。
- 阶段 1：只观测 PoC，已完成本地验证。
- 阶段 2：救援韧性验证，已完成本地验证，生产复核待授权。
- 阶段 3：自动风险处置机制评估，已完成本地验证。
- 阶段 4：Guardian 最小实现与本地受控闭环，已完成；生产交接待外部条件。
- 阶段 5：本地生产仿真性能报告，已完成；报告已形成，生产测试权限待申请。
- 当前执行蓝图：[docs/14-execution-roadmap.md](docs/14-execution-roadmap.md)，以 Beszel 为监控基础。
- 当前活动目标：执行 Goal 6 Beszel 二次开发集成；详见 [自动执行路线](docs/16-autonomous-execution-roadmap.md) 和 [Goal 6](goals/resource-protection.md#goal-6beszel-二次开发集成)。
- 当前下一步：进入 Beszel 告警路径与 Guardian 本机检测对照（G6-T04）；G6-T01 已完成指标可见性、缺失项、更新间隔和开销验收，G6-T03 已完成只读 Adapter 和空数据 fail-closed。生产测试授权、保护名单和 x86_64 兼容性复核仍待外部条件。

权威状态和待办只看 [PROGRESS.md](PROGRESS.md)；不要只依据聊天记录、旧 PPT 或本机运行态判断项目进度。

## 当前环境快照

以下是已记录的环境事实，详细来源和限制见 [生产基线](docs/10-production-baseline.md) 与 [本地 Beszel PoC](docs/11-local-beszel-poc.md)：

| 环境 | 已知情况 | 边界 |
| --- | --- | --- |
| 生产环境 | Ubuntu 22.04.5、systemd 249、cgroup v2、Docker Engine 29.1.3；68 个容器中 67 个没有资源边界 | 生产原始报告不入库；实际 SSH 故障表现、保护名单和测试授权仍需确认 |
| 当前主测试环境（Mac Multipass） | Ubuntu 22.04.5 ARM64；2 vCPU/4GB；Docker Engine 29.1.3；systemd/cgroup v2/PSI 可用 | 用于 Goal 4 功能和有界压力实验；虚拟机访问 GitHub/Docker Hub 仍不稳定，不能替代生产 x86_64 测试机 |
| 历史本地 PoC（Windows WSL2） | Ubuntu 26.04.1；Docker Engine 29.1.3；Beszel 0.19.0 Hub/Agent 已上线 | Goal 1–3 的实验环境；结果不改写为 Mac 或生产结论 |

## 仓库结构与职责

### 顶层目录

| 目录 | 作用 | 管理边界 | 入口 |
| --- | --- | --- | --- |
| `config/` | Guardian 配置样例和默认策略 | 只放样例；危险动作默认关闭，不放真实凭据 | [guardian.example.yaml](config/guardian.example.yaml) |
| `deploy/` | Beszel、systemd、容器等部署配置 | 当前 `deploy/beszel/` 是本地 PoC；真实 `.env` 不提交 | [deploy/README.md](deploy/README.md) |
| `goals/` | 跨 agent 可接手的目标、任务、依赖和完成标准 | Goal 编号只增不减；计划不等于实验结果 | [goals/README.md](goals/README.md) |
| `docs/` | 需求、方案、架构、安全、测试、生产事实和证据 | 稳定知识与决策文档；以总目录标记权威关系 | [docs/README.md](docs/README.md) |
| `experiments/` | 实验日志、脱敏核心数据和可审阅证据 | 每次实验使用唯一 `EXP-###`；失败实验也保留 | [experiments/README.md](experiments/README.md) |
| `reports/` | 本机原始采集报告、日志和快照 | 默认不入库，可能包含生产敏感信息 | 仅本机查看 |
| `scripts/` | 环境采集和 PoC 辅助脚本 | 脚本需写明适用环境、权限和副作用 | 直接查看脚本头部说明 |
| `src/` | Guardian 或其他项目源代码 | 当前为占位目录；实现边界确认后再扩展 | [src/README.md](src/README.md) |
| `tests/` | 自动化测试、集成测试和故障演练 | 测试必须绑定环境、授权、停止和恢复条件 | [tests/README.md](tests/README.md) |
| `wsl/` | 本地 WSL2 配置样例 | 只代表本地开发环境，不当作生产配置 | 查看目录内配置 |
| `汇报/` | 最终 PPT、讲稿、预览和生成素材 | 汇报材料不替代工程事实和实验记录 | [最终 PPT](汇报/服务器资源保护-现成策略优先-重构版.pptx) |

### 根目录关键文件

| 文件 | 作用 | 更新时机 |
| --- | --- | --- |
| `README.md` | 项目总览和行动规则 | 项目目标、结构或基本行动方式变化时更新 |
| `PROGRESS.md` | 项目当前状态总账 | 阶段完成、阻塞、决策变化或任务交接后更新 |
| `.gitignore` | 凭据、运行态和原始敏感数据边界 | 新增本地敏感产物类型时更新 |

## 信息权威层级与阅读顺序

不同文件承担不同职责，不能把所有 Markdown 都当作同等权威：

```text
README.md
  → docs/14-execution-roadmap.md（执行蓝图）
  → PROGRESS.md
  → goals/README.md → 当前 Goal 文件
  → docs/README.md → 当前任务对应的规范/方案/测试文档
  → experiments/README.md → 最近相关 EXP 实验记录
```

当前任务的具体参考关系：

1. **技术路线与执行阶段：** [docs/14-execution-roadmap.md](docs/14-execution-roadmap.md)。
2. **项目状态、当前待办：** [PROGRESS.md](PROGRESS.md)。
3. **Leader 最新反馈和测试入口：** [docs/13-leader-test-handoff.md](docs/13-leader-test-handoff.md)。
4. **目标、任务和接手说明：** [goals/resource-protection.md](goals/resource-protection.md)。
5. **测试矩阵、验收和停止条件：** [docs/07-poc-blueprint.md](docs/07-poc-blueprint.md)。
6. **处置安全边界：** [docs/06-safety-policy.md](docs/06-safety-policy.md)。
7. **未知项和待确认授权：** [docs/05-open-questions.md](docs/05-open-questions.md)。
8. **生产环境事实：** [docs/10-production-baseline.md](docs/10-production-baseline.md)。
9. **本地运行事实：** [docs/11-local-beszel-poc.md](docs/11-local-beszel-poc.md)。
10. **真实实验结论：** [experiments/](experiments/README.md) 中对应的 `EXP-###` 记录。

`docs/research-notes/` 是补充研究和证据材料；`汇报/` 是展示成品和生成素材。它们可以支持主文档，但不能绕过主文档单独改变项目范围、安全边界或验收结论。

## 标准行动流程

### 接手任务

1. 拉取最新代码并确认工作区状态。
2. 阅读本文件、`PROGRESS.md`、`goals/README.md` 和当前 Goal 文件。
3. 阅读当前任务指定的最新交接、方案和安全文档。
4. 区分已确认事实、计划、推测、历史资料和待确认问题。
5. 找到本次任务的完成标准、依赖、授权、停止条件和恢复方式。

### 执行任务

1. 先做低副作用的读取、检查、模拟或隔离验证。
2. 需要真实实验时，先分配 `EXP-###` 并写好实验目的、环境、授权、保护对象和停止条件。
3. 每个重要结论都保存可追溯证据：实验数据、日志、截图、命令输出或可靠来源。
4. 发现关键前提缺失时，停在安全状态，记录阻塞，不猜测、不盲目重试。
5. 涉及外部系统、生产环境、权限、删除、重启、自动终止或资源变更时，先确认目标和授权。

### 完成交付

1. 更新实验记录：实际步骤、结果、数据、异常、限制和结论。
2. 更新当前 Goal：勾选任务、调整状态、补充证据链接和下一步。
3. 更新 `PROGRESS.md`：记录完成内容、环境、阻塞和交接入口。
4. 如果改变了需求、架构、安全边界或测试口径，更新对应 `docs/` 主文档并记录原因。
5. 做最小必要验证，报告已验证和未验证范围。
6. 提交并推送仓库，确保下一位 agent 可以从文件继续。

## 必须遵循的项目规则

- **先观测、再建议、后自动化。**没有可靠观测和证据，不直接进入自动处置。
- **先取证、再分级处置；先验证、再升级动作。**任何动作都要能说明处理对象、允许原因和预期终态；资源限制不是默认动作。
- **计划不等于事实。**“准备测试”“命令成功”“资源下降”分别不等于“测试完成”“业务成功”“问题恢复”。
- **不依据单个瞬时阈值杀进程。**资源利用率、压力、持续时间、业务影响和对象身份需要组合判断。
- **关键对象必须有保护名单。**系统关键进程、业务核心服务、不可中断任务和未知对象不能默认进入自动处置范围。
- **高影响动作必须可停止、可恢复、可审计。**缺少授权、停止条件、回滚路径或恢复验证时，只执行安全只读检查。
- **环境必须带边界。**开发机、测试机、生产机与本机 WSL2 环境的版本、权限、凭据和运行态不能混写。
- **失败实验必须保留。**失败、提前停止和不确定结果同样是证据，不能删除来美化结论。
- **不提交敏感信息。**密码、token、`.env`、生产原始日志、未脱敏业务标识和无界运行态数据不能进入共享仓库。
- **不要过度建设。**先完成当前 Goal 的最小闭环，只有实测证明现成能力不足时，才扩大代码、平台或自动化范围。

## 仓库与本地运行态

GitHub 仓库保存工程文档、配置样例、实验记录和脚本；本地运行态（凭据、容器、指标数据）不进入仓库。

开始工作：

```bash
git pull --rebase origin main
git status --short --branch
```

完成一轮工作：

```bash
git add <changed-files>
git commit -m "<说明本轮事实变化>"
git push origin main
```

同步约定：

- 推送前先拉取。
- 不使用强制推送，不把未验证的本地运行态写成共享事实。
- `PROGRESS.md` 记录项目状态；生产原始报告、`.env`、Docker 容器和 Beszel 运行数据保留在本机，不入库。
- 交接时必须写明：已经完成什么、证据在哪里、还缺什么、下一步先做什么。

## 当前第一步

当前不要直接开发完整平台，也不要直接在生产机做故障注入。先按 [执行蓝图](docs/14-execution-roadmap.md) 定义 Guardian 的最小风险检测和自动处置闭环：

1. 已完成主机/容器风险信号、趋势窗口和误报抑制规则的第一版设计。
2. 已完成对象分类、保护名单、允许动作和 `observe/simulate/enforce` 边界设计。
3. 已实现只生成计划的 `simulate`，不执行真实终止、重启或资源变更。
4. 使用受限动作适配器在可丢弃测试对象上验证，禁止把通用 Docker 内存限制作为默认动作。
5. 在 Ubuntu 22.04 非生产环境复核后，再讨论生产灰度。

## 相关入口

- [执行路线蓝图](docs/14-execution-roadmap.md)
- [自动化执行路线与 Agent 接手协议](docs/16-autonomous-execution-roadmap.md)
- [Guardian 风险信号规范](docs/17-risk-signal-specification.md)
- [Guardian 对象策略规范](docs/18-object-policy-specification.md)
- [项目文档总目录与管理规范](docs/README.md)
- [目标与任务目录](goals/README.md)
- [实验日志与核心数据目录](experiments/README.md)
- [当前项目进度](PROGRESS.md)
- [Leader 最新测试交接](docs/13-leader-test-handoff.md)
- [PoC 验证蓝图](docs/07-poc-blueprint.md)
- [生产安全与处置策略](docs/06-safety-policy.md)
- [GitHub 仓库](https://github.com/fcsfang/server-resource-guardian)
