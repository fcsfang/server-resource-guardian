# EXP-003：第二层现成自动资源保护策略评估（systemd-oomd / Monit / Docker 限制）

- 实验 ID：`EXP-003`
- 状态：`PASSED`
- 创建日期：2026-09-18
- 最近更新：2026-09-18
- 关联 Goal：`Goal 3 / G3-T01、G3-T02、G3-T03、G3-T04、G3-T05`
- 实验负责人：当前电脑 WSL2（由 agent 执行）

## 1. 实验目的

以只读和模拟方式评估现成自动资源保护工具的触发条件、对象选择、动作类型、排除规则和恢复行为，判定"现成能力已足够"或列出 Guardian 的最小缺口：

1. 只读验证：压力观测、对象定位（容器/cgroup/进程组）可复现（G3-T01）。
2. 逐项记录 systemd-oomd 行为：监控对象、触发条件、动作与恢复（G3-T02）。
3. 逐项记录 Monit 的检查规则、动作能力和适用边界（G3-T03）。
4. 模拟动作验证对象选择、审计和恢复流程（G3-T04）。
5. 对照业务要求输出缺口分析结论（G3-T05）。

## 2. 授权与安全边界

- 测试环境：当前电脑 WSL2（Ubuntu 26.04.1、systemd 259、内核 6.18、Docker 29.1.3）。
- 允许动作：只读查询（systemctl、busctl、cat /sys/fs/cgroup、/proc/pressure）；安装 Monit（只读配置，不启用任何自动动作）。
- 未执行：真实 SIGKILL、容器重启、systemd-oomd kill 属性修改、Monit 自动动作启用。
- 保护对象：Beszel Hub/Agent 容器、sshd、systemd 关键服务。

## 3. 环境与初始状态

| 项目 | 值 |
| --- | --- |
| OS | Ubuntu 26.04.1（WSL2） |
| 内核 | 6.18.33.2-microsoft-standard-WSL2 |
| systemd | 259 |
| Docker | 29.1.3 |
| systemd-oomd | active (running)，enabled，PSI 可用 |
| Monit | 5.35.2（新安装，默认配置，无监控项） |
| PSI | /proc/pressure/{memory,cpu,io} 均存在 |

## 4. 实验结果

### 4.1 systemd-oomd 行为记录（G3-T02）

**[观测结果] 运行状态**：

| 项目 | 值 |
| --- | --- |
| 服务状态 | `active (running)`，enabled |
| 运行时依赖 | `/proc/pressure/{cpu,io,memory}` 全部存在 |
| D-Bus 接口 | `org.freedesktop.oom1` |
| 当前日志 | 无 OOM kill 事件 |

**[观测结果] 默认配置**（`/etc/systemd/oomd.conf`）：

| 参数 | 默认值（注释 = 默认） | 含义 |
| --- | --- | --- |
| SwapUsedLimit | 90% | swap 使用超过 90% 时视为压力 |
| DefaultMemoryPressureLimit | 60% | 内存 PSI 超过 60% 视为压力 |
| DefaultMemoryPressureDurationSec | 30s | 压力持续 30 秒才触发动作 |

**[观测结果] ManagedOOM 属性（关键发现）**：

```
system.slice:
    ManagedOOMSwap=auto
    ManagedOOMMemoryPressure=auto
    ManagedOOMPreference=none
user.slice:
    ManagedOOMSwap=auto
    ManagedOOMMemoryPressure=auto
    ManagedOOMPreference=none
```

**结论：systemd-oomd 虽然运行中，但 `ManagedOOM*=auto` 表示不主动 kill。** 要启用自动终止，必须显式设置：

```bash
systemctl set-property system.slice ManagedOOMSwap=kill ManagedOOMMemoryPressure=kill
```

或针对特定 slice/service 设置。**默认配置不会自动终止任何进程。**

**[观测结果] Docker 容器覆盖情况**：

