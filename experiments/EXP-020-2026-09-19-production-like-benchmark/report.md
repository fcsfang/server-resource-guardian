# 本地生产仿真性能与 Guardian 安全性基线报告

报告日期：2026-09-19  
实验：EXP-020  
用途：申请生产测试权限前的技术依据

## 1. 结论摘要

> 证据修订：本实验的无 Guardian 组没有出现真实系统错误，因此它不能单独证明 Guardian 的有效性。它证明的是正常压力下不误动作、动作链路可运行和自身开销较低；真正的“无 Guardian 失败、Guardian 止损”对照见 [EXP-021](../EXP-021-2026-09-19-failure-prevention-comparison/report.md)。

在本地 Multipass Ubuntu 22.04 ARM64 中，Guardian 已完成一轮有界的生产仿真对照：

- 12 个正常容器、CPU/IO/PID 负载和短生命周期容器 churn 场景没有触发错误动作。
- 无 Guardian 对照中，700 MiB 级别的内存增长目标在观察窗口结束时仍保持运行。
- Guardian 对同类、单一、明确授权的 disposable 目标执行过 `graceful_stop` 并得到 `recovered / target_stopped`；仓库当前没有每一组重复都独立汇总的完整审计结果，因此不把“5 组计时”写成 5 轮完整动作成功。
- 单目标 observe 事件生成耗时约 P50 `1.06s`、近似 P95 `1.17s`；enforce 加恢复验证约 P50 `638ms`、近似 P95 `707ms`。
- 多对象场景不会盲选目标，而是升级 `ambiguous_object_identity`；无稳定身份和不健康业务状态也会 fail-closed。

因此，本实验只证明 Guardian 的安全性和运行基线，不把“风险出现后能够阻止真实错误”作为本实验结论；该结论由 EXP-021 提供。

## 2. 环境对照

| 项目 | 生产基线 | 本地仿真 |
| --- | --- | --- |
| OS/用户空间 | Ubuntu 22.04.5 LTS | Ubuntu 22.04.5 LTS |
| systemd | 249.11 | 249.11 |
| cgroup | v2 | v2 |
| Docker | 29.1.3 | 29.1.3 |
| 架构 | x86_64 | ARM64 |
| CPU/内存 | 约 32 逻辑 CPU / 31 GiB | 2 vCPU / 4 GiB |
| 容器规模 | 55 个运行中 | 12–14 个代表性容器 |
| 数据和业务 | 真实业务 | disposable BusyBox 测试对象 |

本地环境对运行时和策略流程具有较高相似度，但不具备生产的容量、架构、业务请求和网络拓扑。

## 3. 测试矩阵与结果

### 3.1 基线与正常压力

| 场景 | 容器数 | Guardian 结果 | 说明 |
| --- | ---: | --- | --- |
| 空载基线 | 0 | `normal / none` | 单次 observe 约 21–22ms |
| 代表性正常容器 fleet | 12 | `normal / none` | 对象被采集，但 observe 不产生动作 |
| CPU/IO 有界压力 | 14 | `normal / none` | CPU 1 核、IO 8×16 MiB 写入，未误触发内存处置 |
| CPU/IO/PID smoke | 15 | `normal / none` | 增加 100 个受限 PID，未产生动作 |
| 短生命周期 churn | 15 | `normal / none` | 10 个短生命周期容器 + 5 个背景对象，未产生动作 |

当前第一版风险 evaluator 以主机内存和 OOM 事件为 P0；CPU/IO 负载被采集用于解释和后续增强，不会单独触发破坏性动作。

### 3.2 无 Guardian 与 Guardian 对照

测试对象使用 700 MiB `/dev/shm` 内存压力，容器 cgroup 上限 900 MiB，仅用于保护本地 VM，不代表产品默认资源策略。

| 对照组 | 观察结果 |
| --- | --- |
| 无 Guardian | 5 秒观察窗口结束时目标仍为 `Running=true`，没有自动处置 |
| Guardian | 先生成 enforce 事件，再执行授权 `graceful_stop`；可见的完整结果记录为 `recovered / target_stopped`，跨文件计时组不等于完整重复动作结果 |

这说明 Guardian 的效果不是“阻止内存增长”，而是在风险达到测试策略条件后，按授权把明确目标安全停止，并确认目标已退出。

