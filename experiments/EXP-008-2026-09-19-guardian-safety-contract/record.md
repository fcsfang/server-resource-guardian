# EXP-008：Guardian 动作授权与恢复安全契约验证

- 实验 ID：`EXP-008`
- 状态：`PASSED`
- 创建日期：2026-09-19
- 最近更新：2026-09-19
- 关联 Goal：`Goal 4 / G4-T05、G4-T06`
- 实验负责人：当前 Agent

## 1. 实验目的

验证 Guardian 在进入 `enforce` 前的安全边界：保护对象、动作白名单、稳定容器 ID、短期授权、环境约束和超时校验；同时验证动作后的恢复状态、冷却窗口和失败熔断判断可以在不调用真实 Docker 动作的情况下被测试。

## 2. 授权与安全边界

- 测试环境：宿主机 Python 单元测试和 Mac Multipass `guardian-ubuntu` Ubuntu 22.04.5 ARM64。
- 测试对象：fixture、fake runner 和 mock executor；没有真实容器被停止、重启或终止。
- 保护对象：所有真实主机、生产服务和当前 Docker 运行态。
- 允许动作：读取代码、执行单元测试、调用 fake runner、生成动作计划。
- 停止条件：不得调用真实 `docker stop`、`docker restart` 或 `docker kill`；不得连接生产。
- 回滚方式：无运行态变更；删除测试临时目录即可清理测试产物。

## 3. 环境与初始状态

| 项目 | 值 |
| --- | --- |
| 主测试机 | Mac Apple Silicon + Multipass `guardian-ubuntu` |
| OS/架构 | Ubuntu 22.04.5 LTS / `aarch64` |
| Docker | 29.1.3；本实验不调用真实 Docker 动作 |
| 测试框架 | Python 标准库 `unittest` |
| 代码范围 | `src/guardian_actions.py`、`src/guardian_recovery.py` |
| 实验模式 | mock / fixture only |

## 4. 实验步骤

1. 构造本地可丢弃环境授权、目标 container ID、动作白名单和保护状态。
2. 测试保护对象、缺失授权、过期授权、非法目标 ID、动作不在白名单等拒绝路径。
3. 用 fake runner 验证授权通过时生成参数数组，确认不经过 shell 字符串拼接。
4. 用 mock executor 验证请求会被记录但 `executed=false`。
5. 用恢复 fixture 验证 graceful stop、restart、超时、未知动作和 cooldown/动作上限/连续失败熔断。
6. 在宿主机和 Ubuntu 虚拟机各运行完整测试集。

## 5. 结果与核心数据

- 宿主机测试：16/16 通过。
- Ubuntu 虚拟机测试：16/16 通过。
- 保护对象和缺失/过期授权：在调用 runner 前拒绝。
- Docker 参数构造：使用参数数组；fake runner 未收到 `shell=True`。
- Mock executor：返回 `mock_only_not_executed`，不改变任何运行态。
- 恢复模型：支持 `recovered`、`pending`、`failed`，超时和未知动作默认失败关闭。
- 冷却模型：支持冷却窗口、动作上限和连续失败熔断。
- 真实动作：未执行，符合本实验授权边界。

## 6. 结论

- 验收状态：通过（安全契约和 mock 层）。
- 结论类型：观测结果 + 工程判断。
- 支持的结论：Guardian 已具备进入真实 enforce 实验前的最小安全校验、mock 执行和恢复判断基础。
- 尚不能证明的内容：真实 `graceful_stop`/`restart`/`terminate` 的 Docker 行为、业务健康检查、动作后资源恢复和生产兼容性尚未验证。
- 后续任务：获得明确的本地可丢弃对象授权后，创建独立实验执行一次 `graceful_stop`；随后验证恢复和失败升级。

## 7. 更新记录

- 2026-09-19：建立 EXP-008，完成动作安全契约、mock executor、恢复和冷却逻辑验证。
