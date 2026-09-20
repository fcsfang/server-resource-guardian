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

Observer 使用 5 秒采样间隔，由 `/usr/bin/time` 和 24 小时 `timeout` 包裹。完成后记录实际时长、最大 RSS、CPU 时间、进程是否持续、审计文件大小是否不超过 1 MiB、达到上限后的降级比例、readiness、VM 状态和错误摘要。

当前仅记录实验已启动；未完成前不把 PG-P0-07 标记为 DONE，也不进入 PG-P0-08。
