# EXP-059：稳定对象身份下的联合风险计划 fixture

- 实验 ID：`EXP-059`
- 状态：`INCONCLUSIVE`
- 日期：2026-09-20
- 关联 Goal：`Goal 7 / PG-P0-14`
- 目的：在不安装 Docker、不调用动作适配器的 disposable VM 中，用明确隔离的只读 synthetic `stats/inspect` fixture，把固定对象 ID 映射到 workload 的真实 systemd cgroup，验证 CPU/内存归因和混合联合 simulate 路径。

## 1. 安全边界

- 仅使用新建的 `guardian-p014-target` Multipass VM；现有 `guardian-ubuntu` 未操作，未连接生产。
- `p014_fake_docker.py` 只实现 Observer 使用的 `stats` 和 `inspect` 读取命令；任何其他命令返回失败，不包含 stop/restart/kill/delete 能力。
- workload、rescue/workload slice、loopback fixture 和采样窗口均有界；Guardian 只运行 `simulate`，所有计划必须是 `execution=not_executed`。
- 实验结束后停止 transient workload、清理 fixture 并删除 disposable VM；原始中间结果保留，不覆盖失败调参记录。

## 2. 输入与结果

- VM：Ubuntu 22.04.5 ARM64、2 vCPU、3 GiB 内存、12 GiB 虚拟磁盘；systemd 249、cgroup v2、PSI 可用。
- 场景：CPU、memory、capacity、I/O、mixed；三组共 15 条控制记录；15/15 控制探针成功。
- Guardian：5 个 probe window、每个 3 个连续样本，共 15 个样本；probe 进程全部退出 0。
- 原始数据：[`target-fixture-v3.json`](data/target-fixture-v3.json)。v1/v2 为保留的 dwell 调参中间结果。

`mixed` 第 3 个样本的关键证据：

- `memory`：`critical`，`TARGET_CONFIRMED`，固定 synthetic ID `aaaaaaaa...`。
- `cpu`：`warning`，`TARGET_CONFIRMED`，同一固定 synthetic ID。
- 联合状态：`critical`，`active_resources=["memory", "cpu"]`，`target_state=TARGET_CONFIRMED`。
- 联合计划：`action=graceful_stop`，`execution=not_executed`。
- 单资源 CPU 和 memory 场景也分别生成同样边界的 simulate 计划。
- I/O cgroup 缺少 `io.stat`，因此保持降级/不动作；capacity 没有 writer ownership 时保持 fail-closed。

## 3. 结论与边界

本实验验证了“稳定 ID + 稳定 cgroup + 连续风险窗口 → 同目标联合 simulate 计划”的代码集成路径，并证明没有动作执行。但 synthetic Docker 不是 Docker daemon，不能证明真实 Docker stats/inspect、真实容器生命周期、真实 SSH SLO、生产阈值或生产动作安全性；ARM64、单轮短窗口和 I/O controller 缺失也限制了结论。

因此 EXP-059 保持 `INCONCLUSIVE`，PG-P0-14 仍为 `IN_PROGRESS`。P0-14 还需要在真实非生产 Docker 对象或等价的已授权环境补齐三组长窗口、真实对象 churn、I/O/capacity 归因和救援 SLO；P0-16 的本地 fake sink 合同已由 EXP-061 完成，但在 P0-14 外部证据、明确授权和生产准入前不进入真实 enforce 或生产。
