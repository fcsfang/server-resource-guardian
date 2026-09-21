# EXP-063：真实 Docker 对象与生命周期的 P0-14 补证

- 实验 ID：`EXP-063`
- 状态：`INCONCLUSIVE`
- 日期：2026-09-20～2026-09-21（本地 Asia/Shanghai）
- 关联 Goal：`Goal 7 / PG-P0-14`
- 目的：在全新 disposable Multipass VM 的真实 Docker daemon 中，验证 Guardian 的真实 full ID→PID→cgroup 映射、对象生命周期变化、CPU/内存/I/O 观测与同一 Observer 会话恢复；同时记录业务 health、Guardian 自身开销、误触发/漏检真值和 Beszel 时间差边界。

## 1. 安全边界

- 只创建新的 `guardian-p014-real-docker` disposable VM；不操作现有 `guardian-ubuntu`，不连接生产或其他外部主机。
- workload 只使用本实验构建的本地 BusyBox 镜像，容器自动自然退出；不调用 Guardian action adapter，不创建 capability，不执行 Docker/systemd stop、restart 或 kill。
- Guardian 只运行 `--mode simulate`，所有动作字段必须保持 `execution=not_executed`；Docker `run` 仅用于创建本实验 workload/churn fixture，真实动作与 fixture 启动严格分开记录。
- 场景、对象、健康接口、停止时间和预期资源真值写入结构化原始数据；失败轮次保留，不为了通过而删除或放宽门槛。
- VM 结束后按精确 VM 名删除；若实验中止，先保留原始输出，再清理该 VM。

## 2. 预置验收条件

1. CPU、memory、capacity、I/O、mixed 五场景各完成至少三轮；每轮包含 unprotected、rescue、guardian 三组。
2. Guardian 场景真实使用 Docker `stats`、`inspect` 和真实容器 PID/cgroup；不使用 synthetic Docker identity。
3. Guardian 场景由同一个 Observer 进程连续采样，workload 自然退出后继续采样，记录风险状态恢复和业务 health 状态变化。
4. 同时运行有限 idle churn 容器，验证稳定 full ID、容器退出/新增和归因变化；只读记录 churn，不对其执行处置。
5. 原始 JSON 可由脚本重算：控制探针成功率、资源真值、状态覆盖、目标 ID 稳定性、action=0、Guardian CPU/RSS/审计增长、误触发/漏检和 Beszel 缺失边界。
6. 任意真实 Docker 映射、memory/I/O 恢复、业务 health、开销、误触发/漏检或 Beszel 时间差证据缺失时，实验必须保持 `INCONCLUSIVE`。

## 3. 执行与环境

执行命令：

```bash
python3 scripts/run_p014_real_docker.py \
  --vm guardian-p014-real-docker --rounds 3 \
  --output experiments/EXP-063-2026-09-20-real-docker-p014/data/real-docker-v2.json
python3 scripts/check_p014_real_docker.py \
  experiments/EXP-063-2026-09-20-real-docker-p014/data/real-docker-v2.json \
  --output experiments/EXP-063-2026-09-20-real-docker-p014/data/real-docker-v2-check.json
```

最终运行环境：全新 Multipass `guardian-p014-real-docker`，Ubuntu 22.04.5 LTS，Linux 5.15.0-191-generic，ARM64，2 vCPU、约 3 GiB RAM、12 GiB 磁盘；systemd 249，Docker 29.1.3，overlayfs，cgroup v2/systemd controller。实验结束后已卸载挂载并删除该 VM；现有 `guardian-ubuntu` 未修改。

本实验不把 BusyBox HTTP healthcheck 的 `healthy` 等同于业务恢复；容器自然退出只表示 fixture 生命周期结束。

## 4. 结果

### 4.1 原始执行结果

- `real-docker-v1-fixture-failure.json` 保留了第一次夹具失败：无界 I/O 写入填满第一台 disposable VM，观察到 `no space left on device`；之后改为每轮最多 8 个 4 MiB 文件，并在新 VM 重跑。
- 修正后的 v2 完成 3 轮、45 个组合记录和 15 个 Guardian 窗口；Guardian 原始采样 `480/480`，控制探针 `179/180` 成功。
- 唯一控制失败为 `r3/guardian/io` 第 2 次只读探针，耗时 `12007.186ms` 后 `probe_timeout`；失败保留在原始 JSON，没有从统计中删除。
- 15/15 Guardian 目标均出现真实 64 位 Docker full ID；均至少有 high-confidence `full ID → PID → docker-<id>.scope` 记录，观测中的稳定 cgroup path 可重算。`r2/memory` 有一次容器 cgroup 文件瞬时缺失，作为质量缺口保留。
- 15/15 目标在 Guardian 窗口前由 Docker healthcheck 观测到 `healthy`；这只证明 fixture 的活动健康接口，不证明业务恢复。
- 15/15 churn 窗口均启动 3 个不同 full ID，fixture 自然退出；Guardian 未对其处置。
- Guardian 开销共 1,058 个 systemd 资源样本；原始 `CPUUsageNSec` P95 为 `18,264,064`，`MemoryCurrent` P95 为 `674,845,000` bytes，`TasksCurrent` P95 为 `8`；审计文件大小为 `642,373～663,688` bytes。这些是观测值，不是 owner 批准的 SLO。

### 4.2 资源真值与风险状态

`real-docker-v2-check.json` 对每个窗口保留预期资源、观测资源、漏检和误触发：CPU 场景三轮均观测到 CPU，但同时有 IO warning；memory 场景仅部分轮次形成 memory 风险；capacity 场景的 64 MiB `/work` 写入没有把宿主根盘推过高水位，因此不能作为有效的容量高水位命中；IO 场景均观测到 IO，但混有 CPU/memory 背景风险；mixed 场景部分轮次漏掉 memory，并持续出现 IO 背景风险。该结果说明当前 fixture 已能记录真实误触发/漏检边界，但不能宣称三资源检测准确。

同一 Observer 会话的 32 秒窗口中，没有形成可确认的整体 `recovered` 闭环；自然退出、Docker stats 降级和宿主背景 IO/CPU 状态不能被写成恢复成功。

## 5. 结论

`code_gate=FAIL`（只因保留的 1 次控制探针失败）；`status=INCONCLUSIVE`。本实验已经补齐真实 Docker daemon、full ID/cgroup、对象 churn、活动 health probe、Guardian 开销和误触发/漏检原始证据，但仍缺少稳定 memory/I/O 恢复、完整控制探针、容量高水位命中、Beszel 同负载时间差和 owner 批准的 Rescue SLO。未调用 action adapter、未创建 capability、未执行 Docker/systemd stop/restart/kill、未连接生产或真实通知渠道。

## 6. 后续行动

1. 保持 PG-P0-14 `IN_PROGRESS/INCONCLUSIVE`，先补一个隔离 loopback bind mount 的真实容器容量高水位夹具，避免把宿主根盘写满。
2. 针对 control timeout、Docker stats 3 秒超时和背景 IO warning 分别保留基线并评估是否需要 source-level quality/timeout 设计，而不是调阈值掩盖。
3. 若继续补 recovery，使用更长的自然 workload/observer 窗口，并把资源恢复、宿主缓解和业务恢复分开记录。
4. Beszel 时间差和 owner Rescue SLO 仍需外部条件；缺失前不进入 P0-15 或生产测试。

## 7. 结论边界

即使本实验通过，也只覆盖本地 ARM64 disposable VM 的真实 Docker 对象证据，不能替代 x86_64 非生产 7 天 soak、owner Rescue SLO、生产 Observe/Simulate 审批或生产 Enforce 准入。
