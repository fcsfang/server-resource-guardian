# EXP-058：三资源 Rescue/Guardian 对照与联合仲裁

- 实验 ID：`EXP-058`
- 状态：`INCONCLUSIVE`
- 日期：2026-09-20
- 关联 Goal：`Goal 7 / PG-P0-14`
- 目的：在不执行真实动作的前提下，对 CPU、内存、容量/inode、I/O 及至少一个混合场景建立“无保护 / 仅 Rescue Plane / Rescue + Guardian observe/simulate”三组可重算对照，并验证多资源同目标合并与冲突放弃。

## 1. 安全边界

- 只使用新建的本地 disposable Multipass VM；不修改现有 `guardian-ubuntu`，不连接非生产或生产主机。
- 所有 workload、slice、临时 loopback 挂载点和日志均有界；容量实验不填充 VM 根盘。
- Guardian 只运行 `observe`/`simulate`；`execution` 必须为 `not_applicable` 或 `not_executed`。
- 不调用 Docker stop/restart/terminate，不执行 systemd stop/restart，不删除未知路径，不修改生产配置，不发送真实通知。
- 停止条件：探针超时/失败、根盘或临时挂载点越过预设上限、内存持续下降、cgroup 状态异常、任何未预期服务状态变化。
- 清理：停止并回收 transient workload，卸载 loopback，删除 disposable VM；原始结构化结果只保留脱敏核心数据。

## 2. 预置验收条件

1. 三组都能按相同场景和相同只读探针重算；Rescue 组额外核对救援 slice，Guardian 组额外保存 observe/simulate 事件。
2. 四类单资源风险分别记录压力信号、探针成功率/P95、Guardian 状态/归因和计划边界。
3. 至少一个 CPU+内存或内存+I/O 混合场景形成一个联合事件；同一稳定目标可以合并，不同目标或缺失/过期归因必须 `MULTI_RESOURCE_AMBIGUOUS`，动作次数为 0。
4. 所有三组和所有场景均无真实动作；原始数据可复核，失败或不确定结果不删除。

## 3. 当前进度

- 联合仲裁代码和负向测试已先行实现：`src/guardian_multirisk.py`、`tests/test_guardian_multirisk.py`。
- `data/three-way-short.json` 保留了首次短 smoke；因 runner 当时同时传入 `--once` 和 `--samples`，每个 Guardian 场景只有一个窗口，不能用于 dwell 结论。
- `data/three-way-fast-v3.json` 为修正统计口径后的连续采样结果（v2 原始结果保留）：15/15 控制探针成功，5 个 Guardian 场景各采到 2 个样本；首样本为 `degraded_observability`，第二样本在 CPU/磁盘/I/O 联合活动下因 `docker_observation_unavailable` 和 `observation_quality_status_missing` 进入 `escalated`，目标为 `DEGRADED_OBSERVABILITY`，所有计划均 `execution=not_executed`，真实动作次数为 0。
- 三组相同场景的短时可进入性 smoke 已完成，但仍未形成稳定目标的正向 simulate 计划、检测提前量、恢复窗口或足够长的 P95 对照；同目标合并与不同目标冲突由联合仲裁单测覆盖，未在本次无 Docker disposable VM 中取得正向实测归因。

## 4. 结果

环境为新建 `guardian-p014`、Ubuntu 22.04.5 ARM64、2 vCPU、3 GiB 内存、12 GiB 虚拟磁盘；场景为 CPU、memory、capacity、I/O、mixed，各一轮。三组共 15 条控制记录，控制探针 15/15 成功；Guardian 5 个窗口共 10 个样本，探针进程均退出 0。实验结束后已删除并 purge 该 disposable VM；现有 `guardian-ubuntu` 不在实验范围内。

## 5. 结论

本次结果为 `INCONCLUSIVE`：证明了三组短时控制路径可运行，以及质量降级/联合目标不确定时 fail-closed；没有证明稳定目标的检测提前量、同目标实测合并、恢复 SLO 或生产级 SSH 行为。PG-P0-14 仍未完成，下一步应在隔离且具备稳定对象身份的环境补齐连续窗口和正/负混合对照；在此之前不进入 PG-P0-16 或 PG-P0-15，不连接生产。即使后续通过，也只代表当前 ARM64 disposable VM 和有界 fixture，不代表生产 x86_64、任意资源耗尽、真实 SSH 客户端 SLO 或生产动作安全性。
