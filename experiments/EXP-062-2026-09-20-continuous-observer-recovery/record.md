# EXP-062：同一 Observer 会话的长窗口与恢复

- 实验 ID：`EXP-062`
- 状态：`INCONCLUSIVE`
- 日期：2026-09-20
- 关联 Goal：`Goal 7 / PG-P0-14`
- 目的：在不执行真实动作的前提下，验证一个连续运行的 Observer 是否能在同一进程内先观察到风险、再在精确停止 workload 后输出 `recovered`；同时保留不少于 30 秒的有界观察窗口。

## 1. 安全边界

- 只使用本实验新建的 disposable Multipass VM；不操作现有 `guardian-ubuntu`，不连接生产或其他外部主机。
- workload、`rescue.slice`/`workload.slice` 和 loopback fixture 均只存在于该 VM；停止和清理只针对本实验生成的精确 unit、路径和 fixture 状态。
- synthetic Docker fixture 只提供 Observer 所需的只读 `stats`/`inspect`；不安装 Docker daemon，不调用 Docker mutation command。
- Observer 只使用 `--mode simulate`，所有决策必须保持 `execution=not_executed`；不调用 action adapter、不发送通知。
- 观察进程和 workload 均设置硬上限；超时、探针失败、清理失败或目标范围不明确时停止本轮并保留原始结果。

## 2. 预置验收条件

1. 五个场景均有无保护/Rescue/Guardian 的原始记录，Guardian 每个场景由同一个 Observer 进程输出连续样本。
2. Guardian Observer 的单次会话窗口不少于 30 秒，并记录启动、精确停止 workload、清理 synthetic identity 和退出时间。
3. 至少一个场景在同一 Observer 会话中出现风险状态后再出现 `recovered`；若没有则保持明确缺口，不修改门槛。
4. 所有原始 JSON 可由 P0-14 重算器复核；执行字段只能是 `not_executed` 或 `not_applicable`。
5. 不把 workload 被停止、对象消失或重新建立基线单独写成业务恢复；本实验只验证 Observer 风险状态恢复，不宣称业务健康恢复。

## 3. 执行结果

- 原始数据：[`continuous-recovery-v4.json`](data/continuous-recovery-v4.json)；v1 保留 runner PATH 错误导致的 fail-closed 中间结果，v2/v3 保留封装与读取截断问题的中间结果，均未覆盖既有 EXP-059/060。
- 环境：新建 `guardian-p014-recovery`、Ubuntu 22.04.5 ARM64、2 vCPU、3 GiB 内存、12 GiB 虚拟磁盘；实验结束后已卸载项目挂载并按精确 VM 名删除。现有 `guardian-ubuntu` 保持运行且未操作。
- 覆盖：CPU、memory、capacity、I/O、mixed 五场景；无保护/Rescue/Guardian 三组共 15 条记录；控制探针 15/15 成功；每个 Guardian 场景由一个连续 Observer 服务输出 36 条样本，Observer 服务运行约 37–39 秒并正常退出。
- 同一会话恢复：`capacity`、`cpu`、`mixed` 在同一 Observer 进程内先出现 active 风险状态，精确停止 workload 后再出现 `recovered`；`memory`、`io` 本轮没有形成稳定的恢复状态，保留为负向/未完成证据。部分场景在后段再次出现 warning，说明本地宿主背景压力/阈值仍会影响状态，不能把一次 recovered 写成业务恢复。
- 检测提前量：首个 warning/critical 相对 systemd 激活单调时钟约为 capacity 2,522.307 ms、cpu 2,583.654 ms、io 2,625.556 ms、memory 2,556.398 ms、mixed 2,743.739 ms；样本数和原始字段可由 `summarize_evidence` 重算。
- Rescue/Guardian 只读控制探针 P95：Rescue 约 73.287–133.470 ms，Guardian 约 93.582–221.948 ms；`rescue_slo.status=measured_not_approved`，没有 owner 批准阈值，因此不判定 SLO 通过。
- P0-14 重算：EXP-062 单独 `code_gate=PASS`、`status=INCONCLUSIVE`；与 EXP-059 v3、EXP-060 v2 合并后仍 `code_gate=PASS`、`status=INCONCLUSIVE`。合并覆盖 45 条原始记录、210 条 Guardian 样本，冲突/缺失目标 fail-closed 和零动作边界通过。

## 4. 结论与边界

本实验补齐了“长窗口 + 同一 Observer 会话恢复”的本地代码/安全证据：连续 Observer 可以跨越 workload 停止保留状态机，并在部分场景输出 `recovered`。但结果仍为 `INCONCLUSIVE`，不能改写 PG-P0-14 为 `DONE`，原因是：

- Docker 身份仍是 synthetic fixture，不是真实 Docker daemon/容器生命周期；
- 恢复只代表 Guardian 风险状态恢复，不包含业务 health probe，也不证明 SSH/SLO 恢复；
- memory/I/O 场景本轮未形成同会话 `recovered`，且后段存在再次 warning；
- Rescue SLO 没有 owner 批准阈值；
- VM 为 ARM64 disposable 环境，不覆盖生产 x86_64、真实设备和真实对象 churn。

因此 PG-P0-14 继续保持 `IN_PROGRESS`，PG-P0-15 仍阻塞；不进入真实 enforce、不连接真实通知渠道或生产。
