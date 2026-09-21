# Guardian Rescue Plane deployment Runbook

本目录只提供可审查的 systemd unit、slice、tmpfiles 和只读/计划脚本。仓库不会自动复制、启用或启动任何 unit；当前默认是 `observe`，Broker 没有自动安装入口，也没有默认启用标记。

## 组成与边界

- `rescue.slice`：Rescue Plane 的相对 CPU/IO 权重、内存回收保护和任务数边界。
- `workload.slice`：disposable 压力对象的竞争对照域，不是生产配额。
- `guardian-runtime.service`：持续 Observer → bounded queue → Coordinator 的 observe Runtime；不加入 `docker` 组，不直接持有 Docker socket。
- `guardian.tmpfiles`：共享状态、审计、快照和运行时目录的 owner/mode/setgid 约束。
- `guardian-runtime.slice`：Runtime 自身的有限控制面预算；它与 Rescue Plane 资源域分开评审。
- `guardian-observer.service`、`guardian-broker.service`、`guardian-broker.slice`、`guardian-audit.logrotate`：既有 Observer/Broker 和审计模板；本 T11 安装默认不启用 Broker。
- `scripts/guardian_rescue_probe.py`：默认只生成 dry-run 计划；只有显式 `--run-read-only` 才运行固定只读 Multipass 探针。
- `scripts/guardian_rescue_plan.py`：安装、诊断、停用、回滚的非变更计划器；它不调用 systemd、Docker 或 Multipass。
- `scripts/guardian_rescue_matrix.py`：把 CPU、内存、I/O、容量/inode、PID 和四类维护探针汇总为一个 fail-closed 只读判定；缺证据只返回 `INCONCLUSIVE`。

Rescue Plane 的目标是提高“登录 → 只读诊断 → 人工控制”的可维护概率，不保证任意资源耗尽时 SSH 必然可用，也不能替代带外控制台、BMC 或云控制台。

## 依赖图和成员边界

持久化前必须逐项核对以下链路：

```text
网络链路/DNS → ssh.service → 登录 session → user.slice/session scope
                         ├→ 只读诊断
                         ├→ Docker → containerd 控制面
                         └→ Guardian Runtime → journald/审计
```

| 依赖 | 处理原则 |
| --- | --- |
| SSH/网络/DNS | `ssh.service`、`network-online.target`、`systemd-networkd`/NetworkManager、`systemd-resolved` 是入口依赖；安装后用现有会话和带外通道验证，不能只证明 Guardian 进程存活。 |
| logind/`user.slice` | `systemd-logind` 负责创建 session；登录 shell 通常位于 `/user.slice/user-*.slice/session-*.scope`，不能假设把 `sshd.service` 移入一个 slice 就保护了 shell。需按目标机 systemd 版本单独评审 `user.slice` drop-in。 |
| Docker/containerd | 只读 `docker ps/info` 和 containerd 健康检查属于诊断面；Runtime 不得获得 Docker socket。任何控制动作必须仍留在独立、默认关闭的 Broker。 |
| journald | 负责 unit 日志和最小审计可见性；要核对持久化策略、限流、journal 占用和根盘余量。日志满盘时只能人工清理或按批准的保留策略处理。 |
| Beszel | 只读监控/展示依赖，不是 Rescue Plane 控制依赖；停用 Guardian 不得停止、重启、删除或修改 Beszel。 |

不要无差别把所有系统服务迁入 `rescue.slice`。任何 SSH、network、logind、Docker、containerd、journald 成员迁移，都必须在独立 disposable VM/快照中完成，并记录依赖、回滚和维护窗口。

## 资源边界

`rescue.slice` 的 `CPUWeight=1000`、`IOWeight=1000` 相对高于 `workload.slice` 的 `100`，只代表竞争时的相对调度优先级，不是专用 CPU/IO 预留。`MemoryMin=128M`、`MemoryLow=512M` 是本地 2 vCPU/4 GiB disposable VM 的候选值，必须结合目标机上 SSH、session、网络、日志、控制面和 Guardian 的 P99 RSS 重新审核。`TasksMax=512` 也不是无限制的登录保障。

当前故意不设置 `MemoryMax`：这组模板不是业务限额方案。若目标机需要专用 CPU、硬内存上限、磁盘 quota 或独立分区，必须由 SRE/运维批准并另留回滚证据。权重和 low/min 保护在全局内存、IO、内核或根盘耗尽时都可能失效。

## 根盘与日志边界

- Guardian 状态、审计和快照位于 `/var/lib/guardian`；共享目录保持 `root:guardian-shared`、setgid `2770`，Runtime 目录保持 `guardian:guardian`、`0700`。
- unit 日志进入 journald，并由 unit 限流；安装前必须记录 `journalctl --disk-usage`、根盘剩余空间和 `/var/lib/guardian` 所在文件系统。
- `guardian-audit.logrotate` 只提供经审查的审计保留模板；它不负责清理业务文件，也不应在根盘紧张时未经人工批准扩大写入。
- 生产环境应预留管理员可用的紧急空间，或使用已批准的独立分区/quota；不要让 Guardian 自动删除业务文件、容器数据、镜像或历史审计。
- 根盘接近满时，优先经带外通道人工停止写入源、保留证据并按保留策略释放空间；不能把“日志能写入”当作根盘安全的证明。

