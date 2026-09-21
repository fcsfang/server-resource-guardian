# EXP-077：Emergency Shedding v1 真实 CPU 持续危险窗口

- 实验 ID：`EXP-077`
- 日期：2026-09-21
- 关联任务：`Goal 7 / PG-P0-17 Emergency Shedding v1`
- 状态：`INCONCLUSIVE`
- 安全边界：仅使用一次性 disposable Multipass VM 和真实 Docker fixture；Guardian 仅 `simulate`，只做观测、回放和契约检查，不创建 capability、不调用 action adapter、不执行 Docker stop/restart/kill，不连接生产、Beszel 或真实通知渠道。
- 目标：补充真实 Docker CPU 持续压力下的整机危险确认、完整身份、资源 TOP 贡献和 P0-17 生成计划边界；即使证据不足也保留原始数据，不把 Docker CLI 成功、容器自然退出或资源缓解写成业务恢复。

## 计划

在 2 vCPU、3 GiB、12 GiB 的一次性 Ubuntu 22.04 ARM64 VM 中，执行 3 轮、每轮 30 秒的真实 Docker CPU fixture；每轮保留 Guardian 32 个样本、控制探针、Docker full ID/cgroup/created_at/inode、业务 health 状态和 Guardian 开销。实验完成后精确删除该 VM，并以 P0-14 检查器和 P0-17 离线回放器重算结果。

计划命令：

```bash
python3 scripts/run_p014_real_docker.py \
  --vm guardian-p017-cpu-window-v1 --rounds 3 --scenario cpu \
  --experiment-id EXP-077 --workload-seconds 30 \
  --output experiments/EXP-077-2026-09-21-emergency-shedding-v1-cpu-window/data/cpu-window-source-v1.json

python3 scripts/check_p014_real_docker.py \
  experiments/EXP-077-2026-09-21-emergency-shedding-v1-cpu-window/data/cpu-window-source-v1.json \
  --output experiments/EXP-077-2026-09-21-emergency-shedding-v1-cpu-window/data/cpu-window-source-v1-check.json

python3 scripts/check_p017_replay.py \
  experiments/EXP-077-2026-09-21-emergency-shedding-v1-cpu-window/data/cpu-window-source-v1.json \
  --output experiments/EXP-077-2026-09-21-emergency-shedding-v1-cpu-window/data/p017-cpu-window-v1.json
```

## 结果

- 数据文件：[`cpu-window-source-v1.json`](data/cpu-window-source-v1.json)、[`cpu-window-source-v1-check.json`](data/cpu-window-source-v1-check.json)、[`p017-cpu-window-v1.json`](data/p017-cpu-window-v1.json)。
- 环境：全新 `guardian-p017-cpu-window-v1`，2 vCPU、3 GiB、12 GiB、Ubuntu 22.04 ARM64；一次性安装 Docker 29.1.3、cgroup v2。实验完成后已精确删除 VM，未触碰 `guardian-ubuntu`。
- 采集：3 轮、每轮 32 个 Guardian 样本，共 96；控制探针 `36/36`；Guardian 窗口、identity 窗口、health 窗口均为 `3/3`；开销原始行 255；fixture 容器均自然退出，未执行 Docker stop/restart/kill。
- 真实身份：三轮 Guardian 均观察到目标 Docker full ID，保留 created_at、cgroup path、cgroup inode 等 registry 证据；Guardian 使用 `simulate`，action adapter、capability 和 Docker control mutation 均为 0。
- 资源时序：CPU 三轮均出现 `warning`；I/O 同时出现 `critical/normal/recovered/warning`，memory 仅第 1 轮出现 `warning/recovered`，第 3 轮出现 `degraded_observability`；三轮整体均未形成同一 Observer `recovered`。
- P0-14 检查：`code_gate=PASS`、整体 `status=INCONCLUSIVE`。保留的缺口包括 Beszel 时间差、业务恢复不能由 healthcheck 单独证明、1 个 Docker stats 不可用样本、owner Rescue SLO、资源误触发/漏检、同 Observer 恢复和窗口长度边界。
- P0-17 回放：`code_gate=PASS`、整体 `status=INCONCLUSIVE`；96 个样本中旧策略计划 8 个，新策略计划 `0`，计划契约失败 `0`，新动作执行 `0`。主要原因计数为 `HOST_RISK_NOT_CONFIRMED=81`、`INCOMPLETE_SAMPLES=15`、`NO_EFFECTIVE_SHEDDING_TARGET=15`；历史样本 cgroup inode 缺口 `1` 被保留并继续 fail-closed。
- 安全声明：数据只来自 local-disposable VM；未连接生产、Beszel 或真实通知渠道，未读取真实凭据；结果不代表真实动作、业务恢复、SSH 可进入性或生产准入。

## 结论与后续

EXP-077 补充了真实 Docker CPU 持续窗口和数据质量边界，但没有把 CPU warning 自动升级成 P0-17 计划：当整机危险确认、样本完整性或有效贡献门不满足时，策略保持零计划、零执行，符合 fail-closed 预期。它不证明 CPU 资源级稳定恢复，也不证明整体或业务恢复；不得据此进入 PG-P0-15A，后者仍需要 P0-17 通过和用户对当次 local-disposable 对象的单独明确授权。
