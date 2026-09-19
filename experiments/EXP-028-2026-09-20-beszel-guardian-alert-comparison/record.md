# EXP-028：Beszel 告警路径与 Guardian 检测路径对照

- 实验 ID：`EXP-028`
- 状态：`RUNNING`
- 创建日期：2026-09-20
- 关联 Goal：`Goal 6 / G6-T04`
- 目的：在同一台本地 `guardian-ubuntu` 和同一类可丢弃有限压力对象上，对照 Beszel 告警历史路径与 Guardian 本机检测路径的检测延迟、漏报、误报、数据中断和降级行为。

## 1. 已完成的只读预检

- Hub/Agent 仍为 healthy，Hub `/api/health` 返回 200。
- 登录态 `alerts_history` 查询返回 `totalItems=0`；当前没有真实告警 payload 可供对照。
- Beszel 首页告警类别均为 `off`。
- 通知设置页显示存在已配置的通知投递字段；目的地已被有意忽略，不读取、不记录、不回显。
- Guardian 实机单次 `observe` 返回 `normal`、可用内存约 90.041%、memory PSI full 为 0、动作 `none`；Docker 只读采集可用，当前发现 2 个 Beszel 自身容器。

## 2. 已获授权与当前配置

- 用户已明确授权本地实验所需权限；不保存授权原文、通知目的地或任何凭据。
- 已开启且仅开启 `guardian-ubuntu` 的“内存使用率”告警。
- 为使单次本地实验在有限窗口内可观测，阈值为使用率超过 15%，持续 1 分钟；实验结束后恢复为关闭。
- 通知设置页面未打开、未保存；如 Beszel 按既有配置产生通知，最多接受一次，并只记录结果摘要。

## 3. 实验边界

- 仅使用本地 Multipass VM 和 disposable 测试对象；不连接生产，不读取凭据。
- 压力有界：一个 256 MiB 内存 worker，最长 75 秒，自然退出；不创建、停止、重启或 kill 容器，不修改资源限制。
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

## 5. 实际执行结果

- 共执行两次本地 bounded memory pressure；每次只分配 256 MiB，最长 75 秒，worker 均以退出码 0 自然结束。
- Guardian 两次均在约 6.15 秒进入 warning，并在压力释放后约 77.2–77.3 秒恢复；Guardian 运行模式为 `observe`，无动作。
- 两次运行期间 Hub health 均为 HTTP 200，Docker 只读采集可用，memory PSI full 所有样本均为 0。
- Beszel 告警分别设置为“使用率超过 15%/12%，持续 1 分钟”；两个观察窗口之后，登录态 `alerts_history` 查询均为 `totalItems=0`，没有可测的 Beszel 告警时延、恢复记录或通知结果。
- 第二次运行后再次等待一个采样周期复核，仍无历史记录；最后已关闭内存告警，首页核验全部告警类别均为 `off`。
- 只读前端 bundle 进一步确认：用户告警配置通过 `POST/DELETE /api/beszel/user-alerts` 写入；运行态告警由 `alerts` 集合提供，告警历史页读取 `alerts_history` 集合。在一次不注入压力的配置核验中，UI 开关可见为 `on`，运行态 `alerts` 仍为 `totalItems=0`，随后已恢复为 `off`；这表示未观测到 active alert，不等于配置写入失败。该配置接口的 GET 形式返回 404，当前没有只读配置回读入口。
- 追加低阈值 idle baseline：空闲内存约 10.6%，配置为 1%/1 分钟且等待约 90 秒；完整刷新后配置仍显示为启用，但 `alerts` 和 `alerts_history` 仍均为 0。随后关闭告警并再次刷新，首页不再显示启用告警。该结果将未解决问题收敛到告警 evaluator、Agent 指标资格或历史/通知写入链路，仍不猜测具体内部原因。
- 原始采样见 [`runtime-result.json`](data/runtime-result.json) 与 [`runtime-result-2.json`](data/runtime-result-2.json)，管线回读见 [`alert-pipeline-readback.json`](data/alert-pipeline-readback.json)，汇总见 [`result-summary.json`](data/result-summary.json)。

## 6. 当前结论

实验状态为 `INCONCLUSIVE`。本实验提供了“同一有界内存压力下 Guardian 本机路径能够发现并恢复”的直接证据；Beszel 在两次压力窗口和一次低阈值 idle baseline 中均未产生可见 active/history 事件，因此 Beszel 告警事件路径未被观测到，但尚不能据此断言具体故障位置或把空记录直接当成完整漏报结论。G6-T04 仍未完成，下一步需要已知 live alert payload 或更底层的 Agent/Hub evaluator 证据；在此之前不进入 `simulate/enforce` 联动。
