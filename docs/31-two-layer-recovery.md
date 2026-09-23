# Guardian 两层恢复判断

## 1. 语义拆分

一次处置动作后，不能用“容器停止”直接宣称业务恢复。Guardian 现在把结果拆为两个独立层：

| 层 | 状态 | 判断依据 |
| --- | --- | --- |
| 宿主缓解 | `MITIGATED` | 风险状态回到 `normal/recovered`、没有新 OOM，且可用内存或 memory PSI full 相对动作前改善 |
| 业务恢复 | `BUSINESS_RECOVERED` | 目标身份一致、目标运行、健康状态在允许集合内，且业务 probe 明确返回成功 |
| 业务未恢复 | `BUSINESS_DEGRADED` | 业务目标停止/不健康、probe 缺失/失败、身份不一致或恢复窗口超时 |

整体结果可能是：

- `BUSINESS_RECOVERED`：两层都通过；
- `MITIGATED`：宿主资源已缓解，但业务恢复未确认；
- `RECOVERY_PENDING`：证据仍不足以结束恢复窗口；
- `RECOVERY_FAILED`：宿主仍有风险、新 OOM 或观测证据不完整。

## 2. 实现

- [`src/guardian_recovery.py`](../src/guardian_recovery.py) 新增 `HostRecoveryObservation`、`BusinessRecoveryObservation`、两个 policy 和 `assess_two_layer_recovery()`。
- [`src/guardian_controller.py`](../src/guardian_controller.py) 可接收两层观测，返回 `recovery_layers`；宿主未缓解不会被算作成功，业务降级不会被伪装成业务恢复。
- 旧 `RecoveryObservation`/`assess_recovery()` 保留兼容历史单层测试；新生产化调用应使用两层结果。

## 3. Fail-closed 规则

- 宿主可用内存、PSI 或 OOM 计数任一缺失，宿主恢复失败，不猜测“已恢复”。
- 新 OOM 出现在动作之后，宿主恢复失败。
- 业务 probe 未配置或返回非 `true`，不能输出 `BUSINESS_RECOVERED`。
- 目标 ID 不一致、健康状态不达标和恢复窗口超时均输出 `BUSINESS_DEGRADED`。
- 恢复判断只产出状态和审计结果，不自动升级到 restart/terminate。

## 4. 限制

- A successful stop or host resource improvement does not prove business recovery.
- A business probe must be explicitly configured and authorized by the service owner.
- Local observations do not establish x86_64 or production behavior.
