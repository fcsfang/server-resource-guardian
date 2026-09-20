# EXP-042：有界高压下 Guardian 只读采样

- 实验 ID：`EXP-042`
- 日期：2026-09-20
- 关联目标：Goal 7 / `PG-P0-07`
- 状态：`INCONCLUSIVE`（fixture 在施压前失败，未形成高压证据）
- 实验负责人：当前 Agent

## 1. 目的

在当前 Multipass disposable VM 中加入可控的本地 CPU/内存压力，观察正在运行的 Guardian Observer 是否保持存活、采样和 fail-closed 语义，为 PG-P0-07 的“高压采样”边界补充证据。该实验不验证自动处置有效性，也不替代 24 小时 soak。

## 2. 保护边界与停止条件

- VM：`guardian-ubuntu`，Ubuntu 22.04.5 LTS ARM64，2 vCPU，约 3.8 GiB 内存。
- 压力对象：VM 内一个临时 Python worker，最多分配 128 MiB 并占用约一个 CPU，目标运行约 20 秒后自然退出。
- Observer：复用 EXP-039 主进程，只做 `/proc`/PSI/cgroup/Docker 只读采集；使用只读资源采样器记录 RSS、CPU、FD、线程。
- 停止条件：worker 到时自然退出；若 VM 出现 SSH/Multipass 失联、MemAvailable 快速下降、memory PSI full 非零或 Guardian 进程消失，立即停止扩大压力并保留已有数据。
- 禁止动作：不执行 Docker stop/restart/kill，不修改 cgroup/资源上限，不安装/启用 systemd，不连接生产。

## 3. 预定步骤

1. 记录 VM/Guardian 初始状态。
2. 启动有界 worker；worker 自带约 20 秒自然结束条件。
3. 并行运行 `guardian_resource_sampler.py` 和三次 `observe --once` 只读采样。
4. 等待 worker 自然退出，读取 Observer、资源时序、VM 状态和审计文件；不向任何目标发送信号。
5. 从结构化数据计算最大 RSS、CPU、FD、线程、采样条数和风险状态分布。

## 4. 预期验收

- worker 自然退出，未改变 Docker 容器状态。
- Guardian 主进程在压力期间保持存活，observe 采样可完成；缺少足够历史时仍保持 `degraded_observability`/observe，不生成可执行动作。
- 记录压力前后 VM MemAvailable、memory PSI、OOM 增量及异常；若未形成真正高压，结论只能写成“有限压力安全性检查”。

## 5. 结果

- 首次预检未形成高压：资源采样器脚本尚未传入 VM，worker 单行 Python 语法在分配/循环前失败；worker 返回非零且没有产生压力时序。
- 三次 `observe --once` 仍在独立审计文件中完成只读采样；这只能证明普通只读路径当时未因预检错误受影响，不能作为高压采样通过证据。
- VM 的 MemAvailable、memory PSI、Docker 容器清单和 EXP-039 主进程前后保持可读，EXP-039 PID `1076273` 仍存活；未执行 Docker/systemd 动作。
- 预检失败的命令输出保留在 VM 临时路径 `/tmp/guardian-p0-03-check/pressure-worker-1789878614.*`、`pressure-observer-1789878614.*`；不作为共享原始数据。

结论：`INCONCLUSIVE`。该失败轮次保留，不纳入高压统计；修正后的实验另建 EXP-043。

## 6. 后续

使用新实验记录重新传入只读采样器，改为多行自退出 worker，再进行同样的有界压力验证。
