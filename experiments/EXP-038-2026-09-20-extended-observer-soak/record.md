# EXP-038：Guardian Observer 5 分钟延长 soak

- 实验 ID：`EXP-038`
- 日期：2026-09-20
- 关联目标：Goal 7 / `PG-P0-07`
- 状态：`PASSED`（5 分钟局部 soak；不等同于路线要求的 24 小时验收）
- 实验负责人：当前 Agent

## 1. 目的

在当前 Multipass disposable VM 中以 5 分钟硬上限运行只读 Observer，补充比 EXP-036 更长的 RSS、CPU、采样连续性和审计写入基线。该实验只用于本地工程筛查，不替代路线要求的 24 小时 soak 或生产 P99 校准。

## 2. 预置边界

- VM：`guardian-ubuntu`，Ubuntu 22.04.5 ARM64，2 vCPU，约 3.8 GiB 内存。
- 运行目录：`/tmp/guardian-p0-03-check`，输出为临时文件。
- 模式：`observe`；Docker 仅只读 stats/inspect。
- 停止条件：300 秒自动停止；出现非预期动作、路径越界、错误持续增长或无法停止时提前停止并保留输出。
- 禁止动作：不安装/enable/start systemd，不执行 Docker stop/restart/kill，不修改资源上限，不连接生产。

## 3. 运行命令

Observer 使用 `--interval 5`，由 `/usr/bin/time` 包裹 `timeout --signal=TERM 300s`。需要记录：退出码、实际时长、最大 RSS、CPU 时间、输出/审计条数、状态分布、stderr 字节数和 readiness 内容。

## 4. 结果

| 指标 | 结果 |
| --- | --- |
| 结束状态 | `timeout` 退出码 124，符合 300 秒停止条件 |
| 实际运行时间 | 300.02 秒 |
| 最大 RSS | 27,672 KiB，约 27.0 MiB |
| CPU 时间 | user 1.23 秒 + sys 0.79 秒，共 2.02 秒 |
| 采样/输出条数 | 43 |
| 审计条数 | 43，全部 `written` |
| 状态序列 | 首条 `degraded_observability`，随后 42 条 `normal` |
| readiness | `observe:ready` |
| 错误输出 | 没有 Python traceback；stderr 文件仅包含预期的 timeout/time 摘要 |

结构化摘要见 [`data/verification.json`](data/verification.json)。

## 5. 结论与限制

5 分钟局部 soak 内，Observer 连续运行、审计写入和 RSS 均稳定，未观察到单调增长、未处理异常或 Docker 变更。该结果比 EXP-036 提供了更长的本地筛查证据，但仍不能证明 24 小时无泄漏、真实 systemd manager watchdog、磁盘满恢复、容器 churn 安全或生产 P99。

在完成更长 soak、真实系统故障边界和 P99 资源测量前，PG-P0-07 保持 `IN_PROGRESS`，不进入 PG-P0-08。

## 6. 更新记录

- 2026-09-20：完成 300 秒有界 Observer soak；43 条采样和审计全部写入，最大 RSS 27,672 KiB。
