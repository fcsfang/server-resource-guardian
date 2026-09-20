# EXP-036：Guardian Observer 有界长跑基线

- 实验 ID：`EXP-036`
- 日期：2026-09-20
- 关联目标：Goal 7 / `PG-P0-07`
- 状态：`PASSED`（45 秒局部 soak；不等同于路线要求的 24 小时验收）
- 实验负责人：当前 Agent

## 1. 实验目的

在当前 Multipass disposable VM 中以有限时长连续运行 Observer，测量初步 RSS/CPU/采样和审计行为，检查是否出现明显退出、错误输出、审计丢失或资源单调增长迹象；同时确认 readiness 的语义是“采集进程已就绪”，而不是“首个风险样本正常”。

## 2. 环境与停止条件

- VM：`guardian-ubuntu`，Ubuntu 22.04.5 LTS ARM64，2 vCPU，约 3.8 GiB 内存，Docker 29.1.3。
- 运行目录：`/tmp/guardian-p0-03-check`，仅临时源文件、配置、JSONL 和输出。
- Observer：`observe`，2 秒配置间隔；Docker 只读 stats/inspect。
- 停止条件：45 秒硬上限；出现非预期 Docker 变更、路径越界、错误增长或无法停止即提前终止。本次由 `timeout` 按预设上限停止，未触发异常停止。

## 3. 结果

| 指标 | 结果 |
| --- | --- |
| 结束状态 | `timeout` 退出码 124，符合 45 秒停止条件 |
| 实际运行时间 | 45.04 秒 |
| 最大 RSS | 27,672 KiB，约 27.0 MiB |
| CPU 时间 | user 0.27 秒 + sys 0.15 秒 |
| 采样/输出条数 | 11 |
| 审计条数 | 11，全部 `written` |
| 状态序列 | 首条 `degraded_observability`（建立 OOM 基线/样本不足），随后 10 条 `normal` |
| readiness | 修正后写入 `observe:ready` |
| stderr | 0 字节 |

结构化摘要见 [`data/verification.json`](data/verification.json)。

## 4. 结论

在 45 秒局部 soak 内，Observer 能持续采样并写入审计，RSS 约 27 MiB，未观察到 stderr 错误或 Docker 变更；首个样本降级、后续恢复为 normal，符合组合风险引擎的基线语义。readiness 与风险状态已分离，短时初始降级不会把 readiness 文件永久写成风险状态。

## 5. 限制

- 45 秒不是 24 小时，无法证明长期无泄漏、日志轮换、数据库维护或容器 churn 安全。
- 11 个样本不足以计算生产意义上的 P99；27 MiB 只能作为当前 VM/当前容器数量/当前配置的局部观测值。
- 本实验没有安装或启用 systemd unit，因此未证明真实 manager 的 watchdog 投递和重启恢复。
- 未执行磁盘耗尽、Hub 不可用或真实 Docker 超时故障注入；这些继续由 fixture 和后续有界实验覆盖。

## 6. 更新记录

- 2026-09-20：完成 45 秒有界 observe soak；随后修正 readiness 语义并用 12 秒 smoke 验证 `observe:ready`。
