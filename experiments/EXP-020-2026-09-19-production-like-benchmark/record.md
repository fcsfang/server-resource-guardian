# EXP-020：本地生产仿真性能与 Guardian 安全性基线

- 实验 ID：`EXP-020`
- 状态：`PASSED`
- 创建日期：2026-09-19
- 最近更新：2026-09-19（完成 5 轮有效重复和 churn 场景）
- 关联 Goal：`Goal 5 / G5-T01~G5-T06`
- 实验负责人：当前 Agent

> 证据修订：本实验的无 Guardian 组没有真实失败，因此不单独作为 Guardian 有效性证明；真实失败预防对照见 EXP-021。

## 1. 实验目的

在没有生产访问权限的前提下，使用与生产一致的 Ubuntu 22.04/systemd 249/cgroup v2/Docker 29.1.3 用户空间，复刻代表性多容器角色和可控资源危机场景，建立 Guardian 的安全性/性能基线；真实“无 Guardian 失败、Guardian 止损”对照由 EXP-021 补充。

## 2. 仿真边界

### 可复刻部分

- Ubuntu 22.04.5、systemd 249、cgroup v2、Docker Engine 29.1.3。
- 容器数量、容器 churn、CPU/内存/IO/PID 压力模式和 Docker 状态变化。
- Guardian 的检测、对象定位、保护判断、动作、恢复、冷却和审计链路。

### 不可等价复刻部分

- 生产为 x86_64、约 32 逻辑 CPU/31 GiB/55 个运行容器；本地为 ARM64、2 vCPU/4 GiB。
- 真实业务请求、业务健康接口、生产网络拓扑、磁盘阵列、数据规模和生产保护名单。
- 生产故障注入和生产动作授权。

## 3. 授权与安全边界

- 用户已授权本项目后续所需的本地测试动作。
- 只使用 Multipass `guardian-ubuntu` 和新建 disposable 容器；不连接生产，不读取生产凭据。
- 压力容器使用明确的资源预算和自动清理；不以统一资源限制作为产品方案，仅作为保护本地 VM 的测试护栏。
- 默认先执行只读 observe/simulate；真实动作最多对单个 disposable 目标执行一次 `graceful_stop`，不执行 restart、terminate 或 kill。
- 停止条件：VM 可用内存低于 15%、Docker/systemd 异常、Guardian 自身 RSS 超过 80 MiB、非实验容器被触碰、出现无法自动清理的运行中容器。

## 4. 测试矩阵

| 场景 | 对照 | 主要问题 | 关键指标 |
| --- | --- | --- | --- |
| A 基线 | 无 Guardian | 空载和代表性容器的基线 | CPU、内存、load、Docker stats、容器数量 |
| B 正常高负载 | observe | 正常繁忙是否误报 | 风险状态、误报率、Guardian 开销 |
| C 单对象内存增长 | 无动作 vs simulate/enforce | 是否提前发现、定位并恢复 | 检测延迟、预警窗口、恢复时间、动作成功率 |
| D 多对象同时恶化 | simulate | 是否避免误选对象 | 候选数、歧义升级率、错误动作数 |
| E CPU/IO/PID 退化 | observe/simulate | 辅助信号是否可解释 | PSI、load、IO、PID、风险状态 |
| F 容器 churn | observe | 容器短生命周期是否造成误报或身份错误 | 事件完整率、稳定 ID、误报率 |

## 5. 执行记录

- 第一轮仿真使用未处理 SIGTERM 的内存目标，Guardian 返回 `failed / target_force_killed`；该失败被保留为边界证据。
- 修正测试目标加入 SIGTERM trap 后，完成 5 轮有效重复；12 个正常容器、14 个 CPU/IO 压力对象、15 个 CPU/IO/PID smoke 对象和短生命周期 churn 对象均未触发错误动作。
- 在 14 个容器和 CPU/IO 压力下持续采样 32 秒，Guardian 峰值 RSS 约 27.6 MiB、user+sys CPU 0.12 秒。
- 无 Guardian 对照目标在 5 秒观察窗口结束时仍为 `Running=true`；Guardian 对同类单目标执行 `graceful_stop` 并恢复成功。
- 5 轮有效重复的时延、事件、审计和结果已写入 `data/`；完整解读见 [`report.md`](report.md)。
- 每轮结束运行中容器为 0；不连接生产，不执行 restart、terminate 或 kill。

## 6. 结论

- 验收状态：通过（安全性/性能基线）；详细报告见本目录 [`report.md`](report.md)，有效性对照见 [EXP-021](../EXP-021-2026-09-19-failure-prevention-comparison/report.md)。
- 本地实测：正常 fleet、CPU/IO 压力和 churn 未误触发；无 Guardian 对照目标保持运行；Guardian 对可优雅退出目标完成恢复。
- 容量缩放推断：本地 12–14 个容器的流程可运行，不代表生产 55 个运行容器下的绝对性能。
- 生产待复核：x86_64、真实业务健康、生产网络/磁盘、保护名单、业务 SLO 和长期稳定性。

## 7. 核心数据与报告

- 核心 CSV/JSON：本目录 `data/`。
- 可审阅证据：本目录 `data/` 中的 CSV、JSON、TXT 原始产物。
- 面向 leader 的报告：本目录 `report.md`。

## 8. 更新记录

- 2026-09-19：建立仿真矩阵、资源预算、停止条件和生产差异说明；开始 G5-T01。
- 2026-09-19：第一轮仿真因测试容器未响应 SIGTERM，Guardian 正确返回 `target_force_killed`；修正测试容器后完成 5 轮有效重复、正常高负载、churn 和无 Guardian/Guardian 对照；实验通过。
