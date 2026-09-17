# Server Resource Guardian

服务器资源监控、危机预警与受控处置项目。

## 项目背景

公司服务器曾多次因为 CPU、内存或其他资源耗尽而失去响应，SSH 可能无法登录，最终只能重启服务器并影响生产。本项目的目标是在故障发生前识别风险，在故障发生时保留诊断和救援能力，并通过受控手段恢复服务。

项目不是重新开发一套完整监控平台。首选复用成熟监控组件，在此基础上补充一个轻量的本机守护服务（Guardian），负责危机现场取证和经过授权的处置。

## 当前状态

当前已完成首轮官方资料调研、生产环境基线采集和本地 WSL2 环境初始化。隔离的 Beszel 0.19.0 Hub/Agent 已成功上线，项目进入只读监控 PoC 验证阶段，尚未进入生产代码开发。任何自动终止进程、重启容器或限制资源的能力默认关闭，必须在确认业务保护名单和审批策略后启用。

## 文档导航

- [任务背景与需求基线](docs/01-requirements.md)
- [成熟方案调研与选型](docs/02-solution-research.md)
- [推荐架构](docs/03-architecture.md)
- [分阶段实施计划](docs/04-delivery-plan.md)
- [待确认问题](docs/05-open-questions.md)
- [生产安全与处置策略](docs/06-safety-policy.md)
- [PoC 验证蓝图](docs/07-poc-blueprint.md)
- [调研证据与官方资料](docs/08-research-evidence.md)
- [生产环境采集与 WSL2 复现](docs/09-production-discovery.md)
- [生产环境基线与风险分析](docs/10-production-baseline.md)
- [本地 Beszel PoC 部署](docs/11-local-beszel-poc.md)
- [资源耗尽救援方案：讨论与汇报笔记](docs/12-leader-discussion-research.md)
- [配置样例](config/guardian.example.yaml)
- [原始任务截图](docs/assets/task-screenshot.png)

## 建议的第一阶段范围

1. 选取一台非生产 Linux 测试机部署采集端。
2. 采集主机、进程、Docker/cgroup 和 PSI 指标。
3. 建立持续时间、恢复阈值和冷却时间明确的告警规则。
4. 危机时自动保存现场快照并通知值班人员。
5. 通过人工审批执行限流、优雅终止或容器重启。
6. 压测验证监控服务在资源紧张时仍能运行。

## 目录结构

```text
server-resource-guardian/
|-- config/        # 配置样例，危险动作默认关闭
|-- deploy/        # 后续存放 systemd、容器和监控部署文件
|-- docs/          # 需求、架构、调研和安全策略
|-- reports/       # 本地环境报告，不提交版本库
|-- scripts/       # 环境采集和后续 PoC 辅助脚本
|-- src/           # 后续存放 Guardian 源代码
`-- tests/         # 后续存放单元、集成和故障演练测试
```

## 核心原则

- 先观测、再建议、后自动化。
- 先取证、再处置；先限流、再终止。
- 不依据单个瞬时 CPU 或内存阈值杀进程。
- 系统关键进程和业务核心服务必须进入保护名单。
- 每次决策和动作都必须可审计、可追踪、可验证结果。
- Guardian 不是带外管理的替代品；SSH 完全不可用时仍需独立控制通道。
