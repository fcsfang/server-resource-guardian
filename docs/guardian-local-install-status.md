# Guardian 本地安装验收

更新时间：2026-09-21

## 已验证功能

在本地 disposable Ubuntu 22.04 VM `guardian-t11-matrix` 上，使用当前工作区代码完成：

1. 默认 dry-run 安装计划，未修改系统。
2. 首次安装：创建或复用运行账户和组，安装代码、配置、systemd unit、资源 slice、tmpfiles 和 journald drop-in。
3. 安装后 runtime 为 `active`，开机启用，readiness 为 `runtime:ready:observe`。
4. 重复执行安装，仍能成功启动，并为已管理文件生成备份目录。
5. 重启 VM 后，runtime 自动恢复为 `active/enabled`，readiness 恢复为 `runtime:ready:observe`。
6. 代码安装目录为 `root:root`；配置为 `root:guardian-shared`、`0640`。
7. `/etc/guardian/broker.enabled` 不存在，`/run/guardian-broker/broker.sock` 不存在，Broker 未启用。
8. 安装提供只读命令 `sudo guardian-status`，现场输出包含 Runtime 在线状态、`observe` 模式、自动动作关闭、最近本地风险状态和 Broker 关闭边界。

## 安全边界

- 应用安装必须显式带 `--apply --environment local-disposable`；默认只输出计划。
- 配置不是 `observe` 或自动动作不是关闭状态时，安装会失败并停止。
- 发现 Broker 已授权或正在运行时，安装会拒绝覆盖。
- 这次验收只证明本地 Ubuntu 安装、开机恢复和默认关闭动作，不证明生产机、SSH 抗压或非生产服务器可用性。
- 本次没有连接生产服务器，没有开启 Broker，没有停止或重启任何业务容器。

`guardian-status` 的 `Recent local risk` 来自 Guardian 本地审计；Beszel 告警是否触发、恢复和通知仍以 Beszel 页面/通知渠道为准，命令不会修改任何系统状态。
