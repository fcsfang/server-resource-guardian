# Guardian 受控动作适配器契约（G4-T05）

更新时间：2026-09-19

> 本文定义 `enforce` 的最小安全接口。当前只实现校验和可注入执行器；没有获得明确授权前，不对真实 Docker 对象执行动作。

## 1. 允许进入执行器的必要条件

一个动作请求必须同时满足：

- 动作属于有限集合：`graceful_stop`、`restart`、`terminate`；
- 目标是稳定且格式有效的完整/短 Docker container ID，不能只传名称；
- 目标未命中保护名单；
- 动作在目标的动作白名单中；
- 存在明确授权 ID；
- 授权环境必须是 `local-disposable`；
- 授权目标和动作与请求完全一致；
- 授权未过期；
- 优雅动作超时在 1–120 秒范围内。

任何一项失败都在调用 Docker 之前拒绝。

## 2. 执行器边界

- `MockActionExecutor` 只记录请求并返回 `mock_only_not_executed`，用于单元测试和 simulate 验证。
- `DockerActionAdapter` 只接受已经通过校验的请求，并以参数数组调用 Docker，不经过 shell 字符串拼接。
- `graceful_stop` 使用 `docker stop --time`；`restart` 使用 `docker restart --time`；`terminate` 使用 `docker kill`。
- `terminate` 不是默认动作，必须同时通过动作白名单和短期授权。
- 本模块不负责决定风险等级、保护名单或恢复成功；这些由上层策略和恢复验证负责。

## 3. 当前完成与未完成

- [x] 授权对象、动作白名单、保护对象和容器 ID 校验。
- [x] Mock executor，确保测试不会改变容器状态。
- [x] Docker 参数数组构造，禁止 shell 注入路径。
- [x] 5 个动作安全单元测试。
- [x] 恢复状态、冷却窗口和失败熔断的纯逻辑模型，见 EXP-008。
- [ ] 在获得明确本地可丢弃对象授权后，执行一次 `graceful_stop` 并记录 EXP。
- [ ] 完成动作后的资源恢复、健康检查、冷却和失败升级。
