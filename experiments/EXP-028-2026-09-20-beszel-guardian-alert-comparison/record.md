# EXP-028：Beszel 告警路径与 Guardian 检测路径对照（预检）

- 实验 ID：`EXP-028`
- 状态：`PLANNED`
- 创建日期：2026-09-20
- 关联 Goal：`Goal 6 / G6-T04`
- 目的：在同一台本地 `guardian-ubuntu` 和同一类可丢弃有限压力对象上，对照 Beszel 告警历史路径与 Guardian 本机检测路径的检测延迟、漏报、误报、数据中断和降级行为。

## 1. 已完成的只读预检

- Hub/Agent 仍为 healthy，Hub `/api/health` 返回 200。
- 登录态 `alerts_history` 查询返回 `totalItems=0`；当前没有真实告警 payload 可供对照。
- Beszel 首页告警类别均为 `off`。
- 通知设置页显示存在已配置的通知投递字段；目的地已被有意忽略，不读取、不记录、不回显。
- Guardian 实机单次 `observe` 返回 `normal`、可用内存约 90.041%、memory PSI full 为 0、动作 `none`；Docker 只读采集可用，当前发现 2 个 Beszel 自身容器。

## 2. 动作前确认门槛

在执行本实验前，必须得到用户对以下两件事的明确确认：

1. 允许在本地 `guardian-ubuntu` 上开启一个 Beszel 告警类别，并在实验结束后恢复原状态。
2. 允许该告警使用当前已有通知投递配置，最多产生一次本地测试通知；实验记录只保存成功/失败和延迟，不保存目的地。

未确认前不得点击告警开关、保存通知设置、注入压力或声称 G6-T04 已完成。

## 3. 计划边界（确认后才执行）

- 仅使用本地 Multipass VM 和 disposable 测试对象；不连接生产，不读取凭据。
- 压力有界：一个 CPU worker 与一个 128 MiB 内存 worker，最长 12 秒，自然退出；不创建、停止、重启或 kill 容器，不修改资源限制。
- Guardian 只运行 `observe`，不进入 `simulate` 之外的动作路径，不调用 Docker/systemd 变更接口。
- 先记录告警开关和通知设置摘要，再开启单一告警类别；出现 SSH/Hub 健康退化、内存 PSI full 持续增长、通知异常扩散或 worker 未按时退出立即停止。
- 实验结束恢复告警开关原状态，并再次确认 Hub 健康；恢复动作本身需要保留审计结果。

## 4. 计划指标

| 指标 | 采集方式 |
| --- | --- |
| Guardian 检测延迟 | 以本机单调时钟记录压力开始到 observe 首次进入 warning/critical |
| Beszel 告警延迟 | 以同一压力开始时刻到 `alerts_history` 出现记录的时间差 |
| 恢复延迟 | worker 自然退出到 Guardian recovered / Beszel resolved 的时间差 |
| 漏报 | 预期触发但在规定观察窗口内无记录 |
| 误报 | 无压力基线窗口内出现告警或 critical |
| 数据中断/降级 | Hub API 失败、空记录、超时或 Adapter `beszel_get_failed` |
| 安全边界 | Hub 健康、VM 状态、worker 退出码、告警开关恢复状态 |

## 5. 当前结论

预检完成，但实验未开始。G6-T04 仍未完成；Guardian 的正常基线、EXP-025 的安全动态探针或 EXP-027 的空告警历史查询，都不能单独写成 Beszel 告警路径有效性证据。
