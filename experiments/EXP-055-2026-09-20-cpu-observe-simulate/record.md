# EXP-055：CPU observe/simulate 与 cgroup 归因

- 实验 ID：`EXP-055`
- 状态：`PASSED`
- 日期：2026-09-20
- 关联 Goal：`Goal 7 / PG-P0-12`
- 目的：为持续 CPU 饥饿建立独立的只读观测、持续性状态机、调度响应信号、cgroup CPU 时间归因和 simulate 计划边界。

## 1. 授权与安全边界

- 只使用本机新建的 `guardian-p011-migration` disposable Multipass VM；不连接生产、Beszel 外部服务或真实业务主机。
- 压力对象是 systemd transient `workload.slice/guardian-p012-cpu-fixture.service`，由 `stress-ng --cpu 2 --timeout 15s` 自动到期。
- 不执行 Docker stop/restart/kill，不执行 PID kill，不修改宿主根盘，不写入或删除业务文件。
- simulate 只生成计划；本实验所有记录的 `execution` 均为 `not_executed`，真实动作次数为 0。
- 原 `guardian-ubuntu` VM 未参与压力注入；PG-P0-11 的迁移 VM 仅保留本地实验所需的 disposable 服务和 observer。

## 2. 实现产物

- [`src/guardian_cpu.py`](../../src/guardian_cpu.py)：`/proc/stat`、loadavg、CPU PSI、cgroup `cpu.stat/cpu.max/cpu.pressure`、调度探针、CPU 风险状态机和对象 CPU 时间归因。
- [`scripts/guardian_cpu_probe.py`](../../scripts/guardian_cpu_probe.py)：有界只读采样窗口，支持 host 压力、systemd cgroup fixture 和 simulate 计划输出。
- [`tests/test_guardian_cpu.py`](../../tests/test_guardian_cpu.py)：解析、fixture、去抖、短 burst、缺失数据、单对象、竞争对象和身份变化负向测试。
- `config/guardian.example.json`、`config/guardian.schema.json` 和 `src/guardian_config.py`：版本化 CPU policy；实验短 dwell 配置单独保存在 `data/simulate-config.json`，不改变默认生产语义。

CPU 默认门限是本地 PoC 参数而非生产 SLA：利用率 warning/critical 为 `85%/95%`，CPU PSI some/full 为 `1%/0.5%`，调度响应 warning/critical 为 `250ms/1000ms`，warning/critical 持续窗口为 `30s/10s`，至少 2 个样本。`cpu.max` 缺失只记录为可选限制信息；`cpu.stat` 或 CPU PSI 缺失才会降低观测质量。

## 3. 环境

| 项目 | 值 |
| --- | --- |
| VM | `guardian-p011-migration`，Ubuntu 22.04.5 LTS ARM64 |
| systemd / cgroup | systemd 249.11 / cgroup v2 |
| Docker | Engine 29.1.3；本实验只读，不启动业务容器 |
| 资源 | 2 vCPU；约 3 GiB 内存；独立 disposable VM |
| 采样 | 1 秒间隔；首样本建立 counter baseline；单个窗口 6–12 个样本 |
| 运行模式 | baseline/pressure 为 observe；短 dwell 窗口为 simulate |

## 4. 结果

### 4.1 空闲 baseline

`data/baseline.json` 共 6 个样本：首样本为预期的 `degraded_observability`（建立 baseline），随后 5 个样本为 `normal`；无对象时归因从首样本降级后为 `NO_TARGET`。CPU 利用率约 `0.917%–2.970%`，调度探针约 `1.059–1.108ms`，动作均为 `none/not_applicable`。

### 4.2 有界 CPU 压力与短 burst

`data/cpu-pressure.json` 共 12 个样本，利用率为 `100%`，CPU PSI some 为 `0.25%–0.75%`，PSI full 为 `0`，调度探针为 `1.057–1.083ms`。其中 11 个样本的 `candidate_state=warning`，但默认 30 秒持续窗口未达到，因此风险 `state` 仍为 `normal`，没有动作计划。这证明“高利用率”和“控制面已饥饿”没有被混为一谈；本窗口未达到 critical 的 PSI/调度支持信号。

### 4.3 单对象 cgroup 归因

`data/cpu-attribution.json` 将 transient workload 的稳定 unit/cgroup 作为登记对象。10 个样本中首样本建立 baseline，之后 9 个样本均为 `TARGET_CONFIRMED`，候选状态为 `warning`；默认 30 秒 dwell 未到，动作保持 `none`。利用率为 `100%`，调度探针为 `1.053–1.076ms`，每条记录的执行字段均为 `not_executed`。

### 4.4 simulate 计划

`data/cpu-simulate.json` 使用仅供实验回放的 3 秒 warning dwell、5 秒 critical dwell 配置，不代表生产阈值。稳定 workload cgroup 在持续窗口满足后生成 4 条 `graceful_stop` 计划，全部为 `execution=not_executed`；压力结束后出现 `recovered`，未重复计划。首样本质量不足时升级为 `escalate`，但同样不执行。

### 4.5 负向路径

`data/negative-cases.json` 与 `tests/test_guardian_cpu.py` 覆盖：

- 单次短 burst 不产生动作计划；
- 两个 CPU 增量接近的对象返回 `AMBIGUOUS_TARGET`；
- 同一稳定 ID 的 cgroup 路径变化返回歧义；
- 低置信度/映射错误返回 `DEGRADED_OBSERVABILITY`；
- protected target 在 simulate 中升级而不执行；
- CPU 观测缺失、counter 复位、采样间隔过期均 fail-closed。

## 5. 验证与核心数据

- 主机全量单元测试：`149/149` 通过；`py_compile`、配置加载和 schema JSON 解析通过；`git diff --check` 通过。
- 结构化数据：[baseline](data/baseline.json)、[CPU pressure](data/cpu-pressure.json)、[CPU attribution](data/cpu-attribution.json)、[CPU simulate](data/cpu-simulate.json)、[negative cases](data/negative-cases.json)、[verification summary](data/verification.json)。
- 所有窗口均记录原始 CPU risk、candidate、PSI、scheduler delay、cgroup counter、归因状态、reason code 和 action/execution；统计可从 JSON 重算。

## 6. 结论与边界

- 本地 PG-P0-12 gate：`PASSED`。CPU 已从“辅助采集字段”独立为带质量门、持续性和归因的 observe/simulate 通道。
- 当前支持的结论：短 burst 不计划；持续高利用率在默认 dwell 内只保持 candidate warning；稳定单对象 cgroup 可解释归因；双对象竞争、身份变化、保护/未知对象不进入动作计划；simulate 计划永不执行。
- 尚不能证明：生产 x86_64、真实业务容器的 CPU 饥饿阈值、长期 CPU/PSI 校准、复杂 overlay/cgroup 映射、真实 SSH 客户端 SLO、任何资源耗尽下绝对可进入，以及 CPU 真实 enforce 效果。
- 下一步是 PG-P0-13：把磁盘容量/inode 与磁盘 I/O 分成独立通道；P0-11～14 全部通过且取得当次明确 disposable 授权前，不开展新的真实动作。

## 7. 更新记录

- 2026-09-20：完成 CPU 采样/schema、调度探针、风险状态机、cgroup 归因、负向测试和隔离 VM 有界 observe/simulate 窗口；真实动作次数为 0。
