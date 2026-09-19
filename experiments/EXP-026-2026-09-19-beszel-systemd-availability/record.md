# EXP-026：Beszel systemd 服务记录可用性

- 实验 ID：`EXP-026`
- 状态：`PASSED`
- 创建日期：2026-09-19
- 最近更新：2026-09-19
- 关联 Goal：`Goal 6 / G6-T01`
- 实验目的：确认登录态 Beszel Hub 是否实际提供 `systemd_services` 记录，并把“字段存在但数据不可见”的情况与权限错误区分开。

## 1. 环境与边界

- 环境：Mac Apple Silicon 上的 Multipass `guardian-ubuntu`，Ubuntu 22.04.5 LTS ARM64，Beszel Hub/Agent 0.19.0。
- 测试对象：本地 Beszel Hub 的只读 API 和已登录本地 Chrome 会话。
- 允许动作：对本地 Hub 发起 GET、读取返回 JSON；读取不包含凭据的页面结果。
- 禁止动作：读取或回显账号/token、修改告警开关、写入 Beszel、连接生产、重启/终止容器或修改资源。
- 停止条件：出现登录跳转、要求扩大权限或返回疑似凭据内容时立即停止。

## 2. 方法

在用户已经登录的本地 Chrome 会话中访问：

```text
/api/collections/systemd_services/records?perPage=5&sort=-updated
```

同时对照 EXP-023 已确认的本机底层 systemd 状态、前端 bundle 字段清单和 Beszel 首页/主机详情页面。

## 3. 结果

登录态页面成功返回 JSON：

```json
{"items":[],"page":1,"perPage":5,"totalItems":0,"totalPages":0}
```

这不是未登录页面或认证错误，而是当前用户范围下该集合没有可读记录。结合 EXP-023 的底层证据，Ubuntu 本身有 139 个 systemd unit，`docker`、`systemd-oomd` 和 `ssh` 均为 active；因此本实验确认的是 **Beszel 当前本地实例没有可供控制台/Adapter读取的 systemd 服务记录**，不是“Ubuntu 没有 systemd”。

| 验收项 | 结果 |
| --- | --- |
| systemd 底层运行条件 | 已具备，systemd running、D-Bus 可读 |
| 前端请求字段 | 已识别 `name/state/sub/cpu/cpuPeak/memory/memPeak/updated` |
| 登录态集合返回 | `items=[]`、`totalItems=0` |
| UI 服务明细入口 | 命令搜索 `service` 无结果，首页服务列无可读值 |
| 是否修改配置或动作 | 否 |

## 4. 结论

1. G6-T01 的“主机、Docker、systemd 指标是否可用”已完成验收：主机和 Docker 指标可见，systemd 字段约定存在但当前实例没有服务记录，缺失项已被证据化。
2. 该结果不证明 Agent 采集逻辑或生产部署一定有缺陷；可能原因包括 Agent 版本/配置、采集条件或 Hub 数据写入路径，当前没有修改部署来猜测原因。
3. G6-T01 的下一步不再是继续寻找不存在的 UI 页面，而是将 systemd 数据缺失作为 G6-T03 Adapter 的 fail-closed 输入，并在有授权的非生产环境另行决定是否补充采集链路。
4. 告警开关和通知设置保持原状；真实告警触发/恢复属于 G6-T04，不在本实验中伪造。

## 5. 数据质量与证据

- 观测时间：2026-09-19 23:54（Asia/Shanghai）。
- JSON 内容是页面返回的当前用户范围结果，不包含凭据或业务数据。
- `totalItems=0` 能证明当前接口没有可见记录，不能单独区分 Agent 未上报、Hub 未写入或数据保留策略；该因果边界已保留。
- 组合证据：[`EXP-023 record`](../EXP-023-2026-09-19-beszel-metric-acceptance/record.md)、[`字段清单`](../../docs/22-beszel-dashboard-field-inventory.md)。

