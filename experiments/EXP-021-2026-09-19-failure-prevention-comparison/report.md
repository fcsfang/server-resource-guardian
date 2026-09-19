# 无 Guardian 与 Guardian 的真实故障预防对照报告

报告日期：2026-09-19
实验：EXP-021
用途：修正 EXP-020 对 Guardian 有效性的证据缺口

## 1. 结论先行

这次对照满足真正的有效性判定：

> 同一台本地 Ubuntu 虚拟机、同一个无界内存泄漏对象、同一个健康探针；无 Guardian 时发生 global OOM 并杀死健康服务，Guardian 在 critical 阈值处置泄漏对象后，健康服务保持可用。

| 结果 | 无 Guardian | Guardian |
| --- | --- | --- |
| 宿主机 global OOM | 发生 | 未发生 |
| 健康探针 | Python 进程被 OOM killer 杀死 | 进程存活，HTTP 返回 `health-ok` |
| Docker/SSH 系统服务 | `dockerd`、`sshd`、journald 等被 OOM 影响 | 未出现本轮 OOM 事件 |
| 风险容器 | `exited/137` | 授权 `graceful_stop` 后 `exited/0` |
| Guardian 审计 | 未运行 | `recovered / target_stopped` |

因此，Guardian 已经有了“没有 Guardian 会出错，有 Guardian 能止损”的本地实证。EXP-020 的结论应限定为安全性和性能基线；本报告才是 Guardian 有效性的核心对照证据。

## 2. 实验条件

- Ubuntu 22.04.5 LTS ARM64，2 vCPU，约 4GB 内存，无 swap。
- systemd 249、cgroup v2、Docker 29.1.3。
- 泄漏容器不设置内存上限；每约 250ms 增加 64MiB tmpfs 占用，约 256MiB/s。
- 健康探针监听 `127.0.0.1:18080/health`，返回 `health-ok`；为提高故障 fixture 的可重复性，其 `oom_score_adj=1000`。
- Guardian 组阈值：warning 可用内存 70%，critical 可用内存 60%；critical 后只允许一个 `graceful_stop`。

本实验使用 `--shm-size=3800m` 作为测试对象的增长空间，不是给业务容器提出的统一资源限制。

## 3. 无 Guardian 组：真实错误发生

### 3.1 过程

泄漏持续增长时，可用内存从约 3.5GB 下降至 `100,092KiB`。随后 Linux 触发 global OOM。

### 3.2 错误证据

内核日志明确记录：

- 健康探针 Python PID `8940` 被 `Out of memory: Killed process` 杀死。
- `dockerd`、`sshd`、`systemd-journal`、`systemd-network`、`systemd-resolved` 等系统进程被 OOM killer 处理。
- 泄漏容器为 `exited/137`。
- systemd 进入 degraded 状态。

这组不是“没有 Guardian 所以没有动作”，而是已经发生了宿主机级错误，并影响到健康服务、容器运行时和 SSH 链路。

## 4. Guardian 组：在错误前止损

### 4.1 检测和定位

第 4 次观察时：

- 可用内存：`2,377,428KiB`，比例 `59.227%`。
- Guardian 状态：`critical`。
- 原因：`host_memory_available_critical`。
- 候选对象：唯一稳定容器 ID，泄漏目标。
- 计划动作：`graceful_stop`。

### 4.2 动作和恢复

- 授权执行器返回码：`0`。
- 审计状态：`recovered`。
- 恢复原因：`target_stopped`。
- 泄漏容器退出码：`0`，而不是 OOM 的 `137`。
- 处置后可用内存：`3,624,616KiB`。
- 健康探针：进程存活，HTTP 仍返回 `health-ok`。
- Guardian 组没有 kernel OOM 日志，结束时运行中容器为 `0`。

## 5. 这份对照能证明什么

可以证明：

1. Guardian 能在宿主机资源耗尽前发现风险趋势。
2. Guardian 能从 Docker 观测中定位到唯一风险对象。
3. Guardian 能通过授权门执行一次受控动作，而不是直接无限制 kill。
4. 受控停止后能验证目标退出、内存回收和健康探针保持可用。
5. 该动作避免了本轮 baseline 中已经发生的 global OOM 和系统服务连锁受影响。

不能证明：

- 真实业务容器在 `graceful_stop` 后一定能自动恢复业务；本轮验证的是止损和健康探针保活。
- ARM64/4GB 上的阈值、时延和结果可直接迁移到生产 x86_64/31GB。
- 多风险对象、真实业务 health check、真实网络和生产白名单已经完成验证。

## 6. 对 EXP-020 的修正

EXP-020 中的正常压力和安全边界结果仍然有效，但它的无 Guardian 组没有失败，因此只能支持：

- Guardian 在正常压力下不误动作；
- Guardian 的动作、恢复、冷却和失败升级链路可运行；
- Guardian 自身资源开销较低。

“Guardian 能解决没有 Guardian 时会发生的错误”应以本 EXP-021 为主要证据。

## 7. 申请下一阶段权限的依据

可以向 leader 提交本报告，申请：

1. 一台与生产同版本的非生产 x86_64 Ubuntu 22.04 测试机。
2. 先只读 observe，确认真实业务 health check 和对象身份。
3. 指定一个无状态、可重建、低风险容器，申请一次 `graceful_stop` 灰度授权。
4. 暂不申请 restart、terminate、kill 或批量资源配置权限。

## 8. 证据入口

- 原始对照数据：[`data/`](data/)
- 对照摘要：[`data/comparison-summary.csv`](data/comparison-summary.csv)
- 实验记录：[`record.md`](record.md)
- 可复现实验脚本：[`scripts/run-failure-prevention-comparison.sh`](../../scripts/run-failure-prevention-comparison.sh)
