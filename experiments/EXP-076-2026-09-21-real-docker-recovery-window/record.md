# EXP-076：真实 Docker 同一 Observer 内存自然恢复窗口

- 实验 ID：`EXP-076`
- 日期：2026-09-21
- 关联任务：`Goal 7 / PG-P0-14`，并为 `PG-P0-17` 提供真实 Docker 恢复边界
- 状态：`INCONCLUSIVE`
- 代码/安全 gate：真实 Docker 源数据检查 `PASS`；P0-17 回放 `PASS`；整体不宣称恢复或生产通过
- 核心数据：[`real-docker-recovery-source-v1.json`](data/real-docker-recovery-source-v1.json)、[`real-docker-recovery-check-v1.json`](data/real-docker-recovery-check-v1.json)、[`p017-recovery-replay-v1.json`](data/p017-recovery-replay-v1.json)

## 目的

在新的本地 ARM64 disposable Multipass VM 中，只运行真实 Docker memory workload：
workload 在有界 deadline 后自然退出，Guardian 同一 Observer 进程继续以
`simulate` 采样，检查真实 Docker full ID/cgroup 身份、危险窗口和自然退出后的风险
状态是否能在同一会话中被观测。该实验不执行 Guardian 动作，不复现 SSH 卡顿。

## 安全边界

- 只创建并清理本实验精确命名的 disposable VM；不触碰现有 `guardian-ubuntu`。
- 只构建仓库内 BusyBox fixture，Docker `run` 仅创建本实验 workload/churn；不调用
  Guardian action adapter，不创建 capability，不执行 Docker/systemd stop、restart 或
  kill，不连接生产、Beszel、通知渠道或读取凭据。
- workload 通过自身 deadline 自然退出；自然退出不被解释为业务恢复。
- 所有 Guardian 决策必须保持 `execution=not_executed`；控制探针、身份缺口、恢复缺口
  和超时均保留在原始 JSON，不为通过而删除记录或放宽阈值。

## 预置执行命令

```bash
multipass launch 22.04 --name guardian-p014-real-recovery-v1 \
  --cpus 2 --memory 3G --disk 12G

python3 scripts/run_p014_real_docker.py \
  --vm guardian-p014-real-recovery-v1 --rounds 3 --scenario memory \
  --experiment-id EXP-076 --workload-seconds 8 --memory-mb 1024 \
  --memory-start-delay-seconds 4 \
  --output experiments/EXP-076-2026-09-21-real-docker-recovery-window/data/real-docker-recovery-source-v1.json

python3 scripts/check_p014_real_docker.py \
  experiments/EXP-076-2026-09-21-real-docker-recovery-window/data/real-docker-recovery-source-v1.json \
  --output experiments/EXP-076-2026-09-21-real-docker-recovery-window/data/real-docker-recovery-check-v1.json
```

## 结果

- 第一次启动在 Docker fixture 安装阶段因全新 VM 未预装 Docker 失败，错误原样保留在
  执行记录；随后只在本实验 VM 内安装 Ubuntu `docker.io`，启动 Docker 29.1.3/cgroup v2
  后按同一参数重跑，未修改仓库代码或放宽策略。
- 重跑完成 3 轮 memory workload、96 个 Guardian 样本和控制探针 `36/36`；3/3
  Guardian 窗口均观察到真实 Docker full ID/cgroup 身份、健康窗口和 workload 自然退出，
  Guardian overhead 共 206 行。
- 每轮的资源级 memory 风险都出现 `critical → recovered`，但整体 Observer 事件状态没有
  形成同会话 `recovered`：CPU/I/O 背景 warning 仍存在，说明资源级恢复不能被写成整机
  恢复。业务 health 仅证明 workload 活动期间的 fixture health，不证明业务恢复。
- P0-14 重算 `code_gate=PASS`、`status=INCONCLUSIVE`；缺口为 Beszel 时间差、owner
  Rescue SLO、真实业务恢复、短 workload 窗口、资源误触发/漏检，以及并非所有窗口都有
  整体同会话恢复。
- P0-17 离线重放 `code_gate=PASS`、新计划 `0`、契约违规 `0`、执行 `0`；真实内存
  数据在当前窗口没有形成可执行的 v1 TOP simulate 计划。

## 结论

EXP-076 补充了真实 Docker、同一 Observer 进程、自然退出和资源级恢复的证据，但不能
把 `memory=recovered` 升级为整机或业务恢复。实验整体保持 `INCONCLUSIVE`；不进入
P0-15A，不执行真实动作，不连接生产。
