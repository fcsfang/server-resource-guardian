# 文档总目录与管理规范

更新时间：2026-09-17

这是本项目文档的唯一总入口。阅读、修改或新增文档前，先从这里判断它属于哪一类，以及哪一份文档是当前生效依据。

## 0. 执行与实验入口

- [目标与任务目录](../goals/README.md)：记录跨 agent 可接手的目标组和任务清单；当前从 `Goal 1` 开始。
- [实验日志与核心数据](../experiments/README.md)：记录真实实验过程、结果和脱敏核心数据；实验编号使用 `EXP-###`。
- 当前执行目标：[Goal 1：两层资源保护方案测试](../goals/resource-protection.md#goal-1-两层资源保护方案测试)。

## 1. 当前最新参考关系

| 要回答的问题 | 当前首要参考 | 使用规则 |
| --- | --- | --- |
| 项目现在做到哪一步、两台电脑如何分工？ | [PROGRESS.md](../PROGRESS.md) | 双机进度、环境差异和待办的总账；每次阶段性工作后更新 |
| leader 最新要求是什么、下一步测什么？ | [Leader 测试交接](13-leader-test-handoff.md) | 当前两层方案测试的直接入口；与旧讨论笔记冲突时，以本文件为当前任务方向 |
| 测试如何设计、怎样验收和何时停止？ | [PoC 验证蓝图](07-poc-blueprint.md) | 通用测试矩阵、指标、输出物和停止条件；按 13 号文档补充 leader 最新范围 |
| 任何处置动作的安全边界是什么？ | [生产安全与处置策略](06-safety-policy.md) | 默认关闭危险动作；保护名单、审批、审计和熔断优先级最高 |
| 生产服务器已经确认了什么？ | [生产环境基线与风险分析](10-production-baseline.md) | 只引用已采集事实；未知项仍看 [待确认问题](05-open-questions.md) |
| 本地 PoC 已经验证了什么？ | [本地 Beszel PoC 部署](11-local-beszel-poc.md) | 仅代表本地 WSL2/PoC 环境，不直接代表生产兼容性 |
| 最终向 leader 展示什么？ | [最终 PPT](../汇报/服务器资源保护-现成策略优先-重构版.pptx) + [配套讲稿](../汇报/服务器资源保护-现成策略优先-讲稿.md) | 汇报成品；不能替代工程测试记录 |

### 当前推荐阅读顺序

```text
README.md
  → PROGRESS.md
  → docs/13-leader-test-handoff.md
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
| [13-leader-test-handoff.md](13-leader-test-handoff.md) | leader 最新反馈、两层方案、测试顺序、授权前提和办公电脑接续方式 | **当前下一步工作的最新入口** |

## 3. 本地调研补充材料

[`local-research/`](local-research/README.md) 保存本地电脑形成的补充研究、资料复核和汇报重排依据。它们适合查证背景和比较方案，但不是新的决策总账。

| 文件 | 作用 | 使用定位 |
| --- | --- | --- |
| [服务器资源保护服务-综合调研报告.md](local-research/服务器资源保护服务-综合调研报告.md) | 综合阅读、资源类型和方案路线汇总 | 汇报和研究参考 |
| [技术调研与初步选型.md](local-research/技术调研与初步选型.md) | Ubuntu、Docker、cgroup、PSI、oomd 等技术比较 | 技术细节参考 |
| [监控数据能力对比与守卫取数建议.md](local-research/监控数据能力对比与守卫取数建议.md) | Beszel、Prometheus、Zabbix 等取数能力比较 | 观测方案参考 |
| [五台服务器的方案收敛.md](local-research/五台服务器的方案收敛.md) | 多服务器统一实现、逐机配置的收敛建议 | 规模化配置参考 |
| [需求确认与验证计划.md](local-research/需求确认与验证计划.md) | 补充的需求确认、证据包和验证路径 | 与 07 号蓝图交叉参考，验收总线以 07 为准 |
| [组长决策摘要与覆盖范围.md](local-research/组长决策摘要与覆盖范围.md) | 面向 leader 的完整性说明、方案路线和缺口 | 决策背景；当前测试行动以 13 为准 |
| [现成资源保护策略-调研与汇报重排建议.md](local-research/现成资源保护策略-调研与汇报重排建议.md) | 现成策略核验和 PPT 重排依据 | 汇报修订参考 |
| [定位与处置-同题比较与修订依据.md](local-research/定位与处置-同题比较与修订依据.md) | 定位、处置页面的比较和修订依据 | 汇报修订参考 |
| [Anysearch复核与新增发现.md](local-research/Anysearch复核与新增发现.md) | 资料复核、新发现及其对验证优先级的影响 | 证据补充；不可替代正式环境实测 |
| [Prometheus路线补充-来源与边界.md](local-research/Prometheus路线补充-来源与边界.md) | Prometheus、采集器、告警和查询边界 | 方案证据补充 |

## 4. 汇报与生成素材

| 位置 | 作用 | 管理规则 |
| --- | --- | --- |
| `汇报/服务器资源保护-现成策略优先-重构版.pptx` | 当前保留的最终汇报 PPT | 经过确认的汇报成品 |
| `汇报/服务器资源保护-现成策略优先-讲稿.md` | 与最终 PPT 对应的讲稿 | 与 PPT 页码保持一致 |
| `汇报/汇报大纲.md` | 汇报结构和早期组织稿 | 若与最终讲稿冲突，以最终讲稿为准 |
| `汇报/preview/` | PPT 的 HTML/SVG 视觉预览 | 生成物，不作为内容事实来源 |
| `汇报/doubao_html_20260917_134412.html` | 生成汇报素材时保留的 HTML 来源 | 生成记录；不是工程文档 |
| `汇报/服务器资源保护-海报生图提示词.md` | 海报生成提示词和准确性约束 | 视觉素材参考 |

## 5. 测试结果与环境数据的边界

- `reports/` 保存机器本地原始采集报告，可能含生产信息，默认不入库。
- 脱敏、可复现、可审阅的测试结论放在 [`docs/test-results/`](test-results/README.md)，并在 `PROGRESS.md` 记录链接。
- `deploy/beszel/.env`、Docker 容器、Beszel 运行态数据和本地 WSL2 状态不作为仓库事实；只在对应机器维护。
- 生产事实必须区分“采集到的证据”“工程推断”和“待确认事项”，不能把 PoC 结果直接写成生产结论。

## 6. 文档新增与更新规范

### 何时更新哪份文档

| 变化类型 | 应更新的位置 |
| --- | --- |
| 阶段完成、电脑分工、下一步待办 | `PROGRESS.md` |
| leader 新反馈、范围变化、测试前提 | `docs/13-leader-test-handoff.md`，必要时同步 `05`/`04` |
| 未决问题得到答案 | `docs/05-open-questions.md`，并补充证据来源 |
| 测试步骤、验收指标、停止条件变化 | `docs/07-poc-blueprint.md` |
| 真实测试结果 | `docs/test-results/YYYY-MM-DD-<scenario>.md` |
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

当前顶层目录按职责划分是合理的，暂时不建议大规模重命名或搬迁。优先级如下：

1. **已完成：**建立本文件作为唯一文档入口，收敛根目录 README 导航。
2. **已完成：**明确 `PROGRESS.md`、13 号交接、07 号蓝图、06 号安全策略之间的权威关系。
3. **下一步建议：**每次真实测试后在 `docs/test-results/` 产生一份脱敏结果，并回链到 `PROGRESS.md`。
4. **后续可选：**将 `汇报/` 再拆为 `final/`、`preview/`、`source/`；当前文件数量不多，且已有外部引用，暂不为整齐而搬迁。
5. **暂不处理：**`src/` 和 `tests/` 目前是工程占位目录；在测试方案和实现边界确认前，不提前制造空的代码层级。

管理原则：**一个主题只保留一个当前决策源；补充材料保留证据和推导；测试结果单独归档；汇报材料不反向充当工程事实。**