### 3.3 时延重复测量

跨 `timings.csv` 和 `timings-repeated.csv` 可见 5 组本地计时；由于重复数据分散，且没有每轮完整独立事件/审计/恢复结果的统一汇总，本报告只把它作为本地性能基线，不把 P95（即使按小样本最大值近似）视为统计结论或生产 SLA。

| 操作 | P50 | 近似 P95 | 说明 |
| --- | ---: | ---: | --- |
| 空载 observe | 22ms | 22ms | 0 个容器 |
| 12 容器 fleet observe | 2.10s | 2.21s | Docker stats 读取占主要时间 |
| 14 容器 CPU/IO observe | 2.06s | 2.10s | 有界压力下仍正常完成 |
| 单目标内存风险 observe | 1.06s | 1.17s | 生成事件和对象候选 |
| enforce + 恢复验证 | 638ms | 707ms | `docker stop` + inspect，目标正常响应 SIGTERM |

### 3.4 失败边界

第一轮仿真目标没有实现 SIGTERM 处理，Docker 返回 0 但目标 exit 137；Guardian 返回 `failed / target_force_killed`，没有误报为恢复成功。修正测试目标加入 SIGTERM trap 后，重复测试全部恢复成功。该差异证明 Guardian 能区分“命令返回成功”和“业务对象真正安全退出”。

## 4. Guardian 自身资源开销

来自 EXP-019 的本地空载基线：

- 单次 observe 峰值 RSS：约 25.8 MiB。
- 每 5 秒持续采样 32 秒峰值 RSS：约 26.5 MiB。
- 持续采样累计 CPU 时间：约 0.12 秒，约 0.38% 单核时间。
- 测量时 VM 无运行中容器；该结果是基线，不是 55 容器生产规模上限。

在 14 个代表性容器和 CPU/IO 压力下持续采样 32 秒的补充测量：峰值 RSS `28,292 KiB`（约 27.6 MiB），user+sys CPU `0.12s`；结束后运行中容器为 0。

## 5. 本实验能向 leader 证明什么

1. Guardian 不是单纯的阈值告警，而是有检测、对象身份、保护判断、动作授权、恢复验证、冷却和失败升级的闭环。
2. 在代表性多容器和正常压力下，默认 observe 不会因为“容器多”或 CPU/IO 忙就自动停止对象。
3. 在目标明确、授权存在且目标可优雅退出时，Guardian 能在秒级完成一次受控恢复；目标不符合安全退出条件时会 fail-closed 并升级。
4. Guardian 自身空载资源占用较低，不需要通过给所有业务容器设置内存上限来实现保护。

本实验不能证明无 Guardian 时一定会失败；请将 [EXP-021](../EXP-021-2026-09-19-failure-prevention-comparison/report.md) 作为有效性对照主证据。

## 6. 不能向 leader 声称的内容

- 不能声称已经证明生产 x86_64、32 CPU、31 GiB 容量下的最终性能。
- 不能声称已经验证真实业务请求、业务健康接口和生产保护名单。
- 不能声称可以避免所有宕机；当前主要验证的是风险发现后的受控缓解。
- 不能把本地 1–2 秒的采样/处置时间直接承诺为生产 SLA。
- 当前 Guardian 仍是 Python 原型/CLI 流程，不是已完成 systemd 部署、通知和自保护的生产服务。

## 7. 申请生产测试权限时建议提出的最小范围

1. 提供一台与生产同版本的非生产 x86_64 Ubuntu 22.04 测试机，先做只读 observe。
2. 指定一个可重建、无状态、低风险的测试容器和明确保护名单。
3. 先验证业务健康接口、快照、告警和人工回滚，再申请一次 `graceful_stop` 授权。
4. 暂不申请 `restart`、`terminate`、`kill` 或批量资源配置权限。
5. 生产灰度前补充 55 容器规模、真实网络、磁盘和业务 SLO 的压力复核。

## 8. 证据

- 核心数据：本目录 `data/`。
- 可复现实验脚本：[`scripts/run-production-like-benchmark.sh`](../../scripts/run-production-like-benchmark.sh)。
- 相关实验：EXP-014～EXP-019；真实故障预防对照见 [EXP-021](../EXP-021-2026-09-19-failure-prevention-comparison/report.md)。
