# EXP-019：Guardian 本地资源占用基线

- 实验 ID：`EXP-019`
- 状态：`PASSED`
- 创建日期：2026-09-19
- 最近更新：2026-09-19
- 关联 Goal：`Goal 4 / 本地闭环验收补充`
- 实验负责人：当前 Agent

## 1. 实验目的

测量当前 Guardian Python 原型在 Mac Multipass Ubuntu 22.04 ARM64 测试机上的一次性 `observe` 和持续采样资源占用，确认它不会因为自身开销成为明显的测试环境负担。

## 2. 环境与边界

- VM：`guardian-ubuntu`，Ubuntu 22.04.5 ARM64，2 vCPU，4GB 内存上限。
- 测量时运行中容器：0；因此本记录是空载 Guardian 基线，不代表生产多容器压力下的最终上限。
- 模式：只读 `observe`，未执行 Docker stop/restart/kill 或资源变更。
- 测量工具：Ubuntu `/usr/bin/time`；观测脚本调用 Docker stats 只读接口。

## 3. 结果

| 场景 | 运行时间 | 峰值 RSS | CPU 时间 | 结果 |
| --- | ---: | ---: | ---: | --- |
| 单次 `observe --once` | 0.03s | 26,428 KiB（约 25.8 MiB） | 0.03s | 正常输出，Docker 可用，容器数 0 |
| 持续 `observe --interval 5` | 32s | 27,100 KiB（约 26.5 MiB） | user+sys 0.12s，约 0.38% 单核时间 | 正常运行至有界测量结束 |

测量前 VM `free -h` 显示已用内存约 233 MiB、可用约 3.4 GiB；该数值包含 Ubuntu、Docker 等基础环境，不应全部归因于 Guardian。

## 4. 结论与限制

- 当前原型的本地空载资源占用较低，持续采样的内存稳定在约 26 MiB，CPU 时间主要集中在采样瞬间。
- 该结果只能说明当前 0 容器、2 vCPU/4GB VM 下的基线；容器数量、采样周期、快照写入、压力场景和业务探针都会改变开销。
- Guardian 当前不是完整 systemd 常驻服务，实测对象是 Python 观测进程；生产部署前仍需做多容器、内存压力、快照和业务 health check 的上限测试。

## 5. 更新记录

- 2026-09-19：完成 Mac Multipass Ubuntu 空载单次和 32 秒持续采样资源基线测量。
