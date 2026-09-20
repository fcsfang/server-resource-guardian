# Guardian systemd 常驻运行基线

更新时间：2026-09-20  
关联 Goal：Goal 7 / PG-P0-07  
状态：`IN_PROGRESS`（本地 MVP 静态、短时运行和有界存储基线已通过；完整 24 小时耐久和 P99 资源校准未完成）

## 1. 目的与范围

本文件记录 Guardian 从脚本化 observe 原型向 systemd 常驻服务迁移的第一版运行边界。当前产物只包括可审查的 unit/slice 模板、readiness/watchdog 适配和隔离目录校验；没有安装、enable、start 或修改 Multipass 系统服务。

本阶段只保证：

- Guardian 以非 root 用户启动，并有独立 systemd slice；
- systemd 可以通过 `Type=notify` 和 watchdog 观察 Guardian 是否完成启动、是否持续采样；
- readiness 文件和 journald 输出有明确位置与上限；
- 运行时没有把 Docker stop/restart/kill 写进常驻 unit；
- 采集失败、样本不足和依赖不可用时保持 observe/fail-closed，不生成真实动作。
- 快照和 JSONL 审计达到容量上限或写入失败时拒写并标记降级，不自动删除历史证据。

本阶段不保证 24 小时无泄漏、磁盘满场景完整恢复、生产阈值或 x86_64 兼容性。

## 2. 产物

| 产物 | 作用 | 当前边界 |
| --- | --- | --- |
| [`deploy/guardian/guardian-observer.service`](../deploy/guardian/guardian-observer.service) | Guardian 只读 Observer 的 systemd 模板 | 不安装、不启用；生产部署前需重新审查用户、路径、Docker 读取代理和资源值 |
| [`deploy/guardian/guardian-observer.slice`](../deploy/guardian/guardian-observer.slice) | 独立 CPU/内存/任务边界 | 参数是本地起始值，不是生产配额 |
| [`src/guardian_runtime.py`](../src/guardian_runtime.py) | systemd notify、readiness 文件和 watchdog 的最小适配 | systemd 不可用时安全 no-op；不执行服务管理或 Docker 动作 |
| [`src/guardian_observer.py`](../src/guardian_observer.py) | 每轮采样发送 readiness/watchdog | 仍是只读 observe；动作必须由独立控制层另行接管 |

## 3. 权限与安全边界

- unit 使用 `User=guardian`、`Group=guardian`、`NoNewPrivileges=true`、空 capability 集合、`PrivateDevices=true`、`ProtectHome=true` 和 `ProtectSystem=strict`。
- Observer 通过 `SupplementaryGroups=docker` 读取 Docker 状态。Docker 组本身是高权限控制面，因此这是当前本地模板中最需要在生产部署前拆分/评审的边界；它不等于已经具备生产动作权限，action broker 仍必须是独立服务。
- `ReadWritePaths` 仅保留 Guardian 状态目录和 readiness 目录；审计写入失败时，上层动作门禁仍保持 fail-closed。
- `RestrictAddressFamilies` 只保留 Unix/IPv4/IPv6，unit 没有公网目的地配置。
- `Type=notify`、`NotifyAccess=main` 和 `WatchdogSec=60s` 用于运行态观察；没有 `NOTIFY_SOCKET` 时适配层不报错退出，也不伪造 systemd 已接收通知。
- `RuntimeDirectory=guardian` 和 `StateDirectory=guardian` 让目录由 systemd 管理；当前验证没有触碰真实 `/run` 或 `/var/lib`。

## 4. 资源参数的证据边界

当前 slice 的 `MemoryMin=16M`、`MemoryLow=32M`、`MemoryHigh=192M`、`MemoryMax=256M`、`TasksMax=128` 和 `CPUWeight=50` 只是本地 MVP 起始值。

它们参考了 [EXP-019](../experiments/EXP-019-2026-09-19-guardian-local-resource-overhead/record.md) 的空载基线：Guardian Python 观测进程持续采样峰值 RSS 约 26.5 MiB；该测量只有 0 个业务容器、2 vCPU/4GB ARM64 VM，不能推出生产 P99。后续必须用至少多容器、快照、依赖超时、采集高压和日志边界场景重新测量 P99，并以余量校准；在此之前不得把这些数字复制到外部主机。

## 5. 验证结果

本次验证使用 Mac 主机和 Multipass `guardian-ubuntu`（Ubuntu 22.04.5 ARM64、2 vCPU、约 3.8 GiB 可用内存）。只把源文件复制到 VM 的 `/tmp/guardian-p0-03-check`，未覆盖 VM 项目目录。

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| 主机 Python 测试 | `114/114` 通过 | 包括 runtime、Observer 有界存储、配置、风险、归因、状态和恢复相关测试 |
| VM 隔离相关测试 | `71/71` 通过 | 在临时目录运行，未安装 unit |
| `systemd-analyze verify` | 退出码 `0` | Guardian unit/slice 无自身语法错误；VM 还输出了 `netplan-ovs-cleanup.service` 权限警告和系统 `snapd.service` 不认识 `RestartMode` 的无关警告 |
| `observe --once` | 退出码 `0`，输出/审计各 1 条 | readiness 写入 `observe:degraded_observability`；首次样本因 OOM 基线和样本不足而降级，不执行动作 |
| 有界常驻 smoke | 8 秒上限，按预期由 `timeout` 返回 `124` | 输出/审计各 2 条，readiness 正常写入；未安装 systemd、未改 Docker |

`degraded_observability` 在上述首次/短时运行中是预期的安全结果，不是故障恢复成功证明。它表明 Guardian 在尚未形成采样窗口时不会把单个样本升级为可执行风险。

有界快照和审计的容量拒写、历史保留和异常路径证据见 [EXP-035](../experiments/EXP-035-2026-09-20-bounded-storage-fail-closed/record.md)。

## 6. 尚未完成与下一步

PG-P0-07 仍保持 `IN_PROGRESS`，原因是任务卡还要求：

1. 24 小时本地 observe soak 和资源增长/日志增长统计；
2. 进程崩溃、Docker 超时、Hub 不可用、日志写失败和高压采样的故障注入；
3. 有界快照/审计存储的容量水位与恢复检查；
4. 基于完整场景 P99 + 余量重校准 slice 参数。

这些工作仍只能在本地 disposable 环境进行。完成前不进入 PG-P0-08，不执行真实 `graceful_stop`，也不安装为持久 systemd 服务。

## 7. 回滚与部署边界

当前没有系统级变更需要回滚。模板被复制到外部主机前，必须重新审查路径、用户、Docker 只读通道、配置 digest、日志额度和资源参数；安装/enable/start 属于独立部署动作，不由本实验隐式授权。
