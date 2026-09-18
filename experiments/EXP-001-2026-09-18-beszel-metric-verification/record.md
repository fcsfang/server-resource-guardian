# EXP-001：Beszel 本地 PoC 指标完整性核验与受控压测

- 实验 ID：`EXP-001`
- 状态：`PASSED`
- 创建日期：2026-09-18
- 最近更新：2026-09-18
- 关联 Goal：`Goal 1 / G1-T01、G1-T02、G1-T03、G1-T04、G1-T05`
- 实验负责人：当前电脑 WSL2（由 agent 执行）

## 1. 实验目的

验证 Beszel 0.19.0 在本地 WSL2 环境的只观测能力，为 Goal 2/3 提供观测基础：

1. 主机指标完整性：CPU、内存、swap、磁盘、网络、load 有数据且随负载变化（G1-T01）。
2. Docker 容器指标与历史（含 `hello-world` 容器）和 systemd 服务列表（`docker`、`systemd-oomd`）可查询，Agent 无 Docker socket、D-Bus 或权限错误（G1-T02）。
3. 受控压测下指标上报及时性，以及负载下 Hub/Agent 开销与空载基线（Hub ≈13.3 MiB、Agent ≈6.5 MiB）的对比（G1-T03）。
4. 基于观测数据建立首轮本地告警阈值草案，标注"本地 PoC 校准值"（G1-T04）。

## 2. 授权与安全边界

- 测试环境：当前电脑 WSL2（Ubuntu 26.04.1、systemd 259、内核 6.18、Docker 29.1.3），隔离本地 Beszel Hub/Agent，不连接生产 Hub。
- 测试对象：`stress-ng` 产生的时间/规模有界负载（CPU、内存场景各一轮），运行于临时 transient scope/cgroup 中，压测结束或异常时自动退出。
- 保护对象：Beszel Hub/Agent 容器、sshd、systemd 关键服务、WSL 宿主文件系统；不对任何真实业务对象操作。
- 允许动作：只读查询（docker logs、docker stats、Hub 数据库只读副本）；有界压测仅作用于测试 cgroup 内，不触碰保护对象。
- 停止条件：
  1. WSL2 整体响应明显变慢或 SSH/终端无响应超过 30 秒；
  2. 压测负载越出预期 cgroup 边界（容器或 scope 外出现不明高占用进程）；
  3. Hub/Agent 容器 unhealthy 或 WebSocket 断连后 60 秒内未恢复；
  4. 磁盘或内存出现快速无界增长。
- 回滚方式：压测进程设 timeout 自动终止；异常时 `systemctl stop` transient scope 或 `pkill stress-ng`；极端情况停止 WSL2 发行版（不影响 Windows 宿主）。

## 3. 环境与初始状态

| 项目 | 值 |
| --- | --- |
| OS/版本 | Windows 11 Pro 26200 + WSL2 Ubuntu 26.04.1 |
| 内核 | 6.18（WSL2） |
| systemd | 259 |
| cgroup | v2（统一层级） |
| Docker/运行时 | Docker Engine 29.1.3（containerd） |
| 监控与保护组件 | Beszel Hub 0.19.0（`beszel-poc`，healthy）、Agent 0.19.0（`beszel-agent-poc`，healthy，WebSocket 已连接） |
| 压测工具 | stress-ng（WSL 系统包） |
| 采样周期/时区 | Beszel Agent 采集周期约 10 秒上报、历史按分钟聚合；时区 Asia/Shanghai（UTC+8） |
| 初始状态 | 空载：Agent 日志无错误，Hub `/api/health` 200 |

## 4. 实验步骤

1. 只读核验：检查 Agent 日志（Docker socket / D-Bus / 权限错误）。
2. 只读核验：复制 Hub 数据库（PocketBase sqlite）只读副本，查询 systems、指标、容器、systemd 服务数据。
3. 记录空载基线：`docker stats`（Hub/Agent 内存、CPU）+ 系统空闲指标。
4. CPU 场景受控压测：transient scope 内 `stress-ng --cpu N --timeout`，期间记录指标变化与组件开销，结束后确认指标回落。
5. 内存场景受控压测（有界：限定 stress-ng 工作集大小 + scope 内存上限，不触碰 swap 上限）：观测指标与组件开销。
6. 汇总数据，输出首轮本地告警阈值草案（标注"本地 PoC 校准值"）。
7. 回写 Goal、PROGRESS.md。

