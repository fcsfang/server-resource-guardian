# 文档总目录与管理规范

更新时间：2026-09-20

这是本项目文档的唯一总入口。阅读、修改或新增文档前，先从这里判断它属于哪一类，以及哪一份文档是当前生效依据。

## 0. 执行与实验入口

- [目标与任务目录](../goals/README.md)：记录跨 agent 可接手的目标组和任务清单；当前生产化主线为 `Goal 7`。
- [实验日志与核心数据](../experiments/README.md)：记录真实实验过程、结果和脱敏核心数据；实验编号使用 `EXP-###`。
- 当前生产化执行目标：按 [Guardian 生产化技术路线](27-production-guardian-roadmap.md) 推进 Goal 7；Goal 6 只保留尚未完成的 Beszel UI 评审边界。
- 后续 Agent 执行总路线：[Guardian 生产化技术路线与执行手册](27-production-guardian-roadmap.md)。
- [自动化执行路线与 Agent 接手协议](16-autonomous-execution-roadmap.md) 保留为 PoC 历史路线，不再作为生产化完成判定。
- 当前风险信号设计：[Guardian 风险信号规范](17-risk-signal-specification.md)。
- 当前对象策略设计：[Guardian 对象策略规范](18-object-policy-specification.md)。
- 当前动作边界设计：[Guardian 受控动作适配器契约](19-action-adapter-contract.md)。
- 当前 Mac Beszel 运行状态：[Mac Multipass 本地 Beszel 部署记录](20-local-beszel-multipass-deployment.md)。
- 当前声称、证据和边界口径：[Guardian 声称—证据—边界对照表](28-claim-evidence-boundary.md)。

## 1. 当前最新参考关系

| 要回答的问题 | 当前首要参考 | 使用规则 |
| --- | --- | --- |
| Guardian 生产化选什么路线、下一步做什么？ | [Guardian 生产化技术路线](27-production-guardian-roadmap.md) | **当前唯一生产化执行基线**；任务状态、阶段门、授权和 Agent 接手均按此文 |
| 项目现在做到哪一步、环境如何？ | [PROGRESS.md](../PROGRESS.md) | 项目进度、环境差异和待办的总账；每次阶段性工作后更新 |
| leader 最新要求是什么、下一步如何执行？ | [Leader 测试交接](13-leader-test-handoff.md) | 两层方案和授权边界背景；后续生产化任务与旧记录冲突时以 27 号路线为准 |
| 测试如何设计、怎样验收和何时停止？ | [PoC 验证蓝图](07-poc-blueprint.md) | 通用测试矩阵、指标、输出物和停止条件；按 13 号文档补充 leader 最新范围 |
| 任何处置动作的安全边界是什么？ | [生产安全与处置策略](06-safety-policy.md) | 默认关闭危险动作；保护名单、审批、审计和熔断优先级最高 |
| 生产服务器已经确认了什么？ | [生产环境基线与风险分析](10-production-baseline.md) | 只引用已采集事实；未知项仍看 [待确认问题](05-open-questions.md) |
| 本地 Beszel 已经部署和验证了什么？ | [Mac Multipass 本地 Beszel 部署记录](20-local-beszel-multipass-deployment.md) + [本地 Beszel PoC 部署](11-local-beszel-poc.md) | 20 号文档是当前 Mac 运行状态；11 号文档保留历史 WSL2 验证和通用步骤 |
| 最终向 leader 展示什么？ | [最终 PPT](../汇报/服务器资源保护-现成策略优先-重构版.pptx) + [配套讲稿](../汇报/服务器资源保护-现成策略优先-讲稿.md) | 汇报成品；不能替代工程测试记录 |

### 当前推荐阅读顺序

```text
README.md
  → docs/27-production-guardian-roadmap.md
  → PROGRESS.md
  → docs/13-leader-test-handoff.md
  → docs/15-experiment-findings.md
  → goals/resource-protection.md → Goal 7
  → docs/07-poc-blueprint.md
  → docs/05-open-questions.md
  → docs/06-safety-policy.md
  → docs/10-production-baseline.md / docs/11-local-beszel-poc.md
```

## 2. 工程主文档（编号目录）

编号文档是项目的工程主线。编号用于稳定导航，不代表所有文件都同样新；具体以“当前最新参考关系”和文档内更新时间为准。

