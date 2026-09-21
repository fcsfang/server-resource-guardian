# EXP-054：Rescue Plane 资源域与可进入性基线

- 实验 ID：`EXP-054`
- 状态：`PASSED`
- 创建日期：2026-09-20
- 最近更新：2026-09-20
- 关联 Goal：`Goal 7 / PG-P0-11`
- 实验负责人：当前 Agent

## 1. 实验目的

在本地 Multipass Ubuntu 22.04.5 VM 中建立 `rescue.slice` / `workload.slice` 原型，确认最小人工救援链路的空闲基线和有界压力下行为。重点区分：Guardian 自身存活、Multipass 管理的 SSH session、只读诊断、Docker 控制面读取和审计写入，不能把任意一个结果扩大为“SSH 永远可用”。

## 2. 授权与安全边界

- 测试环境：本机 `guardian-ubuntu` Multipass VM，以及新建的独立 `guardian-p011-migration` disposable Multipass VM；不连接生产或外部主机。
- 测试对象：仅限可丢弃的 transient workload fixture；原 `guardian-ubuntu` 的 Beszel 现有容器只读观测，不改变其状态；迁移 VM 使用空 Docker daemon。
- 保护对象：宿主 SSH、网络、Docker/containerd、systemd、Beszel 现有容器和 Guardian 进程。
- 允许动作：静态 unit 校验、只读状态查询、bounded transient fixture、只读探针；迁移 VM 允许安装并回滚候选 Rescue Plane drop-in、重启该 disposable VM 和启动只读 observer；不执行 Docker stop/restart/kill，不填充宿主根盘。
- 停止条件：SSH/诊断/Docker/Guardian 探针失败、SLO 明显超预算、可用内存快速下降、根盘高水位、Docker 状态变化或任何未预期服务状态变化。
- 回滚方式：原 `guardian-ubuntu` 不安装持久 unit；迁移 VM 的 `/etc/systemd/system` drop-in 和 observer unit 在实验结束前可移除，transient fixture 到期或显式结束，原始结果保留后销毁迁移 VM。

## 3. 环境与初始状态

| 项目 | 值 |
| --- | --- |
| OS/版本 | Ubuntu 22.04.5 LTS ARM64 |
| 内核 | 5.15.0-191-generic aarch64 |
| systemd | 249.11 |
| cgroup | cgroup v2；cpu/io/memory/pids 等控制器可见 |
| Docker/运行时 | Docker Engine 29.1.3；containerd active |
| VM 预算 | 2 vCPU；约 3.8 GiB；根盘约 39 GiB |
| 现有容器 | Beszel Hub/Agent，各自 healthy；只读观测 |
| 现有 Guardian | 原 VM 未安装持久服务；迁移 VM 仅运行本实验的只读 observer fixture |
| 采样周期/时区 | 探针单轮；实验主机 Asia/Shanghai，guest 时间单独记录 |

## 4. 实验步骤

1. 在原 `guardian-ubuntu` 静态校验 `rescue.slice`、`workload.slice` 和 Guardian unit，不持久安装。
2. 通过 transient systemd fixture 验证两个 slice 名称和 cgroup 归属，然后回收 fixture。
3. 使用 `guardian_rescue_probe.py` 对原 VM 空闲和有界压力执行每类 20 次顺序只读探针。
4. 在独立 `guardian-p011-migration` VM 记录默认 `system.slice/user.slice` 的空闲和四类压力基线。
5. 在迁移 VM 安装 Rescue Plane drop-in，将 SSH、logind、networkd/resolved、D-Bus、containerd、Docker、journald 和 Guardian observer 放入 `rescue.slice`；将内置 `user.slice` 作为并行救援资源域配置，然后重启并核对实际 cgroup。
6. 在相同迁移 VM、相同 workload fixture 和相同探针下重复空闲与四类压力对照；记录服务成员、登录 session 边界、审计间隔和回滚。
7. 根盘容量只使用隔离 loopback fixture，不填充 VM 根盘。

## 5. 结果与核心数据

- 静态校验：`systemd-analyze verify` 退出码为 `0`；仅出现宿主已有的 snapd `RestartMode` 兼容性提示。Transient fixture 分别落在
  `/rescue.slice/guardian-p011-rescue-fixture.service` 与
  `/workload.slice/guardian-p011-workload-fixture.service`。
