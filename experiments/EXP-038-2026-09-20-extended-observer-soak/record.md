# EXP-038：Guardian Observer 5 分钟延长 soak

- 实验 ID：`EXP-038`
- 日期：2026-09-20
- 关联目标：Goal 7 / `PG-P0-07`
- 状态：`RUNNING`
- 实验负责人：当前 Agent

## 1. 目的

在当前 Multipass disposable VM 中以 5 分钟硬上限运行只读 Observer，补充比 EXP-036 更长的 RSS、CPU、采样连续性和审计写入基线。该实验只用于本地工程筛查，不替代路线要求的 24 小时 soak 或生产 P99 校准。

## 2. 预置边界

- VM：`guardian-ubuntu`，Ubuntu 22.04.5 ARM64，2 vCPU，约 3.8 GiB 内存。
- 运行目录：`/tmp/guardian-p0-03-check`，输出为临时文件。
- 模式：`observe`；Docker 仅只读 stats/inspect。
- 停止条件：300 秒自动停止；出现非预期动作、路径越界、错误持续增长或无法停止时提前停止并保留输出。
- 禁止动作：不安装/enable/start systemd，不执行 Docker stop/restart/kill，不修改资源上限，不连接生产。

## 3. 运行命令与待记录数据

Observer 使用 `--interval 5`，由 `/usr/bin/time` 包裹 `timeout --signal=TERM 300s`。需要记录：退出码、实际时长、最大 RSS、CPU 时间、输出/审计条数、状态分布、stderr 字节数和 readiness 内容。

实验结束后补充结果、结构化数据、异常和限制；在完成前不把 PG-P0-07 标记为 DONE。
