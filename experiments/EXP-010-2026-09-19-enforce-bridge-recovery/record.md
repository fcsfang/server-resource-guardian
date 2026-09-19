# EXP-010：Guardian `enforce` 运行桥接与恢复探测验证

- 实验 ID：`EXP-010`
- 状态：`PASSED`
- 创建日期：2026-09-19
- 最近更新：2026-09-19
- 关联 Goal：`Goal 4 / G4-T05、G4-T06`
- 实验负责人：当前 Agent

## 1. 实验目的

验证 Guardian 可以把事件快照和授权文件交给一次性 `enforce` 运行入口，并在不执行真实 Docker 动作的前提下完成 mock 路径、真实执行器双重确认门禁、fake runner 参数验证和动作后只读恢复探测。

## 2. 授权与安全边界

- 测试环境：宿主机 Python 单元测试和 Mac Multipass `guardian-ubuntu` Ubuntu 22.04.5 ARM64。
- 测试对象：JSON fixture、fake runner 和 mock executor；没有真实容器被停止、重启或终止。
- 真实 Docker 执行器在测试中只被注入 fake runner；没有连接生产 Docker socket。
- 真实运行入口默认使用 mock；真实 Docker 路径同时要求授权文件中的 `environment=local-disposable`、`--executor docker` 和 `--confirm-local-disposable`。
- 停止条件：不得调用真实 `docker stop`、`docker restart` 或 `docker kill`；不得连接生产。

## 3. 验收步骤

1. 用事件 fixture 和授权 fixture 执行 mock bridge，确认输出 `planned` 且 `executed=false`。
2. 不提供本地可丢弃确认时，确认 Docker executor 在调用 runner 前拒绝。
3. 用 fake runner 验证 Docker 参数数组和动作后的只读 `docker inspect` 恢复探测。
4. 验证恢复探测在目标停止后立即结束，不进行多余轮询。
5. 在宿主机和 Ubuntu 虚拟机各运行完整测试集。

## 4. 结果与核心数据

- 宿主机测试：28/28 通过。
- Ubuntu 虚拟机测试：28/28 通过。
- mock bridge：返回 `planned`，不消耗真实动作冷却，不改变运行态。
- Docker executor 门禁：缺少 `--confirm-local-disposable` 时返回 `local_disposable_confirmation_required`。
- fake runner：验证调用顺序为动作参数数组，再调用只读 `docker inspect --format {{json .State}}`。
- 恢复探测：停止动作后可生成 `target_stopped`，并由控制器返回 `recovered`。
- Ubuntu 实机 CLI smoke test：`guardian_observer --once --mode enforce` 成功生成 `execution=pending_controller` 的 JSON 快照；当时风险为 `normal`、无对象候选，因此没有动作计划。
- 真实 Docker 动作：未执行，符合本实验授权边界。

## 5. 结论

- 验收状态：通过（运行桥接、门禁和 fake recovery probe）。
- 已验证：`enforce` 不再只有库级接口，具备事件快照 → 授权文件 → 控制器 → 执行器 → 恢复探测 → JSON 结果的单次运行入口。
- 尚不能证明：真实容器的停止耗时、真实业务健康检查、动作失败后的真实升级和生产兼容性。
- 下一步：获得明确的本地可丢弃对象授权后，使用该入口执行一次真实 `graceful_stop`，并单独记录动作前后快照。

## 6. 更新记录

- 2026-09-19：完成 `guardian_enforce.py` 运行桥接和只读恢复探测；宿主机与 Ubuntu 虚拟机完整测试均 28/28 通过。