| 文件 | 作用 | 状态与权威性 |
| --- | --- | --- |
| [01-requirements.md](01-requirements.md) | 任务背景、目标、范围、功能与非功能需求 | 需求基线；需求变化时更新 |
| [02-solution-research.md](02-solution-research.md) | 成熟方案能力对比、组合方式和是否自研的初步研究 | 方案参考；不是最终测试结论 |
| [03-architecture.md](03-architecture.md) | 观测通路、控制通路、资源边界、Guardian 和安全模型 | 架构基线；实现前后需与实测结果对照 |
| [04-delivery-plan.md](04-delivery-plan.md) | 阶段 0–4 的实施计划、阶段门和度量指标 | 项目路线图；阶段状态以 PROGRESS 为准 |
| [05-open-questions.md](05-open-questions.md) | P0/P1/P2 未决问题、责任人和所需证据 | 活文档；得到答案后必须回填 |
| [06-safety-policy.md](06-safety-policy.md) | 保护对象、动作优先级、执行前后检查和熔断 | 安全规范；任何代码和测试不得绕过 |
| [07-poc-blueprint.md](07-poc-blueprint.md) | PoC 工作包、故障演练矩阵、验收指标和停止条件 | 当前通用测试基线 |
| [08-research-evidence.md](08-research-evidence.md) | 官方资料、证据索引和从证据推导出的约束 | 事实/来源层；不把工程判断写成厂商保证 |
| [09-production-discovery.md](09-production-discovery.md) | 生产信息采集方法、脚本、WSL2 复现边界 | 生产事实采集说明；原始报告不入库 |
| [10-production-baseline.md](10-production-baseline.md) | 生产版本、容器资源边界和主要风险 | 当前生产基线摘要；新增采集结果需更新 |
| [11-local-beszel-poc.md](11-local-beszel-poc.md) | 本地 Beszel Hub/Agent 部署、认证、开销和生产差异 | 本地运行记录；不等同于生产验收 |
| [12-leader-discussion-research.md](12-leader-discussion-research.md) | 围绕 leader 原始问题形成的讨论、路线和技术解释 | 讨论背景；最新行动方向以 13 号交接文档为准 |
| [13-leader-test-handoff.md](13-leader-test-handoff.md) | leader 最新反馈、两层方案、测试顺序、授权前提和新会话接续方式 | **当前下一步工作的最新入口** |
| [15-experiment-findings.md](15-experiment-findings.md) | 实验关键结论汇总：失效机制、管道时效、架构决策、Guardian 最小功能 | **实验结论的唯一汇总入口** |
| [14-execution-roadmap.md](14-execution-roadmap.md) | PoC 总执行蓝图：技术路线（Beszel 基础）、执行阶段和测试方式 | 历史 PoC 总纲；生产化以 27 号路线为准 |
| [16-autonomous-execution-roadmap.md](16-autonomous-execution-roadmap.md) | Goal 4–6 的阶段、任务顺序和 Agent 交接协议 | 历史 PoC/集成执行记录；不作为生产化完成判定 |
| [17-risk-signal-specification.md](17-risk-signal-specification.md) | G4-T01 风险信号、状态机和事件输出结构 | 当前实现设计入口 |
| [18-object-policy-specification.md](18-object-policy-specification.md) | G4-T02 对象身份、保护名单、动作等级、冷却和熔断 | 策略设计入口；对象归因 MVP 证据见 EXP-031 |
| [19-action-adapter-contract.md](19-action-adapter-contract.md) | G4-T05 授权校验、mock executor 和 Docker 动作参数契约 | 真实动作前的安全边界 |
| [20-local-beszel-multipass-deployment.md](20-local-beszel-multipass-deployment.md) | 当前 Mac Multipass Hub/Agent 部署、认证证据和后续指标核验 | 当前本地运行状态 |
| [21-beszel-guardian-event-contract.md](21-beszel-guardian-event-contract.md) | Beszel → Guardian 事件格式、身份映射、过期和 fail-closed 规则 | Goal 6 联调契约 |
| [22-beszel-dashboard-field-inventory.md](22-beszel-dashboard-field-inventory.md) | 静态 bundle 字段基线与未登录 API 边界 | G6-T01 页面验收前置清单 |
| [23-beszel-guardian-ui-integration-design.md](23-beszel-guardian-ui-integration-design.md) | Beszel 与 Guardian 的旁路 UI、view model、策略状态和人工确认设计 | G6-T06 设计稿，评审前不修改 Beszel 上游 |
| [24-guardian-effectiveness-one-page.md](24-guardian-effectiveness-one-page.md) | Guardian 故障预防效果、Beszel 集成效果和组长评审结论一页纸 | 当前组长评审材料 |
| [25-leader-review-delivery-roadmap.md](25-leader-review-delivery-roadmap.md) | 交付前文档、页面、演示、验收和同步路线 | 当前交付执行路线 |
| [26-guardian-beszel-live-demo-runbook.md](26-guardian-beszel-live-demo-runbook.md) | 明早验收和组长现场演示步骤、话术和安全边界 | 当前现场演示入口 |
| [27-production-guardian-roadmap.md](27-production-guardian-roadmap.md) | 生产化主方案、安全不变量、P0/P1/P2 任务、实验门、生产准入和 Agent 交接 | **当前生产化唯一执行基线** |
| [28-claim-evidence-boundary.md](28-claim-evidence-boundary.md) | 当前项目声称、源码/实验依据和不可扩大边界 | Goal 7 M0 事实口径；新增结论必须回链 |
| [29-guardian-config-schema.md](29-guardian-config-schema.md) | Guardian JSON 配置、启动门禁、digest 和回滚边界 | Goal 7 PG-P0-02；组合风险现状回链 EXP-030 |
| [30-guardian-durable-state.md](30-guardian-durable-state.md) | SQLite WAL、capability、intent/result、审计、冷却和崩溃恢复 | Goal 7 PG-P0-05 本地 MVP |

