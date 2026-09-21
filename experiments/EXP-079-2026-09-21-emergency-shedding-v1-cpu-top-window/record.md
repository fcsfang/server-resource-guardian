# EXP-079：Emergency Shedding v1 多 worker CPU TOP 窗口

- 实验 ID：`EXP-079`
- 日期：2026-09-21
- 关联任务：`Goal 7 / PG-P0-17 Emergency Shedding v1`
- 状态：`INCONCLUSIVE`
- 安全边界：仅使用一次性 disposable Multipass VM 和真实 Docker fixture；Guardian 仅 `simulate`，只做观测、回放和契约检查，不创建 capability、不调用 action adapter、不执行 Docker stop/restart/kill，不连接生产、Beszel 或真实通知渠道。
- 目标：在 2 vCPU VM 中使用 2 个有界 CPU worker 形成可重现的真实 Docker CPU TOP 贡献窗口，验证“整机危险确认 → CPU TOP → local-disposable actionable_set”在 P0-17 回放中的计划边界；若危险或样本门未满足，必须保留零计划的 fail-closed 结果。

## 计划

在 2 vCPU、3 GiB、12 GiB 的一次性 Ubuntu 22.04 ARM64 VM 中，执行 3 轮、每轮 45 秒的真实 Docker CPU fixture，使用 `--cpu-workers 2`；每轮保留 Guardian 32 个样本、控制探针、Docker full ID/cgroup/created_at/inode、health 状态和开销。实验完成后精确删除该 VM，并以 P0-14 检查器和 P0-17 离线回放器重算结果。

计划命令：

```bash
python3 scripts/run_p014_real_docker.py \
  --vm guardian-p017-cpu-top-v1 --rounds 3 --scenario cpu \
  --experiment-id EXP-079 --workload-seconds 45 --cpu-workers 2 \
  --output experiments/EXP-079-2026-09-21-emergency-shedding-v1-cpu-top-window/data/cpu-top-source-v1.json

python3 scripts/check_p014_real_docker.py \
  experiments/EXP-079-2026-09-21-emergency-shedding-v1-cpu-top-window/data/cpu-top-source-v1.json \
  --output experiments/EXP-079-2026-09-21-emergency-shedding-v1-cpu-top-window/data/cpu-top-source-v1-check.json

python3 scripts/check_p017_replay.py \
  experiments/EXP-079-2026-09-21-emergency-shedding-v1-cpu-top-window/data/cpu-top-source-v1.json \
  --output experiments/EXP-079-2026-09-21-emergency-shedding-v1-cpu-top-window/data/p017-cpu-top-v1.json
```

## 结果

- 数据文件：[`cpu-top-source-v1.json`](data/cpu-top-source-v1.json)、[`cpu-top-source-v1-check.json`](data/cpu-top-source-v1-check.json)、[`p017-cpu-top-v1.json`](data/p017-cpu-top-v1.json)。
- 环境：全新 `guardian-p017-cpu-top-v1`，2 vCPU、3 GiB、12 GiB、Ubuntu 22.04 ARM64；一次性安装 Docker 29.1.3、cgroup v2。实验完成后已精确删除 VM，未触碰 `guardian-ubuntu`。
- 采集：3 轮、每轮 32 个 Guardian 样本，共 96；控制探针 `36/36`；Guardian 窗口、identity 窗口、health 窗口均为 `3/3`；开销原始行 307；fixture 容器均自然退出且退出码为 0，未执行 Docker stop/restart/kill。
- CPU 归因：三轮均观察到真实 Docker full ID/cgroup/created_at/inode；每轮 32 个 Guardian 样本中有 17 个样本的 CPU attribution 为 `TARGET_CONFIRMED`，这些样本的目标贡献均为 `100%`、mapping confidence 为 `high`，目标身份完整。其余样本的无目标/多资源状态按原始证据保留。CPU 风险状态三轮为 `normal/warning`，未形成 `CRITICAL_CONFIRMED`；memory/I/O 仅出现背景 warning/recovered。
- P0-14 检查：`code_gate=PASS`、整体 `status=INCONCLUSIVE`。保留的缺口包括 Beszel 时间差、业务恢复不能由 healthcheck 单独证明、owner Rescue SLO、资源误触发/漏检、同 Observer 恢复和实际 Observer 窗口长度边界。
- P0-17 回放：`code_gate=PASS`、整体 `status=INCONCLUSIVE`；96 个样本中旧策略计划 28 个，新策略计划 `0`，计划契约失败 `0`，新动作执行 `0`。原因计数为 `HOST_RISK_NOT_CONFIRMED=96`；历史样本 cgroup inode 缺口 `1` 被保留并继续 fail-closed。
- 安全声明：数据只来自 local-disposable VM；未连接生产、Beszel 或真实通知渠道，未读取真实凭据；结果不代表真实动作、业务恢复、SSH 可进入性或生产准入。

## 结论与后续

EXP-079 证明了“CPU TOP 贡献成立”与“整机危险确认成立”是两个独立门：即使目标贡献为 100% 且身份完整，主机只处于 warning 时 P0-17 仍保持零计划、零执行、零契约失败，符合 fail-closed 预期。它不证明 CPU 资源级稳定恢复、SSH 可进入性或业务恢复；不得据此进入 PG-P0-15A，后者仍需要 P0-17 通过和用户对当次 local-disposable 对象的单独明确授权。
