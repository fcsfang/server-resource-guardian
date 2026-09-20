# EXP-032：Guardian 持久授权、意图和崩溃恢复状态

- 实验 ID：`EXP-032`
- 状态：`PASSED`
- 创建日期：2026-09-20
- 最近更新：2026-09-20
- 关联 Goal：`Goal 7 / PG-P0-05`
- 实验目的：验证 Guardian 的一次性 capability、原子 intent/result、持久冷却/失败熔断和启动 reconciliation；确认审计写失败、进程崩溃和并发请求都不会导致未经授权或重复动作。

## 1. 环境与边界

- 宿主：Mac Apple Silicon，Python 标准库 `sqlite3` 测试。
- VM：Multipass `guardian-ubuntu`，Ubuntu 22.04.5 LTS ARM64；只在 `/tmp/guardian-p0-05-check` 临时目录验证源码。
- 执行器：mock/fake executor；未调用真实 Docker stop/restart/kill。
- 允许动作：创建临时 SQLite WAL 文件、运行单元测试、运行 mock enforce CLI smoke。
- 禁止动作：真实 Docker/systemd 变更、故障注入、生产连接、凭据读取、删除/重建 VM。

## 2. 验收矩阵

| 场景 | 结果 |
| --- | --- |
| capability 首次消费/重复消费 | 首次允许，第二次 `capability_already_consumed` |
| capability 冲突/过期 | fail-closed 拒绝 |
| 同一事件重放 | 同一 idempotency key 返回既有 intent，不重复执行 |
| 同一对象动作并发 claim | SQLite 事务保证一个 claim，另一个 `active_intent_exists` |
| 审计 payload 无法序列化 | intent 事务回滚，Controller 在 executor 前返回 escalated |
| intent/执行开始后进程重启 | `reconcile_pending()` 标记 `RECONCILIATION_REQUIRED`，不自动重试 |
| 结果重复写回 | 保留第一次终态，不被后续结果覆盖 |
| 冷却/连续失败 | 跨 Store 实例保留，达到阈值后 failure breaker 生效 |
| Docker enforce 缺少 state DB | 在调用 executor 前拒绝 |

## 3. 执行结果

- 宿主机全量测试：`101/101` 通过。
- Multipass 临时隔离测试：同一组状态/控制/执行测试通过；未修改 VM 项目目录，未触发 Docker/systemd 变更。
- SQLite WAL 文件只存在于临时目录，测试结束后随临时目录回收；没有写入仓库、生产路径或凭据。
- mock/fake enforce 的审计输出包含 `intent_id`；真实 Docker 路径现在要求同时提供 `--ledger-file` 和 `--state-db`。

## 4. 结论与限制

1. PG-P0-05 的本地 MVP 已形成可崩溃恢复的状态和动作前安全门，满足“单次 capability、原子 intent/result、幂等、并发单赢家、冷却/熔断、审计失败前置”的代码测试验收。
2. 这不是生产动作授权，也不是已执行真实动作的证据；本实验没有停止、重启或 kill 容器。
3. 生产 capability 发行、多方审批、SQLite 文件权限/磁盘满、跨主机协调和真实业务恢复仍需后续阶段及外部授权。
