# EXP-073：Emergency Shedding v1 受限内存压力增强窗口

- 实验 ID：`EXP-073`
- 日期：2026-09-21
- 关联任务：`Goal 7 / PG-P0-17 Emergency Shedding v1`
- 状态：`INCONCLUSIVE`
- 代码/安全 gate：真实 Docker 源数据检查 `PASS`；P0-17 重放 `PASS`；整体不宣称策略通过
- 核心数据：[`strong-memory-source-v1.json`](data/strong-memory-source-v1.json)、[`strong-memory-source-v1-check.json`](data/strong-memory-source-v1-check.json)、[`p017-strong-memory-v1.json`](data/p017-strong-memory-v1.json)

## 目的与边界

在 2 GiB ARM64 disposable VM 中使用显式 `--memory-mb 1280` 的单次内存填充，确认
增强压力是否能进入真实危险窗口。该轮仍沿用原有 fixture 的“快速填充”方式，重点记录
它是否会错过对象增长贡献；不复现 SSH 卡顿、不连接生产、不执行 Guardian 动作。

- VM：`guardian-p017-memory-pressure-v2`，2 vCPU、2 GiB、12 GiB；实验后已精确删除。
- Guardian：`simulate`，action adapter/capability/Docker 控制 mutation 均为 0。
- 仅 harness 的 `docker run` 启动 fixture；容器三轮均 `ExitCode=0`、`OOMKilled=false`，自然退出。
- 未触碰 `guardian-ubuntu`，未读取凭据，未连接 Beszel 或真实通知渠道。

## 执行与结果

```bash
python3 scripts/run_p014_real_docker.py \
  --vm guardian-p017-memory-pressure-v2 --rounds 3 --scenario memory \
  --experiment-id EXP-073 --workload-seconds 20 --memory-mb 1280 \
  --output experiments/EXP-073-2026-09-21-emergency-shedding-v1-strong-memory/data/strong-memory-source-v1.json
```

- 真实源数据：3 个 Guardian 窗口、96 个样本、控制探针 `36/36`；最低可用内存约
  `17.14%`，第二轮出现 1 个已 dwell 的 `critical` 样本。
- P0-17 重放：`code_gate=PASS`、旧计划样本 `1`、新计划 `0`、生成计划契约违规数 `0`、执行 `0`；原因计数为
  `HOST_RISK_NOT_CONFIRMED=54`、`INCOMPLETE_SAMPLES=42`、
  `NO_EFFECTIVE_SHEDDING_TARGET=42`，历史缺失 cgroup inode `2` 个。
- 关键限制：容器在 Observer 对象增长基线建立前已完成大部分分配；危险样本与真实对象
  增长贡献没有稳定重叠，不能把这轮写成 TOP 贡献验证。

## 结论

EXP-073 是有效的时序负向证据：提高内存占用本身不能保证 P0-17 同时得到危险确认和
贡献归因。后续 EXP-074/075 分别增加了基线延迟和分块分配；本实验保持
`INCONCLUSIVE`，不授权真实 `graceful_stop`。
