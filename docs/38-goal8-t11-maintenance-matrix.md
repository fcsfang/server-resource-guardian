# G8-T11 Rescue Plane 合并维护矩阵证据

更新时间：2026-09-21  
关联路线：[`docs/37`](37-goal8-runtime-functionalization-roadmap.md) v1.1  
状态：`PASSED_LOCAL_DISPOSABLE`

## 范围与环境

本次使用从 Docker 健康的 `guardian-ubuntu` 克隆出的独立 `guardian-t11-matrix`，不连接生产环境。VM 为 Ubuntu 22.04.5、2 vCPU、约 3.8 GiB 内存；Docker/containerd active，已有两个本地 Beszel 容器仅作只读控制面对象。

在该 VM 中安装当前 Runtime、`rescue.slice`、`workload.slice`、Runtime slice 和 tmpfiles，保持 observe 模式。Broker marker 和 Unix socket 全程不存在。

## 统一矩阵

每个压力域顺序执行四类固定只读探针各 20 次：Multipass 管理 session、只读诊断、`docker ps`、Guardian `observe --once`。P95 门限为：SSH `3000ms`、诊断 `5000ms`、Docker `5000ms`、Guardian `10000ms`。

| 压力域 | SSH 20/20 P95 | 诊断 20/20 P95 | Docker 20/20 P95 | Guardian 20/20 P95 |
| --- | ---: | ---: | ---: | ---: |
| CPU | 74.034ms | 78.048ms | 88.592ms | 1840.894ms |
| 内存 | 111.720ms | 105.510ms | 128.230ms | 2172.454ms |
| I/O | 86.828ms | 94.839ms | 100.547ms | 1885.164ms |
| 容量/inode | 161.439ms | 84.989ms | 91.789ms | 1886.235ms |
| PID/Tasks | 143.870ms | 80.993ms | 85.686ms | 1890.868ms |

矩阵评估器输出：`guardian.rescue.maintenance-matrix.v1` / `PASSED`，原因码为空；所有压力域和探针均通过，未创建新的阈值 EXP。

容量/inode 场景使用明确的 loopback fixture：128 MiB ext4、约 94% blocks 已使用、约 86% inode 已使用；探针完成后卸载并删除了该 fixture，VM 根盘和业务文件未填充或删除。

## 安装、重启、停用和回滚

- 初次安装：`systemd-analyze verify` 通过，Runtime readiness 为 `runtime:ready:observe`，共享 `state.db` 为 `guardian:guardian-shared 0660`。
- VM 重启：Runtime、Docker、containerd 自动恢复；readiness 重新出现，重启后四类只读探针各 `5/5` 通过。
- 停用恢复：`guardian-runtime.service` disable/stop 后 readiness 消失，再 enable/start 后恢复；Docker、containerd、Beszel 未被停用。
- 快照回滚：恢复 `guardian-t11-matrix.guardian-t11-before-install-20260921` 后，Rescue Plane unit 和 Guardian 账户回到安装前状态；按同一 Runbook 重新安装并通过静态校验、readiness、权限和 Broker 关闭检查。
- 配置错误回滚：将 live Runtime unit 临时替换为非法 `[Service` section，`systemd-analyze verify` 返回 `1`；恢复已审查副本并 daemon-reload 后，live Runtime 保持 `active`、readiness 保持存在，坏配置没有被启动。
- 日志边界：安装并生效 `guardian-journald.conf`，当前 `SystemMaxUse=200M`、`RuntimeMaxUse=64M`、`RateLimitIntervalSec=30s`、`RateLimitBurst=200`；审计 logrotate 模板已安装并保留 7 份、单文件 50M 上限。

## 安全边界与限制

矩阵记录的动作计数为 0：没有 Docker stop/restart/kill，没有宿主 PID 操作，没有生产连接，没有自动删除业务文件。CPU、内存、I/O、PID workload 均有界并自然退出；容量 fixture 仅限明确路径。

该结果证明本地 disposable VM 上的维护链路在声明的有界压力下可进入、可诊断、可读取 Docker 控制面，并支持持久安装、重启和快照回滚；不证明生产 SSH 在任意资源耗尽下必然可用，也不替代带外控制台/BMC/云控制台、生产 x86_64 复核或真实根盘满盘验证。
