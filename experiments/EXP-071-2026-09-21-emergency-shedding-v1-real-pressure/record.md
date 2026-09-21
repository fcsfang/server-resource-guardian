# EXP-071：Emergency Shedding v1 真实压力 / 贡献与身份数据质量补证

- 实验 ID：`EXP-071`
- 日期：2026-09-21
- 关联任务：`Goal 7 / PG-P0-17 Emergency Shedding v1`
- 状态：`INCONCLUSIVE`
- 代码/安全 gate：真实 Docker 源数据检查 `PASS`；P0-17 重放 `PASS`；整体不宣称策略通过
- 核心数据：[`real-pressure-source-v1.json`](data/real-pressure-source-v1.json)、[`real-pressure-source-v1-check.json`](data/real-pressure-source-v1-check.json)、[`p017-real-pressure-v1.json`](data/p017-real-pressure-v1.json)

## 目的

在新的本地 ARM64 disposable Multipass VM 中，用现有真实 Docker CPU/内存 workload
产生有限压力窗口，再用当前 P0-17 策略离线重放，补充 EXP-069 中“危险和贡献只是
显式策略 fixture”的证据缺口。实验重点是确认真实 registry 身份、资源贡献字段和
数据不完整时的 fail-closed 行为；不以达到危险阈值或生成计划为成功条件。

## 安全边界

- 只创建 `guardian-p017-pressure`，规格为 Ubuntu 22.04.5 ARM64、2 vCPU、约 3 GiB
  内存、12 GiB 磁盘；实验结束后已精确执行 `multipass delete guardian-p017-pressure`。
- 未操作现有 `guardian-ubuntu`，未连接生产、Beszel 或真实通知渠道，未读取凭据。
- workload 使用仓库内的 BusyBox Docker fixture；Guardian 只运行 `simulate`，不创建
  capability、不调用 action adapter，不执行 Docker/systemd stop、restart、kill 或 remove。
- 容器均等待自身 deadline 自然退出；没有把自然退出写成业务恢复。

## 执行

```bash
python3 scripts/run_p014_real_docker.py \
  --vm guardian-p017-pressure \
  --rounds 3 \
  --scenario cpu \
  --scenario memory \
  --experiment-id EXP-071 \
  --workload-seconds 20 \
  --output experiments/EXP-071-2026-09-21-emergency-shedding-v1-real-pressure/data/real-pressure-source-v1.json

python3 scripts/check_p014_real_docker.py \
  experiments/EXP-071-2026-09-21-emergency-shedding-v1-real-pressure/data/real-pressure-source-v1.json \
  --output experiments/EXP-071-2026-09-21-emergency-shedding-v1-real-pressure/data/real-pressure-source-v1-check.json

python3 scripts/check_p017_replay.py \
  experiments/EXP-071-2026-09-21-emergency-shedding-v1-real-pressure/data/real-pressure-source-v1.json \
  --output experiments/EXP-071-2026-09-21-emergency-shedding-v1-real-pressure/data/p017-real-pressure-v1.json
```

## 结果

- 6 个 Guardian 窗口、192 个真实 Observer 样本；控制探针 `72/72`，健康窗口
  `6/6`，真实 registry 身份窗口 `6/6`，Guardian 开销采样 `445` 行。
- 真实 registry 共观察到 50 个对象记录，其中 48 个带正整数 `cgroup_inode`；2 个
  样本缺失 inode，被 P0-17 重放保留为数据质量问题，没有猜测或补造身份。
- P0-17 重放 `code_gate=PASS`、新计划 `0`、生成计划契约违规数 `0`、执行 `0`：179 个样本为
  `HOST_RISK_NOT_CONFIRMED`，13 个为 `INCOMPLETE_SAMPLES` +
  `NO_EFFECTIVE_SHEDDING_TARGET`。
- 旧版 Observer 在 6 个样本产生过旧 simulate action，但新 P0-17 边界没有把它们
  继承为计划；这证明新策略不会把旧计划或不完整证据直接升级为动作。
- 真实压力窗口没有达到当前 P0-17 要求的 `CRITICAL_CONFIRMED`，因此没有证明真实
  危险状态下的 TOP 选择、宿主缓解或恢复闭环；这是实验的有效限制，不是通过条件。

## 结论与后续

EXP-071 补齐了比 EXP-069 更接近真实运行态的 CPU/内存 workload、真实 Docker
full ID/created/cgroup path/inode 采样和零动作重放证据。当前结论仍为
`INCONCLUSIVE`：短窗口和当前压力强度未形成完整危险确认，且存在少量身份字段缺失。
P0-17 仍需在安全、获准的 disposable 环境中补足持续危险/贡献窗口和更完整的数据质量
边界；在此之前不进入 P0-15A，不连接生产，也不执行真实 `graceful_stop`。
