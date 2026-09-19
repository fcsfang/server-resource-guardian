# Beszel 控制台字段清单与本地 API 边界

更新时间：2026-09-19

状态：LOCAL-SOURCE-INVENTORY，不等同于已登录 UI 验收。

## 1. 目的

G6-T01 需要逐项核验 Beszel 主机、Docker、systemd 和历史指标。当前自动化浏览器没有登录态，因此本文件只记录本地 Beszel 0.19.0 控制台静态 bundle 中实际使用的集合、字段和采集周期，作为页面验收的字段基线。

## 2. 已识别的集合与字段

| 控制台用途 | Beszel 集合 | 读取字段 | 当前映射意义 |
| --- | --- | --- | --- |
| 主机详情 | system_details | hostname, kernel, cores, threads, cpu, os, os_name, arch, memory, podman | 主机身份、CPU、内存、架构和运行时信息 |
| Docker 容器列表 | containers | id, name, image, ports, cpu, memory, net, health, status, system, updated | 对象身份、资源使用、健康状态和归属系统 |
| systemd 服务列表 | systemd_services | name, state, sub, cpu, cpuPeak, memory, memPeak, updated | 服务状态、CPU/内存当前值和峰值 |
| 系统历史趋势 | system_stats | created, stats | 主机历史统计点 |
| 容器历史趋势 | container_stats | created, stats | 容器历史统计点 |
| 告警历史 | alerts_history | id, name, value, state, created, resolved, expand.system.name | 告警名称、值、状态、开始/恢复时间和系统归属 |

## 3. 历史采集周期

前端 bundle 声明的历史视图包括：

- 1m：期望间隔 2 秒，后端类型 1m。
- 1h：期望间隔 60 秒，后端类型 1m。
- 12h：期望间隔 10 分钟，后端类型 10m。
- 24h：期望间隔 20 分钟，后端类型 20m。
- 1w：期望间隔 120 分钟，后端类型 120m。
- 30d：期望间隔 480 分钟，后端类型 480m。

这些是前端请求/展示约定，不是本次实验测得的实际端到端延迟或落盘周期。

## 4. 本地只读探针结果

对本地 Hub 发起无凭据 GET：

- /api/health 返回 HTTP 200，消息为 API is healthy.。
- systems、system_details、containers、systemd_services、system_stats、container_stats 和 alerts_history 的列表接口均返回 HTTP 200，但当前无凭据查询看到 totalItems=0。
- 该结果不能解释为 Hub 没有数据：未登录请求可能受 PocketBase 规则或用户范围过滤。没有读取 token，也没有尝试绕过权限。

## 5. 来源和限制

本清单来自当前 Hub 返回的前端静态资源：

- assets/index-CZQtfejN.js
- assets/system-DXM3m_Rc.js
- assets/containers-table-CUaw4bEs.js
- assets/alerts-history-data-table-RYSRlk-c.js

静态资源能证明控制台代码请求哪些字段，不能证明当前账号能看到这些字段、Agent 已产生了多少条数据，或页面刷新/告警延迟满足要求。要完成 G6-T01，仍需本地 Beszel 登录会话下的逐项截图或脱敏导出，以及有界负载前后的数据变化。