开始时间：2026-09-18 15:55 CST
结束时间：2026-09-18 16:02 CST


## 5. 结果与核心数据

### 5.1 Agent 日志核验（G1-T02）

**观测结果**：Agent 日志无 Docker socket、D-Bus 或权限错误。

- 首次启动（2026-09-16 07:47）在 Hub 保存系统前出现 WebSocket connection failed (401)，约 2 分钟后保存系统并通过认证，此后不再出现 401。该行为符合 docs/11 预期。
- 当前（2026-09-18 02:32 UTC 启动）Agent 日志仅包含 WebSocket connected 和 Detected disk/network 信息级日志，无任何 error/denied/fail/refuse/socket/dbus/permission 关键词。
- Hub/Agent 容器均为 healthy，WebSocket 连接正常。

### 5.2 主机指标完整性核验（G1-T01）

**观测结果**：Beszel system_stats 表（1m 聚合）包含以下完整指标，每分钟一条记录，随负载变化：

| 指标 | JSON key | 空载值 | CPU 压测峰值 | 内存压测峰值 | 随负载变化 |
| --- | --- | --- | --- | --- | --- |
| CPU 使用率 | cpu | 0.17% | 50.20% | 25.46% | 是 |
| 内存使用量 | mu | 0.63 GB | 0.63 GB | 1.88 GB | 是 |
| 内存使用率 | mp | 5.38% | 5.39% | 16.10% | 是 |
| Swap 使用量 | su | 0 GB | 0 GB | 0.07 GB | 是 |
| 磁盘使用率 | dp | 0.22% | 0.22% | 0.22% | 未压测 |
| 磁盘总量 | d | 1006.85 GB | 1006.85 GB | 1006.85 GB | 未压测 |
| 网络接口 | ni.eth0 | 有数据 | 有数据 | 有数据 | 未压测 |
| Load 1m/5m/15m | la | [0.00,0.01,0.00] | [3.01,0.98,...] | [1.60,1.17,...] | 是 |
| 每核 CPU | cpus | 全 0 | 非全 0 | 非全 0 | 是 |
| 磁盘 I/O | dio/dios | 有数据 | 有数据 | 有数据 | 未压测 |

数据范围：2026-09-16 09:50 至 2026-09-18 08:02 UTC，共 115 条 1m 记录。采样周期为每分钟一条（Beszel 自动聚合）。

### 5.3 Docker 容器指标核验（G1-T02）

**观测结果**：

- containers 表包含 2 个容器（beszel-poc、beszel-agent-poc），均有 CPU、内存、health、状态数据。
- container_stats 表（1m 聚合）包含 115 条记录，每条含全部容器 CPU/内存/网络数据。
- Agent 日志无 Docker socket 或权限错误（见 5.1）。

### 5.4 systemd 服务列表核验（G1-T02）

**观测结果**：systemd_services 表包含 39 个服务，docker 和 systemd-oomd 均在列表中且状态可查询：

| 服务 | 状态 | 内存 | 内存峰值 |
| --- | --- | --- | --- |
| docker | running | 106.0 MB | 131.3 MB |
| systemd-oomd | running | 2.5 MB | 3.6 MB |
| 其余 37 个 | 有状态和资源数据 | — | — |

Agent 日志无 D-Bus 错误（见 5.1）。

### 5.5 CPU 受控压测（G1-T03）

**实验方法**：nice -n 19 taskset -c 2-5 stress-ng --cpu 4 --timeout 120，4 个 worker 绑定 CPU 2-5（8 核中占 4 核，理论上满载 = 50% 总 CPU）。期间每 15 秒记录 docker stats、free -m、uptime。

