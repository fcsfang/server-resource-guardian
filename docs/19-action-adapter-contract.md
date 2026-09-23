# Guardian Action Broker 与 Adapter 契约

## 1. 当前产品动作面

v2 自动路径的唯一候选变更动作是对明确 `actionable_set` 对象执行一次 `graceful_stop`。仓库保留的 `restart`/`terminate` adapter 是历史原型代码；它们不得因代码存在而进入当前自动策略或生产准入。

## 2. 不可变请求

请求至少绑定：schema version、event/plan ID、host/boot/environment、resource kind、完整目标身份、action、grace timeout、policy/config digest、authorization ID、nonce、签发/过期时间、最大执行次数 1。

Broker 只接受结构化枚举，不接受 shell、命令字符串、路径删除、PID kill、批量对象或任意资源参数。

## 3. 执行前门禁

1. 事件为 `enforce` 且仍新鲜；observe/simulate 永不进入 adapter。
2. 联合风险已确认，且计划只有一个稳定目标。
3. 目标同时在 `actionable_set`、不在 `protected_set`，动作被逐对象允许。
4. capability 与 host/boot/environment/target/action/plan digest 完全一致、未过期且未消费。
5. durable state、审计和熔断可用；intent 在 adapter 前提交。
6. 执行前重新 inspect unit/container，身份未变化。

## 4. Adapter 规则

- 参数数组调用，不经过 shell。
- Docker `graceful_stop` 使用完整 container ID，且只发送一次 `SIGTERM`。批准 timeout 只是后续恢复检查的等待窗口，不得在超时后升级为 `SIGKILL`。
- stdout/stderr 有界、脱敏；runner timeout 转换为明确失败结果。
- adapter 不自行重试、不升级 restart/terminate/SIGKILL、不选择其他目标；目标未在等待窗口内退出时必须转人工。
- 返回成功只表示运行时接受/完成该动作，不表示资源或业务恢复。

## 5. 状态与崩溃恢复

```text
PLANNED -> INTENT_DURABLE -> CLAIMED -> EXECUTING -> RESULT_DURABLE
Any unknown crash point -> RECONCILIATION_REQUIRED
```

intent 前崩溃可以安全重新规划；intent/claim 后任何未知状态必须人工对账，禁止自动重试。并发 claim 只能有一个胜者，重复结果幂等。

## 6. 恢复与熔断

动作后先判断对应资源是否 `MITIGATED`，再执行业务 probe。结果为 `BUSINESS_RECOVERED`、`BUSINESS_DEGRADED` 或 `NOT_MITIGATED`。动作失败、超时、验证失败、新风险、审计/状态写失败均打开熔断并通知人工，不继续扩大动作。

## 7. 权限模型

- Observer/API 不应持有 Docker/systemd 写权。
- Broker 使用本机 Unix socket、peer credential、有限协议和输入上限；生产 capability 由受控发行方签发并可撤销。
- 当前 `local-disposable` JSON/CLI 授权只用于本地实验，不能复制到生产。

## 8. 当前证据

历史本地记录证明单个可丢弃容器曾完成一次正常温和停止，持久状态和两层恢复逻辑也已有实现。当前仍缺少持续服务中的真实压力自动闭环、管理员告警展示和目标环境权限审计。
