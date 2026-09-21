# 里程碑一：本地整体验收

更新时间：2026-09-21

验收环境：本地 disposable Ubuntu 22.04 VM `guardian-t11-matrix`。未连接生产服务器。

## 用户可见结果

- Beszel Hub/Agent 健康运行，隔离 Hub API 返回健康状态。
- Beszel 告警历史可见并已恢复三类资源告警：CPU 85%、内存 85%、磁盘 90%。
- `sudo guardian-status` 输出 Runtime `active (enabled)`、readiness `runtime:ready:observe`、模式 `observe`、自动动作 `disabled`、Broker `closed`。
- 只读维护入口完成一次联调：登录会话、只读诊断、Docker 列表、Guardian 单次观察均成功。
- 本次维护探针声明 `docker_mutation_invoked=false`、`systemd_mutation_invoked=false`、`production_connected=false`。

## 安全边界

- 自动动作配置保持关闭，Broker marker 和 socket 均不存在。
- Beszel 隔离环境没有配置外部邮件/Webhook，因此本次只验证页面告警和恢复，不宣称外部通知已送达。
- 这次验收证明本地可运行版本的安装、在线状态、告警展示和只读维护入口；不证明生产机在 CPU 满载、根盘耗尽或网络故障时一定可登录。