- Docker 容器 cgroup 位于 `/sys/fs/cgroup/system.slice/docker-*.scope`。
- 这些 cgroup 有 `memory.pressure` PSI 数据可用（systemd-oomd 可监控）。
- 如果设置 `systemctl set-property system.slice ManagedOOMMemoryPressure=kill`，则 system.slice 下所有容器都会被 systemd-oomd 监控。
- 但 **无法单独排除某个容器**（ManagedOOMPreference=omit 仅支持 user slice 级别，不能按容器设置）。

**[观测结果] 触发条件与动作逻辑**：

| 条件 | 动作 | 对象选择 |
| --- | --- | --- |
| 内存 PSI > 60% 持续 30s | kill 整个 cgroup | 选择 memory.pressure 最高的子 cgroup |
| Swap > 90% | kill 整个 cgroup | 选择 swap 使用量最大的子 cgroup |
| cgroup 超过 memory.max | 内核 OOM killer（非 systemd-oomd） | 内核自动选择 |

### 4.2 Monit 行为记录（G3-T03）

**[观测结果] 安装与运行**：

| 项目 | 值 |
| --- | --- |
| 版本 | 5.35.2 |
| 服务状态 | active (running) |
| 检查间隔 | 120 秒（可配置，最短 1 秒） |
| 默认监控项 | 无（空配置） |
| HTTP 接口 | 未启用 |

**[观测结果] 能力矩阵**：

| 能力 | 支持情况 | 说明 |
| --- | --- | --- |
| CPU 使用率监控 | ✅ | 按进程 PID 文件或进程名匹配 |
| 内存使用监控 | ✅ | 按进程，支持绝对值和百分比 |
| 磁盘/文件系统 | ✅ | 使用率、inode |
| 进程存在性 | ✅ | PID 文件或进程名 |
| 系统级（load/memory） | ✅ | 系统整体 loadaverage、memory |
| 动作：alert | ✅ | 邮件通知 |
| 动作：exec | ✅ | 执行任意 shell 命令 |
| 动作：restart/stop/start | ✅ | 通过 init 系统重启服务 |
| 容器自动发现 | ❌ | 需要显式定义每个要监控的服务 |
| 保护名单/排除规则 | ❌ | 只有 "if X then Y" 规则，无内置排除逻辑 |
| 审计日志 | ✅ | 自带日志，但无结构化审计 |
| 冷却/冷却周期 | ✅ | 可设置 cooldown |
| Docker 容器监控 | 间接 | 需通过 Docker CLI 包装脚本 |

**[观测结果] 适用边界**：

- **优点**：配置简单，规则明确，适合对已知服务设置固定检查和动作。
- **限制**：无法自动发现新容器/进程；无法按业务重要性设置保护名单；动作执行依赖 shell 命令，缺少内置回滚和审计。

### 4.3 只读压力观测与对象定位（G3-T01）

**[观测结果]**（复用 EXP-002 的压力场景验证）：

在 EXP-002 S1（CPU 无限制）压测期间，可通过以下方式观测和定位压力源：

| 观测方法 | 命令 | 输出 |
| --- | --- | --- |
| 系统 PSI | `cat /proc/pressure/memory` | some avg10=X avg60=X avg300=X |
| 容器 cgroup PSI | `cat /sys/fs/cgroup/system.slice/docker-*.scope/memory.pressure` | 容器级 PSI |
| Docker stats | `docker stats --no-stream` | 容器 CPU/内存 |
| Beszel | 数据库 `system_stats` / `container_stats` 表 | 历史数据 |
| 压力源定位 | `docker ps` + `docker inspect` | 定位到具体容器 |

以上方法在 EXP-002 中全部验证通过，可在 10 秒内定位到具体异常容器。

### 4.4 模拟动作验证（G3-T04）

**[观测结果]**（未执行真实 kill/重启，仅确认机制）：

