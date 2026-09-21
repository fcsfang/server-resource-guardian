# Guardian 独立只读 Collector 本地验收

更新时间：2026-09-22

在本地 disposable Ubuntu 22.04 VM `guardian-t11-matrix` 上验证：

- `guardian-collector.service` 以独立 `guardian-collector` 账户运行，加入 `docker` 和 `guardian-shared` 组。
- `guardian-runtime.service` 仍以 `guardian` 账户运行，SupplementaryGroups 只有 `guardian-shared guardian-broker`，不含 `docker`。
- Collector socket 为 `guardian-collector:guardian-shared 0660`，Runtime 可通过 socket 读取。
- 固定 snapshot 请求返回 `read_only=true`、`mutation_commands=[]`，当前返回 4 个容器对象。
- Runtime 最新审计中的容器事实为 `docker=true`、对象注册表 `ok`、4 个对象。
- `guardian-status` 已展示当前候选数量、最高候选、模拟动作边界和保护配置数量；observe 模式下动作显示为 `none/not_applicable`。
- 持续 Runtime 已将候选排名、保护原因、模拟目标和 `execution` 写入 `guardian.runtime.presentation.v1` 展示投影；状态命令不读取或执行 Docker 控制命令。
- 2026-09-21 bounded simulate：t11 通过 Collector 连续采集 3 个样本，4 个容器进入候选列表，风险为 `warning`，因目标归因未确认给出 `escalate/not_executed`；容器数量和状态保持不变。
- 2026-09-21 里程碑二统一演示：t11 上创建一个临时 CPU 压力容器，连续采样期间主机 CPU 达到 100%，该容器 CPU 贡献约 99.998%，归因为 `TARGET_CONFIRMED` 并成为第一候选；`beszel-isolated-t12` 被明确列为受保护候选。临时演示配置生成 `graceful_stop` 模拟计划，执行为 `not_executed`，Guardian 未执行 Docker 动作；演示后临时容器已清理，4 个既有 Beszel 容器仍为 healthy。
- 任意非 `snapshot` 操作会被协议拒绝；Collector 代码没有 stop、restart、rm、kill 等动作入口。
- 里程碑三统一验收已在同一台 VM 完成：真实 CPU 压力容器经连续采样、目标身份复核和一次性授权后，由独立 Broker 仅执行一次 `graceful_stop`；审计记录为 `execution=REAL`、`action_completed`、`target_stopped`，主机状态为 `MITIGATED`。没有真实业务健康检查，因此业务恢复只记录为待人工确认，不能由测试容器停止推断。目标已清理，4 个既有 Beszel 容器仍为 healthy；验收结束后 Runtime 恢复 `observe`，Broker inactive、socket absent。

失败边界：Collector 不可用或返回数据不完整时，Runtime 只将对象观测标记为不可用/降级，不猜测目标，也不执行动作。

这证明的是本地服务边界和只读数据链路，不等于 Docker socket 在内核权限层面已经成为不可变更的安全代理；后续生产部署仍需单独评审 Docker socket 最小权限方案。
