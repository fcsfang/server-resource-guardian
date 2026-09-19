# EXP-009：Guardian 受控闭环控制层集成验证

- 实验 ID：`EXP-009`
- 状态：`PASSED`
- 创建日期：2026-09-19
- 最近更新：2026-09-19
- 关联 Goal：`Goal 4 / G4-T05、G4-T06`
- 实验负责人：当前 Agent

## 1. 实验目的

把 Observer 生成的 `enforce` 事件、动作授权、对象策略、冷却门禁、注入式执行器和恢复判断串成一条可审计的控制路径，验证未授权、保护对象、对象歧义和非 `enforce` 事件均不能到达执行器。

## 2. 授权与安全边界

- 测试环境：宿主机 Python 单元测试和 Mac Multipass `guardian-ubuntu` Ubuntu 22.04.5 ARM64。
- 测试对象：fixture、fake executor 和 `MockActionExecutor`；没有真实容器被停止、重启或终止。
- 保护对象：所有真实主机、生产服务和当前 Docker 运行态。
- 允许动作：读取代码、执行单元测试、调用 fake/mock executor、生成动作计划。
- 停止条件：不得调用真实 `docker stop`、`docker restart` 或 `docker kill`；不得连接生产。

## 3. 验收步骤

1. 用 `build_event(..., mode="enforce")` 生成待控制层接管的动作计划，确认事件只标记 `pending_controller`。
2. 用 mock executor 验证动作计划和恢复 fixture 可以串联，但不会改变运行态，也不会消耗真实动作冷却。
3. 用 fake executor 验证成功动作会进入恢复判断，并验证冷却窗口和动作上限。
4. 验证缺失授权、保护对象、多个候选对象、非 `enforce` 事件和失败熔断路径均在 executor 之前拒绝或升级。
5. 在宿主机和 Ubuntu 虚拟机各运行完整测试集。

## 4. 结果与核心数据

- 宿主机测试：23/23 通过。
- Ubuntu 虚拟机测试：23/23 通过。
- `observe`/`simulate` 不会进入控制层；`enforce` 计划必须显式交给 `GuardianController`。
- 控制层在调用注入式 executor 前再次执行动作契约校验，fake executor 不能绕过授权、保护和稳定身份检查。
- mock 结果为 `planned`，不会记录为真实动作失败；真实执行器结果才会更新冷却和连续失败计数。
- 真实 Docker 动作：未执行，符合本实验授权边界。

## 5. 结论

- 验收状态：通过（控制层集成和 mock/fake 安全路径）。
- 已验证：动作控制路径具备明确的 `observe`、`simulate`、`enforce` 边界；保护、授权、冷却、恢复和失败升级可以在不改变运行态的情况下测试。
- 尚不能证明：真实 `graceful_stop` 的 Docker 行为、动作后的真实健康检查和业务恢复、生产兼容性。
- 下一步：获得明确的本地可丢弃对象授权后，单独建立真实 `graceful_stop` 实验；仅在该实验中接入 `DockerActionAdapter`。

## 6. 更新记录

- 2026-09-19：完成 `GuardianController` 集成；宿主机和 Ubuntu 虚拟机完整测试均 23/23 通过。
