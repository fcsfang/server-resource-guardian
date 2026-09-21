# EXP-060：P0-14 检测提前量与 Rescue 探针耗时边界

- 实验 ID：`EXP-060`
- 状态：`INCONCLUSIVE`
- 日期：2026-09-20
- 关联 Goal：`Goal 7 / PG-P0-14`
- 目的：在不执行真实动作的前提下，为三资源对照补充 workload 的 systemd 单调时钟起点、Guardian 首次风险样本和 Rescue/Guardian 只读控制探针耗时；不把本地测量写成批准的生产 SLO。

## 1. 安全边界

- 只使用本轮新建的本地 disposable Multipass VM；不操作现有 `guardian-ubuntu`，不连接非生产或生产主机。
- 使用 EXP-058 中明确标注的有界 workload、loopback fixture、`rescue.slice`/`workload.slice` 和只读 synthetic Docker identity；不安装或调用 Docker daemon。
- Guardian 只运行 `simulate`；不调用 stop/restart/terminate/kill，不执行资源变更，不发送真实通知。
- 只读时间基准使用 systemd `ActiveEnterTimestampMonotonic`，事件时间使用 Guardian 输出的 `observed_monotonic_ns`；若任一字段缺失则保留缺失，不补估计值。
- 探针超时、根盘/loopback 越界、未预期服务变化或清理失败即停止该轮并保留原始结果。
- 结束后只停止本轮精确 transient unit、卸载本轮 loopback、清理本轮目录并删除本轮 disposable VM。

## 2. 预置验收条件

1. 五个场景均保留无保护/Rescue/Guardian 记录，Guardian 每个场景至少两个连续样本。
2. 原始 JSON 能重算 P0-14 代码/安全门禁；所有执行字段仍为 `not_executed` 或 `not_applicable`。
3. 对至少一个 active 风险场景记录从 workload 激活到首次 warning/critical 样本的单调时钟差；无法测量时明确记录 `not_observed`。
4. 记录 Rescue/Guardian 只读控制探针的 P95，但因没有 owner 批准的目标阈值，比较结果保持 `measured_not_approved`。
5. 不把自然退出、重新建立基线或普通状态写成 recovered；恢复窗口未在同一连续 Observer 会话中验证时保持缺口。

## 3. 原始数据

运行结果保存在 [`timing-boundary-v1.json`](data/timing-boundary-v1.json) 和修正时间字段后的 [`timing-boundary-v2.json`](data/timing-boundary-v2.json)；中间失败或不确定结果没有覆盖旧 EXP-058/059 数据。

## 4. 实际结果

- 环境：新建 `guardian-p014-timing`、Ubuntu 22.04.5 ARM64、2 vCPU、3 GiB 内存、12 GiB 虚拟磁盘；实验结束后已卸载本地仓库挂载并删除该 VM。现有 `guardian-ubuntu` 仍在运行但未操作。
- 覆盖：CPU、memory、capacity、I/O、mixed 五场景；无保护/Rescue/Guardian 三组共 15 条记录；两轮数据均为 15/15 控制探针成功，Guardian 每个场景 3 个连续样本，所有执行字段保持 `not_executed`。
- `timing-boundary-v1.json` 的 P0-14 代码/安全门禁为 `PASS`，但时间字段尚未透传到顶层样本，因此不用于检测提前量结论；该文件保留作为第一次运行结果。
- `timing-boundary-v2.json` 修正为从 Guardian `signals.observed_monotonic_ns` 读取时间，测到首次 warning/critical 样本约 2,563.828–2,647.760 ms；Rescue/Guardian 只读控制探针 P95 约 64.719–122.810 ms，但 `rescue_slo.status=measured_not_approved`，没有 owner 批准阈值，不能判定 SLO 通过。
- v2 的短 3 秒窗口未稳定产生 CPU/memory 单资源确认计划，重算器因此将该文件 `code_gate=FAIL`；这不是放宽门槛的理由，v3/EXP-059 仍是当前同目标 mixed 正向代码门禁证据。
- 将 EXP-059 v3 与本实验 v2 作为两个独立输入组成证据包后，`check_p014_evidence.py` 的 bundle `code_gate=PASS`，同时观察到 mixed 同目标正向、raw conflict fail-closed、时间字段和无执行边界；bundle 仍为 `INCONCLUSIVE`。
- 回收 probe 中出现 `normal` 与 `critical`，但它们来自新 Observer 的重新基线/残留 synthetic 状态，未在同一连续 Observer 会话中证明 `recovered`；恢复证据继续保持缺失。

## 5. 结论

本实验为 `INCONCLUSIVE`：补齐了单调时钟和只读探针耗时的测量字段，证明代码可以保留这些证据，但没有形成批准的 Rescue SLO，也没有完成真实 Docker、长窗口或同一会话恢复验证。PG-P0-14 继续保持 `IN_PROGRESS`；不进入真实 enforce、不连接生产。

## 6. 边界

即使代码门禁通过，若仍使用 synthetic identity、短窗口、未验证恢复或没有 owner 批准的 Rescue SLO，结果必须保持 `INCONCLUSIVE`，PG-P0-14 不得改为 `DONE`，也不得进入真实 enforce 或生产。
