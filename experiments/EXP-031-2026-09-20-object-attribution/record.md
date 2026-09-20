# EXP-031：Guardian Docker/cgroup 对象归因与歧义放弃验证

- 实验 ID：`EXP-031`
- 状态：`PASSED`
- 创建日期：2026-09-20
- 最近更新：2026-09-20
- 关联 Goal：`Goal 7 / PG-P0-04`
- 实验目的：验证 Guardian 能否把 Docker 稳定身份映射到 cgroup v2，基于连续样本计算对象内存贡献度、置信度和领先幅度，并在对象信号缺失、容器重建、ID/cgroup 不一致或候选接近时放弃目标。

## 1. 环境与边界

- 宿主：Mac Apple Silicon，Python 标准库测试。
- VM：Multipass `guardian-ubuntu`，Ubuntu 22.04.5 LTS ARM64，Docker Engine 29.1.3，cgroup v2。
- VM 现有容器：仅读取既有 `beszel-poc`、`beszel-agent-poc`；没有创建或改变容器。
- 允许动作：Docker `ps/stats/inspect` 只读查询、读取 `/proc/<pid>/cgroup` 和 cgroup 内存文件、运行单元测试和两次无压力 observe 采样。
- 禁止动作：压力注入、容器创建/停止/重启/kill、systemd 变更、资源限制修改、Beszel 写接口、生产连接和凭据读取。
- 停止条件：无法确认稳定身份、cgroup 映射错误、候选歧义时不得继续生成目标；本次均按规则放弃，没有动作执行。

## 2. 实施内容

- 新增 [`src/guardian_attribution.py`](../../src/guardian_attribution.py)：
  - 单次只读 `docker inspect` 获取 full ID、PID、创建时间、状态、健康状态、镜像和 labels。
  - 通过 `/proc/<container-pid>/cgroup` 定位 cgroup v2，读取 `memory.current`、`memory.max`、`memory.events`。
  - 连续样本计算对象增长速率、占宿主内存下降比例、OOM 增量、贡献度、评分和领先幅度。
  - 同名容器 ID 变化、同一 ID cgroup 变化、ID/cgroup 不一致、对象数据缺失全部阻止目标确认。
- `guardian_observer` 将 registry 和 `object_attribution` 纳入事件；simulate/enforce 计划只有在归因状态为 `TARGET_CONFIRMED` 时才允许继续走已有的策略门禁，其他状态升级或不执行。

## 3. 验收矩阵

| 场景 | 结果 |
| --- | --- |
| 单对象两次采样，贡献度 80% 且高置信 | `TARGET_CONFIRMED`，记录 target、贡献度和领先幅度 |
| 两个候选贡献度接近 | `AMBIGUOUS_TARGET`，带 `candidate_lead_margin_too_small` |
| 对象 cgroup/内存信号缺失 | `DEGRADED_OBSERVABILITY`，无 target |
| 同名容器发生 ID 重建 | `AMBIGUOUS_TARGET`，带 `container_recreated` |
| ID 与 cgroup scope 不一致 | 不确认 target，保持降级/放弃 |
| 只有稳定容器但没有内存增长 | `NO_TARGET`，不因“只有一个容器”选中对象 |
| 未确认归因的 simulate | action 为 `escalate`，不生成可执行 graceful_stop 计划 |

## 4. 执行结果

- 宿主机全量测试：`91/91` 通过。
- `guardian-ubuntu` 临时目录隔离测试：`27/27` 通过；未修改 VM 项目目录。
- VM 一次 observe 的 registry 状态为 `ok`，两个既有 Beszel 容器均映射到真实 full-ID Docker scope，身份置信度为 `high`。
- VM 连续两次、间隔约 1 秒的无压力观测：第一次为 `DEGRADED_OBSERVABILITY`（建立对象基线），第二次为 `NO_TARGET`；两个对象增长速率为 0，OOM 增量为 0，没有错误选中目标。
- 宿主机无 Docker 时，registry 输出 `unavailable`，归因状态保持 `DEGRADED_OBSERVABILITY`；没有因为本机缺少 Docker 而伪造对象证据。
- 本实验没有启动压力 worker，也没有执行 Docker/systemd 变更。

## 5. 结论与限制

1. PG-P0-04 的本地 MVP 已具备 Docker↔cgroup 映射、对象时序、贡献度评分、置信度和歧义放弃证据。
2. 当前贡献度阈值和领先幅度是本地校准默认值，不是生产 SLA；尚未在有界泄漏对象上重新取得新的动作授权证据。
3. 当前仍未完成对象 registry 的持久化、业务 owner/保护策略、崩溃恢复、并发一致性和生产 x86_64 复核；这些分别属于 PG-P0-05 及后续阶段。
