# EXP-045：systemd watchdog 与最大采样间隔余量契约

- 实验 ID：`EXP-045`
- 日期：2026-09-20
- 关联目标：Goal 7 / `PG-P0-07`
- 状态：`PASS`（本地代码契约验证）
- 范围：只读模板和测试；未安装、启动或重启持久 systemd 服务

## 1. 背景与目的

配置 schema 允许 Observer 的采样间隔最高为 60 秒。原 unit 也使用
`WatchdogSec=60s`，在采样耗时或调度抖动下没有明确余量，可能把正常慢采样误判为
watchdog 失活。本实验将模板调整为 `WatchdogSec=90s`，并把“必须大于 60 秒”写入
回归测试，避免该边界回退。

## 2. 验证

| 检查 | 结果 |
| --- | --- |
| unit 正向契约 | `WatchdogSec=90s`，且测试断言值严格大于 60 秒 |
| 回归测试 | `python3 -m unittest discover -s tests -p 'test_*.py'` → `121 tests ... OK` |
| VM 静态解析 | 在 `/tmp/guardian-exp045` 使用合法 unit 文件名运行 `systemd-analyze verify`，退出码 `0`；仅有 VM 系统无关警告 |
| 负向边界 | 将契约值视为 `60s` 时不满足 `> 60`，因此回归断言会失败；未修改工作树做破坏性回退 |
| 正在运行的 EXP-039 | Observer 和资源 sidecar 仍存活，未受模板修改影响 |

## 3. 结论与边界

本实验只证明模板保留了相对于最大配置采样间隔的名义 watchdog 余量；没有触发
watchdog 超时，也没有证明 systemd 超时后的自动恢复。实际目标机仍需重新校准采样
耗时、`WatchdogSec`、重启策略和资源预算；生产部署仍未授权。
