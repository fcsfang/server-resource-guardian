# Mac Multipass 本地 Beszel 部署记录

更新时间：2026-09-19

## 1. 范围与状态

本记录描述 Mac Apple Silicon 上 `guardian-ubuntu` Multipass 虚拟机中的本地 Beszel Hub/Agent 部署。它只用于项目本地观测与后续 Guardian 联调，不连接生产环境，不包含任何 Token、Key、管理员密码或指标数据。

当前状态：**Hub/Agent 已部署，认证连接已验证；登录态主机、容器和历史曲线 UI 已核验，静态字段基线和有界动态运行态已补充；systemd 服务数值、实际刷新延迟和真实告警仍待专项核验。**

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

1. **G6-T01**：依据 [字段清单](22-beszel-dashboard-field-inventory.md) 补齐 systemd 服务表具体值、实际采集/刷新延迟和经授权的告警触发对照；登录态主机、容器、历史曲线和告警类别证据见 EXP-023，底层运行态和有界动态探针见 EXP-025。
2. 按 [`Goal 6`](../goals/resource-protection.md#goal-6beszel-二次开发集成) 和 [`docs/16`](16-autonomous-execution-roadmap.md) 完成事件契约、只读 Adapter 和双路径对照。
3. 不要把 Beszel 告警直接等同于自动处置授权；`observe/simulate` 证据完成前不进入 `enforce`。
4. 生产部署必须另行设计网络、认证、密钥管理、最小权限、备份和升级回滚方案。
