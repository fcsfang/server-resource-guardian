# EXP-072：Emergency Shedding v1 受限内存压力边界

- 实验 ID：`EXP-072`
- 日期：2026-09-21
- 关联任务：`Goal 7 / PG-P0-17 Emergency Shedding v1`
- 状态：`INCONCLUSIVE`
- 代码/安全 gate：真实 Docker 源数据检查 `PASS`；P0-17 重放 `PASS`；整体不宣称策略通过
- 核心数据：[`critical-pressure-source-v1.json`](data/critical-pressure-source-v1.json)、[`critical-pressure-source-v1-check.json`](data/critical-pressure-source-v1-check.json)、[`p017-critical-pressure-v1.json`](data/p017-critical-pressure-v1.json)

## 目的

在比 EXP-071 更受限的本地 ARM64 disposable VM 中，只运行有界内存 workload，观察
真实 Docker registry、cgroup 身份和 Guardian 资源窗口能否达到当前 P0-17 的
`CRITICAL_CONFIRMED` 危险门，并将同一份源数据送入 Emergency Shedding v1 重放。
实验不以生成计划为预设结果，也不尝试复现 SSH 交互卡顿。

## 环境与安全边界

- 仅创建 `guardian-p017-critical-pressure`：Ubuntu 22.04.5 ARM64、2 vCPU、2 GiB
  内存、12 GiB 磁盘；结束后已精确执行 `multipass delete guardian-p017-critical-pressure`。
- 未操作仍在运行的 `guardian-ubuntu`，未连接生产、Beszel 或真实通知渠道，未读取凭据。
- 使用仓库内 `run_p014_real_docker.py` 的真实 Docker fixture；Guardian 运行在
  `simulate`，不创建 capability，不调用 action adapter，不执行 Docker/systemd
  `stop`、`restart`、`kill` 或 `remove`。
- workload 仅允许 fixture 容器在 deadline 后自然退出；fixture 的 `docker run` 是唯一
  harness mutation，不能被解释为 Guardian 动作或业务恢复。

## 执行

```bash
python3 scripts/run_p014_real_docker.py \
  --vm guardian-p017-critical-pressure \
  --rounds 3 \
  --scenario memory \
  --experiment-id EXP-072 \
  --workload-seconds 20 \
  --output experiments/EXP-072-2026-09-21-emergency-shedding-v1-critical-pressure/data/critical-pressure-source-v1.json

python3 scripts/check_p014_real_docker.py \
  experiments/EXP-072-2026-09-21-emergency-shedding-v1-critical-pressure/data/critical-pressure-source-v1.json \
  --output experiments/EXP-072-2026-09-21-emergency-shedding-v1-critical-pressure/data/critical-pressure-source-v1-check.json

python3 scripts/check_p017_replay.py \
  experiments/EXP-072-2026-09-21-emergency-shedding-v1-critical-pressure/data/critical-pressure-source-v1.json \
  --output experiments/EXP-072-2026-09-21-emergency-shedding-v1-critical-pressure/data/p017-critical-pressure-v1.json
```

## 结果

- 3 个 Guardian 窗口、96 个真实 Observer 样本；控制探针 `36/36`，健康窗口
  `3/3`，registry 身份窗口 `3/3`，开销记录 `221` 行。
- P0-17 重放 `code_gate=PASS`、新计划 `0`、生成计划契约违规数 `0`、执行 `0`；旧计划样本 `1`，新策略原因
  统计为 `HOST_RISK_NOT_CONFIRMED=85`、`INCOMPLETE_SAMPLES=11`、
  `NO_EFFECTIVE_SHEDDING_TARGET=11`。
- 真实内存 workload 仍未形成 `CRITICAL_CONFIRMED`，因此没有证明真实危险状态下的
  TOP 选择、贡献达标、宿主缓解或恢复闭环。3 个历史记录缺失 cgroup inode，重放保留
  为数据质量缺口，没有猜测或补造身份。
- 全部执行计数保持为 0；本次没有 SSH 卡顿复现，也没有把 VM/fixture 的自然退出写成
  Guardian 动作后的恢复。

## 结论与后续

EXP-072 比 EXP-071 进一步收紧了可用内存，但在当前有界 workload 和 20 秒窗口内仍
未达到 P0-17 的真实危险门。该结果是有效的负向边界证据，不能将策略状态从
`INCONCLUSIVE` 提升为通过。后续仍应在获准的 disposable 环境中补足持续危险、真实
贡献和完整身份数据质量窗口；在此之前保持 `observe/simulate`，不进入 P0-15A，不连接
生产，也不执行真实 `graceful_stop`。
