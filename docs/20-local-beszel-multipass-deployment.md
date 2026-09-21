# Mac Multipass 本地 Beszel 部署记录

更新时间：2026-09-20

## 1. 范围与状态

本记录描述 Mac Apple Silicon 上 `guardian-ubuntu` Multipass 虚拟机中的本地 Beszel Hub/Agent 部署。它只用于项目本地观测与后续 Guardian 联调，不连接生产环境，不包含任何 Token、Key、管理员密码或指标数据。

当前状态：**Hub/Agent 已部署，认证连接已验证；主机、容器、历史和告警字段已经核对，Guardian 只读桥接已经实现。CPU、内存和磁盘告警规则已配置，隔离通知环境中的统一触发/恢复演示尚未完成。**

## 2. 运行环境

| 项目 | 实际值 |
| --- | --- |
| 虚拟机 | `guardian-ubuntu` |
| 地址 | `192.168.252.2`（Multipass 私有网卡，可能随重建变化） |
| OS | Ubuntu 22.04.5 LTS ARM64 |
| 资源 | 2 vCPU / 约 4 GiB 内存 / 约 40 GiB 虚拟磁盘上限 |
| Docker | 29.1.3 |
| Docker Compose | 2.40.3 |
| Beszel Hub | `henrygd/beszel:0.19.0` |
| Beszel Agent | `henrygd/beszel-agent:0.19.0` |
| Compose 文件 | [`deploy/beszel/docker-compose.yml`](../deploy/beszel/docker-compose.yml) |

## 3. 部署方式

- Hub 使用持久卷保存本地数据和 Unix socket。
- Agent 使用 host network，通过 `/beszel_socket/beszel.sock` 与 Hub 通信。
- Agent 只读挂载 Docker socket 和 system D-Bus；这仍属于高权限本地 PoC 配置，生产部署前必须重新评审。
- 由于宿主机浏览器需要完成首次初始化，当前本地 compose 将 Hub 的 8090 端口绑定到 VM 私有网络接口；这不是生产暴露配置。
- Docker Hub 在 VM 内直连不稳定，本次通过宿主机拉取固定版本镜像后离线导入 VM；不代表生产环境的镜像分发方案。

## 4. 验证证据

| 验证项 | 结果 |
| --- | --- |
| Hub `/api/health` | `200`，返回 `API is healthy` |
| Hub 容器 | `healthy` |
| Agent 容器 | `healthy` |
| 初次保存系统前 | Agent 按预期收到 WebSocket `401` |
| 用户点击 Add System 后 | Agent 日志出现 `WebSocket connected host=localhost:8090` |
| 连接后短窗口 | 未出现新的 401、error 或 warn |
| 凭据文件 | VM 内 `.env`，权限 `600`；未写入仓库 |

## 5. 下一步

1. 按 [里程碑一](../ROADMAP.md#里程碑一看得见进得去) 在无外部通知的隔离 Beszel 中完成 CPU、内存和磁盘告警触发/恢复演示。
2. 按 [`docs/21`](21-beszel-guardian-event-contract.md) 和根目录 [`ROADMAP.md`](../ROADMAP.md) 保持 Beszel 观测与 Guardian 决策解耦；Beszel 不直接触发动作。
3. 不要把 Beszel 告警直接等同于自动处置授权；里程碑三完成前不进入自动处置。
4. 生产部署必须另行设计网络、认证、密钥管理、最小权限、备份和升级回滚方案。
