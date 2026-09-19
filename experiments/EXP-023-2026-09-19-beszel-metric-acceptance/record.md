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

Beszel 页面上的每一个指标卡片、历史曲线和告警字段本次没有通过自动化浏览器逐项读取，因此实验不能标记为 PASSED。该限制不否定 Hub/Agent 已连接，只表示 G6-T01 的页面级验收仍需补齐。

2026-09-19 22:49（Asia/Shanghai）补充无凭据浏览器核验：打开
http://192.168.252.2:8090/ 后进入 Beszel 登录页，未登录会话无法读取主机卡片、Docker、
systemd、历史曲线或告警字段；未填写账号、密码，也未读取或保存凭据。该结果进一步确认了
页面级验收的外部会话依赖，不能替代已完成的底层运行态核验。

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

1. 控制台是否逐项显示全部主机、Docker、systemd 指标。
2. 指标采集周期、页面刷新延迟和历史数据落盘完整性。
3. 受控 CPU/内存负载下指标是否按预期变化。
4. 当前自动化浏览器没有登录态，因此不能从 UI 验证页面字段和历史数据。
5. 静态 bundle 暴露的是前端请求约定，不是当前账号可见数据或端到端延迟测量。

## 5. 后续动作

- 补齐 G6-T01 页面级指标核验和短时有界动态负载核验。
- 将本记录作为 G6-T02 事件契约的环境边界输入。
- 不把本次单点快照写成 Beszel 稳定资源开销结论。
