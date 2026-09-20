# Guardian systemd 常驻运行基线

更新时间：2026-09-20  
关联 Goal：Goal 7 / PG-P0-07  
状态：`IN_PROGRESS`（本地 MVP 静态、短时运行、有界存储、依赖 fixture 和 transient notify/watchdog 通知基线已通过；完整 24 小时耐久、超时恢复和 P99 资源校准未完成）

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
- `Type=notify`、`NotifyAccess=main` 和 `WatchdogSec=90s` 用于运行态观察。配置 schema 的采样间隔上限为 60 秒，unit 保留名义上的 30 秒 watchdog 余量；实际超时恢复仍未注入验证。没有 `NOTIFY_SOCKET` 时适配层不报错退出，也不伪造 systemd 已接收通知。
- `RuntimeDirectory=guardian` 和 `StateDirectory=guardian` 让目录由 systemd 管理；当前验证没有触碰真实 `/run` 或 `/var/lib`。

## 4. 资源参数的证据边界

当前 slice 的 `MemoryMin=16M`、`MemoryLow=32M`、`MemoryHigh=192M`、`MemoryMax=256M`、`TasksMax=128` 和 `CPUWeight=50` 只是本地 MVP 起始值。

它们参考了 [EXP-019](../experiments/EXP-019-2026-09-19-guardian-local-resource-overhead/record.md) 的空载基线：Guardian Python 观测进程持续采样峰值 RSS 约 26.5 MiB；该测量只有 0 个业务容器、2 vCPU/4GB ARM64 VM，不能推出生产 P99。后续必须用至少多容器、快照、依赖超时、采集高压和日志边界场景重新测量 P99，并以余量校准；在此之前不得把这些数字复制到外部主机。

## 5. 验证结果

本次验证使用 Mac 主机和 Multipass `guardian-ubuntu`（Ubuntu 22.04.5 ARM64、2 vCPU、约 3.8 GiB 可用内存）。只把源文件复制到 VM 的 `/tmp/guardian-p0-03-check`，未覆盖 VM 项目目录。

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| 主机 Python 测试 | `125/125` 通过 | 包括 runtime、Observer 有界存储/依赖故障、配置、风险、归因、状态和恢复相关测试；新增审计 fsync 正负向契约 |
| VM 隔离相关测试 | `78/78` 既有基线通过；当前 Observer/Runtime 相关回归 `25/25` 通过 | 在临时目录运行，未安装 unit；EXP-047 首次夹具漏拷贝 unit/slice 的错误已修正后重跑 |
| `systemd-analyze verify` | 退出码 `0` | Guardian unit/slice 无自身语法错误；VM 还输出了 `netplan-ovs-cleanup.service` 权限警告和系统 `snapd.service` 不认识 `RestartMode` 的无关警告 |
| `observe --once` | 退出码 `0`，输出/审计各 1 条 | readiness 写入 `observe:degraded_observability`；首次样本因 OOM 基线和样本不足而降级，不执行动作 |
| 有界常驻 smoke | 8 秒上限，按预期由 `timeout` 返回 `124` | 输出/审计各 2 条，readiness 正常写入；未安装 systemd、未改 Docker |

`degraded_observability` 在上述首次/短时运行中是预期的安全结果，不是故障恢复成功证明。它表明 Guardian 在尚未形成采样窗口时不会把单个样本升级为可执行风险。

有界快照和审计的容量拒写、历史保留和异常路径证据见 [EXP-035](../experiments/EXP-035-2026-09-20-bounded-storage-fail-closed/record.md)。

45 秒局部 observe soak 和 readiness 语义修正见 [EXP-036](../experiments/EXP-036-2026-09-20-bounded-observer-soak/record.md)：最大 RSS 27,672 KiB、11 条采样全部写入审计；这只是局部基线，不是 24 小时或生产 P99 结论。

随后完成的 5 分钟延长 soak 见 [EXP-038](../experiments/EXP-038-2026-09-20-extended-observer-soak/record.md)：300.02 秒、43 条采样/审计、最大 RSS 27,672 KiB、无 Python traceback；仍不能替代 24 小时 soak。

真实 systemd 249 user manager 的 transient `Type=notify` readiness 和回收见 [EXP-040](../experiments/EXP-040-2026-09-20-systemd-transient-notify/record.md)：一次 `--once` Observer 成功收到 readiness 并退出，unit 被 `--collect` 回收；这不等同于持久 unit 安装或 watchdog 超时恢复验证。

watchdog 通知接收路径见 [EXP-041](../experiments/EXP-041-2026-09-20-systemd-watchdog-notify/record.md)：transient unit 配置 `WatchdogSec=30s`，systemd 记录到非零 `WatchdogTimestampMonotonic`，并在 READY 后观察到 `ActiveState=active/SubState=running`，随后 unit 以 success 自然结束。该实验没有触发 watchdog 超时，也没有验证崩溃后的自动重启。

模板与最大采样间隔的余量契约见 [EXP-045](../experiments/EXP-045-2026-09-20-watchdog-margin-contract/record.md)：持久 unit 模板使用 `WatchdogSec=90s`，严格大于配置允许的 60 秒采样间隔；这只是静态防回退约束，不等同于 watchdog 超时恢复证据。

