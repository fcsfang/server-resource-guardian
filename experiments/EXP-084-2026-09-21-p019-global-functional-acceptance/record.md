# EXP-084 — PG-P0-19 本地统一功能验收

日期：2026-09-21

状态：`PASSED_LOCAL`

## 目的

在 PG-P0-18 本地功能收敛完成后，用一个统一矩阵验证事件、计划、通知 outbox、ActionIntent、capability、broker、fake adapter、恢复、冷却、熔断、人工接管和重启对账的闭环。该验收只使用本地 SQLite 临时状态、Python fixture 和 fake/unsafe adapter，不连接 Docker、systemd、真实通知渠道或生产主机。

## 执行

```bash
python scripts/run_p019_global_acceptance.py \
  --output experiments/EXP-084-2026-09-21-p019-global-functional-acceptance/data/result.json
```

runner 以 33 个命名合同测试组成单一矩阵，覆盖：

- normal/短峰、CPU、内存、磁盘容量/inode、磁盘 I/O、mixed；
- protected TOP、unknown TOP、无有效目标、身份变化、数据缺失；
- 通知落盘失败、intent/审计写失败、capability 过期/重放、broker 拒绝；
- 默认 observe、simulate fake-only、fake 成功/失败/超时、重启 reconciliation；
- outbox 重启、禁用通知通道、宿主缓解、业务恢复/退化/未缓解/等待；
- 冷却、失败熔断、持久化 action slot、action slot 重启对账和有界审计。

## 结果

`data/result.json`：

- `status=PASS`
- 37/37 case 通过
- `real_docker_actions=0`
- `real_notification_deliveries=0`
- `external_connections=0`

新增的 4 个运行态场景覆盖真实 `ObserverSampler` 事件进入同进程
`Coordinator`、有界队列满时 fail-closed、启动 reconciliation 保持未就绪，
以及 Coordinator 异常导致 Runtime 非零退出；另行完成 Runtime/Broker
`guardian.service.preflight.v1` 配置、路径和 digest 预检，结果均为 `PASS`。

## 边界

该结果只证明当前本地 fixture/mock/fake 运行态矩阵和服务预检通过，不证明
Multipass/systemd 持久安装、生产 SSH 救援 SLO、真实通知收件、真实 Docker
动作或业务 owner 恢复条件。PG-P0-14、PG-P0-16C、PG-P0-07 仍是生产准入
债务；任何真实 `graceful_stop` 仍需另行取得当次、指定目标授权。
