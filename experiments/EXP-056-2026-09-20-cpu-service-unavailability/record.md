# EXP-056：CPU 满载导致服务/外部 SSH 不可用复现

- 实验 ID：`EXP-056`
- 状态：`INCONCLUSIVE`
- 日期：2026-09-20
- 关联 Goal：`Goal 7 / CPU 故障复现补充`
- 目的：专门验证“CPU 占满 → 正常服务异常卡顿/不可用 → 外部客户端无法进入服务器”的完整链路。

## 1. 授权与安全边界

- 仅使用新建的本地 disposable Multipass VM，不动现有 `guardian-ubuntu`，不连接生产或公司服务器。
- 只注入 CPU 压力，不注入内存、磁盘、网络或 PID 压力；不执行 Docker stop/restart/kill、不执行 PID kill、不修改生产配置。
- 使用临时 SSH key；实验结束后销毁 VM 和临时 key，结构化结果保留在本目录。
- 压力上限：2 vCPU、CPU worker 数有限、单次最长 60 秒；如果 VM 出现非预期服务状态、磁盘/内存异常或外部连接风险，立即停止压力。

## 2. 验收问题

1. CPU 是否达到接近 100%，CPU PSI/load 是否表明调度竞争？
2. 正常 HTTP health service 的成功率、P95/P99 和最大延迟是否显著退化？
3. 从宿主机发起的真实 TCP/SSH 建连、认证和命令执行是否超时或失败？
4. SSH 已进入后，通过真实强制 TTY 交互 shell 连续执行常见运维操作时，响应时间是否异常升高或超时？
5. 若服务或交互操作退化但 SSH 仍可用，能否证明“CPU 满载”不足以解释“只能重启”？

## 3. 实验场景

- `baseline`：无压力，外部 SSH + HTTP health 采样。
- `cpu_only`：在默认 systemd 资源域启动 `stress-ng --cpu 8`，2 vCPU，最长 60 秒；并行进行外部 SSH/HTTP 采样和 guest CPU/PSI/cgroup 采样。
- `cpu_runnable_storm`：仍只注入 CPU 压力，但启动 `stress-ng --cpu 256`，提高 runnable queue 竞争；最长 60 秒。

本轮新增的 `ssh_interactive_ops` 探针不是单条远程命令：它强制分配 TTY，启动非登录交互 bash，并连续执行 `uptime`、`ps`、`systemctl is-active`、`ls` 后再退出；探针超时或未收到 `done` 标记即视为失败。

## 4. 结果

### 4.1 环境与探针

- disposable VM：Ubuntu 22.04.5 LTS、aarch64、Linux 5.15.0-191-generic、systemd 249.11、cgroup v2、2 vCPU、4 GiB 内存。
- 正常服务：独立 systemd unit `guardian-cpu-repro-http.service`，Python 标准库 HTTP health endpoint，端口 `18080`。
- 外部探针：从宿主机执行真实 `/usr/bin/ssh`，使用一次性 key 完成 TCP 建连、认证、远程 health 命令；HTTP 探针从宿主机直接访问 health endpoint。
- 交互探针：强制分配 TTY，启动 `bash --noprofile --norc -i`，连续执行 `uptime`、`ps`、`systemctl is-active`、`ls`，收到 `done` 后退出；整段响应超时或缺少 `done` 均算失败。
- 未执行 reboot、kill、服务重启或其他恢复动作；压力单元自然到期。

### 4.2 压力是否真实达到目标

两种 CPU-only 压力均达到 CPU 满载；第二种更激进的 runnable queue 场景还显著提高了 load：

| 场景 | CPU 利用率最大值 | load1 最大值 | CPU PSI some 最大值 | CPU PSI full 最大值 | 调度探针 P95 | 压力 cgroup CPU 时间增量 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `cpu_only`，8 workers | 100% | 5.51 | 98.46% | 0% | 1.08 ms | 116.8 s |
| `cpu_runnable_storm_tty`，256 workers | 100% | 166.52 | 98.77% | 0% | 1.08 ms | 116.6 s |

上述数值来自 guest monitor；压力 unit 的 `Result=success`、`ExecMainStatus=0`，且服务 unit 在压力结束后仍为 `active`。

### 4.3 外部可用性与登录后操作

带 TTY 的追加场景共 60 个样本，结果如下：

| 探针 | 无压力基线 | 256-worker CPU 压力 | 观察 |
| --- | --- | --- | --- |
| 外部 HTTP health | 20/20 成功，P95 4.57 ms | 60/60 成功，P95 3.79 ms | 未出现服务不可用 |
| SSH 建连/认证/`true` | 20/20 成功，P95 213.0 ms | 60/60 成功，P95 192.6 ms | 未出现无法进入 |
| SSH 远程 health 命令 | 20/20 成功，P95 176.2 ms | 60/60 成功，P95 166.6 ms | 未出现命令失败 |
| SSH 强制 TTY 交互操作 | 20/20 成功，P95 185.8 ms | 60/60 成功，P95 177.5 ms | 60 次均收到 `done`，无超时 |

因此，本实验没有观察到“SSH 可登录但登录后常见操作异常卡顿到只能重启”，也没有观察到服务健康检查或交互 shell 的失败。压力期间 CPU risk evaluator 在高 runnable queue 场景进入 `critical`，说明风险观测通道能识别资源压力；但调度探针仍约 1 ms，未出现控制面失去调度的证据。

### 4.4 结论与边界

- 本次目标链路：`CPU 满载 → SSH 可登录 → 登录后操作卡顿/超时 → 只能重启`，**未复现**。
- 这不是“CPU 满载永远不会导致卡顿”的证明。当前结果只适用于本地 ARM64/2-vCPU Multipass、systemd/cgroup v2、短时 CPU-only 压力和最小服务 fixture；不替代生产 x86_64、真实业务工作负载、CPU quota/priority 配置、I/O/内存/PID 组合压力或带外救援验证。
- 更准确的工程判断是：仅凭 CPU 利用率 100% 不能解释“只能重启”；要继续定位该现象，应同时采集 runnable queue、CPU PSI full、调度延迟、业务请求耗时、磁盘 I/O/内存压力，以及 SSH 登录后具体卡住的命令/进程。

原始结构化数据放在 [`data/`](data/)，临时服务 fixture 放在 [`fixtures/`](fixtures/)；不保存私钥或无界原始日志。

## 5. 更新记录

- 2026-09-20：完成 baseline、8-worker CPU-only、256-worker runnable queue 和强制 TTY 交互场景；目标链路未复现，状态记为 `INCONCLUSIVE`，保留负向证据和生产边界。
