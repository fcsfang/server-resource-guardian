# EXP-065：真实 Docker 内存负载与 Beszel 时间差补证

- 实验 ID：`EXP-065`
- 日期：2026-09-21
- 目的：在新的 disposable Multipass VM 中，把真实 Docker 内存负载、Guardian `observe/simulate` 原始时间和本地 Beszel Hub/Agent 的持久化采样放到同一时间基准，补 PG-P0-14 的 Beszel 时差证据。
- 状态：`INCONCLUSIVE`

## 安全边界

- 仅使用新建的 `guardian-p014-beszel` disposable VM；现有 `guardian-ubuntu` 不启动负载、不改配置、不读取登录态凭据。
- Beszel Hub/Agent 使用本地固定镜像 `henrygd/beszel:0.19.0` / `henrygd/beszel-agent:0.19.0`，一次性本地用户和 token 只存在于 disposable VM，实验后随 VM 删除。
- Guardian 仅 `simulate`；不创建 capability，不调用 action adapter，不调用 Docker/systemd stop/restart/kill；目标容器自然退出。
- 失败、空采样、时间对齐不足均保留原始证据，不把 Beszel 的空数据或单次 API 状态当作成功。

## 执行记录

### 命令

```bash
python3 scripts/run_p014_real_docker.py \
  --vm guardian-p014-beszel \
  --rounds 3 \
  --scenario memory \
  --experiment-id EXP-065 \
  --workload-seconds 75 \
  --output experiments/EXP-065-2026-09-21-real-docker-beszel-timing/data/real-docker-memory-v1.json
```

Beszel Hub/Agent 在同一 disposable VM 内使用本地固定镜像启动；实验结束前只通过已认证用户对 `systems`、`system_stats` 和 `container_stats` 做 GET-only 回读。回读原始响应见 [`beszel-readback-v1.jsonl`](data/beszel-readback-v1.jsonl)，其中 `systems.users` 已由对齐脚本排除。

### 环境

| 项目 | 实际值 |
| --- | --- |
| VM | `guardian-p014-beszel`，新建 disposable Multipass VM |
| OS / 架构 | Ubuntu 22.04.5 LTS / ARM64 |
| 资源 | 2 vCPU / 约 4 GiB 内存 / 16 GiB 磁盘 |
| Docker | 29.1.3，真实 Docker daemon |
| Beszel | Hub/Agent `0.19.0`，同 VM，系统状态 `up` |
| Guardian | `observe/simulate`，不创建 capability，不调用 action adapter |

### 结果

- 三轮 `unprotected/rescue/guardian` 内存窗口完成，控制探针 `36/36` 成功；目标均以自身 75 秒期限自然退出。
- 三个 Guardian 目标均被 Beszel 的真实 `container_stats` `1m` 记录看到，目标内存约 `257.13–257.45 MiB`；这是同负载的对象采样时间差证据，不是告警投递或通知时延。
- 从目标 Docker `StartedAt` 到 Beszel 首个包含同名对象的 `container_stats` 记录分别为 `23.658s`、`32.286s`、`40.383s`；从 Guardian 首个风险样本到该 Beszel 记录分别为 `11.947s`、`20.110s`、`29.004s`。原始对齐结果见 [`timing-alignment-v1.json`](data/timing-alignment-v1.json)。
- 三个 Guardian 窗口的业务 health probe 均在窗口开始前观察到 `healthy`，目标均自然退出；这不等同于业务恢复，未据此声称 recovery 通过。
- Beszel Hub/Agent 在长窗口期间保持运行；没有生产连接、真实动作或通知投递。

### 数据质量缺口

- 当前 runner 的 Observer 等待上限为 55 秒；在 Beszel 同负载采集和本轮真实 VM 资源竞争下，三个 Observer 均超时，只保留 `22/32`、`23/32`、`23/32` 条样本，共 `68/96`。该缺口已由 [`check_p014_beszel_timing.py`](../../scripts/check_p014_beszel_timing.py) 保留为 `guardian_observer_completion` / `guardian_observer_timeout`，不能把本轮当作完整连续采样通过。
- 本轮没有 owner 批准的 Rescue SLO，也没有证明同一 Observer 会话中的稳定恢复或业务恢复；P0-14 仍保持 `IN_PROGRESS/INCONCLUSIVE`。
- Beszel 记录是 `container_stats` 对象采样，不替代 `alerts_history`、通知渠道或生产 endpoint 证据；系统时间差只适用于本机 ARM64 disposable 环境。

## 结论

本实验补齐了真实 Docker 内存目标与本地 Beszel `container_stats` 的同负载时间对齐，证明在本机 ARM64 disposable 环境中 Beszel 首次持久化看到目标对象晚于目标启动约 `23.658–40.383s`，且晚于 Guardian 首个风险样本约 `11.947–29.004s`。同时，本轮暴露了 Beszel 采集负载下 Observer 55 秒等待边界不足，Guardian 连续采样门禁未通过。因此实验状态为 `INCONCLUSIVE`，不能据此进入 P0-15 或生产测试。
