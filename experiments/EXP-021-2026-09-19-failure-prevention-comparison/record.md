# EXP-021：无 Guardian 与 Guardian 的真实故障预防对照

- 实验 ID：`EXP-021`
- 状态：`PASSED`
- 创建日期：2026-09-19
- 最近更新：2026-09-19
- 关联 Goal：`Goal 5 / G5-T02~G5-T06`
- 实验目的：验证“无 Guardian 会发生真实系统错误，而 Guardian 能在错误发生前停止风险对象并保住健康探针”。

## 1. 为什么补做本实验

EXP-020 主要证明 Guardian 在正常 fleet、CPU/IO/PID 和 churn 场景下不会误动作，以及在已经人为设定的单对象风险中能够执行动作；无 Guardian 组没有出现真实失败，因此不能单独证明 Guardian 的有效性。

本实验补充同一泄漏条件下的真正对照：

```text
无 Guardian：无界内存泄漏 → 宿主机 global OOM → 健康服务被杀、Docker/SSH 等系统服务受影响
Guardian：同一泄漏 → 识别 host memory critical → graceful_stop 泄漏对象 → 健康服务保持可用
```

## 2. 环境和安全边界

- 环境：Mac Apple Silicon 上的 Multipass `guardian-ubuntu`。
- OS：Ubuntu 22.04.5 LTS、ARM64、2 vCPU、约 4GB 内存、无 swap、systemd 249、cgroup v2、Docker 29.1.3。
- 仅使用本地虚拟机，不连接生产，不读取生产凭据。
- 每组只有一个运行中的 disposable 泄漏容器，避免 Guardian 因多对象身份歧义而拒绝动作。
- 泄漏容器没有配置 Docker 内存上限；`--shm-size=3800m` 只是给测试对象提供可控的 tmpfs 空间，不是产品资源策略。
- 健康探针是本地 Python HTTP 服务，仅用于验证宿主机进程是否被 OOM 杀死；测试将其 `oom_score_adj` 设为 `1000`，这是故障 fixture，不代表生产服务配置。
- Guardian 组唯一实际动作是对 disposable 泄漏容器执行一次已授权 `graceful_stop`；不执行 restart、terminate、kill 或批量资源变更。

## 3. 测试方法

两组使用相同泄漏对象和健康探针：泄漏对象每约 250ms 向 `/dev/shm` 增加 64MiB，理论增长速率约 256MiB/s；健康探针监听 `127.0.0.1:18080/health`。

| 组别 | Guardian | 处置规则 |
| --- | --- | --- |
| baseline | 不运行 observer/enforce | 让泄漏继续，直到系统自然进入 OOM |
| guardian | 每轮运行 `guardian_observer` | warning 70% / critical 60% 可用内存；critical 后授权一次 `graceful_stop` |

完整脚本：[`scripts/run-failure-prevention-comparison.sh`](../../scripts/run-failure-prevention-comparison.sh)。

## 4. 结果

### 4.1 直接对照

| 指标 | 无 Guardian | Guardian |
| --- | --- | --- |
| 泄漏目标内存上限 | 无 | 无 |
| 风险对象处置 | 无 | critical 后执行一次 `graceful_stop` |
| 处置前可用内存 | 100,092KiB，随后降至 OOM | 2,377,428KiB 采样时识别 critical |
| 健康探针 | 被内核 global OOM kill | 进程保持存活，`health-ok` |
| 宿主机 OOM | 发生 | 未发生 |
| 泄漏容器 | `exited/137` | `exited/0` |
| Guardian 审计 | 未运行 | `recovered / target_stopped`，returncode 0 |
| 实验结束运行中容器 | 0 | 0 |

### 4.2 无 Guardian 的错误证据

内核日志记录了：

- `global_oom` 触发；健康探针 Python PID `8940` 被 OOM killer 杀死。
- `dockerd`、`sshd`、`systemd-journal`、`systemd-network`、`systemd-resolved` 等进程也被 OOM killer 处理。
- Docker 泄漏目标最终为 `exited/137`，systemd 处于 degraded 状态。

这不是“没有动作所以没有变化”，而是系统真实进入了错误状态。

### 4.3 Guardian 组的恢复证据

- 第 4 次观察时可用内存比例为 `59.227%`，事件状态为 `critical`，原因 `host_memory_available_critical`。
- 事件只包含一个稳定容器对象，决策为 `graceful_stop`。
- 授权控制器返回 `recovered`，动作 returncode 为 `0`，恢复原因 `target_stopped`。
- 处置后可用内存恢复至 `3,624,616KiB`，健康探针仍存活且返回 `health-ok`。
- Guardian 组没有内核 OOM 日志，结束时运行中容器为 `0`。

## 5. 结论

本实验提供了 EXP-020 缺失的有效性证据：在相同的无界内存泄漏条件下，无 Guardian 会导致宿主机 global OOM、健康服务被杀和 Docker/SSH 等系统服务受影响；Guardian 能在达到本地 critical 阈值后识别风险对象、执行受控停止、回收内存并保住健康探针。

因此，当前可以向 leader 证明 Guardian 的核心价值是**在资源危机升级为宿主机故障前自动止损**，而不是给所有容器统一加内存限制。

这仍然不能证明真实生产业务恢复：本实验只验证了宿主机健康探针和 disposable 泄漏对象，生产还需要真实业务 health check、保护名单、动作授权和 x86_64 非生产环境复核。

## 6. 异常和数据质量说明

- 第一次 Guardian 组临时执行因 shell 引号错误没有生成有效授权文件，随后同一泄漏导致探针被 OOM；该轮不计入结果，也不作为 Guardian 组证据。
- 修正后使用仓库脚本完整重跑 Guardian 组，授权文件、事件、审计和恢复结果均由脚本保存。
- baseline 因 shell/sshd 在 global OOM 中被杀，脚本无法在 OOM 后写入 final 文件；内核日志、目标 inspect 状态和已落盘观察 CSV 保留了失败证据。
- “可用内存比例”和“系统状态”是本地 VM 观测值，不能直接外推为生产阈值或 SLA。

## 7. 核心证据

- 对照摘要：[`data/comparison-summary.csv`](data/comparison-summary.csv)
- 无 Guardian 原始数据：[`data/baseline/`](data/baseline/)
- Guardian 原始数据：[`data/guardian/`](data/guardian/)
- 可复现实验脚本：[`scripts/run-failure-prevention-comparison.sh`](../../scripts/run-failure-prevention-comparison.sh)
- 面向 leader 的报告：[`report.md`](report.md)

## 8. 更新记录

- 2026-09-19：确认 EXP-020 不能证明无 Guardian 失败，建立真实故障预防对照。
- 2026-09-19：无 Guardian 组复现 global OOM；健康探针、dockerd、sshd 和多个 systemd 服务被 OOM 影响，泄漏容器退出 137。
- 2026-09-19：Guardian 组在 critical 阈值处置泄漏目标，恢复成功且健康探针保持可用；实验通过。
