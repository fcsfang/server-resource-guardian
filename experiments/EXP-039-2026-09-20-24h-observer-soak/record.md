# EXP-039：Guardian Observer 24 小时本地 soak

- 实验 ID：`EXP-039`
- 日期：2026-09-20
- 关联目标：Goal 7 / `PG-P0-07`
- 状态：`RUNNING`
- 实验负责人：当前 Agent

## 1. 目的

在 Mac Multipass disposable VM 中运行最长 24 小时的只读 Observer，观察长期进程存活、RSS/CPU、采样连续性、审计容量门禁和 readiness 行为，为 PG-P0-07 的长跑验收提供证据。

## 2. 环境与安全边界

- VM：`guardian-ubuntu`，Ubuntu 22.04.5 LTS ARM64，2 vCPU，约 3.8 GiB 内存，Docker 29.1.3。
- 运行目录：`/tmp/guardian-p0-03-check`，stdout 丢弃到 `/dev/null`，审计文件使用临时配置将上限设为 1 MiB，避免实验原始输出无限增长。
- 模式：`observe`；Docker 只读 stats/inspect。
- 停止条件：`timeout --signal=TERM 86400s` 硬上限；发生 VM 资源异常、路径越界、Docker 变更、无法停止或审计目录超出上限时提前结束并保留摘要。
- 禁止动作：不安装/enable/start systemd，不执行 Docker stop/restart/kill，不修改资源限制，不连接生产。

## 3. 运行与验收

Observer 使用 5 秒采样间隔，由 `/usr/bin/time` 和 24 小时 `timeout` 包裹；资源时序由只读、有界的 [`guardian_resource_sampler.py`](../../scripts/guardian_resource_sampler.py) 采集 RSS、CPU、FD 和线程数。完成后记录实际时长、最大 RSS、CPU 时间、进程是否持续、审计文件大小是否不超过 1 MiB、达到上限后的降级比例、readiness、VM 状态和错误摘要。

当前仅记录实验已启动；未完成前不把 PG-P0-07 标记为 DONE，也不进入 PG-P0-08。

## 4. 更新记录

- 2026-09-20：在 `guardian-ubuntu` 启动 24 小时硬上限只读 soak；初始 readiness 为 `observe:ready`，审计文件约 7.7 KiB，进程 RSS 约 17 MiB，仍在运行。
- 2026-09-20：新增资源采样器并通过主机 119/119、VM 76/76；对同一 Observer 完成 20 秒只读 sidecar，RSS 16,996 KiB、线程 1、FD 3–5，目标进程仍存活。
- 2026-09-20：中途复核同一主 soak 的资源采样器已有 50 个有效样本；RSS 16,996 KiB 恒定、CPU 采样最高 0.0%、FD 3–5、线程 1，审计文件约 692 KiB。该数据仅作运行中证据，最终 P99 需等 soak 结束。
- 2026-09-20：运行约 19 分钟时复核目标进程仍存活；临时审计文件达到容量门禁前的 1,046,557 bytes（小于 1 MiB 上限）后保持不再增长，Observer 未退出；辅助只读资源时序已有 101 个有效样本，RSS 16,996–17,492 KiB、CPU 0.0%、FD 3–5、线程 1。该结果证明当前有界写入不会因达到上限而删除旧证据或停止 Observer，但最终 24 小时统计/P99 仍待完成。
- 2026-09-20：新增 [`guardian_resource_summary.py`](../../scripts/guardian_resource_summary.py) 对有界 TSV 做可重算的 nearest-rank P50/P95/P99 汇总；当前辅助时序 119 个样本、1,185 秒窗口的中途结果为 RSS P99 17,492 KiB、CPU P99 0.0%、FD P99 5、线程 P99 1。该结果只代表运行中的本地 ARM64 观测窗口，不能作为最终 24 小时或生产资源参数。
- 2026-09-20：中途结构化摘要写入 [`data/verification.json`](data/verification.json)；Observer 运行约 2,175 秒、辅助时序 201 个样本/2,007 秒，RSS/CPU/FD/线程 P99 为 17,492 KiB/0.0%/5/1，审计 1,046,557 bytes，进程仍存活。该文件明确标记 `RUNNING`，不代表 24 小时完成。
- 2026-09-20：运行中增量复核；Observer 约 3,701 秒，资源 sidecar 355 个样本/3,551 秒，RSS/CPU/FD/线程 P99 为 17,492 KiB/0.0%/5/1，RSS 均值 17,384.719 KiB、FD 均值 3.596，审计仍为 1,046,557 bytes 且进程存活。该摘要仍是中途证据，`verification.json` 保持 `RUNNING`，不代表 24 小时完成。
- 2026-09-20：运行中增量复核；Observer 约 4,564 秒，资源 sidecar 441 个样本/4,414 秒，RSS/CPU/FD/线程 P99 为 17,492 KiB/0.0%/5/1，RSS 均值 17,405.397 KiB、FD 均值 3.603，审计仍为 1,046,557 bytes 且进程存活。该摘要仍是中途证据，`verification.json` 保持 `RUNNING`，不代表 24 小时完成。
- 2026-09-20：再次增量复核发现资源窗口出现真实变化；Observer 约 5,713 秒，sidecar 556 个样本/5,567 秒，RSS P99/最大值升至 17,752 KiB，CPU/FD/线程 P99 仍为 0.0%/5/1，RSS 均值 17,426.115 KiB、FD 均值 3.619，审计仍为 1,046,557 bytes 且进程存活。该摘要仍是中途证据，`verification.json` 保持 `RUNNING`，最终 P99 需等 soak 结束。
- 2026-09-20：再次只读复核；Observer 约 6,441 秒，sidecar 628 个样本/6,289 秒，RSS P95/P99/最大值均为 17,752 KiB，CPU/FD/线程 P99 为 0.0%/5/1，RSS 均值 17,463.478 KiB、FD 均值 3.615，审计仍为 1,046,557 bytes 且两个进程存活。`verification.json` 继续保持 `RUNNING`；该结果仍是中途 ARM64 观测窗口，不代表 24 小时完成或新代码（EXP-047）已被长跑验证。
