# EXP-057：磁盘容量/inode 与 I/O observe/simulate 本地验证

- 实验 ID：`EXP-057`
- 状态：`PASSED`
- 日期：2026-09-20
- 关联 Goal：`Goal 7 / PG-P0-13`
- 目的：在全新 disposable Ubuntu VM 中验证磁盘容量/inode 与块 I/O 两个独立通道的 Linux 实机采样、质量门、cgroup `io.stat` 归因和 simulate 计划边界。

## 1. 安全边界

- 仅使用新建的本地 disposable Multipass VM，不动现有 `guardian-ubuntu`，不连接生产或外部主机。
- 容量 fixture 只使用临时 loopback 文件和独立挂载点；不填充 VM 根盘，不删除宿主机文件。
- I/O 压力只在临时 systemd transient unit 中运行，单次最长 30 秒、数据量有界；不修改 I/O 限制，不执行动作。
- Guardian probe 只读 `/proc`、`statvfs`、mountinfo、diskstats 和 cgroup `io.stat`；`simulate` 的 `execution` 必须保持 `not_executed`。
- 实验结束停止压力、卸载临时挂载点、销毁 VM；只保留脱敏 JSON 结果和记录，不保留 key 或无界日志。

## 2. 预置验收条件

1. 独立挂载点能够读到 free bytes/free inodes、mount identity 和 time-to-full 计算所需字段。
2. 容量/inode 低余量进入独立 `disk_capacity` 候选状态；首样本/缺字段保持 `degraded_observability`。
3. 独立 I/O 压力能够读到 I/O PSI、device counters 和压力 cgroup 的 `io.stat` 增量。
4. 单一稳定 I/O cgroup 在连续样本后可归因；缺少 writer ownership 的容量通道明确放弃归因。
5. observe/simulate 均不执行 stop、restart、terminate、删除文件或 I/O 限速。

## 3. 结果

### 3.1 环境与修复

- VM：Ubuntu 22.04.5 LTS、aarch64、Linux 5.15.0-191-generic、systemd 249.11、cgroup v2、2 vCPU、3 GiB 内存。
- 使用两个临时 loopback 文件系统：`guardian-disk-fixture.img` 用于容量/inode，`guardian-io-fixture.img` 用于 I/O；均位于 disposable VM 内。
- 首次 smoke 发现代码读取了 `/proc/mountinfo`，而 Linux 实际挂载信息位于 `/proc/self/mountinfo`；已修正并用回归测试固定，修正后的数据使用 `disk-baseline-v2.json` 及后续压力数据。

### 3.2 容量/inode

在容量 loopback 挂载点创建 23,500 个小文件并预分配 75 MiB payload，最终观测到：

- free bytes ratio：`4.15%`；free inode ratio：`4.329%`。
- `disk_capacity` 的 candidate 保持 `critical`；经过默认 30 秒 critical dwell 后进入 `critical`。
- `disk_capacity` attribution 保持 `DEGRADED_OBSERVABILITY`，reason 为 `capacity_writer_ownership_unavailable`；没有把挂载点风险错误归给某个对象。
- simulate 只在首个质量降级样本和 dwell 后的 critical 样本生成 `escalate`，从未执行 stop、删除文件或其他动作。

### 3.3 I/O

在独立 transient `guardian-disk-io-pressure.service` 中运行有界 `stress-ng --hdd 2 --hdd-bytes 128M --timeout 20s`，写入临时 I/O loopback 挂载点：

- I/O PSI some 最高 `26.60%`，full 最高 `19.21%`；设备利用率最高 `70.955%`，平均延迟最高 `15.417ms`。
- 压力 cgroup 的 `io.stat` 增量可读取；连续样本中对象 `guardian-disk-io-pressure` 以 `100%` I/O 字节贡献、稳定 cgroup 路径和 high mapping confidence 进入 `TARGET_CONFIRMED`。
- I/O 风险在 PSI full 达到门槛并经过 dwell 后进入 `critical`。压力 unit 自然结束，`Result=success`、`ExecMainStatus=0`。
- 压力结束、cgroup 消失或计数证据不再连续后，归因不继续沿用旧目标；状态降为 `NO_TARGET/DEGRADED_OBSERVABILITY`，simulate 计划只升级人工，不执行动作。

### 3.4 验收与边界

- 挂载点容量、inode、mount identity、I/O PSI、device counters 和 cgroup `io.stat` 均已在 Linux disposable VM 读取并写入结构化 JSON。
- 容量/inode 与 I/O 使用不同 resource kind、阈值、状态机、质量门、归因和 reason code；容量风险没有借用 I/O 归因，I/O 风险没有借用容量阈值。
- 主机全量测试最终为 `159/159`；实验内所有 `decision.execution` 均为 `not_applicable` 或 `not_executed`，真实动作次数为 0。
- 本实验只证明当前 aarch64 disposable VM 和有界 loopback fixture 的代码路径；不证明生产 x86_64 的阈值、块设备映射、overlay/writeback 归因或任何生产动作安全性。

核心 JSON：[修正后 baseline](data/disk-baseline-v2.json)、[容量/inode pressure](data/disk-capacity-pressure.json)、[I/O pressure](data/disk-io-pressure.json)、[verification summary](data/verification.json)。不保存私钥或无界原始日志。

## 4. 更新记录

- 2026-09-20：首次 smoke 暴露 `/proc/mountinfo` 路径错误；修正为优先读取 `/proc/self/mountinfo`，加入回归测试后重跑。
- 2026-09-20：完成容量/inode 高水位、I/O PSI/device/cgroup `io.stat` 有界 fixture 和 observe/simulate 验收；所有真实动作次数为 0，实验状态改为 `PASSED`。