## 3. 补充研究材料

[`research-notes/`](research-notes/README.md) 保存补充研究、资料复核和汇报重排依据。它们适合查证背景和比较方案，但不是新的决策总账；目录名称不绑定特定环境。

| 文件 | 作用 | 使用定位 |
| --- | --- | --- |
| [服务器资源保护服务-综合调研报告.md](research-notes/服务器资源保护服务-综合调研报告.md) | 综合阅读、资源类型和方案路线汇总 | 汇报和研究参考 |
| [技术调研与初步选型.md](research-notes/技术调研与初步选型.md) | Ubuntu、Docker、cgroup、PSI、oomd 等技术比较 | 技术细节参考 |
| [监控数据能力对比与守卫取数建议.md](research-notes/监控数据能力对比与守卫取数建议.md) | Beszel、Prometheus、Zabbix 等取数能力比较 | 观测方案参考 |
| [五台服务器的方案收敛.md](research-notes/五台服务器的方案收敛.md) | 多服务器统一实现、逐机配置的收敛建议 | 规模化配置参考 |
| [需求确认与验证计划.md](research-notes/需求确认与验证计划.md) | 补充的需求确认、证据包和验证路径 | 与 07 号蓝图交叉参考，验收总线以 07 为准 |
| [组长决策摘要与覆盖范围.md](research-notes/组长决策摘要与覆盖范围.md) | 面向 leader 的完整性说明、方案路线和缺口 | 决策背景；当前测试行动以 13 为准 |
| [现成资源保护策略-调研与汇报重排建议.md](research-notes/现成资源保护策略-调研与汇报重排建议.md) | 现成策略核验和 PPT 重排依据 | 汇报修订参考 |
| [定位与处置-同题比较与修订依据.md](research-notes/定位与处置-同题比较与修订依据.md) | 定位、处置页面的比较和修订依据 | 汇报修订参考 |
| [Anysearch复核与新增发现.md](research-notes/Anysearch复核与新增发现.md) | 资料复核、新发现及其对验证优先级的影响 | 证据补充；不可替代正式环境实测 |
| [Prometheus路线补充-来源与边界.md](research-notes/Prometheus路线补充-来源与边界.md) | Prometheus、采集器、告警和查询边界 | 方案证据补充 |

## 4. 汇报与生成素材

