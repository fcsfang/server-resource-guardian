# EXP-007：Mac Multipass Ubuntu 测试环境基线

- 实验 ID：`EXP-007`
- 状态：`PASSED`
- 创建日期：2026-09-19
- 最近更新：2026-09-19
- 关联 Goal：`Goal 4 / G4-T01`（环境基线前置）
- 实验负责人：当前 Agent

## 1. 实验目的

确认 Mac Apple Silicon 上的 Multipass Ubuntu 是否具备 Guardian 后续功能实验所需的完整 Linux 主机能力，并明确它与历史 WSL2、生产 x86_64 环境的边界。

## 2. 授权与安全边界

- 测试环境：本地 Mac 上的 Multipass `guardian-ubuntu`，非生产环境。
- 测试对象：当前 Ubuntu 虚拟机基础系统和 Docker daemon。
- 保护对象：宿主机、生产环境、用户凭据和生产数据。
- 允许动作：创建和配置本地虚拟机、安装测试依赖、读取版本和运行时状态、复制项目文件。
- 停止条件：不得连接生产、不得注入未计划的故障、不得删除或重建已有虚拟机。
- 回滚方式：停止虚拟机；本实验未对生产或宿主机业务数据做变更。

## 3. 环境与初始状态

| 项目 | 值 |
| --- | --- |
| 宿主机 | Apple Silicon Mac |
| Multipass 实例 | `guardian-ubuntu`，QEMU 后端 |
| OS/版本 | Ubuntu 22.04.5 LTS |
| 架构 | `aarch64` / ARM64 |
| 资源 | 2 vCPU、4GB 内存、40GB 虚拟磁盘上限 |
| 内核 | `5.15.0-191-generic` |
| systemd | `249.11-0ubuntu3.22` |
| cgroup | cgroup v2，文件系统类型 `cgroup2fs` |
| Docker/运行时 | Docker Engine 29.1.3，overlayfs |
| Docker Compose | 2.40.3 |
| systemd-oomd | active |
| PSI | CPU、memory、I/O 均可读 |
| 项目路径 | `/home/ubuntu/server-resource-guardian` |

## 4. 实验步骤

1. 创建 Multipass Ubuntu 22.04 实例并设置 2 vCPU、4GB 内存和 40GB 虚拟磁盘上限。
2. 安装 Git、Docker、Docker Compose、`stress-ng`、`sysstat`、`jq`、OpenSSH 和 `systemd-oomd`。
3. 启用 Docker、SSH 和 `systemd-oomd`，将 Docker cgroup driver 设置为 systemd。
4. 检查 systemd、cgroup v2、Docker、Compose、PSI 和项目文件。
5. 尝试从 GitHub 和 Docker Hub 直接同步；由于虚拟机出网不稳定，改用宿主机文件传输复制项目。

## 5. 结果与核心数据

- systemd：`running`。
- cgroup：cgroup v2 可用。
- Docker：服务 `active`，cgroup driver 为 `systemd`。
- `systemd-oomd`：`active`。
- PSI：CPU、memory、I/O 文件均可读。
- Docker Hub：`hello-world` 镜像拉取因虚拟机网络超时失败；不能据此判定 Docker daemon 故障。
- GitHub：HTTPS 克隆因虚拟机 TLS/HTTP 连接不稳定失败；项目已通过宿主机传输复制，当前版本为 `b97d34f`。

## 6. 结论

- 验收状态：通过。
- 结论类型：观测结果 + 工程判断。
- 支持的结论：该虚拟机可以作为 Goal 4 的主功能和有界压力测试环境；完整 Ubuntu 主机能力、systemd、cgroup v2、Docker 和 PSI 均可用。
- 尚不能证明的内容：ARM64 虚拟机不能替代生产 x86_64 的最终兼容性和性能验证；虚拟机出网稳定性尚未解决。
- 后续任务：完成 G4-T01 风险信号定义，再实现 `observe` 模式。

## 7. 更新记录

- 2026-09-19：建立并完成本地 Mac Ubuntu 测试环境基线。
