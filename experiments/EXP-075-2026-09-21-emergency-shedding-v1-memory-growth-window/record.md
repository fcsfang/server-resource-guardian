# EXP-075：Emergency Shedding v1 真实内存增长贡献窗口

- 实验 ID：`EXP-075`
- 日期：2026-09-21
- 关联任务：`Goal 7 / PG-P0-17 Emergency Shedding v1`
- 状态：`INCONCLUSIVE`
- 代码/安全 gate：真实 Docker 源数据检查 `PASS`；P0-17 重放 `PASS`；整体不宣称生产或动作通过
- 核心数据：[`memory-growth-window-source-v1.json`](data/memory-growth-window-source-v1.json)、[`memory-growth-window-source-v1-check.json`](data/memory-growth-window-source-v1-check.json)、[`p017-memory-growth-window-v1.json`](data/p017-memory-growth-window-v1.json)

## 目的与环境

在新的 2 GiB ARM64 disposable VM 中，使用 1280 MiB 总量、4 秒基线延迟和每次 64 MiB、
间隔 0.5 秒的分块分配，验证真实内存增长、宿主危险确认、完整 Docker 身份和 P0-17
TOP 选择是否能在同一窗口重叠。该实验只验证纯 `simulate` 计划，不复现 SSH 卡顿。

- VM：`guardian-p017-memory-growth-v2`，2 vCPU、2 GiB、12 GiB；实验后已精确删除。
- 3 轮、每轮 20 秒；控制探针 `36/36`，Guardian 样本 `96`，容器均 `ExitCode=0`、
  `OOMKilled=false`，Guardian action adapter/capability/Docker 控制 mutation 均为 0。
- 未触碰 `guardian-ubuntu`，未连接生产、Beszel 或真实通知渠道，未读取凭据。

## 执行

```bash
python3 scripts/run_p014_real_docker.py \
  --vm guardian-p017-memory-growth-v2 --rounds 3 --scenario memory \
  --experiment-id EXP-075 --workload-seconds 20 --memory-mb 1280 \
  --memory-start-delay-seconds 4 \
  --output experiments/EXP-075-2026-09-21-emergency-shedding-v1-memory-growth-window/data/memory-growth-window-source-v1.json

python3 scripts/check_p014_real_docker.py \
  experiments/EXP-075-2026-09-21-emergency-shedding-v1-memory-growth-window/data/memory-growth-window-source-v1.json \
  --output experiments/EXP-075-2026-09-21-emergency-shedding-v1-memory-growth-window/data/memory-growth-window-source-v1-check.json

python3 scripts/check_p017_replay.py \
  experiments/EXP-075-2026-09-21-emergency-shedding-v1-memory-growth-window/data/memory-growth-window-source-v1.json \
  --output experiments/EXP-075-2026-09-21-emergency-shedding-v1-memory-growth-window/data/p017-memory-growth-window-v1.json
```

## 结果

- 3 轮均观察到真实 memory `critical` 与对象增长贡献重叠；关键样本的对象增长约
  `61–132 MB/s`，memory attribution 为 `TARGET_CONFIRMED`，对象贡献约 `67–76%`，
  registry identity 为完整 Docker full ID/created_at/cgroup path/inode。
- P0-17 重放 `code_gate=PASS`：新策略生成 `3` 个 `memory` 资源 TOP 的
  `graceful_stop` simulate 计划，均为 `execution=not_executed`；3 个计划均通过
  schema、单动作上限、非根因边界和完整 Docker identity 契约校验，契约违规数 `0`，真实执行计数 `0`。
  计划原因均为 `PLAN_GENERATED` + `TOP_ACTIONABLE_CONSUMER`。另有 1 个历史样本缺失
  cgroup inode，重放保持 fail-closed。
- 重放总体仍 `INCONCLUSIVE`：`HOST_RISK_NOT_CONFIRMED=73`、
  `INCOMPLETE_SAMPLES=20`、`NO_EFFECTIVE_SHEDDING_TARGET=20`；P014 检查仍提示
  Beszel 时间差、owner Rescue SLO、真实业务恢复和短窗口限制，另有 1 个 Docker stats
  不可用样本。

## 结论与后续

EXP-075 首次在本地真实 Docker pressure 数据中把“整机危险 + 真实内存贡献 + 完整身份”
连接到 P0-17 的单目标 simulate 计划，证明 v1 的本地策略链路可在真实观测数据上产生
受限计划；它不证明真实动作、业务恢复、SSH 可进入性或生产安全。后续仍需维持零动作，
补足同会话恢复、更多数据质量边界和 owner SLO；P0-15A 仍需 P0-17 通过及当次明确授权。
