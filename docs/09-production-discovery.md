# 生产环境信息采集与 WSL2 复现

## 1. 已确认信息

- 公司当前使用 Beszel 监控。
- 生产服务器很可能运行 Docker，仍需报告确认。
- 本地测试机是 Windows，已安装 Ubuntu 26.04.1 WSL2，用于构建功能验证环境。
- 本地只能通过 Microsoft Store 获得 Ubuntu 26.04；它不是生产 Ubuntu 22.04 的等价环境，最终兼容性结论仍需在获批的 Ubuntu 22.04 测试机复核。
- 暂时没有历史故障指标或日志；PoC 将通过受控故障注入建立基线。
- 生产报告已采集并分析，技术基线和聚合风险见 [生产环境基线与风险分析](10-production-baseline.md)。

## 2. Beszel 对本项目的影响

Beszel 已覆盖主机 CPU、内存、swap、磁盘、I/O、网络、load、温度、Docker/Podman 容器统计、systemd 服务概览、历史数据和资源告警，因此不再建议为本项目新建 Prometheus/Grafana 主干。

Beszel Hub 与 Agent 支持两种通信方式：

- SSH：Hub 主动连接 Agent，默认端口 45876。
- WebSocket：Agent 主动连接 Hub，适合 Agent 可以访问 Hub、但 Hub 不方便主动访问 Agent 的环境。

Beszel 的 SSH 服务只用于指标通信，不提供伪终端，也不接受输入，即使密钥泄露也不能通过它执行命令。这是安全优点，同时意味着 Beszel 不能完成“SSH 失效时远程杀进程或重启容器”的处置需求。

因此当前推荐架构调整为：

```text
Beszel Agent -> Beszel Hub -> 现有告警渠道
     |
     +-> 主机、Docker、systemd 指标与历史数据

独立只读快照服务（PoC 第一阶段）
独立受控处置服务（仅在证明确有缺口后开发）
```

优先验证 Beszel 当前版本和部署方式能否满足 systemd 服务监控。官方资料指出，Docker 方式的 Agent 需要只读挂载 system D-Bus socket；systemd 服务枚举的完整兼容能力要求 systemd 243 或更高版本。不应为了方便直接赋予 Agent `privileged`，官方也只将其作为排障的最后手段。

## 3. 生产采集脚本

脚本位置：[collect-production-environment.sh](../scripts/collect-production-environment.sh)

脚本只执行查询操作。它不会读取或输出：

- Beszel `KEY`、`TOKEN` 或其他环境变量。
- 容器完整 inspect 数据或容器环境变量。
- 应用配置、业务数据和应用日志。
- SSH 私钥、Docker registry 凭据或 shell 历史。

报告仍可能包含主机名、容器名、镜像名、监听端口和默认路由，带回本机后不要上传到公共仓库。

## 4. 在生产服务器执行

先从 Windows PowerShell 上传脚本，其中 `<user>` 和 `<server>` 替换为实际 SSH 用户和服务器地址：

```powershell
scp "D:\Project_Codex\server-resource-guardian\scripts\collect-production-environment.sh" <user>@<server>:/tmp/
ssh <user>@<server>
```

登录服务器后，先人工查看脚本，再执行：

```bash
sed -n '1,260p' /tmp/collect-production-environment.sh
sudo bash /tmp/collect-production-environment.sh | tee ~/production-environment.txt
```

执行过程中出现个别 `command exited` 或权限提示不会修改系统，脚本会继续收集其余信息。执行完退出服务器：

```bash
exit
```

回到 Windows PowerShell，将报告下载到项目的忽略目录：

```powershell
scp <user>@<server>:~/production-environment.txt "D:\Project_Codex\server-resource-guardian\reports\"
```

将文件放到 `reports/` 后通知我继续分析即可。

## 5. 为什么采集脚本能补足未知项

用户目前无法直接回答 cgroup 和历史故障问题，其中 cgroup 情况可由脚本自动识别：

- `/sys/fs/cgroup/cgroup.controllers` 存在通常表示 cgroup v2。
- `findmnt`、挂载信息和 Docker info 会进一步确认 cgroup 版本与 driver。
- `/proc/pressure/*` 确认 PSI 是否可用。
- systemd 与内核版本决定 systemd-oomd、资源控制和 Beszel systemd 监控能力。
- swap 与相关 sysctl 决定内存压力演练的前提。

没有历史故障资料不会阻塞本地 PoC，但无法据此制定生产阈值。第一轮告警阈值只能用于实验，之后必须用生产基线逐步校准。

## 6. WSL2 复现边界

拿到报告后按以下顺序构建：

1. 安装与生产相同或最接近的 WSL2 Linux 发行版；当前实际使用 Ubuntu 26.04，并记录版本偏差。
2. 启用 systemd，并确认 cgroup 版本。
3. 安装与生产相近的 Docker Engine/Compose 或使用明确记录版本的替代环境；本地已验证 Docker Engine 29.1.3、Compose 2.40.3、systemd cgroup driver 和 overlayfs。
4. 部署相同版本和模式的 Beszel Agent，并在本地部署隔离的测试 Hub 或连接专用测试 Hub。
5. 创建受限测试容器和 systemd 服务。
6. 运行 CPU、内存和 I/O 故障注入，验证 Beszel 告警和只读快照。

WSL2 可以复现 Linux 用户空间、systemd、cgroup 和 Docker 的大部分行为，但不能精确复现生产物理机/虚拟机内核、磁盘、网络调度、BMC 或真实容量。因此：

- 功能开发与安全规则测试可在 WSL2 完成。
- 阈值、Agent 抗压能力、OOM 受害者选择和性能结论必须再到获批的 Linux 测试机验证。
- 不把 WSL2 测试结果直接等同于生产容量结论。

## 7. Beszel 官方资料

- [What is Beszel](https://beszel.dev/guide/what-is-beszel)
- [Agent Installation](https://beszel.dev/guide/agent-installation)
- [Security](https://beszel.dev/guide/security)
- [Systemd Services](https://beszel.dev/guide/systemd)
- [Healthchecks](https://beszel.dev/guide/healthchecks)

## 8. 本地 WSL2 规划

当前 Windows 主机有 16 个逻辑处理器和约 31.5 GB 内存，不能一比一复制生产的 32 个逻辑处理器。项目提供以下本地文件：

- [`wsl/.wslconfig.example`](../wsl/.wslconfig.example)：为 WSL2 分配 8 CPU、12 GB 内存和 4 GB swap。
- [`wsl/wsl.conf`](../wsl/wsl.conf)：在 Ubuntu WSL 中启用 systemd。
- [`setup-wsl-ubuntu.sh`](../scripts/setup-wsl-ubuntu.sh)：只允许在 Ubuntu 22.04 或 26.04 WSL2 中运行，安装 Docker Engine、Compose v2（仓库可用时）、systemd-oomd 和压测工具；26.04 会输出兼容性警告。
- [`inspect-beszel-deployment.sh`](../scripts/inspect-beszel-deployment.sh)：生产侧只读确认 Beszel 的非标准部署方式。

本地 WSL2 已按 8 CPU、12 GB 内存和 4 GB swap 配置。实测为 Ubuntu 26.04.1、WSL2 内核 6.18、systemd 259、cgroup v2，CPU/内存/I/O PSI 均可用；生产基线仍为 Ubuntu 22.04.5、内核 6.8 和 systemd 249。
