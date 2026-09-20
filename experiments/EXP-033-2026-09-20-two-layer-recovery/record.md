# EXP-033：Guardian 宿主缓解与业务恢复两层判断

- 实验 ID：`EXP-033`
- 状态：`PASSED`
- 创建日期：2026-09-20
- 最近更新：2026-09-20
- 关联 Goal：`Goal 7 / PG-P0-06`
- 实验目的：验证处置后的宿主资源缓解和业务恢复不会被混为一个状态；验证新 OOM、风险未下降、业务 probe 缺失/失败、目标身份不一致和健康状态异常均 fail-closed。

## 1. 环境与边界

- 宿主：Mac Apple Silicon，Python 标准库纯函数和 Controller 集成测试。
- VM：Multipass `guardian-ubuntu`，只复制源码到 `/tmp/guardian-p0-06-check` 做隔离测试。
- 观测：内存/PSI/OOM 和业务健康均为脱敏 fixture；不连接真实业务 probe。
- 动作：仅 fake executor；不执行真实 Docker/systemd 变更。

## 2. 验收矩阵

| 场景 | 结果 |
| --- | --- |
| 宿主风险恢复、无新 OOM、内存/PSI 改善 | `MITIGATED` |
| 动作后出现新 OOM | 宿主恢复失败，不宣称缓解 |
| 宿主仍为 critical | 宿主恢复失败 |
| 业务运行且健康 probe 成功 | `BUSINESS_RECOVERED` |
| 容器停止但宿主已缓解 | `MITIGATED` + `BUSINESS_DEGRADED`，不宣称业务恢复 |
| 业务健康异常、probe 缺失、目标 ID 不一致 | `BUSINESS_DEGRADED` |
| Controller 集成 | `recovery_layers` 分层输出；宿主未缓解不算动作成功 |

## 3. 执行结果

- 宿主机全量测试：`107/107` 通过。
- Multipass 临时隔离相关测试：`64/64` 通过；未修改 VM 项目目录，未触发 Docker/systemd 变更。
- fake Controller 测试确认：宿主为 `MITIGATED`、目标停止且业务 probe 失败时，整体状态为 `mitigated`，业务层保持 `BUSINESS_DEGRADED`。
- 业务健康 fixture 通过时，整体结果为 `BUSINESS_RECOVERED`。

## 4. 结论与限制

PG-P0-06 的本地 MVP 已完成两层恢复语义和 fail-closed 判断，但真实业务 probe、真实压力后的宿主重采样、7 天 soak、x86_64 和生产恢复窗口仍未验证；本实验没有执行真实动作。
