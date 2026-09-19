# EXP-023：Mac Multipass Beszel 指标验收

- 状态：INCONCLUSIVE
- 日期：2026-09-19
- 关联 Goal：Goal 6 / G6-T01
- 目的：核验当前 Mac Multipass 中 Beszel Hub/Agent 的运行健康、基础观测来源、Docker/systemd/PSI 接入条件和初始开销。

## 1. 授权与边界

- 范围：本机 guardian-ubuntu Multipass 虚拟机和本地 Beszel PoC。
- 测试对象：Hub/Agent 容器、Ubuntu 主机的只读运行态。
- 允许动作：只读 API、只读 Docker inspect/stats、只读 systemd/proc/cgroup 查询。
- 禁止动作：生产连接、凭据回显、压力注入、容器重启/终止、资源变更和删除操作。
- 停止条件：发现 Hub/Agent 非 healthy、systemd 不稳定、或任何查询需要扩大权限时立即停止。

## 2. 执行摘要

本次只读核验确认：

- Ubuntu、systemd、cgroup v2、PSI 和 Docker 运行正常。
- Hub /api/health 返回 200，Hub/Agent 容器均为 healthy，Agent health 返回 ok。
- Agent 容器确实挂载只读 Docker socket、只读 system D-Bus 和共享 Beszel Unix socket。
- 能取得主机内存、Swap、磁盘、Load、PSI、systemd 服务和 Docker stats 的底层观测来源。
- 单次空载快照中 Hub 约 10.43 MiB、Agent 约 3.996 MiB；该数值不是稳定开销结论。

Beszel 页面级验收随后通过用户已经登录的本地 Chrome 会话补充完成了主机概览、容器列表、主机详情和告警类别读取；没有填写、读取或保存任何凭据，也没有修改告警开关。

页面级证据仍不足以将实验标记为 PASSED：systemd 服务表的具体当前值尚未在 UI 中独立枚举，告警开关全部关闭，因此没有测量真实告警触发/恢复延迟；有界动态运行态另见 EXP-025。

2026-09-19 23:39–23:41（Asia/Shanghai）补充已登录 UI 核验：

- 首页显示 `guardian-ubuntu` 在线、`Ubuntu 22.04.5 LTS`、约 `3.82 GB`、`beszel.sock`、客户端 WebSocket `0.19.0`；一次观测为 CPU `1.68%`、内存 `9.69%`、磁盘 `6.17%`、load `0.09 0.17 0.19`、网络 `2.76 KB/s`。
- 主机详情页显示 1 小时历史视图，存在 CPU、Docker CPU、内存、Docker 内存、根使用、根 I/O、带宽、Docker 网络 I/O 和系统负载图表；当前可见时间轴按 5 分钟标记从约 `22:45` 延伸到 `23:40`。这证明历史曲线和字段在登录态页面可见，但不把前端刻度当作实际采集周期测量。
- 容器页显示 `beszel-poc`：CPU `0.07%`、内存 `12.0 MB`、网络 `0.13 KB/s`、Healthy、端口 `8090`、镜像 `henrygd/beszel:0.19.0`；`beszel-agent-poc`：CPU `0.02%`、内存 `4.06 MB`、网络 `0.00 B/s`、Healthy、镜像 `henrygd/beszel-agent:0.19.0`。
- 容器页两次连续页面读取显示 `updated` 从 `23:38:12` 变为 `23:39:12`，提供约 60 秒更新间隔的页面证据；没有同时记录浏览器本地收发时间，因此仍不将其写成严格端到端采集/刷新延迟。
- 告警菜单显示状态、CPU、IOWait、Steal、内存、磁盘、带宽、GPU、温度、1/5/15 分钟负载、电池、容器健康和失败 systemd 服务类别，当前全部为 `off`。因此只能证明告警类别存在，不能证明告警路径已经触发。

随后对本地 Hub 返回的前端静态 bundle 做了只读字段盘点，确认控制台代码使用
system_details、containers、systemd_services、system_stats、container_stats 和
alerts_history 集合，并记录了字段和历史视图周期。盘点结果见
docs/22-beszel-dashboard-field-inventory.md。这补强了字段基线，但不替代登录后的 UI 数值验收。

## 3. 关键数据

脱敏结构化数据见 [data/baseline.json](data/baseline.json)。

| 类别 | 结果 |
| --- | --- |
| 主机 | Ubuntu 22.04.5 LTS ARM64，2 vCPU，约 4 GiB 内存 |
| 运行时 | Docker 29.1.3，systemd running，cgroup2fs |
| 内存 | 总量 4,100,427,776 bytes，可用 3,700,727,808 bytes，Swap 0 |
| 磁盘 | 根盘容量 41,433,624,576 bytes，已用 2,554,441,728 bytes |
| 压力信号 | CPU/Memory/IO PSI 文件均可读取 |
| systemd | 139 个 unit 条目；docker、systemd-oomd、ssh 均 active |
| Beszel Hub | API 200，容器 healthy |
| Beszel Agent | 容器 healthy，health ok |
| Agent 接入 | Docker socket、system D-Bus、共享 Unix socket 挂载已核验 |

## 4. 结论分级

### 已验证

1. 当前本地 Ubuntu 已具备 Beszel 采集主机、Docker、systemd 和 PSI 所需的运行条件。
2. Hub/Agent 认证连接和容器健康检查正常。
3. Beszel 运行态本身的内存开销处于 MiB 级，但还没有形成稳定 P50/P95 基线。

### 尚未验证

1. systemd 服务表的具体服务当前值、峰值和更新时间尚未在登录态 UI 中独立枚举；已有底层 systemd 运行态和静态字段基线不能冒充页面数值证据。
2. 已观察到容器列表 `updated` 字段约 60 秒递进，但指标实际采集周期、页面刷新延迟和历史数据落盘完整性仍未通过带本地收发时间的端到端测量闭合。
3. 受控 CPU/内存负载下 UI 数值前后变化尚未形成同一页面的可复查对照；运行态安全探针见 EXP-025。
4. 告警类别已在登录态页面可见，但全部关闭，未做告警配置变更，因此没有真实告警触发、恢复或通知延迟证据。
5. 静态 bundle 暴露的是前端请求约定；登录态页面证明当前账号能看到主机/容器/历史字段，但不能替代 systemd 数值和端到端告警延迟测量。

## 5. 后续动作

- 补齐 G6-T01 的 systemd 服务表数值、实际采集/刷新延迟和经授权的告警触发对照；不要因已看到告警菜单就将告警路径写成已验证。
- 将本记录作为 G6-T02 事件契约的环境边界输入。
- 不把本次单点快照写成 Beszel 稳定资源开销结论。
