# EXP-043：修正版有界高压下 Guardian 只读采样

- 实验 ID：`EXP-043`
- 日期：2026-09-20
- 关联目标：Goal 7 / `PG-P0-07`
- 状态：`PASSED`（有限高压下只读安全性检查）
- 实验负责人：当前 Agent

## 1. 目的

修正 EXP-042 的 fixture 问题，在当前 Multipass disposable VM 中施加一次严格有界的本地 CPU/内存压力，观察 EXP-039 Guardian Observer 是否保持存活、采样和 fail-closed 语义。

## 2. 环境与安全边界

- VM：`guardian-ubuntu`，Ubuntu 22.04.5 LTS ARM64，2 vCPU，约 3.8 GiB 内存。
- 压力对象：一个临时 Python worker，触碰并持有 128 MiB，使用约一个 CPU，内部 20 秒后自然退出；worker 不访问 Docker/systemd。
- Observer：复用 EXP-039 主进程；资源采样器只读记录其 RSS、CPU、FD、线程；另运行三次 `observe --once`，均使用单独审计文件。
- 停止条件：worker 自然退出；若 MemAvailable 快速下降、memory PSI full 非零、Multipass/SSH 失联或 Observer 消失，不再扩大压力并保留数据。
- 禁止动作：不执行 Docker stop/restart/kill，不修改 cgroup/资源上限，不安装/启用 systemd，不连接生产。

## 3. 执行步骤

1. 将只读 sampler 和 summary helper 复制到 VM 临时目录，记录 VM/容器/Guardian 初始状态。
2. 启动自带 20 秒停止条件的 worker。
3. 并行运行 30 秒、有界 TSV 输出的 `guardian_resource_sampler.py`，并执行三次 Observer `--once`。
4. 等待 worker 和 sampler 自然结束，读取 before/after MemAvailable、PSI、Docker 清单、Observer 审计及资源 P99。

## 4. 验收口径

- worker 返回 0 并自然退出；Docker 容器 ID/状态不变。
- Guardian 主进程在压力期间存活；Observer 事件保持 `observe`，无动作授权/执行。
- 若 memory PSI full、OOM 增量和 MemAvailable 均没有进入风险区，只记为“有限压力安全性检查”，不写成宕机风险有效性证明。

## 5. 结果

| 检查 | 结果 |
| --- | --- |
| worker | 自然退出，`elapsed_s=20.06`，峰值 RSS `138,864 KiB`，CPU 时间 `user=19.98s/sys=0.03s` |
| Guardian 主进程 | EXP-039 PID `1076273` 在压力前后均存活，结束时 RSS `17,492 KiB`、CPU `0.0%`、线程 1 |
| Guardian 资源时序 | 15 个样本、28.517 秒；RSS P99 `17,492 KiB`，CPU P99 `0.0%`，FD P99 `5`，线程 P99 `1` |
| Observer 只读采样 | 3 条事件，均为 `degraded_observability`；`action=none`、`execution=not_applicable` |
| 主机内存/压力 | MemAvailable `3,553,248 → 3,532,736 kB`；三条事件 memory PSI full `avg10=0.0` |
| OOM 信号 | 三条事件 cgroup `oom=0`、`oom_kill=0`；VM 无 swap |
| Docker 状态 | 两个 Beszel 容器 ID/名称前后相同，未执行 Docker 变更 |
| 资源/服务边界 | 未安装或启用 systemd；未修改 cgroup/资源上限；未连接生产 |

结构化摘要见 [`data/verification.json`](data/verification.json)。

本实验通过“有限高压下 Observer 保持只读、存活、无动作”的安全性检查；三次 `--once` 因历史窗口不足而保持 `degraded_observability` 是预期 fail-closed 结果。它没有制造真实宕机风险，也不能证明 Guardian 的提前检测或处置有效性，不能替代 24 小时 soak、真实故障边界和生产 P99。

## 6. 限制与后续

- 压力只在单个临时 VM 进程中施加，MemAvailable 仍保持在安全区，memory PSI full 未上升；这不是 OOM/宕机对照实验。
- 观察窗口约 30 秒，样本量不足以校准生产 slice；只作为 PG-P0-07 的高压采样安全性子证据。
- 仍需完成 EXP-039 24 小时结果、依赖/日志失败边界、watchdog 超时/崩溃恢复和完整场景 P99。

## 7. 更新记录

- 2026-09-20：修正 EXP-042 fixture 后完成有界 CPU/内存压力采样；worker 自然退出，Guardian 保持存活且未产生动作。
- 2026-09-20：将新增资源汇总器和回归测试同步到 VM 临时工作树；当前 Multipass 隔离回归 `78/78` 通过，未影响 EXP-039 主进程。
