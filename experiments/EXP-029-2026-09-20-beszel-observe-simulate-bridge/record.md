# EXP-029：Beszel Adapter 接入 Guardian observe/simulate

- 实验 ID：`EXP-029`
- 状态：`PASSED`
- 日期：2026-09-20
- 关联 Goal：`Goal 6 / G6-T05`
- 目的：验证标准化 Beszel 事件进入 Guardian 后，仍由本机观测、对象策略和 `observe/simulate` 决策链重新判断；外部事件不得直接授权动作。

## 1. 环境与边界

- 宿主：当前 Mac 工作区，Python 标准库 `unittest`。
- VM：Multipass `guardian-ubuntu`，Ubuntu 22.04.5 ARM64，项目路径 `/home/ubuntu/server-resource-guardian`。
- 输入：脱敏 Beszel fixture；不读取凭据、不连接生产、不调用 Beszel 写接口。
- 动作边界：只执行 `observe`/`simulate` 纯函数和单元测试；不执行 Docker stop、restart、kill、资源变更或 `enforce`。
- 停止条件：发现 bridge 绕过本机身份校验、把 Beszel 对象直接作为动作目标，或任何测试触发变更接口时立即停止。本次未触发。

## 2. 实施内容

- 新增 [`src/guardian_beszel_bridge.py`](../../src/guardian_beszel_bridge.py)：复用 `BeszelEventWindow` 和 `is_actionable_observation`，把已标准化事件送入 Guardian `build_event`。
- accepted 事件只作为外部证据；Guardian 使用本机 Docker candidates 和本机风险信号重新决策。
- duplicate、stale、out-of-order、低置信度和 recovered 事件强制进入只读 `observe`，输出稳定原因码和 `execution=not_applicable`。
- `simulate` 仅产生 `execution=not_executed` 计划，`bridge.action_authorized=false`；Beszel event 不会选择 Docker 目标。

## 3. 验收矩阵

| 场景 | 结果 |
| --- | --- |
| 高置信度告警 + 本机 critical + allowlisted simulate | 生成 graceful_stop 计划，但不执行 |
| 高置信度告警 + 本机 normal | 不因外部告警越过本机确认，action 为 none |
| duplicate / stale / out-of-order | fail-closed，只读，action 为 none |
| 低置信度对象 | observation_only，只读，action 为 none |
| recovered 事件 | observation_only，只读，action 为 none |
| 多个本机候选对象 | `ambiguous_object_identity`，升级，不执行 |
| 分页事件复用一个去重窗口 | 两个新事件均 accepted，窗口状态保持一致 |

## 4. 执行结果

- 宿主机新增 bridge 测试 7/7，通过；全量测试 56/56，通过。
- Multipass Ubuntu 新增 bridge 测试 7/7，通过；全量测试 56/56，通过。
- `git diff --check` 通过；未发生 Docker/systemd 资源变更。
- 结构化汇总见 [`data/result-summary.json`](data/result-summary.json)。

## 5. 结论与限制

`G6-T05` 的本地代码和 fixture 验收通过：Beszel 可以进入 Guardian 的 `observe/simulate` 管道，但不会获得动作授权；不完整、重复、过期、乱序、低置信度、恢复和多对象输入均保持 fail-closed。真实 Hub 轮询仍须使用受控登录态或专门的无凭据本地服务，不在本实验中读取或保存凭据；G6-T06 UI 集成设计和 G6-T07 受控 `enforce` 仍未开始。