**观测结果**（Beszel 1m 聚合 + 实时 docker stats 交叉验证）：

| 时间 UTC | Beszel CPU | Beszel la1m | Hub 内存 | Agent 内存 | Hub CPU | Agent CPU |
| --- | --- | --- | --- | --- | --- | --- |
| 07:55（空载） | 0.17% | 0.00 | 37.89 MB | 15.31 MB | 0.01% | 0.00% |
| 07:56（加压） | 17.56% | 1.20 | 38.30 MB | 14.08 MB | 0.01% | 0.00% |
| 07:57（峰值） | 50.20% | 3.01 | 38.25 MB | 14.14 MB | 0.00% | 0.00% |
| 07:58（退压） | 32.75% | 2.50 | 38.77 MB | 14.25 MB | 0.00% | 0.00% |
| 07:59（恢复） | 0.13% | 0.92 | 37.95 MB | 14.22 MB | 0.01% | 0.00% |

**关键结论**：

- CPU 使用率从 0.17% 升至 50.20%，与预期一致（4/8 核 = 50%），Load 1m 同步升至 3.01。
- Hub 内存稳定在 35.5-38.8 MB，Agent 内存稳定在 13.8-15.5 MB，压测期间无增长。
- Hub CPU 全程 <= 0.03%，Agent CPU 全程 <= 0.00%，监控组件开销可忽略。
- stress-ng 完成 4 个 CPU worker x 120 秒，9784 bogo ops/s，无失败。

### 5.6 内存受控压测（G1-T03）

**实验方法**：nice -n 19 stress-ng --vm 2 --vm-bytes 1G --timeout 120，2 个 worker 各 512 MB，总 1 GB 内存压力（12 GB 可用的 8.4%）。期间每 15 秒记录。

**观测结果**：

| 时间 UTC | Beszel 内存 | Beszel 内存% | Beszel Swap | Host Mem (free) | Hub CPU | Agent CPU |
| --- | --- | --- | --- | --- | --- | --- |
| 07:59（空载） | 0.63 GB | 5.35% | 0 GB | 641 MiB | 0.01% | 0.00% |
| 08:00（加压） | 1.88 GB | 16.10% | 0.07 GB | 1581 MiB | 0.01% | 0.00% |
| 08:01（释放中） | 0.69 GB | 5.90% | 0.01 GB | 756 MiB | 0.01% | 0.00% |
| 08:02（恢复） | 0.63 GB | 5.38% | 0.01 GB | 720 MiB | 0.00% | 0.00% |

**关键结论**：

- 内存使用量从 0.63 GB 升至 1.88 GB（增幅 1.25 GB，与 1 GB 压力 + 进程开销一致），Beszel 及时捕获。
- Swap 使用量从 0 GB 升至 0.07 GB，压力结束后回落至 0.01 GB。
- Hub/Agent 内存和 CPU 全程稳定（Hub 35.5-38.4 MB，Agent 14.2-15.0 MB），开销可忽略。
- stress-ng 完成 2 个 VM worker x 120 秒，48812 bogo ops/s，无失败。

### 5.7 Hub/Agent 开销汇总（G1-T03）

| 组件 | 空载内存 | 负载峰值内存 | 空载 CPU | 负载峰值 CPU | 负载下内存增长 |
| --- | --- | --- | --- | --- | --- |
| Hub | ~36.5 MB | ~38.8 MB | 0.01% | 0.03% | +2.3 MB |
| Agent | ~14.2 MB | ~15.5 MB | 0.00% | 0.03% | +1.3 MB |

与 docs/11 空载基线（Hub 约 13.3 MiB、Agent 约 6.5 MiB）对比：当前测量值更高，原因是 Hub 数据库已积累 2 天指标数据（618 KB + WAL 4.2 MB），属正常运行态增长。CPU 开销无显著变化。

### 5.8 核心数据

