# Beszel 控制台字段清单与本地 API 边界

更新时间：2026-09-19

状态：PARTIAL-LOGGED-IN-UI-VERIFICATION。主机、容器和历史曲线已有登录态证据；systemd 服务数值、实际刷新周期和真实告警时延仍未闭合。

## 1. 目的

G6-T01 需要逐项核验 Beszel 主机、Docker、systemd 和历史指标。本文件先记录本地 Beszel 0.19.0 控制台静态 bundle 中实际使用的集合、字段和采集周期，再补充用户已登录 Chrome 会话中可复查的主机、容器和历史曲线证据。

## 2. 已识别的集合与字段

| 控制台用途 | Beszel 集合 | 读取字段 | 当前映射意义 |
| --- | --- | --- | --- |
| 主机详情 | system_details | hostname, kernel, cores, threads, cpu, os, os_name, arch, memory, podman | 主机身份、CPU、内存、架构和运行时信息 |
| Docker 容器列表 | containers | id, name, image, ports, cpu, memory, net, health, status, system, updated | 对象身份、资源使用、健康状态和归属系统 |
| systemd 服务列表 | systemd_services | name, state, sub, cpu, cpuPeak, memory, memPeak, updated | 服务状态、CPU/内存当前值和峰值 |
| 系统历史趋势 | system_stats | created, stats | 主机历史统计点 |
| 容器历史趋势 | container_stats | created, stats | 容器历史统计点 |
| 告警历史 | alerts_history | id, name, value, state, created, resolved, expand.system.name | 告警名称、值、状态、开始/恢复时间和系统归属 |

## 3. 登录态 UI 核验（2026-09-19 23:39–23:41）

### 3.1 主机概览

`guardian-ubuntu` 在首页显示为在线，系统为 Ubuntu 22.04.5 LTS，内存显示约 3.82 GB，连接为 `beszel.sock`，客户端为 WebSocket 0.19.0，运行时间显示约 2 小时。一次页面观测值为 CPU 1.68%、内存 9.69%、磁盘 6.17%、load 0.09/0.17/0.19、网络 2.76 KB/s；这些是页面快照，不是容量基线。

### 3.2 主机历史曲线

主机详情页的时间范围选择为 1 小时，页面可见 CPU、Docker CPU、内存、Docker 内存、根使用、根 I/O、带宽、Docker 网络 I/O 和系统负载图表，时间轴约覆盖 22:45–23:40 并按 5 分钟显示刻度。该证据证明登录态页面能读取历史字段，但没有把图表刻度误写成实际采集周期；容器列表两次读取的 `updated` 从 `23:38:12` 变为 `23:39:12`，只作为约 60 秒更新间隔的页面证据，实际端到端采集/刷新延迟仍待专项测量。

### 3.3 Docker 容器

| 容器 | CPU | 内存 | 网络 | 健康 | 镜像 | 状态 |
| --- | ---: | ---: | ---: | --- | --- | --- |
| `beszel-poc` | 0.07% | 12.0 MB | 0.13 KB/s | Healthy | `henrygd/beszel:0.19.0` | Up 2 hours |
| `beszel-agent-poc` | 0.02% | 4.06 MB | 0.00 B/s | Healthy | `henrygd/beszel-agent:0.19.0` | Up 2 hours |

### 3.4 告警类别

告警菜单可见状态、CPU、IOWait、Steal、内存、磁盘、带宽、GPU、温度、1/5/15 分钟负载、电池、容器健康和失败 systemd 服务类别，当前全部为 `off`。这是类别存在性证据，不是告警触发证据；没有修改开关，也没有读取或保存凭据。

### 3.5 当前缺口

- 登录态页面中尚未独立枚举 systemd 服务表的 `name/state/sub/cpu/cpuPeak/memory/memPeak/updated` 具体值；命令搜索输入 `service` 无结果，首页“服务”列也没有可读数值。静态字段和底层 systemd 运行态只能作为字段/采集条件证据。
- 尚未通过带时间戳的页面刷新或历史点对照测量实际采集周期、页面刷新延迟和落盘完整性。
- 告警开关全部关闭，未形成真实告警触发、恢复和通知延迟证据；打开告警或制造故障应另建、另授权实验。

## 4. 历史采集周期

前端 bundle 声明的历史视图包括：

- 1m：期望间隔 2 秒，后端类型 1m。
- 1h：期望间隔 60 秒，后端类型 1m。
- 12h：期望间隔 10 分钟，后端类型 10m。
- 24h：期望间隔 20 分钟，后端类型 20m。
- 1w：期望间隔 120 分钟，后端类型 120m。
- 30d：期望间隔 480 分钟，后端类型 480m。

这些是前端请求/展示约定，不是本次实验测得的实际端到端延迟或落盘周期。

## 5. 本地只读探针结果

对本地 Hub 发起无凭据 GET：

- /api/health 返回 HTTP 200，消息为 API is healthy.。
- systems、system_details、containers、systemd_services、system_stats、container_stats 和 alerts_history 的列表接口均返回 HTTP 200，但当前无凭据查询看到 totalItems=0。
- 该结果不能解释为 Hub 没有数据：未登录请求可能受 PocketBase 规则或用户范围过滤。没有读取 token，也没有尝试绕过权限。

## 6. 来源和限制

本清单来自当前 Hub 返回的前端静态资源：

- assets/index-CZQtfejN.js
- assets/system-DXM3m_Rc.js
- assets/containers-table-CUaw4bEs.js
- assets/alerts-history-data-table-RYSRlk-c.js

静态资源能证明控制台代码请求哪些字段；登录态页面已经证明当前账号能看到主机、容器和历史曲线，但不能证明 systemd 服务具体值、实际采集/刷新周期或告警延迟满足要求。要完成 G6-T01，仍需补齐上述缺口，并将有界负载前后的数据变化与时间戳绑定。