- Slice 运行态参数核对：`rescue.slice` 为 `CPUWeight=1000`、`IOWeight=1000`、`MemoryMin=128M`、`MemoryLow=512M`、`TasksMax=512`；`workload.slice` 为 `CPUWeight=100`、`IOWeight=100`、`TasksMax=4096`。
- 空闲基线：SSH/诊断/Docker/Guardian 分别为 `20/20` 成功，P95 分别为 `93.326ms/74.440ms/88.996ms/1900.716ms`。
- 有界压力：CPU、memory、I/O、tasks 四个 workload 场景中四类探针均为 `20/20` 成功。最差 P95 为 SSH `145.506ms`、诊断 `91.279ms`、Docker 清单 `96.667ms`；均低于 PG-P0-11 的 `3s/5s` 绝对门槛，也低于空闲 P95 三倍门槛。
- I/O fixture 期间内核 I/O PSI 曾升高（`some avg10=11.14`、`full avg10=10.25`），但探针未失败，实验结束后 fixture 已回收。
- Memory fixture 只分配并触碰了约 `512MiB`，未进入 OOM 或高 memory PSI；这证明边界安全执行，不证明危机压力下的保护效果。
- Tasks fixture 期间观察到 workload cgroup `TasksCurrent=67`，未改变 Docker/Beszel 状态。
- Guardian 观察器：1 秒场景审计 JSONL 为 `23/22/25/24` 行且事件 ID 无重复；5 秒场景为 `5/5` 行，观测间隔 `7.135–7.225s`，低于 `5s×2=10s`。所有动作均为 `none`；当前代码的 `audit_status=written` 出现在 stdout/journal，JSONL 本身不重复该字段。
- 容量：仅在 loopback fixture 验证。40MiB 写入按预期跨过 16MiB 保留空间并被记录为负向结果；32MiB 写入后仍剩 `20,402,176` bytes，通过保留空间检查。宿主根盘未填充。
- 独立迁移 VM：迁移前八个候选 service 均在 `system.slice`；迁移后均实际位于 `/rescue.slice`，Guardian observer 也位于 `/rescue.slice`。`user-1000.slice` 无法脱离 systemd 特殊的 `user.slice` 父级，因此改为给 `user.slice` 配置 `CPUWeight/IOWeight=1000`、`MemoryMin=128M`、`MemoryLow=512M`、`TasksMax=512`；该边界已由 membership 数据记录。
- 前后对照：空闲及 CPU、memory、I/O、Tasks 四个场景均分别完成迁移前/迁移后 `20/20` 探针；所有迁移后 SSH P95 < `3s`，诊断和 Docker 清单 P95 < `5s`。Tasks fixture 期间 workload 达到 `127–128/128`，I/O fixture 期间 I/O PSI 升高，仍无探针失败。
- 迁移 observer：审计 JSONL `26/26` 条事件 ID 唯一、动作均为 `none`，采样间隔 `5.012–5.194s`，低于配置周期 `5s×2`；stdout/journal 中 `audit_status=written`。
- 核心数据：[data/](data/)，原始探针见 `pre-migration-*.json`、`post-migration-*.json` 和 `rescue-recheck-*.json`；对照汇总见 [migration-comparison.json](data/migration-comparison.json) / [migration-comparison.csv](data/migration-comparison.csv)，成员迁移见 [migration-membership.json](data/migration-membership.json)。
- 证据：不保存凭据、SSH key、完整生产日志或无界原始输出。

## 6. 结论

- 验收状态：`PASSED`（本地 disposable gate），PG-P0-11 可进入下一任务。
- 当前支持的结论：在同一独立 Ubuntu 22.04.5 / cgroup v2 VM 上，候选救援 service 迁移和 `user.slice` 并行资源保护生效；迁移前/迁移后空闲、CPU、受控内存、I/O、Tasks 对照均满足本轮 SLO，审计间隔和事件完整性通过。
- 尚不能证明：生产 SSH SLO、真实外部客户端网络/认证、带外能力、任意资源耗尽、根文件系统真实满盘或目标机 x86_64 行为。Multipass 管理 session 仍不是生产客户端 SSH；同主机软件也不能提供绝对可进入保证。
- 不能把本轮结果解释为“Guardian 在危机中执行了止损”：observer 始终为 observe-only，所有动作均为 `none`，Docker 没有 stop/restart/kill。
- 后续任务：进入 PG-P0-12 CPU observe/simulate；本实验的 Rescue Plane 证据不得替代 CPU/内存/容量/inode/I/O 各自的风险状态机和归因证据。

## 7. 更新记录

- 2026-09-20：创建实验记录，先固化依赖清单、草案和停止条件。
- 2026-09-20：完成原 VM 静态/transient 基线，并在独立 `guardian-p011-migration` VM 完成迁移前/迁移后空闲、CPU、memory、I/O、Tasks 配对对照；候选服务实际进入 `rescue.slice`，`user.slice` 以并行救援资源域保护，所有硬门通过，实验改为 `PASSED`。生产和真实客户端边界仍保留。
