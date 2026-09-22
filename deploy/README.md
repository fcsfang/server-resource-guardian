# 部署目录

- `beszel/`：本地 WSL2/Multipass 隔离 PoC 使用的 Beszel Hub/Agent Compose 配置，固定版本。当前为便于宿主机完成 Multipass 首次初始化，Hub 绑定 VM 私有网卡的 8090 端口；它不是生产部署目录，也不应直接暴露到公网。
- `beszel/docker-compose.isolated.yml`：里程碑一告警演示专用的第二套本地 Beszel，使用 8091、独立容器名和独立数据卷；默认不含邮件、Webhook 或其他通知目的地。
- `guardian/`：Guardian 本地 MVP 的 systemd unit、独立 slice、readiness/watchdog 和资源边界模板；通过 `scripts/install-guardian-local.sh` 在显式 `local-disposable` 标记下安装，默认不安装、不启用。
- `guardian-x86/`：Ubuntu 22.04 x86_64 的只观察部署单元；通过 `scripts/install-guardian-x86.sh` 安装、升级或回滚，不使用本地虚拟机的固定 CPU 编号，不开放自动动作。

这里的 Beszel 配置仅供本地功能验证，不是生产部署文件。未来如果出现生产部署定义，应在 `deploy/production/` 下单独维护；生产网络、身份认证、数据持久化、备份、最小权限和版本升级策略仍需单独设计。

告警演示使用隔离配置时，先只启动 Hub 完成首次初始化，再由 UI 生成一次性 Agent 注册信息，最后通过环境变量启动 `agent` profile。不要把 Token、Key 或登录凭据写入仓库。
