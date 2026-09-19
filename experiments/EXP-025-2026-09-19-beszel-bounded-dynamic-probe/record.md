# EXP-025：Mac Multipass Beszel 有界动态探针

- 状态：PASSED
- 日期：2026-09-19
- 关联 Goal：Goal 6 / G6-T01
- 目的：在不创建容器、不改变 Docker/systemd 配置的前提下，观察本地 Beszel Hub/Agent 在短时有界 CPU/内存活动期间的健康和运行态开销。

## 1. 授权与边界

- 环境：Mac Multipass 虚拟机 guardian-ubuntu，Ubuntu 22.04.5 LTS ARM64，2 vCPU，约 4 GiB 内存。
- 测试对象：虚拟机内两个临时 Python worker；Beszel Hub/Agent 只读观测。
- 负载上限：一个 CPU worker、一个 128 MiB 内存 worker，最多 12 秒；worker 到期自然退出。
- 允许动作：读取 /proc、Beszel /api/health 和 Beszel 容器只读 stats。
- 禁止动作：生产连接、读取凭据、创建/停止容器、修改资源限制、重启服务或调用真实 Guardian 动作。
- 停止条件：内存 PSI full 出现持续增长、Hub 健康接口失败、worker 无法按时自然退出或 VM 状态异常。

## 2. 结果摘要

- 两个 worker 均自然退出，退出码为 0。
- Hub /api/health 在 before、during、after 样本中均返回 HTTP 200。
- 内存 PSI full 全程为 0；内存可用量从 3.694 GiB 降至约 3.547 GiB 后回到 3.687 GiB。
- 1 分钟 load average 样本从 0.136 上升到 during 最大约 0.269，after 样本为 0.328；该值受 Linux 平滑窗口影响，不能当作瞬时 CPU 利用率。
- Beszel Hub 容器约 11.42–11.44 MiB，Agent 约 3.977 MiB；未观察到异常增长。

## 3. 结论分级

### 已验证

1. 当前 Mac Multipass 环境可承受这组短时、低强度、可回收的动态探针，Beszel Hub/Agent 保持健康。
2. 在该负载强度下未触发内存压力 full stall，也未造成 Hub 健康接口中断。
3. 动态样本可以作为本地开销和运行态基线补充。

### 尚未验证

1. 登录后的 Beszel 页面是否显示对应变化。
2. Beszel 是否为该变化生成告警，以及告警端到端延迟。
3. 更高强度负载、历史曲线落盘和真实告警 payload。

本实验不能单独证明“Beszel 页面指标完整”或“Beszel 告警路径已闭合”；G6-T01 的字段可见性和缺失项由 EXP-023 与 EXP-026 合并验收，真实告警路径仍属于 G6-T04。

## 4. 可复查入口

- 探针脚本：scripts/run-beszel-bounded-dynamic-probe.sh
- 静态字段基线：docs/22-beszel-dashboard-field-inventory.md
- 脱敏数据：data/probe-summary.json

## 5. 数据质量

- 采样间隔约 2 秒，实际输出 1 个 before、3 个 during、1 个 after 样本。
- 该实验测量的是虚拟机和 Beszel 容器运行态，不是页面刷新延迟。
- 无生产访问、凭据读取、容器变更或 Guardian 动作。
