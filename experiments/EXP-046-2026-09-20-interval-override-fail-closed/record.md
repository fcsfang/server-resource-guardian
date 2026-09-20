# EXP-046：采样间隔命令行覆盖的 fail-closed 校验

- 实验 ID：`EXP-046`
- 日期：2026-09-20
- 关联目标：Goal 7 / `PG-P0-07`
- 状态：`PASS`（代码与隔离回归）
- 范围：Observer 参数边界；未执行 Docker/systemd 动作

## 1. 背景与目的

配置文件由 schema 校验采样间隔，但命令行 `--interval` 可以覆盖配置。此前该覆盖
路径没有复用边界校验，`nan`、无穷大、负数或超过 60 秒的值可能进入运行循环。此次
将有限数值和 0.1–60 秒范围校验放到 `run()` 入口，非法参数在启动前拒绝。

## 2. 验证

| 检查 | 结果 |
| --- | --- |
| 正向边界 | `0.1` 和 `60` 秒接受并标准化为 float |
| 负向边界 | `0`、`-1`、`60.1`、`nan`、`inf`、`-inf`、`True` 均抛出 `ValueError` |
| 主机全量测试 | `123/123` 通过 |
| Multipass 相关回归 | `tests.test_guardian_observer` + `tests.test_guardian_runtime`：`23/23` 通过 |
| 隔离性 | EXP-039 Observer 和 sidecar 在验证前后均存活 |

临时目录的全量测试未纳入统计：它缺少本次源码回归无关的 `config/scripts/docs/demo`
依赖，导致装载错误；直接相关的 23 个 Observer/Runtime 测试已独立通过。

## 3. 结论与边界

命令行覆盖现在与配置 schema 共享同一运行时范围，非法值会在启动前 fail-closed。
这只证明参数门禁，不等同于 watchdog 超时、OOM 恢复或生产部署验证。
