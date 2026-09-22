# EXP-087：WSL2 常驻 Guardian 开销基线与有界压力检测时效

- 实验 ID：`EXP-087`
- 状态：`IN_PROGRESS`
- 日期：2026-09-22
- 环境：WSL2 Ubuntu 26.04.1，4 vCPU / 3.8 GiB RAM / 2 GiB swap，systemd 259，cgroup v2，Docker（systemd cgroup driver）
- 被测对象：本地 `install-guardian-local.sh --apply --environment local-disposable` 安装的常驻 observe Runtime（`guardian-runtime.service`，`rescue.slice`，guardian 用户）+ 只读 `guardian-collector.service`

## 1. 目的

1. 阶段一：测量 observe 模式下 Guardian Runtime 与 Collector 进程自身的资源开销（CPU%、RSS、上下文切换），形成空闲基线。
2. 阶段二：对宿主注入有界、限时压力，测量 Guardian 的检测时效：压力启动（systemd 单调时钟）→ 原始信号越限 → `risk.state` 变化；以及压力结束后的信号恢复时间。
3. 全程不开启 Broker、不执行容器停止、不改变 observe 模式、不连接生产。

## 2. 安全边界

- Guardian 保持 `observe`；Broker marker 与 reserve marker 全程不存在。
- 压力为 systemd transient unit（`guardian-perf-*`），带 `--timeout` 自动退出与 `--collect` 自动清理，单轮 ≤ 45 秒压力 + 60 秒恢复观察。
- 阶段二压力为宿主级 transient unit（`workload.slice` 固定 CPU 1 / MemoryMax 512M 的固定强度在 4 vCPU / 3.8 GiB 上无法触达宿主阈值，故不适用；本实验压力强度仅作用于本地可丢弃 WSL2）。
- 对 Guardian 只读：driver 只 tail 审计文件 `events.jsonl`，不调用 action 路径。
- 内存压力强度 2×1500 MiB（宿主 available 预期降至 10%–15% 区间）；`systemd-oomd` 保持启用；若压力 unit 被提前终止，按实际记录。

## 3. 方法

### 阶段一（开销基线）

- `guardian_resource_sampler.py --pid <MainPID> --interval 5 --duration 900`，对 Runtime 与 Collector 各采 180 个样本。
- 汇总用 `guardian_resource_summary.py`。

### 阶段二（检测时效）

- 场景：`cpu`（stress-ng --cpu 5，45s）、`memory`（--vm 2 --vm-bytes 1500M --vm-keep，45s）、`mixed`（两者组合，45s），各 3 轮。
- 时间基准：压力 unit 的 `ActiveEnterTimestampMonotonic`（µs→ns）与审计事件 `observed_monotonic_ns` 直接做差。
- 判定阈值取自 `/etc/guardian/guardian.json`：memory warning available<15% / critical<10%；cpu warning>85% / critical>95%（由 `/proc/stat` 相邻样本 ticks 计算）；swap warning>25%。
- 风险状态时延取审计事件 `risk.state != normal` 的首个样本（含 Guardian 自身确认窗口，非纯信号时延）。
- 每轮原始行存档 `timing-<scenario>-r<round>-rows.jsonl`，汇总 `timing-results-*.json`。

## 4. 结果

### 4.1 阶段一：Guardian 自身开销基线（observe，15 分钟 × 180 样本）

| 指标 | guardian-runtime | guardian-collector |
| --- | --- | --- |
| RSS 中位 / 最大 | 29,768 KiB / 29,796 KiB | 26,000 KiB / 26,008 KiB |
| CPU 中位 / 最大 | 0.2% / 0.601% | 0.0% / 0.401% |
| 线程 / FD | 3 / 3–4 | 1 / 4–7 |

15 分钟窗口内两组 RSS 无增长趋势（p99 与 max 几乎重合），未见泄漏迹象。全套常驻开销约 56 MiB RSS、不足 1% 单核 CPU。

