# EXP-013：受控 enforce 结构化审计记录验证

- 实验 ID：`EXP-013`
- 状态：`PASSED`
- 创建日期：2026-09-19
- 最近更新：2026-09-19
- 关联 Goal：`Goal 4 / G4-T05、G4-T06`
- 实验负责人：当前 Agent

## 1. 实验目的

验证一次 `enforce` 运行可以把动作前事件和动作后结果封装为统一的 `guardian.enforce.v1` 审计记录，避免真实动作完成后只留下孤立的执行返回值。

## 2. 授权与安全边界

- 测试环境：宿主机 Python 单元测试和 Mac Multipass `guardian-ubuntu` Ubuntu 22.04.5 ARM64。
- 测试对象：mock executor、fake runner 和事件 fixture；没有真实容器被停止、重启或终止。
- 验证内容：事件、动作结果、恢复结果、冷却状态和失败熔断状态的结构化输出。
- 禁止动作：不调用真实 Docker mutation，不连接生产。

## 3. 结果与核心数据

- 宿主机测试：29/29 通过。
- Ubuntu 虚拟机测试：29/29 通过。
- 审计 schema：`guardian.enforce.v1`。
- 审计内容：`event` 原文、`result.action_result`、`result.recovery`、`result.cooldown_state`、`result.failure_breaker_tripped` 和 UTC `recorded_at`。
- Mock 运行结果：`planned`，`executed=false`，不改变运行态。
- 真实 Docker 动作：未执行，符合本实验授权边界。

## 4. 结论

- 验收状态：通过（结构化审计输出）。
- 已验证：未来真实 enforce 实验可以同时保存动作前快照和动作后执行/恢复结果。
- 尚不能证明：真实动作的业务恢复和生产兼容性。
- 下一步：授权真实 `graceful_stop` 后，将该审计记录与 EXP-014 的动作前后快照关联。

## 5. 更新记录

- 2026-09-19：完成 `guardian.enforce.v1` 结构化审计记录和双环境 29/29 测试。
