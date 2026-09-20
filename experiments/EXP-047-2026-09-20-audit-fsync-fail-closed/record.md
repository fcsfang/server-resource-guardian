# EXP-047：审计追加的 fsync fail-closed 契约

- 实验 ID：`EXP-047`
- 日期：2026-09-20
- 关联目标：Goal 7 / `PG-P0-07`
- 状态：`PASSED`（代码契约和正负向单元测试；不等同于崩溃恢复或磁盘耗尽的端到端证明）
- 实验负责人：当前 Agent

## 1. 目的

验证 Guardian 只有在一条审计事件完整写入并同步到文件系统后才把写入视为成功；写入、flush 或 fsync 失败时保持降级，不把不确定的审计状态继续当作可执行链路的成功证据。

## 2. 环境与安全边界

- 环境：本机 macOS，Python 标准库 `unittest`。
- 范围：临时目录和注入的文件同步失败 fixture；不连接 Multipass、Beszel、Docker 或生产主机。
- 保护边界：没有发送信号，没有启动/停止/重启进程或容器，没有修改系统服务和资源配置。
- 当前 EXP-039 仍在 `guardian-ubuntu` 内运行；本实验不触碰、不重启该进程。该长跑启动早于本修正，因此不把它当作本实验的运行时证据。

## 3. 实现

`src/guardian_observer.py::append_audit` 现在：

1. 检查容量上限；
2. 检查完整 payload 是否写入；
3. 调用 `flush()`；
4. 调用 `os.fsync()`；
5. 仅在以上步骤成功后返回 `True`。

短写入或同步异常返回 `False`。Observer 既有上层逻辑会把该结果标记为审计降级，并将决策保持为 `not_executed`/`escalate`，不继续执行动作。

## 4. 正向与负向验证

| 场景 | 结果 |
| --- | --- |
| 正常事件追加 | payload 完整写入，`fsync` 被调用，函数返回 `True` |
| fsync 抛出 `OSError` | 函数返回 `False`，调用方必须按审计不确定处理 |
| 既有容量上限 | 超限时拒写，不删除历史记录 |
| 既有路径写入错误 | 返回 `False`，不抛出未处理异常 |

新增测试：`test_audit_syncs_before_reporting_success`、`test_audit_sync_failure_is_fail_closed`。既有容量与路径错误测试继续通过。

## 5. 验证命令与结果

```text
python3 -m unittest tests.test_guardian_observer tests.test_guardian_runtime
Ran 25 tests ... OK
python3 -m compileall -q src tests
exit 0
```

主机完整回归：`python3 -m unittest discover -s tests`，`125/125` 通过。

Multipass `guardian-ubuntu` 临时目录回归：`tests.test_guardian_observer` + `tests.test_guardian_runtime`，`25/25` 通过。第一次临时复制遗漏 unit/slice 模板而得到 1 个夹具错误；补齐模板后同一代码重跑通过，该夹具错误不计入通过统计。

当前代码的 VM 运行时 smoke 也已通过：在独立临时目录连续执行两次 `observe --once`，两次退出码均为 `0`，审计为 2 行/15,680 bytes，`audit_status=written`，决策均为 `action=none`、`execution=not_applicable`，readiness 文件 14 bytes，stderr 为空。首次采样因尚未建立窗口而为预期的 `degraded_observability`；没有执行 Docker/systemd 动作。

本实验当前结论只覆盖代码契约，不扩大为 systemd watchdog、进程崩溃、磁盘耗尽或 24 小时 soak 证据。

## 6. 结论与未完成项

本实验通过，补齐了审计事件在进程崩溃窗口中的最小持久写入门禁。它不能证明：

- 进程在任意崩溃点重启后不会丢失或重复审计；
- 文件系统真正耗尽时的恢复行为；
- systemd watchdog 超时或 SIGKILL/OOM 后的重启行为；
- EXP-039 长跑已经使用本次新代码。

PG-P0-07 仍保持 `IN_PROGRESS`。EXP-039 结束后，需要使用当前代码重新进行相关运行时窗口验证，再依据完整场景 P99 校准 slice 参数。

## 7. 更新记录

- 2026-09-20：加入完整写入检查、flush/fsync 和对应正负向测试；目标进程与 VM soak 未被触碰。