| 工具 | 模拟结果 |
| --- | --- |
| systemd-oomd | 确认 `ManagedOOM*=auto` 下不会 kill。若改为 `kill`，将在内存 PSI > 60% 持续 30s 后终止 cgroup 内所有进程。**未在本次实验中启用**。 |
| Docker --memory | EXP-002 S4 已验证：cgroup OOM killer 在容器内存超限时 kill 容器内进程（`OOMKilled=true`），宿主机不受影响。**这是最可靠的自动保护机制**。 |
| Monit | 确认可通过 `if memory > 80% then exec "/usr/bin/docker stop $NAME"` 实现有界动作。**未在本次实验中配置**。 |

### 4.5 缺口分析（G3-T05）

**[工程判断]** 对照业务要求"近期资源增长 + 业务保护名单 + 允许动作"：

| 业务要求 | systemd-oomd | Docker 限制 | Monit | 缺口 |
| --- | --- | --- | --- | --- |
| 观测资源增长 | ✅ PSI | ✅ cgroup stats | ✅ 按进程 | 无缺口 |
| 定位压力源 | ✅ 按 cgroup | ✅ 按 container | ⚠️ 需预定义 | 无缺口 |
| 保护名单（不可终止的服务） | ❌ **无内置排除** | ⚠️ 不设置 kill 即安全 | ⚠️ 需显式排除 | **缺口 1** |
| 允许动作（按业务重要性分级） | ❌ 只有 kill | ⚠️ 只有 OOM kill | ⚠️ 需自定义脚本 | **缺口 2** |
| 动作前审计/快照 | ❌ | ❌ | ⚠️ 日志 | **缺口 3** |
| 动作后恢复验证 | ❌ | ❌ | ⚠️ 需自定义 | **缺口 4** |
| 冷却/防反复触发 | ✅ duration | N/A | ✅ cooldown | 部分覆盖 |

**缺口清单（Guardian 最小开发范围）**：

1. **保护名单管理**：一个配置文件列出"不可自动终止"的容器/cgroup/服务名；Guardian 在执行任何动作前检查该名单。
2. **动作分级**：不只 kill，还应支持"先通知 → 等待 → 限流 → 终止"的分级动作链。
3. **动作前快照**：在执行 kill 前保存目标进程的 `/proc/<pid>/{status,cmdline,stack}` 和 `docker inspect` 输出到本地文件。
4. **动作后恢复验证**：kill 后检查业务健康端点或服务状态，将结果记录到审计日志。

## 5. 结论

### 验收状态：PASSED（本地 WSL2 PoC 范围）

### 支持的结论

1. **[观测结果]** systemd-oomd 在默认配置下不会自动终止任何进程（ManagedOOM=auto）。需显式配置才生效。
2. **[观测结果]** Docker 容器 cgroup 内存限制（--memory）配合内核 cgroup OOM killer 是最可靠的自动保护机制（EXP-002 S4 已验证）。
3. **[观测结果]** Monit 适合对已知服务设置固定规则，但不支持容器自动发现和保护名单。
4. **[工程判断]** 现成工具（systemd-oomd + Docker 限制 + Monit）覆盖了"观测 → 定位 → 自动终止"的基本链路，但在**保护名单管理、动作分级、动作前取证和动作后恢复验证**方面存在明确缺口。
5. **[工程判断]** Guardian 的最小开发范围是：保护名单 + 动作分级 + 动作前快照 + 动作后验证。不需要重写监控或 OOM kill 逻辑。

### 尚不能证明的内容

- systemd-oomd 在 Ubuntu 22.04（systemd 249）下的行为差异（cgroup v2 支持可能不完整）。
- 多容器同时高压时的 systemd-oomd 选择行为。
- Monit 对 Docker 容器的间接监控在实际生产环境中的可靠性。

## 6. 更新记录

- 2026-09-18：创建并完成实验。systemd-oomd 行为、Monit 能力、缺口分析已记录。状态 PASSED。
