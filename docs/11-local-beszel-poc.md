# 本地 Beszel PoC 部署

## 1. 目的与边界

本部署只用于 WSL2 内的隔离功能 PoC，验证 Beszel 对 WSL 主机、Docker 容器和 systemd 服务的可见性。它不连接生产 Hub，不使用生产凭据，也不启用任何自动处置动作。

当前固定版本为 Beszel `0.19.0`。Hub 只绑定 Windows/WSL 回环地址 `127.0.0.1:8090`，Agent 通过共享 Unix socket 与本机 Hub 通信，不开放 Agent TCP 端口。

Agent 按官方容器部署方式只读挂载 Docker socket。文件系统只读挂载不限制 Unix socket 可接受的 API 操作，因此 Docker socket 仍是高权限接口；这里只在隔离 PoC 中使用，生产部署前必须单独评审二进制 Agent、socket proxy 或其他最小权限方案。

## 当前验收状态（2026-09-16）

- Hub 与 Agent 容器均为 `healthy`，Hub `/api/health` 返回 200。
- Agent 已通过 WebSocket 完成认证；系统保存前出现的 401 在保存后停止。
- 空载观测时 Hub 约使用 13.3 MiB 内存，Agent 约使用 6.5 MiB，CPU 接近 0%。该数据仅为本地空载基线，不代表生产开销。
- 尚待在页面确认主机、Docker、systemd 指标完整性，并在受控负载下重新测量开销。

## 2. 启动 Hub

在 Ubuntu WSL 中执行：

```bash
cd /mnt/d/Project_Codex/server-resource-guardian/deploy/beszel
docker compose up -d beszel
docker compose ps
```

浏览器打开 <http://localhost:8090>，创建本地管理员账户。不要复用公司或生产密码。

## 3. 添加本地系统

在 Hub 中点击 **Add System**，使用以下信息：

| 字段 | 值 |
| --- | --- |
| Name | `wsl-lab` |
| Host / IP | `/beszel_socket/beszel.sock` |

不要关闭对话框，也不要先点击底部最终的 **Add System**。从对话框提供的 Agent Compose 配置中取得当前生成的 `TOKEN` 和公钥 `KEY`。在 WSL 中创建仅供本地使用的环境文件：

```bash
cd /mnt/d/Project_Codex/server-resource-guardian/deploy/beszel
cp .env.example .env
chmod 600 .env
```

编辑 `.env`，替换两个占位值。`.env` 已被项目 `.gitignore` 忽略，不得把真实值写入 Compose、文档或提交记录。对话框关闭后，尚未登记的临时 Token 可能失效；若已经关闭，应重新打开对话框并同步新生成的值。

## 4. 启动 Agent

```bash
docker compose --profile agent up -d
docker compose --profile agent ps
docker compose logs --tail=100 beszel-agent
```

Agent 启动后，立即回到仍然打开的对话框，点击底部最终的 **Add System**。系统记录保存前，Agent 可能暂时收到 `401`；保存后应在下一次重试（通常 10 秒内）通过认证。确认 `wsl-lab` 变为在线，再等待至少两个采集周期后检查：

- 主机 CPU、内存、swap、磁盘、网络和 load 有数据。
- `hello-world` 等 Docker 容器历史能够显示。
- systemd 服务列表包含 `docker` 和 `systemd-oomd`。Beszel 展示时会去掉 `.service` 后缀，它们分别对应宿主机的 `docker.service` 和 `systemd-oomd.service`。
- Agent 日志没有 Docker socket、D-Bus 或权限错误。

如果 systemd 服务不可见，先检查日志和 D-Bus 挂载；不要直接添加 `privileged: true`。只有日志明确显示 AppArmor 拒绝时，才在隔离环境评估官方建议的 `apparmor:unconfined`，并记录原因。

## 5. 验证与停止

```bash
curl --fail --silent http://127.0.0.1:8090/api/health
docker compose --profile agent ps
docker stats --no-stream beszel-poc beszel-agent-poc
```

停止容器但保留本地数据：

```bash
docker compose --profile agent down
```

只有确定要清空本地 PoC 账户、指标和 Agent 数据时才删除卷：

```bash
docker compose --profile agent down --volumes
```

## 6. 版本与生产差异

- 本地：Ubuntu 26.04.1、systemd 259、WSL2 内核 6.18、Docker 29.1.3。
- 生产：Ubuntu 22.04.5、systemd 249、Linux 6.8、Docker 29.1.3。
- Docker 主版本一致，但本地 systemd 和内核更高；systemd 单元兼容性、OOM 行为、资源阈值和性能结论必须在 Ubuntu 22.04 非生产测试机复核。
