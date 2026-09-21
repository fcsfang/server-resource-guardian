# EXP-081：P0-14 真实 Docker CPU 同一 Observer 恢复窗口

- 实验 ID：`EXP-081`
- 日期：2026-09-21
- 关联任务：`Goal 7 / PG-P0-14`、`Goal 7 / PG-P0-17 Emergency Shedding v1`
- 状态：`INCONCLUSIVE`
- 安全边界：仅使用一次性 disposable Multipass VM 和真实 Docker fixture；Guardian 仅 `simulate`，只做观测、恢复边界和离线回放，不创建 capability、不调用 action adapter、不执行 Docker stop/restart/kill，不连接生产、Beszel 或真实通知渠道。
- 目标：用有界真实 Docker CPU workload，在 workload 自然退出后让同一 Observer 继续采样，验证 CPU 危险/CPU TOP 贡献与后续恢复状态是否能在同一会话中被观察；同时保留 P0-17 的零动作和计划契约边界。

## 计划

在 1 vCPU、3 GiB、12 GiB 的一次性 Ubuntu 22.04 ARM64 VM 中，执行 3 轮、每轮 45 秒、2 个有界 CPU worker 的真实 Docker fixture；Guardian 每轮采集 72 个样本，使 Observer 窗口覆盖 workload 自然退出后的恢复阶段。实验完成后精确删除该 VM，并用 P0-14 检查器和 P0-17 离线回放器重算结果。

计划命令：

```bash
python3 scripts/run_p014_real_docker.py \
  --vm guardian-p014-cpu-recovery-v1 --rounds 3 --scenario cpu \
  --experiment-id EXP-081 --workload-seconds 45 --cpu-workers 2 \
  --observer-samples 72 \
  --output experiments/EXP-081-2026-09-21-p014-real-docker-cpu-recovery-window/data/cpu-recovery-source-v1.json

python3 scripts/check_p014_real_docker.py \
  experiments/EXP-081-2026-09-21-p014-real-docker-cpu-recovery-window/data/cpu-recovery-source-v1.json \
  --output experiments/EXP-081-2026-09-21-p014-real-docker-cpu-recovery-window/data/cpu-recovery-source-v1-check.json

python3 scripts/check_p017_replay.py \
  experiments/EXP-081-2026-09-21-p014-real-docker-cpu-recovery-window/data/cpu-recovery-source-v1.json \
  --output experiments/EXP-081-2026-09-21-p014-real-docker-cpu-recovery-window/data/p017-cpu-recovery-v1.json
```

## 结果

- 数据文件：[`cpu-recovery-source-v1.json`](data/cpu-recovery-source-v1.json)、[`cpu-recovery-source-v1-check.json`](data/cpu-recovery-source-v1-check.json)、[`p017-cpu-recovery-v1.json`](data/p017-cpu-recovery-v1.json)。
- 环境：全新 `guardian-p014-cpu-recovery-v1`，1 vCPU、3 GiB、12 GiB、Ubuntu 22.04 ARM64；一次性安装 Docker 29.1.3、cgroup v2。实验完成后已精确删除 VM，未触碰 `guardian-ubuntu`。
- 采集：3 轮、每轮 72 个 Guardian 样本，共 216；控制探针 `36/36`；Guardian 窗口、identity 窗口、health 窗口和 churn 窗口均为 `3/3`；开销原始行 524；fixture 容器均自然退出，未执行 Docker stop/restart/kill。
- 危险窗口：第 1、2 轮主要为 `normal/warning`，第 3 轮出现 18 个 CPU `critical`；真实 full ID/cgroup/created_at/inode 在 3 个窗口均可见。P0-14 的资源误触发/漏检边界仍保留。
- 同一 Observer 恢复：72 个样本覆盖 workload 自然退出后的窗口，但三轮均未形成完整 `recovered`。容器退出后同一 Observer 长时间保持 `warning + NO_TARGET`，不能把目标消失或无目标当作宿主恢复；第 3 轮 CPU 的 `critical` 之后仍回到 `warning`，而非 `recovered`。
- P0-14 检查：`code_gate=PASS`、整体 `status=INCONCLUSIVE`。缺口包括 Beszel 时间差、业务恢复不能由 healthcheck 单独证明、owner Rescue SLO、资源误触发/漏检、同 Observer 恢复未观察到和工作窗口/生产边界限制。
- P0-17 回放：`code_gate=PASS`、整体 `status=INCONCLUSIVE`；216 个样本中旧策略计划 15 个，新策略计划 `15`，计划契约失败 `0`，新动作执行 `0`，历史样本 cgroup inode 缺口 `0`。原因计数为 `HOST_RISK_NOT_CONFIRMED=198`、`INCOMPLETE_SAMPLES=3`、`NO_EFFECTIVE_SHEDDING_TARGET=3`、`PLAN_GENERATED=15`、`TOP_ACTIONABLE_CONSUMER=15`；计数可重叠，原始每样本结果已保留。
- 安全声明：数据只来自 local-disposable VM；未连接生产、Beszel 或真实通知渠道，未读取真实凭据；结果不代表真实动作、业务恢复、SSH 可进入性或生产准入。

## 结论与后续

EXP-081 证明了更长同一 Observer 窗口能够保留 workload 自然退出后的数据，但当前 CPU/目标生命周期边界下未观察到完整恢复；`warning + NO_TARGET` 被正确保留为未恢复状态。P0-14/P0-17 整体保持 `INCONCLUSIVE`，不授权真实动作，不代表 SSH 可进入性、业务恢复、长跑稳定性或生产准入。
