# EXP-083：P0-14 真实 Docker I/O 同一 Observer 恢复窗口

- 实验 ID：`EXP-083`
- 日期：2026-09-21
- 关联任务：`Goal 7 / PG-P0-14`、`Goal 7 / PG-P0-17 Emergency Shedding v1`
- 状态：`INCONCLUSIVE`
- 安全边界：仅使用一次性 disposable Multipass VM 和真实 Docker fixture；Guardian 仅 `simulate`，只做观测、恢复边界和离线回放，不创建 capability、不调用 action adapter、不执行 Docker stop/restart/kill，不连接生产、Beszel 或真实通知渠道。
- 目标：用有界真实 Docker I/O workload，在 workload 自然退出后让同一 Observer 继续采样，验证 I/O 风险是否能独立形成、是否错误借用 CPU/内存恢复结果，以及 P0-17 在 I/O 证据下是否保持单计划和零执行。

## 计划

在 1 vCPU、3 GiB、12 GiB 的一次性 Ubuntu 22.04 ARM64 VM 中，执行 3 轮、每轮 45 秒的 `io` 真实 Docker fixture；Guardian 每轮采集 72 个样本，使 Observer 窗口覆盖 workload 自然退出后的恢复阶段。实验完成后精确删除该 VM，并用 P0-14 检查器和 P0-17 离线回放器重算结果。

计划命令：

```bash
python3 scripts/run_p014_real_docker.py \
  --vm guardian-p014-io-recovery-v1 --rounds 3 --scenario io \
  --experiment-id EXP-083 --workload-seconds 45 --observer-samples 72 \
  --output experiments/EXP-083-2026-09-21-p014-real-docker-io-recovery-window/data/io-recovery-source-v1.json

python3 scripts/check_p014_real_docker.py \
  experiments/EXP-083-2026-09-21-p014-real-docker-io-recovery-window/data/io-recovery-source-v1.json \
  --output experiments/EXP-083-2026-09-21-p014-real-docker-io-recovery-window/data/io-recovery-source-v1-check.json

python3 scripts/check_p017_replay.py \
  experiments/EXP-083-2026-09-21-p014-real-docker-io-recovery-window/data/io-recovery-source-v1.json \
  --output experiments/EXP-083-2026-09-21-p014-real-docker-io-recovery-window/data/p017-io-recovery-v1.json
```

## 预期判定

- 只接受原始样本可重算、full Docker identity 可见、控制探针完整、I/O 按独立资源判断、以及生成计划满足 `graceful_stop/not_executed/max_actions=1` 的结果。
- workload 自然退出或目标消失不得直接写成宿主恢复；缺少 I/O 新鲜证据时保持 `INCONCLUSIVE` 或 fail-closed。
- 无论是否形成 simulate 计划，真实动作执行数必须为 `0`。

## 结果

- 环境：全新 `guardian-p014-io-recovery-v1`，1 vCPU、3 GiB、12 GiB、Ubuntu 22.04 ARM64；一次性安装 Docker 29.1.3、cgroup v2。实验完成后已精确删除 VM，未触碰 `guardian-ubuntu`。
- 采集：3 轮、每轮 72 个 Guardian 样本，共 216；控制探针 `36/36`；Guardian 窗口、identity 窗口、health 窗口和 churn 窗口均为 `3/3`；开销原始行 517；fixture 容器均自然退出，未执行 Docker stop/restart/kill。
- P0-14 检查：`code_gate=PASS`、整体 `status=INCONCLUSIVE`。三轮均看见真实 full ID/cgroup identity；I/O 资源均出现 `critical → recovered` 资源级状态，但 CPU 仍有 `warning`，内存通道出现 `degraded_observability`，因此同一 Observer 的整体 `recovered` 三轮均为 `false`。预期活动资源为 I/O，实际还观测到 CPU，保留资源误触发/漏检边界；检查器另外保留 `docker_stats_unavailable_samples:3`。
- P0-17 回放：`code_gate=PASS`、整体 `status=INCONCLUSIVE`；216 个样本中旧策略计划 10 个，新策略计划 `0`，计划契约失败 `0`，新动作执行 `0`，历史样本 cgroup inode 缺口 `0`。原因计数包含 `HOST_RISK_NOT_CONFIRMED=188`、`INCOMPLETE_SAMPLES=28`、`NO_EFFECTIVE_SHEDDING_TARGET=28`，计数可重叠。
- 检查器保留的证据缺口：Beszel 时间差、业务恢复、owner Rescue SLO、资源误触发/漏检、Docker stats 的 3 个不可用样本、全部窗口同一 Observer 恢复以及 workload window 判定仍未满足；这些缺口不通过改写原始数据消除。
- 安全声明：数据只来自 local-disposable VM；未连接生产、Beszel 或真实通知渠道，未读取真实凭据；结果不代表真实动作、业务恢复、SSH 可进入性或生产准入。

## 结论与后续

EXP-083 补充了真实 Docker I/O 独立窗口：I/O 资源级 `critical → recovered` 可以在同一 Observer 中出现，但 CPU 背景 warning、内存观测质量下降和额外资源活跃信号使整体恢复仍保持未验证；P0-17 在 I/O 证据不完整时正确保持零计划、零执行。P0-14/P0-17 整体保持 `INCONCLUSIVE`，不授权真实动作，不代表生产准入。后续应优先处理多资源质量门和恢复合并规则，而不是把单一 I/O 资源恢复升级为整机恢复。
