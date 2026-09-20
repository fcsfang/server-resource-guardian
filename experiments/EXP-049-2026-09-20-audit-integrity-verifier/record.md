# EXP-049：可复用的审计完整性只读校验器

- 实验 ID：`EXP-049`
- 日期：2026-09-20
- 关联目标：Goal 7 / `PG-P0-07`
- 状态：`PASSED`（只读校验器、正负向测试和当前长跑文件核验；不等同于磁盘耗尽或崩溃恢复的端到端证明）
- 实验负责人：当前 Agent

## 1. 目的

将 EXP-039 中一次性的 JSONL 审计扫描固化为可重复执行的工程门禁，统一检查审计文件是否可解析、事件 ID 是否完整且唯一、文件是否超过有界容量，并保证校验过程只读、不修复或删除证据。

## 2. 环境与安全边界

- 主机：macOS，本地 Python 标准库 `unittest`。
- VM：`guardian-ubuntu`，Ubuntu 22.04.5 LTS ARM64；仅在 `/tmp/guardian-exp049` 运行测试代码。
- 被核验文件：EXP-039 在 VM 中持续写入的 `/tmp/guardian-p0-03-check/soak24.jsonl`；校验器只读取该文件。
- 禁止动作：不发送信号，不启动/停止/重启进程或容器，不修改 systemd、Docker、资源限制或生产环境；不重启 EXP-039。
- 停止条件：仅做有限测试和一次只读核验；不为获得更大样本而改动或延长实验对象。

## 3. 实现

新增：

- [`src/guardian_audit.py`](../../src/guardian_audit.py)：提供 `verify_audit_file()`，逐行解析 UTF-8 JSONL，检查记录类型、非空 `event_id`、重复 ID 和可选容量上限。
- [`scripts/guardian_audit_verify.py`](../../scripts/guardian_audit_verify.py)：命令行入口，输出结构化 JSON；有效返回 0，发现完整性问题返回 1。
- [`tests/test_guardian_audit.py`](../../tests/test_guardian_audit.py)：覆盖有效文件、格式/字段错误、重复 ID、容量超限且文件保持不变四类场景。

校验器不会写回输入文件，也不会删除旧记录；容量超限、解析失败、缺少事件 ID 或重复事件 ID 均返回无效状态。它是审计质量检查，不是审计轮换或磁盘修复机制。

## 4. 正向与负向验证

| 场景 | 结果 |
| --- | --- |
| 主机定向测试 | `4/4` 通过 |
| 主机完整回归 | `129/129` 通过 |
| 主机编译检查 | `python3 -m compileall -q src tests scripts`，退出码 `0` |
| Multipass 定向回归 | `4/4` 通过 |
| 合法 JSONL、唯一事件 ID、容量未超限 | `status=valid` |
| malformed JSON 或缺少 `event_id` | `status=invalid`，错误计数可见 |
| 重复事件 ID | `status=invalid`，重复计数可见 |
| 超过容量上限 | `status=invalid`，输入文件字节内容保持不变 |

## 5. 当前 EXP-039 文件的只读核验

执行：

```text
multipass exec guardian-ubuntu -- sh -lc 'cd /tmp/guardian-exp049 && python3 scripts/guardian_audit_verify.py --input /tmp/guardian-p0-03-check/soak24.jsonl --max-bytes 1048576'
```

结果：

```json
{
  "bytes": 1046557,
  "capacity_ok": true,
  "duplicate_event_ids": 0,
  "invalid_json_records": 0,
  "lines": 136,
  "max_bytes": 1048576,
  "missing_event_ids": 0,
  "status": "valid",
  "valid_json_records": 136
}
```

该核验确认当前快照在本次读取时具备可解析性、事件 ID 唯一性和容量余量。它没有改变正在运行的 EXP-039；EXP-039 仍为 `RUNNING`，最终 24 小时窗口和资源 P99 尚未完成。由于该长跑早于 EXP-047 的 `flush/fsync` 修正启动，本结果不能追溯证明长跑使用了新写入契约。

## 6. 结论、证据和限制

本实验通过：审计完整性检查从临时命令升级为可复用、可测试、只读的 P0-07 证据门禁，并在当前长跑审计文件上得到有效快照。

本实验不能证明：

- 文件系统真正耗尽时追加、恢复和告警行为；
- 进程在任意崩溃点重启后无丢失或重复审计；
- 24 小时 soak 已完成，或最终 P99 已校准；
- watchdog 超时、真实 SIGKILL/OOM、持久 unit 或生产环境可用性。

结构化结果见 [`data/verification.json`](data/verification.json)。

## 7. 更新记录

- 2026-09-20：新增只读审计校验器及四类正负向测试。
- 2026-09-20：在 Multipass 上完成 `4/4` 定向回归，并只读核验 EXP-039 当前审计快照。
