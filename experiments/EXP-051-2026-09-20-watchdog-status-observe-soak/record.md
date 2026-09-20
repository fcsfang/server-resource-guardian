# EXP-051：当前代码 watchdog 状态 observe soak

- 实验 ID：`EXP-051`
- 日期：2026-09-20
- 关联目标：Goal 7 / `PG-P0-07`
- 状态：`PASSED`（当前代码连续 observe 和审计事件 ID 对齐；记录 stdout 与 JSONL 事后状态字段的边界）
- 实验负责人：当前 Agent

## 1. 目的

在不触碰 EXP-039 的前提下，用当前已提交代码做一次独立、45 秒、有界的 observe-only 连续运行，确认 watchdog 状态字段能够随每轮事件进入审计，并且通知状态改动不会改变无 systemd 环境下的安全无动作行为。

## 2. 环境与安全边界

- VM：`guardian-ubuntu`，Ubuntu 22.04.5 LTS ARM64，2 vCPU，约 3.8 GiB 内存，Docker 只读采集。
- 代码：`/tmp/guardian-exp050` 中的当前代码；运行态：全新 `/tmp/guardian-exp051`。
- 运行：`observe`，5 秒采样，45 秒硬上限；审计使用现有 1 MiB 有界配置。
- 禁止动作：不触发 watchdog 超时，不发送 SIGKILL，不执行 Docker/systemd 变更，不修改资源限制，不连接生产，不重启或读取写入 EXP-039 文件。
- 停止条件：45 秒到达、Observer 异常退出、审计容量越界、Docker 状态发生变更或 VM 资源异常时停止并保留证据。

## 3. 实际步骤与结果

- 使用 `timeout --signal=TERM 45s` 运行当前代码；按预设返回 `124`。
- 输出 7 条、审计 JSONL 7 条；输出事件和审计事件的 `event_id` 序列完全一致。
- 输出事件的 `watchdog_status` 均为 `not_configured`，`audit_status` 均为 `written`；动作均为 `none/not_applicable`，stderr 为 0 bytes。
- 审计完整性校验器结果为 `7/7` 合法 JSON、事件 ID 无重复、`54,344/1,048,576` bytes、`status=valid`。
- 资源和审计运行态之外未执行 Docker/systemd 变更；EXP-039 Observer 和 sidecar 在实验后仍存活。

## 4. 数据语义边界

`audit_status=written` 是 `append_audit()` 在完整写入、flush 和 fsync 成功后，由 Observer 加到 stdout 事件上的事后结果字段，因此不会回写到已经追加的同一条 JSONL。审计 JSONL 的持久性证据是：追加函数成功返回、记录存在、事件 ID 与输出对齐，以及后续完整性校验通过；不能声称审计行自身包含 `audit_status=written`。若未来需要在审计行内表达提交状态，应设计单独的两阶段 commit 记录，不能通过事后原地修改 JSONL。

## 5. 安全边界与限制

- 全部验证均在 `guardian-ubuntu` 临时目录完成；没有安装、enable 或 start 持久 unit。
- 没有触发 watchdog 超时、SIGKILL、OOM、Docker 动作或资源变更；没有触碰 EXP-039 文件。
- 该实验只证明当前代码在无 systemd socket 时连续运行并正确记录 `not_configured`；不证明真实 watchdog 超时恢复、磁盘满恢复、24 小时完成或生产 readiness。

结构化结果见 [`data/verification.json`](data/verification.json)。

## 6. 更新记录

- 2026-09-20：启动 45 秒当前代码 observe-only soak。
- 2026-09-20：按预设返回 `124`，输出/审计各 7 条，事件 ID 序列一致，审计校验有效；补充 stdout `audit_status` 与持久 JSONL 的语义边界。