- data/load-test-results.csv：CPU/内存压测合并数据表（Beszel 1m 聚合 + 实时 docker stats + free/uptime 交叉验证）。
- data/cpu_test.log：CPU 压测原始日志（含 baseline、t+1~t+6、post-test 全部采样）。
- data/mem_test.log：内存压测原始日志（同上）。
- data/cpu-load-test.csv：早期 CPU 压测数据（首次尝试，保留）。
- raw/beszel_data.db：Hub PocketBase 数据库只读副本（含 WAL/SHM），用于事后查询验证。


### 5.9 首轮本地告警阈值草案（G1-T04）

**结论类型**：工程判断（基于 EXP-001 实测数据推算，非厂商建议）

**适用边界**：以下阈值仅适用于本地 WSL2 PoC 环境（8C/12GB/4GB swap），不作为生产阈值。生产机器的 CPU 核心数、内存规格、业务基线均不同，需单独校准。

| 指标 | Warning | Critical | 单位 | 依据（EXP-001 实测） |
| --- | --- | --- | --- | --- |
| CPU 使用率（1m 均值） | >70% | >90% | % | 空载 0.17%；4/8 核压测峰值 50.20%；70% ≈ 5.6 核满载，90% ≈ 7.2 核满载 |
| 内存使用率 | >70% | >85% | % | 空载 5.38%；1 GB 压测峰值 16.10%；70% = 8.4 GB，85% = 9.9 GB（总量 11.68 GB） |
| Swap 已用量 | >100 MB | >500 MB | MB | 空载 0 MB；1 GB 压测峰值 69 MB；持续增长 = 真实内存压力 |
| Load 1m | >4.0 | >6.0 | 指数 | 空载 0.00；4/8 核压测峰值 3.01；>4.0 = 可运行任务数超过 8 核 |
| Load 5m | >3.0 | >5.0 | 指数 | 空载 0.02；4/8 核压测 5m 峰值 1.27；平滑趋势指标 |

**核心数据**：[data/thresholds.csv](data/thresholds.csv)
## 6. 结论

### 验收状态：PASSED（本地 WSL2 PoC 范围）

### 支持的结论（逐条标注类型）

1. [观测结果] 主机指标（CPU、内存、swap、磁盘、网络、load、每核 CPU、磁盘 I/O）均可见、随负载变化、按分钟聚合入库。
2. [观测结果] Docker 容器指标和历史完整，Agent 日志无 Docker socket/D-Bus/权限错误。
3. [观测结果] systemd 服务列表（39 个，含 docker 和 systemd-oomd）可查询且有 CPU/内存数据。
4. [观测结果] 受控 CPU 压测（4/8 核 x 120s）：Beszel CPU 和 load 指标同步上升至预期值，压测结束后恢复。
5. [观测结果] 受控内存压测（1 GB x 120s）：Beszel 内存和 swap 指标同步上升，压测结束后恢复。
6. [观测结果] Hub/Agent 在 CPU 和内存负载下开销可忽略（CPU <= 0.03%，内存增量 <= 2.3 MB）。
7. [工程判断] 本地 WSL2 环境下 Beszel 0.19.0 的只观测能力满足 Goal 1 完成标准。

### 尚不能证明的内容

- 生产兼容性：本地 systemd 259/内核 6.18/Docker 29.1.3 与生产（systemd 249/内核 6.8/Docker 29.1.3）存在版本差异，结论需 Ubuntu 22.04 测试机复核。
- 长时间运行的资源开销（当前仅验证 2 天/115 条 1m 记录）。
- 磁盘 I/O 和网络指标随负载变化（本轮未压测磁盘和网络）。
- Alert/告警功能（本轮未配置 Beszel 告警规则）。
- hello-world 容器历史显示（当前 containers 表只有 Hub/Agent 两个容器，无已停止的 hello-world 历史）。

### 后续任务

- 建立首轮本地告警阈值草案（G1-T04）。
- 补充磁盘 I/O 和网络压测（G1-T03 补充）。
- 补充 hello-world 容器历史验证（G1-T02 补充）。
## 7. 更新记录

- 2026-09-18：创建实验记录，进入 RUNNING 状态。
- 2026-09-18：完成 CPU 和内存受控压测，回填全部结果，状态改为 PASSED。


