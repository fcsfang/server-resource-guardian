# EXP-034：Guardian systemd 常驻运行基线

- 实验 ID：`EXP-034`
- 日期：2026-09-20
- 关联目标：Goal 7 / `PG-P0-07`
- 状态：`PASSED`（本地静态与临时运行子集通过；PG-P0-07 整体仍 `IN_PROGRESS`）
- 实验负责人：当前 Agent

## 1. 实验目的

验证 Guardian 第一版 systemd 常驻运行模板、独立 slice、readiness/watchdog 适配和安全边界能够在当前 Mac Multipass Ubuntu 环境中被加载、测试和短时运行；确认验证过程不会安装或启用持久服务，也不会产生 Docker 变更。

## 2. 环境与授权边界

- 主机：macOS；测试 VM：Multipass `guardian-ubuntu`。
- VM：Ubuntu 22.04.5 LTS ARM64，2 vCPU，约 3.8 GiB 内存，Docker 29.1.3，cgroup v2/systemd 可用。
- 文件边界：仅复制源文件、测试文件和 unit 模板到 VM 的 `/tmp/guardian-p0-03-check`；不覆盖 VM 项目目录。
- 动作边界：只做静态校验、只读 Observer smoke 和临时进程超时；未执行 `systemctl enable/start`，未安装 unit，未执行 Docker stop/restart/kill/kill 或资源变更。
- 停止条件：出现路径覆盖、生产连接、Docker 变更、无法限制运行时长或出现非预期高压时立即停止。本次未触发。

## 3. 实验步骤

1. 在源码中实现 `send_systemd_notification`、原子 readiness 文件写入和 watchdog 通知；保持无 systemd 环境安全 no-op。
2. 添加非 root Observer service 模板和独立 slice 模板，写入静态安全边界、日志限流和有界资源起始值。
3. 主机运行完整 Python 测试。
4. 将相关文件复制到 VM 临时目录，在隔离目录运行相关测试。
5. 使用 VM 的 `systemd-analyze verify` 检查 service/slice 语法。
6. 运行一次 `observe --once`，再以 8 秒 `timeout` 运行短时 Observer smoke；审计文件和 readiness 文件均写入 `/tmp`。

## 4. 结果

| 验收项 | 结果 | 证据/解释 |
| --- | --- | --- |
| notify/readiness 单元测试 | 通过 | 主机 111/111；包含抽象 Unix socket、payload、原子 readiness 和静态 unit 检查 |
| VM 相关测试 | 通过 | 68/68；修正了测试不能识别隔离目录的路径假设后复跑通过 |
| unit/slice 语法 | 通过 | `systemd-analyze verify` 退出码 0；仅剩 VM 其他系统 unit 的无关警告 |
| 首次采样安全降级 | 通过 | `observe --once` 退出码 0，1 条输出、1 条审计；`oom_counter_baseline_established` 与 `insufficient_samples` 导致 `degraded_observability`，decision 为 `none/not_applicable` |
| 短时常驻行为 | 通过 | 8 秒有界运行产生 2 条输出、2 条审计，按预期被 `timeout` 终止（124）；没有动作执行 |
| Docker/systemd 变更 | 未发生 | 未安装 unit，未 enable/start，未调用 Docker 变更命令 |

结构化摘要见 [`data/verification.json`](data/verification.json)。

## 5. 结论

本实验支持以下有限结论：Guardian 已有一个可审查、可在 Ubuntu 22.04 systemd 上解析的常驻运行基线；它能在首次采样质量不足时保持降级和无动作，并在短时运行中持续写入审计和 readiness。

本实验不支持以下结论：24 小时无泄漏、生产级资源上限、磁盘满/日志失败恢复、Docker/Hub 依赖故障全覆盖、x86_64 兼容性、watchdog 在真实 systemd manager 中的端到端触发，或任何自动 `graceful_stop` 有效性。

## 6. 异常与限制

- 初次 `systemd-analyze verify` 发现 Guardian unit 的相对 `Documentation=` 字段警告，已改为有效 GitHub URL 后复核通过。
- VM 校验过程中存在 `netplan-ovs-cleanup.service` 权限警告和系统 `snapd.service` 的未知 `RestartMode` 警告；两者不属于本次 Guardian 文件，命令仍以退出码 0 完成。
- 短时 Observer 运行只有两个间隔样本，风险引擎保持 `degraded_observability` 是预期安全行为；不能替代长时间 soak。

## 7. 下一步

继续 PG-P0-07 的本地安全工作：先做有界 soak 计数、日志/审计容量边界和依赖超时等 fixture 故障注入；完成 P99 资源测量前不把 slice 数值当作生产参数。P0-08 的真实 disposable `graceful_stop` 仍保持单独授权门禁。

## 8. 更新记录

- 2026-09-20：完成 unit/slice、notify/readiness/watchdog 代码、主机 111/111、VM 68/68、systemd verify 和有界 Observer smoke。
