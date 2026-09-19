# EXP-018：多对象竞争与业务健康状态的非破坏性验证

- 实验 ID：`EXP-018`
- 状态：`PASSED`
- 创建日期：2026-09-19
- 最近更新：2026-09-19
- 关联 Goal：`Goal 4 / G4-T06`
- 实验负责人：当前 Agent

## 1. 实验目的

验证同一风险事件同时发现多个稳定容器时，`observe/simulate` 不生成单对象动作计划，而是升级人工；同时验证 Docker health 状态会参与恢复判断，避免“容器仍运行但业务不健康”被误判为恢复成功。

## 2. 授权与安全边界

- 用户已授权本项目后续所需的本地测试动作。
- 环境：Mac Multipass `guardian-ubuntu`，仅使用 `guardian-test-base:local` ARM64 disposable 镜像。
- 允许动作：创建两个短生命周期测试容器；只执行 `observe/simulate` 读取和生成计划；实验结束后停止并清理本实验新建的两个临时对象。
- 禁止动作：不连接生产；不调用 Guardian Docker enforce；不执行 restart、terminate 或 kill；不修改资源限制。
- 停止条件：Observer 调用任何变更命令、出现非本实验容器被处理、对象 ID 缺失却生成可执行动作，立即停止。

## 3. 预期验收

- 两个容器均被 Observer 定位为稳定候选对象。
- 高风险 `simulate` 结果为 `escalate`，原因包含 `ambiguous_object_identity`，`execution=not_executed`。
- unhealthy 容器的恢复判定为 `target_not_healthy`，不返回 `recovered`。
- 清理后运行中容器为 0。

## 4. 执行记录

- 创建两个本地 disposable 容器：
  - `guardian-multi-a-20260919`，完整 ID `8e4ce5db2fd66928751df6822b6ef3a189f1dd0d0c7dcfadddbd3e31e633c9a5`；
  - `guardian-multi-b-20260919`，完整 ID `a96e1f7f493246ee0be72914054edb91fcf270602d86adcd2886c14f03aad982`。
- Ubuntu 实机 `observe --mode simulate` 只读采样成功定位 2 个稳定候选对象。
- 合成高风险窗口下结果：`state=critical`、`candidate_count=2`、`action=escalate`、`execution=not_executed`，原因包含 `ambiguous_object_identity`。
- 无稳定 ID 和多稳定对象的单元测试均通过；多对象不会生成 `graceful_stop` 计划。
- Docker inspect 健康状态注入测试通过：`Health.Status=unhealthy` 时恢复结果为 `pending / target_not_healthy`，不视为 `recovered`。
- 实验前后运行中容器均为 0；两个本实验新建容器已停止并清理；未调用 Guardian Docker enforce，未连接生产。

## 5. 结论

- 验收状态：通过。
- 已验证：多对象竞争和对象身份缺失均 fail-closed；`observe/simulate` 不会把歧义对象转成单对象破坏性动作。
- 已验证：Docker health 状态能够阻止不健康业务被误判为恢复。
- 尚未验证：生产业务自身的健康接口、x86_64 兼容性和真实业务保护名单。

## 6. 更新记录

- 2026-09-19：完成实验边界、停止条件和验收条件；开始本地双对象 observe/simulate 验证。
- 2026-09-19：双对象实机 observe/simulate 升级验证通过；无稳定 ID、多对象和 unhealthy 健康状态测试通过；清理临时容器。