### 4.2 阶段二：检测时效（3 场景有效 + 3 组环境特性记录，各 3 轮）

信号级首越限（压力 unit `ActiveEnterTimestampMonotonic` → 审计样本阈值越限）：

| 场景 | 轮次时延 (ms) | 中位 (ms) | 实测值 |
| --- | --- | --- | --- |
| cpu（5 worker 打满 4 核） | 9,823.8 / 10,194.3 / 10,570.7 | 10,194.3 | CPU 99.9–100% |
| memory_py（2.9 GiB 直配） | 8,254.7 / 5,642.0 / 4,088.7 | 5,642.0 | available 8.16–8.27% |
| mixed_py（CPU 5 worker + 2.4 GiB） | 8,834.6 / 7,225.2 / 5,616.5 | 7,225.2 | CPU 99.8–99.96% |

内存增长速率信号（`memory_growth_warning/critical`，首次出现）：

- memory（400M×2）：2,172.2 / 2,601.6 / 3,025.0 ms
- memory_strong（stress-ng，被 clamp 至 ~1.5 GiB）：4,440.8 / 4,931.5 / 5,424.7 ms
- mixed_py：最快 157.1 ms，最慢 5,616.5 ms
- memory_py：1,209.0 / 5,642.0 / 4,088.7 ms

状态级（`risk.state`）：全部 18 轮、全部场景中从未离开 `normal`。宿主内存 available 深处 critical 区（<10%）持续 45 秒也不升级。原因：压力源为 systemd transient unit，不在 Guardian 的 Docker 对象注册表中，无法满足"持续风险 + 明确对象身份"的升级复核；与 fail-closed 设计一致。

信号恢复：每轮压力自然退出后，信号在恢复观察窗口的第一个采样周期内（≤5 s + 1 周期）回落到阈值内。

### 4.3 环境特性发现（影响压力注入方法）

1. **stress-ng vm stressor clamp**：单实例内存不超过启动时 available 的 ~25%（1600M 请求被降为 800M/602M，日志显式打印），`--vm-bytes` 无论绝对值还是百分比都无法突破。要触达宿主阈值必须绕开（本实验用逐页触碰的 python 分配器）。
2. **WSL2 动态内存缓冲**：3 GB 压力仅使 available 下降 ~1.8 GiB（页缓存回收与 Hyper-V 回收共同缓冲），swap 几乎不介入；total_bytes 全程恒定 4,106,862,592。
3. **间歇性 `degraded_observability`**：顶层 state 偶发降级（signals.quality 同时段为 ok），不影响 signals 数值连续性；原因未在本实验展开。

## 5. 结论

1. observe 模式下 Guardian 常驻开销极低（Runtime ~30 MiB / 0.2% CPU，Collector ~26 MiB / 0.03% CPU），15 分钟无内存增长，适合长期常驻。
2. 信号级检测时效：内存 4.1–8.3 s（中位 5.6 s），CPU ~10 s（受 5 s 采样周期 + 差分窗口结构决定，非性能缺陷）；增长速率信号最快可达亚秒级（157 ms）。
3. 状态级风险升级在"压力源无 Docker 对象身份"时被 fail-closed 阻断，45 秒深度 critical 也不升级 —— 动作链路要求的目标身份复核按设计工作。
4. 宿主级压力注入必须绕开 stress-ng vm clamp；`guardian_maintenance_pressure.py` 的固定强度（单核 + 400M）在 4 vCPU/3.8 GiB 环境不可能触达宿主阈值，仅适用于对象级/维护通道验收。

## 6. 边界与限制

- WSL2 的内存/调度行为与原生 Linux 及 Multipass VM 不同；本结果只描述本 WSL2 环境，不外推到 x86_64 目标机或生产。
- Guardian 确认窗口（emergency_shedding 15s、候选持续要求）会拉长 `risk.state` 变化时间；原始信号越限与状态变化分开报告。
- 本地结果不代表业务恢复、SSH 可进入性或生产准入。
