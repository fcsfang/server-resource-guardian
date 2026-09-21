# EXP-067：真实 Docker + Beszel 时间差重跑（runner 自同步源码）

- 实验 ID：`EXP-067`
- 日期：2026-09-21
- 目的：在 runner 自动同步 `src/`、动态 Observer 等待上限修复后，于全新 disposable VM 重跑真实 Docker 内存负载与 Beszel `container_stats` 同负载时间对齐。
- 状态：`INCONCLUSIVE`

## 安全边界

- 仅使用新建的 `guardian-p014-beszel-v3` disposable Multipass VM；既有 `guardian-ubuntu` 未启动负载、未改配置。
- Beszel Hub/Agent `0.19.0` 只在该 disposable VM 内运行；认证 token 未写入仓库、未出现在本记录，原始回读仅保留脱敏前的本地实验文件。
- Guardian 以 `simulate` 运行；没有创建 capability、没有调用 action adapter，也没有调用 Docker/systemd stop、restart 或 kill。三轮目标均等待自身 75 秒期限自然退出。
- 原始 Guardian/Docker 结果见 [`real-docker-memory-v3.json`](data/real-docker-memory-v3.json)，Beszel 仅通过 GET-only API 读取的原始集合见 [`beszel-readback-v3.jsonl`](data/beszel-readback-v3.jsonl)，只读对齐结果见 [`timing-alignment-v3.json`](data/timing-alignment-v3.json)。

## 执行结果

- 真实 Docker 控制探针 `36/36` 成功；3 个 Guardian 窗口均完成 `32/32` Observer 样本，合计 `96` 个样本，三轮 observer 均未超时。
- 三个目标均在开始前观察到健康，并在 75 秒后自然退出；自然退出不被写成业务恢复。
- Beszel 回读包含 `systems=1`、`system_stats=18`、`container_stats=18`，系统状态为 `up`；三轮目标均被同名 `container_stats` 记录观察到。
- 从目标 Docker `StartedAt` 到 Beszel 首个同名 `container_stats` 分别为 `52.842s`、`34.428s`、`15.847s`；从 Guardian 首个风险样本到该 Beszel 记录分别为 `41.414s`、`23.081s`、`4.938s`。目标内存读数约 `257.14–257.46 MiB`。
- 对齐脚本结果为 `code_gate=PASS`，但实验状态仍为 `INCONCLUSIVE`，唯一保留缺口为没有 owner 批准的 Rescue SLO。

## 结论

EXP-067 修复并验证了 EXP-066 的 runner 源码同步问题，也验证了动态 Observer 等待上限在真实 Docker + Beszel 同机负载下可获得完整 `96/96` 样本。它补充的是本机 ARM64 disposable 环境内“Guardian/真实 Docker/Beszel container_stats”的时间对齐证据，不是告警投递、业务恢复、目标机校准或生产证据；EXP-065/066 的失败和不完整证据继续保留，P0-14 仍不能进入生产 enforce。
