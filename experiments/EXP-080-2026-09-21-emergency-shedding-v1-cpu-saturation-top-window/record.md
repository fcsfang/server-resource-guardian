# EXP-080：Emergency Shedding v1 单 vCPU 多 worker CPU 饱和 TOP 窗口

- 实验 ID：`EXP-080`
- 日期：2026-09-21
- 关联任务：`Goal 7 / PG-P0-17 Emergency Shedding v1`
- 状态：`INCONCLUSIVE`
- 安全边界：仅使用一次性 disposable Multipass VM 和真实 Docker fixture；Guardian 仅 `simulate`，只做观测、回放和契约检查，不创建 capability、不调用 action adapter、不执行 Docker stop/restart/kill，不连接生产、Beszel 或真实通知渠道。
- 目标：在 1 vCPU VM 中使用 2 个有界 CPU worker 形成持续真实 Docker CPU 饱和和 TOP 贡献窗口，补强“整机危险确认”和“CPU TOP 贡献”两个独立门的本地证据；若危险、贡献或样本门未满足，必须保留零计划的 fail-closed 结果。

## 计划

在 1 vCPU、3 GiB、12 GiB 的一次性 Ubuntu 22.04 ARM64 VM 中，执行 3 轮、每轮 45 秒的真实 Docker CPU fixture，使用 `--cpu-workers 2`；每轮保留 Guardian 32 个样本、控制探针、Docker full ID/cgroup/created_at/inode、health 状态和开销。实验完成后精确删除该 VM，并以 P0-14 检查器和 P0-17 离线回放器重算结果。

计划命令：

```bash
python3 scripts/run_p014_real_docker.py \
  --vm guardian-p017-cpu-saturation-top-v1 --rounds 3 --scenario cpu \
  --experiment-id EXP-080 --workload-seconds 45 --cpu-workers 2 \
  --output experiments/EXP-080-2026-09-21-emergency-shedding-v1-cpu-saturation-top-window/data/cpu-saturation-top-source-v1.json

python3 scripts/check_p014_real_docker.py \
  experiments/EXP-080-2026-09-21-emergency-shedding-v1-cpu-saturation-top-window/data/cpu-saturation-top-source-v1.json \
  --output experiments/EXP-080-2026-09-21-emergency-shedding-v1-cpu-saturation-top-window/data/cpu-saturation-top-source-v1-check.json

python3 scripts/check_p017_replay.py \
  experiments/EXP-080-2026-09-21-emergency-shedding-v1-cpu-saturation-top-window/data/cpu-saturation-top-source-v1.json \
  --output experiments/EXP-080-2026-09-21-emergency-shedding-v1-cpu-saturation-top-window/data/p017-cpu-saturation-top-v1.json
```

## 结果

- 数据文件：[`cpu-saturation-top-source-v1.json`](data/cpu-saturation-top-source-v1.json)、[`cpu-saturation-top-source-v1-check.json`](data/cpu-saturation-top-source-v1-check.json)、[`p017-cpu-saturation-top-v1.json`](data/p017-cpu-saturation-top-v1.json)。
- 环境：全新 `guardian-p017-cpu-saturation-top-v1`，1 vCPU、3 GiB、12 GiB、Ubuntu 22.04 ARM64；一次性安装 Docker 29.1.3、cgroup v2。实验完成后已精确删除 VM，未触碰 `guardian-ubuntu`。
- 采集：3 轮、每轮 32 个 Guardian 样本，共 96；控制探针 `36/36`；Guardian 窗口、identity 窗口、health 窗口和 churn 窗口均为 `3/3`；开销原始行 305；fixture 容器均自然退出，未执行 Docker stop/restart/kill。
- CPU 危险与归因：第 1 轮为 `normal/warning`，第 2 轮出现 `critical`（14 个样本），第 3 轮出现 `critical`（17 个样本）。三轮 CPU TOP 归因确认样本分别为 `17/32`、`14/32`、`16/32`；每个确认样本均为 `TARGET_CONFIRMED`、mapping confidence `high`、目标贡献 `100%`，真实 full ID/cgroup/created_at/inode 完整。其余无目标、多资源歧义和少量可观测性退化均保留为原始边界。
- P0-14 检查：`code_gate=PASS`、整体 `status=INCONCLUSIVE`。保留的缺口包括 Beszel 时间差、业务恢复不能由 healthcheck 单独证明、1 个 Docker stats 不可用样本、owner Rescue SLO、资源误触发/漏检、同 Observer 恢复和实际 Observer 窗口长度边界。
- P0-17 回放：`code_gate=PASS`、整体 `status=INCONCLUSIVE`；96 个样本中旧策略计划 15 个，新策略计划 `29`（第 2 轮 13 个、第 3 轮 16 个），全部为 CPU TOP；计划契约失败 `0`，新动作执行 `0`，历史样本 cgroup inode 缺口 `0`。原因计数为 `HOST_RISK_NOT_CONFIRMED=65`、`INCOMPLETE_SAMPLES=2`、`NO_EFFECTIVE_SHEDDING_TARGET=2`、`PLAN_GENERATED=29`、`TOP_ACTIONABLE_CONSUMER=29`；计数可重叠，原始每样本结果已保留。
- 生成计划边界：29 个计划均为 `graceful_stop`、`not_executed`、`max_actions=1`、`root_cause_claimed=false`，目标为完整 64 位 Docker ID，均带 `created_at`、cgroup path 和正 cgroup inode；未创建 capability，未调用 action adapter。
- 安全声明：数据只来自 local-disposable VM；未连接生产、Beszel 或真实通知渠道，未读取真实凭据；结果不代表真实动作、业务恢复、SSH 可进入性或生产准入。

## 结论与后续

EXP-080 补强了“整机 CPU 危险确认”和“CPU TOP 贡献”同窗证据：后两轮出现真实 CPU `critical`，P0-17 生成了 29 个契约有效但未执行的 CPU TOP simulate 计划；这证明当前策略在满足门槛时能够形成受约束计划，但仍不授权真实动作。P0-14/P0-17 整体保持 `INCONCLUSIVE`，不代表业务恢复、SSH 可进入性或生产准入；P0-15A 仍需 P0-17 通过和用户对当次 local-disposable 对象的单独明确授权。
