# EXP-035：Observer 有界快照与审计失败安全

- 实验 ID：`EXP-035`
- 日期：2026-09-20
- 关联目标：Goal 7 / `PG-P0-07`
- 状态：`PASSED`
- 实验负责人：当前 Agent

## 1. 目的

验证 Observer 的快照目录和 JSONL 审计不会无限增长；达到容量上限或遇到不可写路径时拒绝追加、保留已有证据，并把事件置为降级/不可执行，而不是删除历史或继续动作。

## 2. 环境与边界

- 主机：macOS，Python 标准库单测。
- VM：Multipass `guardian-ubuntu`，Ubuntu 22.04.5 ARM64；相关测试在 `/tmp/guardian-p0-03-check` 临时目录运行。
- 只使用临时 fixture 和 observe；未安装 systemd，未执行 Docker stop/restart/kill，未修改资源限制。

## 3. 实施内容

- `write_snapshot(..., max_total_bytes=...)` 在写入前统计目录现有普通文件大小；超过上限返回 `None`，不删除旧文件。
- `append_audit(..., max_total_bytes=...)` 在追加前检查文件大小；容量不足或路径不可写时返回 `False`。
- Observer 将快照容量耗尽和审计写入失败映射为 `escalate` + `execution=not_executed`，并加入明确 reason code。
- 写入成功使用临时文件替换快照，避免留下半写 JSON；审计文件使用二进制追加以按 UTF-8 字节数计算上限。

## 4. 结果

| 检查 | 结果 |
| --- | --- |
| 主机完整测试 | `114/114` 通过 |
| VM 隔离相关测试 | `71/71` 通过 |
| 快照容量 fixture | 达到上限后返回空路径，历史文件内容保持不变 |
| 审计容量 fixture | 达到上限后返回失败，文件大小保持不变 |
| 审计路径异常 fixture | 目录被当作文件时返回失败，不向调用方抛出未处理异常 |
| VM 真实只读 observe smoke | 退出码 0；`degraded_observability`、`not_applicable`、审计写入成功 |

结构化摘要见 [`data/verification.json`](data/verification.json)。

## 5. 结论与限制

本实验支持：Guardian 的本地审计/快照写路径已有显式容量门禁，历史证据不会因达到上限被自动删除，写入失败不会被当作成功。

本实验不支持：磁盘真正耗尽时文件系统和 journald 的端到端恢复、跨进程并发写入安全、长期轮换策略、生产 P99 资源上限或 24 小时耐久性。这些仍属于 PG-P0-07 未完成项。

## 6. 更新记录

- 2026-09-20：完成容量门禁实现；主机 114/114、VM 71/71 和只读 Observer smoke 通过。