命令行采样间隔覆盖的 fail-closed 校验见 [EXP-046](../experiments/EXP-046-2026-09-20-interval-override-fail-closed/record.md)：Observer 在启动入口拒绝非有限值和超出 0.1–60 秒范围的覆盖参数，主机 `123/123`、相关 VM 测试 `23/23` 通过。

审计追加的持久写入契约见 [EXP-047](../experiments/EXP-047-2026-09-20-audit-fsync-fail-closed/record.md)：Observer 只有在完整 payload 写入、`flush` 和 `fsync` 都成功后才把审计事件视为已写入；短写入或同步失败均返回失败，由上层保持降级/不执行动作。主机正负向测试已覆盖；EXP-039 当前运行进程是在该修正之前启动的，因此不能把本次契约直接写成该长跑的运行时证据。

EXP-047 的当前代码 VM smoke 已补充运行时证据：独立临时目录连续执行两次 `observe --once`，两次退出码均为 `0`，审计 2 行/15,680 bytes，`audit_status=written`，动作均为 `none/not_applicable`，stderr 为空；这仍不等同于长跑崩溃恢复或磁盘满恢复。

同一当前代码随后完成有界 45 秒 observe soak：`timeout` 按预设停止条件返回 `124`，输出/审计各 7 条，7 条审计均 `written`，首条 `degraded_observability` 后 6 条 `normal`，动作均为 `none/not_applicable`，最大 RSS 27,612 KiB，无 Python traceback。该结果补充 fsync 版本的连续运行证据，但不替代 EXP-039 的 24 小时长跑或剩余故障注入。

PG-P0-07 的资源时序使用只读、有界的 [`guardian_resource_sampler.py`](../scripts/guardian_resource_sampler.py)，不会向目标进程发送信号；[`guardian_resource_summary.py`](../scripts/guardian_resource_summary.py) 以固定 nearest-rank 定义计算 P50/P95/P99。EXP-039 已完成 20 秒 sidecar smoke，且在运行约 19 分钟时观察到审计文件在 1 MiB 有界门禁前停止增长、Observer 仍存活；截至当前中途快照，sidecar 已有 628 个样本、6,289 秒窗口，RSS P95/P99/最大值均为 17,752 KiB，CPU/FD/线程 P99 为 0.0%/5/1，RSS 均值 17,463.478 KiB、FD 均值 3.615，审计仍为 1,046,557 bytes。24 小时主 soak 仍在运行，最终 P99 待完成；该长跑启动早于 EXP-047 的 fsync 修正。

EXP-043 对 128 MiB/单 CPU、20 秒自然结束的本地 worker 做了有限高压采样：Guardian 保持存活，三次 Observer 只读事件均 fail-closed，memory PSI full 和 cgroup OOM/OOM-kill 为 0，Docker 状态未变。该结果只覆盖安全性检查；它没有制造 OOM，也不证明检测提前量或动作有效性。EXP-042 的 fixture 失败单独保留，不纳入通过统计。

EXP-044 验证了自然失败后的 systemd 重启子路径：transient unit 首轮自然返回非零码，journal 记录重启计划，第二轮 `NRestarts=1`、`ExecMainStatus=0`、readiness 再次生成并最终 success 回收。该实验没有触发 SIGKILL、OOM 或 watchdog 超时，因此仍不等同于崩溃/超时全覆盖。

EXP-048 进一步用 disposable wrapper 自然返回退出码 `137`，验证 transient `Restart=on-failure`：首轮失败后 journal 记录 restart counter `1`，第二轮 Observer 重新写入 readiness/审计，unit 最终 success 并由 `--collect` 回收。第一次 `NotifyAccess=main` 子进程通知夹具失败已保留，修正后的 transient-only `NotifyAccess=all` 结果不代表生产 unit 的权限配置，也不等同于真实 SIGKILL/OOM 恢复。

资源汇总器与回归测试已同步到 Multipass 临时工作树，当前隔离测试为 `78/78` 通过；该结果用于确认 ARM64 VM 上的测试兼容性，不改变 24 小时 soak 或生产 P99 的完成门。

Docker 只读采集超时、快照路径不可写等依赖故障 fixture 见 [EXP-037](../experiments/EXP-037-2026-09-20-dependency-failure-fixtures/record.md)。

## 6. 尚未完成与下一步

PG-P0-07 仍保持 `IN_PROGRESS`，原因是任务卡还要求：

1. 24 小时本地 observe soak 和资源增长/日志增长统计；
2. 进程崩溃、watchdog 超时恢复、Docker 超时、Hub 不可用、日志写失败和高压采样的故障注入；
3. 有界快照/审计存储的容量水位与恢复检查；
4. 基于完整场景 P99 + 余量重校准 slice 参数。

这些工作仍只能在本地 disposable 环境进行。完成前不进入 PG-P0-08，不执行真实 `graceful_stop`，也不安装为持久 systemd 服务。

## 7. 回滚与部署边界

当前没有系统级变更需要回滚。模板被复制到外部主机前，必须重新审查路径、用户、Docker 只读通道、配置 digest、日志额度和资源参数；安装/enable/start 属于独立部署动作，不由本实验隐式授权。