| 位置 | 作用 | 管理规则 |
| --- | --- | --- |
| `汇报/服务器资源保护-现成策略优先-重构版.pptx` | 当前保留的最终汇报 PPT | 经过确认的汇报成品 |
| `汇报/服务器资源保护-现成策略优先-讲稿.md` | 与最终 PPT 对应的讲稿 | 与 PPT 页码保持一致 |
| `汇报/汇报大纲.md` | 汇报结构和早期组织稿 | 若与最终讲稿冲突，以最终讲稿为准 |
| `汇报/preview/` | PPT 的 HTML/SVG 视觉预览 | 生成物，不作为内容事实来源 |
| `汇报/doubao_html_20260917_134412.html` | 生成汇报素材时保留的 HTML 来源 | 生成记录；不是工程文档 |
| `汇报/服务器资源保护-海报生图提示词.md` | 海报生成提示词和准确性约束 | 视觉素材参考 |

## 5. 实验结果与环境数据的边界

- `reports/` 保存机器本地原始采集报告，可能含生产信息，默认不入库。
- 脱敏、可复现、可审阅的实验和故障测试结论统一放在 [`experiments/`](../experiments/README.md) 对应的 `EXP-###` 目录，并在 `PROGRESS.md` 记录链接。
- `deploy/beszel/.env`、Docker 容器、Beszel 运行态数据和本地 WSL2 状态不作为仓库事实；只在本机维护。
- 生产事实必须区分“采集到的证据”“工程推断”和“待确认事项”，不能把 PoC 结果直接写成生产结论。

目录边界遵循：按机器采集的环境原始资料进入 `reports/`；按实验产生的记录、核心数据和证据进入 `experiments/`。不再单独维护 `docs/test-results/`，避免同一测试结果出现两份权威记录。

## 6. 文档新增与更新规范

### 何时更新哪份文档

| 变化类型 | 应更新的位置 |
| --- | --- |
| 阶段完成、环境变化、下一步待办 | `PROGRESS.md` |
| leader 新反馈、范围变化、测试前提 | `docs/13-leader-test-handoff.md`，必要时同步 `05`/`04` |
| 未决问题得到答案 | `docs/05-open-questions.md`，并补充证据来源 |
| 测试步骤、验收指标、停止条件变化 | `docs/07-poc-blueprint.md` |
| 真实实验或故障测试结果 | `experiments/EXP-###-<date>-<scenario>/record.md` |
| 生产环境新事实 | `docs/09-production-discovery.md`、`docs/10-production-baseline.md` |
| 安全边界或动作策略变化 | `docs/06-safety-policy.md`，未经审批不得只改汇报材料 |
| 方案结论已改变 | 更新对应主文档，并在 `PROGRESS.md` 记录原因和日期 |

### 每份活跃文档至少应有

- 明确标题和更新时间。
- 结论、事实、假设、待确认项之间的区分。
- 需要时给出来源、测试环境、版本和证据位置。
- 不把示例阈值、计划动作或推荐路线写成已验证结果。
- 重大结论变化写入 `PROGRESS.md`，避免只修改深层文档而无人发现。

## 7. 当前目录优化判断

当前顶层目录按职责划分基本合理，不合并 `goals/`、`PROGRESS.md`、`docs/`、`reports/`，以保持计划、状态、工程知识和原始数据的边界。已完成的收敛和后续规则如下：

1. **已完成：**建立根目录 README 作为项目总入口，`docs/README.md` 作为文档分类和权威关系入口。
2. **已完成：**明确 `PROGRESS.md`、13 号交接、07 号蓝图、06 号安全策略之间的权威关系。
3. **已完成：**将补充调研目录改名为 `docs/research-notes/`，去除对特定机器的来源依赖。
4. **已完成：**将实验和故障测试结果统一收敛到 `experiments/EXP-###-*/record.md`，不再新增 `docs/test-results/`。
5. **下一步建议：**将 `deploy/beszel/` 明确视为本地 PoC 部署；出现生产部署文件后再按 `deploy/poc/`、`deploy/production/` 分层。
6. **后续可选：**将 `汇报/` 再拆为 `final/`、`preview/`、`source/`；当前先保持既有外部引用稳定。
7. **暂不处理：**`src/` 和 `tests/` 目前是工程占位目录；在测试方案和实现边界确认前，不提前制造空的代码层级。

管理原则：**一个主题只保留一个当前决策源；补充材料保留证据和推导；实验结果统一归档；汇报材料不反向充当工程事实。**
