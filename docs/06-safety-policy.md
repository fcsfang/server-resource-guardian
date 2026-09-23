# Guardian 生产安全策略

优先级：高于功能需求、演示脚本和历史 PoC 行为

## 1. 默认拒绝

- 默认模式为 `observe`；配置缺失、未知字段、版本不兼容、数据过期或审计失败时不得执行动作。
- `simulate` 只能产生 `execution=not_executed` 计划。
- 告警、critical、Top 排名、Beszel webhook 或 UI 操作都不是授权。
- 一次事件最多一个目标、一个动作；并发或重放只有一个胜者。

## 2. 两份正向清单

### `protected_set`

Guardian/Action Broker、PID 1、SSH/认证/网络/DNS、Docker/containerd、日志审计、Beszel Agent、文件系统/挂载关键服务，以及业务 owner 指定的数据库、队列、quorum、有状态/唯一副本任务。

### `actionable_set`

只有明确登记 owner、稳定身份、资源范围、允许动作、grace timeout、健康与恢复合同、最大频率、失效日期的对象才可产生动作计划。

两个集合独立：未命中保护名单仍不代表可处置。对象不在 `actionable_set` 时一律告警/升级人工。

## 3. 稳定身份

- 容器：full ID、image digest、created time、labels、cgroup path/inode。
- systemd：完整 unit、cgroup path、主进程 starttime、配置声明。
- 裸进程：只能作为诊断证据，不能作为自动动作目标。

决策时和执行前各核验一次；重建、PID 重用、cgroup 变化或身份字段缺失均拒绝。

## 4. 动作边界

| 动作 | observe | simulate | local enforce | production automatic |
| --- | --- | --- | --- | --- |
| 记录/快照/告警 | 允许 | 允许 | 允许 | 允许 |
| owner drain hook | 不执行 | 可计划 | 需单次授权和幂等合同 | 逐对象审批后评估 |
| `graceful_stop` | 不执行 | 可计划 | 仅 disposable、一次性授权 | 极小 actionable_set 多方审批后评估 |
| restart | 不执行 | 仅展示建议 | 禁止 | 禁止自动 |
| terminate/SIGKILL | 不执行 | 仅展示 break-glass | 禁止 | 永久禁止自动 |
| 裸 PID kill/整机重启/批量动作 | 禁止 | 禁止 | 禁止 | 永久禁止自动 |
| 删除文件/日志、修改任意资源上限 | 禁止 | 禁止 | 禁止 | 永久禁止自动 |

仓库中的 restart/terminate adapter 代码是历史原型能力，不构成当前策略准入；生产路径必须在 broker 层不可达。

## 5. 执行事务

1. 校验资源证据、对象归因、策略和 capability。
2. 持久化不可变 plan digest 与 intent。
3. 原子 claim capability；失败即拒绝。
4. 执行前重新核验身份和保护/可处置状态。
5. 调用参数数组 adapter，不经过 shell。
6. 持久化 result，随后验证宿主缓解和业务恢复。
7. 任意未知中间态进入 `RECONCILIATION_REQUIRED`，不得自动重试。

## 6. 熔断

保护命中、目标歧义、采样过期、审计/状态库失败、动作超时/失败、恢复未确认、业务恶化、连续失败、达到频率上限、多资源指向不同目标时，立即 `CIRCUIT_OPEN + ESCALATED`。熔断后只允许告警、取证和人工处置。

## 7. Rescue Plane 安全

- Rescue Plane 参数先在 disposable VM 验证，不直接安装到生产。
- CPU/IO weight 不写成绝对预留；memory.min/low 必须核算祖先层级和总保护量。
- 不在生产根盘做填满实验；容量实验只使用隔离 loopback/挂载点。
- 生产 Rescue Plane 变更需维护窗口、回滚、外部 SSH 客户端探针和带外通道。

## 8. 审计最小字段

`event_id`、host/boot、resource kind、sample/window、quality、policy/config digest、target identity、protected/actionable 结果、authorization、intent、adapter result、host verification、business verification、notification delivery、wall/monotonic time。

本地审计有界且同步失败 fail-closed；生产还需批准的异地主机外保留机制。