## 安装前置与静态门禁

安装前必须具备：目标 Ubuntu/systemd/cgroup v2 能力记录、可用 SSH 会话、已验证的 BMC/云控制台等带外通道、快照或可恢复备份、根盘/日志预算，以及明确的维护窗口。生产安装还需要 SRE/运维批准；本仓库不替代审批。

先只生成计划和做静态检查：

```bash
python3 scripts/guardian_rescue_plan.py install
python3 scripts/guardian_rescue_probe.py --output /tmp/guardian-rescue-plan.json
python3 scripts/guardian_rescue_matrix.py --summary-csv experiments/EXP-054-2026-09-20-rescue-plane-baseline/data/summary.csv --output /tmp/guardian-rescue-matrix.json
python3 -m unittest discover -s tests -p 'test_guardian_rescue_deployment.py'
systemd-analyze verify deploy/guardian/rescue.slice deploy/guardian/workload.slice deploy/guardian/guardian-runtime.service
```

在没有 Linux systemd 的开发机上，`systemd-analyze` 不可用是环境限制，不得被解释为 live systemd 通过；应复制到 disposable VM 做同样的静态解析。所有临时文件必须落在明确的 `/tmp` 路径。

经审批后，管理员可在目标 disposable VM 手工执行以下安装骨架；这些命令不由仓库脚本代执行：

```bash
sudo install -o root -g root -m 0644 deploy/guardian/rescue.slice /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/guardian/workload.slice /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/guardian/guardian-runtime.service /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/guardian/guardian.tmpfiles /etc/tmpfiles.d/guardian.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/guardian.conf
sudo systemd-analyze verify /etc/systemd/system/rescue.slice /etc/systemd/system/workload.slice /etc/systemd/system/guardian-runtime.service
sudo systemctl daemon-reload
sudo systemctl enable guardian-runtime.service
sudo systemctl start guardian-runtime.service
```

安装后至少检查 `systemctl status guardian-runtime.service`、`/run/guardian-runtime/ready`、`journalctl -u guardian-runtime.service`、共享 SQLite/WAL owner/mode、当前 cgroup 归属、SSH/session、网络/DNS、Docker/containerd 只读状态和根盘余量。Runtime 用户不得出现在 `docker` 组中。

## 默认 Broker 关闭

安装和重启后都必须确认 `/etc/guardian/broker.enabled` 不存在、Broker socket 不存在或不可用、Broker 没有 `[Install]` 自动入口。Runtime 缺少 Broker 时只能 observe/告警/人工接管；Beszel 告警、webhook、UI 和 SSH 登录都不能授予 capability。任何 disposable VM 的 Broker 验证都需要单独、当次、指定目标授权，本 Runbook 不提供自动开放命令。

## 诊断、停用与回滚

只读诊断先执行：

```bash
python3 scripts/guardian_rescue_plan.py diagnose
sudo systemctl is-active guardian-runtime.service
sudo systemctl show guardian-runtime.service -p Slice -p User -p SupplementaryGroups -p ActiveState
sudo systemctl status ssh.service systemd-logind.service systemd-journald.service --no-pager
sudo systemctl status docker.service containerd.service --no-pager
sudo journalctl --disk-usage
df -h / /var/lib/guardian
docker ps --format '{{.ID}}\t{{.Names}}\t{{.Status}}'
```

完全停用只作用于 Guardian：

```bash
python3 scripts/guardian_rescue_plan.py disable
sudo systemctl disable --now guardian-runtime.service
sudo systemctl stop guardian-broker.service || true
sudo rm -f /etc/guardian/broker.enabled
```

停用后必须确认 Guardian readiness/socket 消失、审计仍可读，并显式核对 Docker、containerd、Beszel、SSH、网络、logind 和 journald 的状态未被停用流程触碰。不要执行 `docker stop/restart/kill`，不要停止或修改 Beszel。

回滚顺序：保持 Broker 关闭 → 恢复上一份代码、unit、config 和 `/var/lib/guardian` 备份 → 重新执行 `systemd-analyze verify` 与 preflight → 仅启动 observe Runtime → 核对启动 reconciliation/readiness、SSH/session、网络/DNS、Docker/containerd 只读诊断和日志/根盘边界。发现启动错误、状态库不一致或维护通道退化时，立即经带外通道保持停用并保留证据。

```bash
python3 scripts/guardian_rescue_plan.py rollback
```

回滚和卸载不自动删除 `/var/lib/guardian`、审计、Docker 数据、Beszel 数据或业务文件；数据清理由管理员按保留策略另行批准。

## 明确限制

`guardian_rescue_probe.py` 的 Multipass 管理 session 只能证明本地 guest 探针可用，不能证明生产外部 SSH、认证、网络故障或任意资源耗尽下的可登录性。根盘真实满盘、带外通道、外部 x86_64 主机和生产 Docker/AuthZ 策略仍需独立验证。本任务不创建新的阈值 EXP，也不执行真实 systemd、Docker 或 Multipass 变更。
