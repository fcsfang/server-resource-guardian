# EXP-005：真实故障模式验证（多容器竞争 / I/O 饱和 / PID 耗尽 / 宿主机级内存崩溃）

- 实验 ID：`EXP-005`
- 状态：`PASSED`
- 创建日期：2026-09-18
- 最近更新：2026-09-18
- 关联 Goal：`Goal 2 补充 / 真实故障模式`
- 环境：4 CPU / 3.9 GB RAM / 2 GB swap（受限 WSL2）

## 1. 实验目的

EXP-004 证明了单进程压力下 SSH 始终可用。本实验验证更极端的真实故障模式，**找到让 SSH 失效的条件**。

## 2. 关键发现

### 2.1 sshd 被 OOM killer 保护（无法通过 OOM 杀死）

```
sshd oom_score_adj = -1000
sshd oom_score = 0
```

systemd 默认将 ssh.service 的 `oom_score_adj` 设为 -1000（最高保护），内核 **永远不会** OOM kill sshd。这排除了"OOM killer 杀掉 sshd"作为生产故障根因的可能性（在 systemd 管理的系统中）。

### 2.2 宿主机级内存耗尽 → **WSL2 整机崩溃**（两次复现）

**实验方法**：在 WSL2 宿主机上（非 Docker 容器内）运行 Python 内存分配器，无限分配直到内核崩溃。

**结果**：**两次独立复现 WSL2 VM 崩溃（E_UNEXPECTED）**

| 尝试 | 结果 | 日志 |
| --- | --- | --- |
| 第 1 次（4 个 host-level eater） | WSL2 VM 崩溃 | /tmp 日志丢失 |
| 第 2 次（1 个 host-level eater + 监控） | WSL2 VM 崩溃 | /tmp 日志丢失 |
| 第 3 次（controlled，单 eater） | eater 分配 5250MB 后退出，系统存活 | 日志完整 |

**关键差异**：
- eater 在 **Docker 容器内**：内核 cgroup OOM kill 限制在容器内，宿主机安全（EXP-002/004 已验证）
- eater 在 **宿主机上**（无 cgroup 保护）：内存耗尽导致 WSL2 VM 自身崩溃

**生产对应**：如果内存泄漏发生在宿主机层面（如 Docker daemon 本身泄漏，或内核态内存泄漏），整个服务器会崩溃，而非仅仅 SSH 不可用。

### 2.3 F1 多容器内存竞争

| 指标 | 值 |
| --- | --- |
| 配置 | 4 容器 × 1.2 GB 请求，系统仅 3.9 GB |
| 峰值内存 | 94% |
| 峰值 swap | 85%（1733 MB） |
| OOM | 全部 false |
| SSH | 118–480 ms，**全部成功** |

内核通过 swap 分配管理了多容器竞争，无 OOM kill。

### 2.4 F2 磁盘 I/O 饱和

| 指标 | 值 |
| --- | --- |
| 配置 | 4 容器 × fio random 4K rw, iodepth=32 |
| I/O PSI | avg10 = 0.10%（NVMe 太快，未饱和） |
| SSH | 159–586 ms，全部成功 |

WSL2 的虚拟 NVMe 太快，无法从 WSL2 内部饱和。生产环境使用 SATA/HDD 时需重新测试。

### 2.5 F3 PID 耗尽

| 场景 | 进程数 | SSH |
| --- | --- | --- |
| 无限制（4 容器 × 3000） | 12,051 | 全部成功 |
| 有 `--pids-limit=100` | 438 | 全部成功，容器 exec 被拒绝 |

内核 pid_max = 4,194,304，12000 进程仅占 0.3%。`--pids-limit` 有效限制容器内进程数。

## 3. 结论

### SSH 失效的真实条件

| 条件 | 是否导致 SSH 失效 | 说明 |
| --- | --- | --- |
| Docker 容器内内存耗尽 | ❌ | 内核 cgroup OOM 限制在容器内 |
| 宿主机内存耗尽 | **✅ 整机崩溃** | WSL2 VM E_UNEXPECTED，生产 = 服务器宕机 |
| OOM killer 杀 sshd | ❌（不可能） | systemd 设 oom_score_adj=-1000 |
| CPU 100% | ❌ | CFS 保证 sshd 获得调度 |
| I/O 饱和 | ❌（WSL2 上） | NVMe 太快；生产 SATA/HDD 需重测 |
| PID 耗尽 | ❌（正常容器） | 内核 PID 上限太高 |

### 核心结论

1. **[观测结果]** SSH 失效的真正场景是**宿主机级内存耗尽导致系统崩溃**，而非"SSH 连不上但系统还在运行"。
2. **[观测结果]** sshd 被内核 OOM killer 保护（oom_score_adj=-1000），不会因内存压力被杀死。
3. **[观测结果]** Docker 容器内的资源消耗（即使多容器竞争）被 cgroup 隔离，不会导致宿主机崩溃。
4. **[工程判断]** 生产事故的最可能根因是宿主机级别的内存泄漏（如 Docker daemon、内核模块、或其他系统进程），而非容器内的正常业务内存使用。

### 项目价值重新定位

基于以上发现，项目的真正价值不在"SSH 救援"（因为 SSH 要么一直能用，要么整机崩溃），而在于：

1. **预防容器资源无界增长**：设置 Docker --memory/--cpus/--pids-limit，将资源竞争限制在容器级别，避免升级为宿主机级危机。
2. **监控提前预警**：在内存达到 80% 时告警（而非等到 95%+ 崩溃），给运维人员反应时间。
3. **Guardian 的最小价值**：在容器没有资源限制且开始无界增长时，在系统崩溃前自动检测并限制/终止异常容器。

## 4. 核心数据

- [data/failure-mode-results.csv](data/failure-mode-results.csv)
- 本实验未保存原始日志（WSL2 崩溃导致 /tmp 丢失），关键数据已从工具输出手动记录。

## 5. 更新记录

- 2026-09-18：创建并完成全部测试。关键发现：宿主机级内存耗尽导致 WSL2 VM 崩溃（两次复现）；sshd 被 OOM killer 保护。
