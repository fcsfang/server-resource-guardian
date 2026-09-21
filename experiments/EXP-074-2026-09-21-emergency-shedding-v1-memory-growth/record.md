# EXP-074：Emergency Shedding v1 内存增长基线延迟边界

- 实验 ID：`EXP-074`
- 日期：2026-09-21
- 关联任务：`Goal 7 / PG-P0-17 Emergency Shedding v1`
- 状态：`INCONCLUSIVE`
- 代码/安全 gate：真实 Docker 源数据检查 `PASS`；P0-17 重放 `PASS`；整体不宣称策略通过
- 核心数据：[`memory-growth-source-v1.json`](data/memory-growth-source-v1.json)、[`memory-growth-source-v1-check.json`](data/memory-growth-source-v1-check.json)、[`p017-memory-growth-v1.json`](data/p017-memory-growth-v1.json)

## 目的与安全边界

在新的 2 GiB ARM64 disposable VM 中，将内存分配延迟 4 秒，让 Observer 有机会先建立
对象基线，再以一次 `dd` 填充 1280 MiB；验证单纯的基线延迟能否使危险确认与贡献归因
重叠。不复现 SSH 卡顿、不连接生产、不执行 Guardian 动作。

- VM：`guardian-p017-memory-growth-v1`，2 vCPU、2 GiB、12 GiB；实验后已精确删除。
- 3 轮各 20 秒；控制探针 `36/36`，Guardian 样本 `96`，容器均 `ExitCode=0`、
  `OOMKilled=false`。
- Guardian 为 `simulate`，action adapter、capability、Docker 控制 mutation 均为 0；
  `guardian-ubuntu` 未触碰，未读取凭据或连接外部渠道。

## 执行与结果

```bash
python3 scripts/run_p014_real_docker.py \
  --vm guardian-p017-memory-growth-v1 --rounds 3 --scenario memory \
  --experiment-id EXP-074 --workload-seconds 20 --memory-mb 1280 \
  --memory-start-delay-seconds 4 \
  --output experiments/EXP-074-2026-09-21-emergency-shedding-v1-memory-growth/data/memory-growth-source-v1.json
```

- 真实数据出现短暂 `critical` 样本，但分配仍在危险确认前后快速完成；贡献字段在
  关键时点为 `0` 或缺失，未形成稳定 `TARGET_CONFIRMED`。
- P0-17 重放：`code_gate=PASS`、旧计划样本 `1`、新计划 `0`、生成计划契约违规数 `0`、执行 `0`；原因计数为
  `HOST_RISK_NOT_CONFIRMED=57`、`INCOMPLETE_SAMPLES=39`、
  `NO_EFFECTIVE_SHEDDING_TARGET=39`，历史缺失 cgroup inode `2` 个。

## 结论

EXP-074 说明“先等几秒再一次性分配”仍不足以构成可重放的危险/贡献同窗。该轮推动了
EXP-075 的分块分配夹具，但自身仍保持 `INCONCLUSIVE`，不进入 P0-15A，也不执行真实
`graceful_stop`。
