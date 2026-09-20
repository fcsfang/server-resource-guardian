# EXP-052：ENOSPC 写入失败 fail-closed fixture

- 实验 ID：`EXP-052`
- 日期：2026-09-20
- 关联目标：Goal 7 / `PG-P0-07`
- 状态：`PASSED`（ENOSPC 负向 fixture；不等同于真实磁盘耗尽端到端验证）
- 实验负责人：当前 Agent

## 1. 目的

验证 Guardian 在审计或快照写入返回 `ENOSPC`（无可用空间）时，能够安全返回失败、保持降级/不执行动作，并且不删除已有证据。该实验只注入标准库异常，不实际填满 VM 磁盘。

## 2. 环境与安全边界

- 主机：macOS，本地 Python 标准库测试。
- VM：`guardian-ubuntu`，只在临时目录运行回归。
- 注入：对临时文件写入调用注入 `OSError(errno.ENOSPC, ...)`；不执行 `fallocate`、不填满文件系统。
- 禁止动作：不触发真实磁盘耗尽，不发送信号，不执行 Docker/systemd 变更，不修改资源限制，不连接生产，不触碰 EXP-039。

## 3. 实际结果

- 审计写入注入 `ENOSPC` 返回 `False`，不向调用方抛出异常；
- 快照写入注入 `ENOSPC` 返回 `None`，已有历史文件内容保持不变；
- 主机 Observer 定向测试 `25/25`、主机全量回归 `134/134`、`compileall` 退出码 `0`；
- Multipass 临时目录 Observer/Runtime 回归 `30/30`；
- 没有执行 `fallocate` 或其他填盘操作，EXP-039 Observer/sidecar 未受影响。

## 4. 安全边界与限制

该实验只证明 Guardian 的写入异常处理路径 fail-closed，不证明真实磁盘耗尽时文件系统、journald、systemd、Docker 或恢复流程的行为。PG-P0-07 的真实磁盘高水位/耗尽边界仍未完成。

结构化结果见 [`data/verification.json`](data/verification.json)。

## 5. 更新记录

- 2026-09-20：新增审计和快照 `ENOSPC` 负向测试。
- 2026-09-20：主机和 Multipass 临时回归通过；未执行真实填盘或任何服务/容器动作。
