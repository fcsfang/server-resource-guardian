# EXP-030：Guardian 组合风险引擎与 cgroup v2 采集路径验证

- 实验 ID：`EXP-030`
- 状态：`PASSED`
- 创建日期：2026-09-20
- 最近更新：2026-09-20
- 关联 Goal：`Goal 7 / PG-P0-03`
- 实验目的：验证 Guardian 是否能将 OOM 增量、内存余量、内存下降趋势、PSI、swap 和采集质量统一为可解释的组合风险结果，并在计数回退、样本滞后、质量元数据缺失时 fail-closed。

## 1. 环境与边界

- 宿主：Mac Apple Silicon，Python 标准库测试。
- VM：Multipass `guardian-ubuntu`，Ubuntu 22.04.5 LTS ARM64，2 vCPU、约 3.8 GiB 可用内存，cgroup v2，Docker 只读采集可用。
- 测试对象：内存和 cgroup/PSI 的内存字典 fixture；VM 仅执行一次 `observe` 采样。
- 允许动作：读取本地测试文件、运行单元测试、运行 `guardian_observer --once`。
- 禁止动作：压力注入、容器创建/停止/重启/kill、systemd 变更、资源限制修改、Beszel 写接口、生产连接和凭据读取。
- 停止条件：发现任何动作执行、配置绕过、质量降级仍授权动作或 VM 健康异常立即停止。本次未触发。

## 2. 实施内容

- 新增 [`src/guardian_risk.py`](../../src/guardian_risk.py)：
  - OOM 计数只跟踪 `oom`/`oom_kill` 增量；历史计数基线不直接触发 critical。
  - 将可用内存、下降趋势、memory PSI some/full、swap 使用率组合为 warning/critical candidate。
  - 计数回退、单调时间戳异常、样本间隔过大、观测质量异常和样本不足输出 `degraded_observability`，不生成可执行状态。
  - 输出 reason codes、质量标记、样本数、趋势速率和各信号摘要；不授权动作。
- `collect_observation()` 增加单调时钟和质量元数据，并依据 `/proc/self/cgroup` 解析 cgroup v2 当前进程目录，避免把根 cgroup 不存在 `memory.events` 误判为宿主采集失败。
- `guardian_observer` 默认使用 `CompositeRiskEvaluator`；旧 `RiskEvaluator` 保留用于历史兼容测试。

## 3. 验收矩阵

| 场景 | 结果 |
| --- | --- |
| OOM 历史计数首次建立 | 仅建立 baseline，不产生新的 OOM critical 原因 |
| `oom`/`oom_kill` 发生增量 | 产生 `new_cgroup_memory_oom_event` 和 critical candidate |
| 仅 `high`/`max` 计数增长 | 不被误判为 OOM 事件 |
| 内存快速下降 + PSI full | 两个独立信号共同形成 critical candidate |
| 可用内存低但无支撑信号 | warning，不直接进入 critical |
| OOM 计数回退 | `degraded_observability`，带 `oom_counter_reset` |
| 样本间隔超过上限 | `degraded_observability`，带 `sample_gap_too_large` |
| 质量元数据缺失 | `degraded_observability`，带 `observation_quality_missing` |
| cgroup v2 当前路径解析 | fixture 和 VM 当前 session scope 均解析成功 |

## 4. 执行结果

- 宿主机全量测试：`84/84` 通过。
- `guardian-ubuntu` 临时目录隔离测试：`20/20` 通过；未修改 VM 项目目录。
- VM 一次 observe：成功输出 `degraded_observability`，原因是首个样本的 `insufficient_samples`；这是预期的启动质量状态，不是动作执行。
- VM 实际解析到当前 cgroup：`/sys/fs/cgroup/user.slice/user-1000.slice/session-<current>.scope`（本次样本为 `session-275.scope`）；读取到 `memory.events` 的 `oom=0`、`oom_kill=0`，说明此前发现的根目录路径问题已修正。
- VM Docker 只读列表返回 2 个既有 Beszel 容器；未创建、停止、重启或修改任何容器。
- `python3 -m py_compile src/*.py tests/*.py` 和 `git diff --check` 通过。

## 5. 结论与限制

1. PG-P0-03 的本地 MVP 组合风险和采集质量门禁已形成代码与测试证据。
2. 组合风险结果仍只是 Guardian 的风险输入，不等于对象归因、策略授权或动作执行；PG-P0-04/05 尚未完成。
3. 当前证据是 fixture 回放加 VM 单次 observe，尚未证明 24 小时常驻、生产阈值、x86_64 兼容性或生产动作安全。
4. 当前 VM 没有 swap，swap 分支通过 fixture 覆盖；swap 的真实长跑校准留到后续本地实验。
