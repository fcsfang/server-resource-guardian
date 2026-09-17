# 部署目录

- `beszel/`：WSL2 隔离 PoC 使用的 Beszel Hub/Agent Compose 配置，固定版本且默认只绑定回环地址。
- 后续存放 Guardian 的 systemd unit、独立 slice、日志轮转和升级回滚配置。

这里的 Beszel 配置仅供本地功能验证，不是生产部署文件。生产网络、身份认证、数据持久化、备份、最小权限和版本升级策略仍需单独设计。
