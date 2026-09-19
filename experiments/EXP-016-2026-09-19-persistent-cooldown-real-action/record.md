# EXP-016：真实 graceful_stop 后的持久化冷却验证

- 实验 ID：`EXP-016`
- 状态：`PASSED`
- 创建日期：2026-09-19
- 最近更新：2026-09-19
- 关联 Goal：`Goal 4 / G4-T06`
- 实验负责人：当前 Agent

## 1. 实验目的

验证 `guardian_enforce.py` 在独立进程之间持久化动作 ledger：第一次真实 `graceful_stop` 成功后，第二次立即提交同一目标/动作时，在调用 Docker 之前被冷却策略拒绝。

## 2. 授权与安全边界

- 用户已授权本项目后续所需的本地测试动作。
- 环境：Mac Multipass `guardian-ubuntu`，仅使用本地 `guardian-test-base:local` ARM64 镜像。
- 测试对象：本实验新建的单个 SIGTERM 可响应 disposable 容器。
- 允许动作：创建、启动和对该对象执行一次 `graceful_stop`；第二次请求只能验证冷却拒绝，不得再次调用 Docker。
- 禁止动作：不连接生产；不执行 restart、terminate 或 kill；不删除历史容器或镜像。
- 证据：事件快照、授权文件、`guardian.enforce.v1` 审计结果和 ledger JSON 均写入 VM `/tmp`。

## 3. 预期验收

- 第一次结果为 `recovered`，ledger 记录一次成功动作。
- 第二次独立进程结果为 `cooldown_active`，`action_result` 为空，Docker 不会再次被调用。
- 测试结束时运行中容器为 0。

## 4. 结果与核心数据

- 目标短 ID：`f53c96b6a6aa`；完整 ID：`f53c96b6a6aae9193f03b7452c6085df0f33cba11402b37d5ec670cb9ea500e7`。
- 第一次独立 CLI 调用：`state=recovered`、`reason_codes=[action_succeeded]`，目标 `exit=0`、`OOMKilled=false`。
- ledger 文件：记录 `last_action_at`、一个 `action_times` 和 `consecutive_failures=0`。
- 第二次独立 CLI 调用：`state=escalated`、`reason_codes=[cooldown_active]`、`action_result=null`。
- 第二次调用没有再次调用 Docker，ledger 内容未增加动作记录。
- 测试结束：`running=0`；未执行 restart、terminate 或 kill；未连接生产。

## 5. 结论

- 验收状态：通过（真实动作后的跨进程冷却）。
- 已验证：真实动作成功结果能够持久化，后续独立进程会在执行器之前阻断冷却窗口内的重复动作。
- 尚未验证：真实连续失败升级、动作超时、多对象竞争和业务健康检查。

## 6. 更新记录

- 2026-09-19：持久化 ledger 代码在宿主机和 Ubuntu 31/31 通过，开始真实跨进程冷却实验。
- 2026-09-19：第一次真实动作恢复成功，第二次独立调用被 `cooldown_active` 拒绝；实验通过。
