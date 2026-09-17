# 生产环境基线与风险分析

分析日期：2026-09-16  
数据来源：本地忽略目录中的 `reports/production-environment.txt`

本文只保留技术基线和聚合统计，不记录生产主机名、容器名、镜像名、IP 或其他资产标识。

## 1. 已确认基线

| 项目 | 生产环境 |
| --- | --- |
| 操作系统 | Ubuntu 22.04.5 LTS (Jammy), x86_64 |
| 内核 | 当前运行 6.8.0-40-generic；系统另有更新的 6.8 HWE 内核已安装 |
| systemd | 249.11 |
| CPU | 32 个逻辑处理器 |
| 内存 | 约 31 GiB |
| swap | 2 GiB 文件，`vm.swappiness=60` |
| 根文件系统 | ext4，NVMe，约 1.9 TiB |
| cgroup | 统一 cgroup v2，systemd driver |
| PSI | CPU、内存和 I/O PSI 均可用 |
| Docker Engine | 29.1.3，Ubuntu `docker.io` 包 |
| containerd / runc | containerd 2.2.1，runc 1.3.4 |
| Docker 存储 | overlayfs，containerd snapshotter |
| Docker Compose | `docker compose` 插件未安装或不可用 |
| 容器规模 | 68 个容器，其中 55 个运行 |
| systemd-oomd | 已启用并运行 |

## 2. 关键风险

### R1：绝大多数容器没有资源边界（高）

聚合检查结果：

- 68 个容器中只有 1 个设置了内存限制、内存保留、CPU 配额和 PID 限制。
- 其余 67 个容器没有 CPU、内存或 PID 限制。
- 大量容器使用 `unless-stopped`，但重启策略不会防止资源耗尽。

这与历史问题高度吻合：任一失控容器或大量副本同时增长，都可以竞争宿主机全部 CPU、内存和 PID。仅在事后寻找并杀进程不足以解决根因。

### R2：systemd-oomd 没有覆盖 Docker 工作负载（高）

虽然 systemd-oomd 正在运行，但 `oomctl` 显示当前监控对象是若干 `user.slice` 会话，没有列出 Docker 所在的 `system.slice/docker-*.scope` 或对应业务 slice，也没有显示 swap 监控 cgroup。

因此当前状态不能推导为“内存耗尽前 systemd-oomd 会处理 Docker”。真实结果更可能是到达容器或宿主内核 OOM 路径后才杀进程，受害者选择不可作为业务恢复策略。

### R3：Beszel Agent 未在本机按常见方式出现（中）

报告未发现：

- `beszel-agent.service`。
- 默认 45876/8090 监听。
- 名称或镜像明显包含 Beszel 的容器。
- PATH 中的 `beszel-agent` 二进制或 Beszel 软件包。

可能原因包括 Agent 使用非标准服务名/路径、由其他管理方式启动、通过另一台主机采集，或这台生产机尚未纳入 Beszel。需要运行专项只读检查确认。

### R4：当前报告不能证明过去没有 OOM（中）

当前启动周期的受限 `dmesg` 查询没有发现 OOM 记录，只说明本次可访问日志中没有匹配项。它不能替代历史 journal、Beszel 趋势或事故记录。

### R5：运行内核落后于已安装内核（低，需运维确认）

报告显示系统已安装更高版本的 6.8 HWE 内核，但仍运行 6.8.0-40。可能是尚未安排重启，也可能有兼容性冻结策略。此项不由本项目自动修改，应由运维确认补丁策略。

## 3. 直接建议

1. 不在生产环境直接启用自动杀进程。
2. 先按业务重要性和实际峰值，为容器设计 `memory`、`memory-reservation`、CPU 和 PID 边界。
3. 先选一个无状态、可重建的低风险容器做灰度，而不是一次性修改 67 个容器。
4. 使用 Beszel 观察限制前后的峰值、告警和业务健康。
5. 在 WSL2 中验证 cgroup v2、systemd-oomd、容器限制和只读快照流程。
6. 在受控 Linux 测试机复核 OOM 和调度行为后，才讨论生产自动处置。

## 4. WSL2 映射

| 生产能力 | 本地复现策略 | 差异 |
| --- | --- | --- |
| Ubuntu 22.04 | 当前 Store 仅可安装 Ubuntu 26.04.1 WSL2 | 用户空间和 systemd 259 与生产 systemd 249 有明显差异，必须在 22.04 测试机复核 |
| Linux 6.8 generic | 使用 Microsoft WSL2 内核 | 无法完全复现生产内核与驱动 |
| systemd 249 | Ubuntu 22.04 启用 systemd | 可复现 unit 与 cgroup 组织 |
| cgroup v2 + systemd driver | WSL 内安装 Docker Engine | 不使用 Docker Desktop daemon |
| 32 CPU / 31 GiB | WSL 已分配 8 CPU / 12 GB | 用较小 cgroup 限额复现逻辑，不复现容量 |
| 2 GiB swap | WSL 配置 4 GB swap | WSL swap 实现与原生 Linux 有差异 |
| 68 个容器 | 只构建少量代表性容器 | 不复制生产数据、凭据和内部镜像 |
| Beszel | 独立测试 Hub + Agent | 版本需专项检查后固定 |

## 5. PoC 优先级

1. **P0**：验证一个无限制测试容器能否拖慢宿主交互和 Beszel 指标。
2. **P0**：为同一容器添加 CPU、内存和 PID 限制，确认故障被限制在容器内。
3. **P0**：验证 PSI、Docker OOM 事件和 Beszel 告警时间线。
4. **P1**：验证受保护的只读快照服务在压力下继续工作。
5. **P1**：评估 systemd-oomd 对专用测试 slice 的行为，不直接指向全部 Docker scope。
6. **P2**：只有人工 runbook 仍无法满足恢复目标时，再实现受控处置服务。
