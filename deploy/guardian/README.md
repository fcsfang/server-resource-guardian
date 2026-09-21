# Guardian Rescue Plane deployment Runbook

本目录提供可审查的 systemd unit、slice、tmpfiles，以及本地安装和只读计划入口。安装脚本默认只输出计划；只有显式使用 `--apply --environment local-disposable` 才会在 Ubuntu disposable VM 上复制、启用并启动 observe Runtime。当前默认是 `observe`，Broker 只安装静态模板，不会启用，也不会创建授权标记。

## 组成与边界

- `rescue.slice`：本地 disposable VM 的维护域，固定到 CPU 0，并提供内存最小保障和任务数边界。
- `workload.slice`：本地压力对象的隔离域，固定到 CPU 1，内存上限 512 MiB、禁用交换并限制任务数。
- `guardian-runtime.service`：持续 Observer → bounded queue → Coordinator 的 observe Runtime；不加入 `docker` 组，不直接持有 Docker socket。
- `guardian-collector.service`：独立的只读容器 Collector；仅接受固定 snapshot 请求，执行固定的 `docker stats`/`docker inspect` 读取，并通过 `/run/guardian-collector/collector.sock` 返回身份、状态和资源事实。
- `guardian.tmpfiles`：共享状态、审计、快照和运行时目录的 owner/mode/setgid 约束。
- `guardian-journald.conf`：journald 的全局磁盘和限流边界模板；安装后仍需按目标机根盘预算复核。
- `guardian-runtime.slice`：Runtime 自身的有限控制面预算；它与 Rescue Plane 资源域分开评审。
- `guardian-collector.slice`：Collector 自身的有限资源边界。
- `guardian-observer.service`、`guardian-broker.service`、`guardian-broker.slice`、`guardian-audit.logrotate`：既有 Observer/Broker 和审计模板；本 T11 安装默认不启用 Broker。
- `scripts/guardian_rescue_probe.py`：默认只生成 dry-run 计划；只有显式 `--run-read-only` 才运行固定只读 Multipass 探针。
- `scripts/install-guardian-local.sh`：本地 Ubuntu 的一次性、可重复安装入口；默认 dry-run，应用时要求 `local-disposable` 标记，只启用 observe Runtime。
- `scripts/guardian_maintenance_status.py`、`scripts/guardian_maintenance_pressure.py`：维护账号可以调用的固定范围只读状态和限时压力入口；不接受任意命令。
- `scripts/guardian_reserve_space.py`：预创建 Guardian 自有应急空间；释放时必须匹配自身清单，不能指定任意删除路径。
- `src/guardian_reserve_recovery.py`：连续 Runtime 中默认关闭的磁盘预留恢复动作；只释放 Guardian 固定预留，验证同一文件系统空间增加，并记录写入源仍需人工处理。
- `scripts/accept-guardian-maintenance-ssh.sh`：从宿主机通过真实 SSH 完成一次基线和四类压力维护验收。
- `scripts/guardian_rescue_plan.py`：安装、诊断、停用、回滚的非变更计划器；它不调用 systemd、Docker 或 Multipass。
- `scripts/guardian_rescue_matrix.py`：把 CPU、内存、I/O、容量/inode、PID 和四类维护探针汇总为一个 fail-closed 只读判定；缺证据只返回 `INCONCLUSIVE`。

Rescue Plane 的目标是提高“登录 → 只读诊断 → 人工控制”的可维护概率，不保证任意资源耗尽时 SSH 必然可用，也不能替代带外控制台、BMC 或云控制台。

## 依赖图和成员边界

持久化前必须逐项核对以下链路：

```text
网络链路/DNS → ssh.service → 登录 session → user.slice/session scope
                         ├→ 只读诊断
                         ├→ Guardian Collector → Docker 只读事实
                         └→ Guardian Runtime → journald/审计
```

| 依赖 | 处理原则 |
| --- | --- |
| SSH/网络/DNS | `ssh.service`、`network-online.target`、`systemd-networkd`/NetworkManager、`systemd-resolved` 是入口依赖；安装后用现有会话和带外通道验证，不能只证明 Guardian 进程存活。 |
| logind/`user.slice` | `systemd-logind` 负责创建 session；登录 shell 通常位于 `/user.slice/user-*.slice/session-*.scope`，不能假设把 `sshd.service` 移入一个 slice 就保护了 shell。需按目标机 systemd 版本单独评审 `user.slice` drop-in。 |
| Docker/containerd | Collector 只执行固定的 `docker stats/inspect` 读取并返回事实；Runtime 不得获得 Docker socket。任何控制动作必须仍留在独立、默认关闭的 Broker。 |
| journald | 负责 unit 日志和最小审计可见性；要核对持久化策略、限流、journal 占用和根盘余量。日志满盘时只能人工清理或按批准的保留策略处理。 |
| Beszel | 只读监控/展示依赖，不是 Rescue Plane 控制依赖；停用 Guardian 不得停止、重启、删除或修改 Beszel。 |

不要无差别把所有系统服务迁入 `rescue.slice`。任何 SSH、network、logind、Docker、containerd、journald 成员迁移，都必须在独立 disposable VM/快照中完成，并记录依赖、回滚和维护窗口。

## 资源边界

本地 2 vCPU/4 GiB disposable VM 把维护域固定到 CPU 0，把压力域固定到 CPU 1；维护域使用 `MemoryMin=512M`、`MemoryLow=768M`，压力域使用 `MemoryHigh=448M`、`MemoryMax=512M`、`MemorySwapMax=0` 和 `TasksMax=256`。这只适用于本地验收，不是生产配置，也不是任意资源耗尽下的 SSH 保证。

压力域的内存和任务上限只限制本地压力对象；维护域的最小内存只提供 cgroup 层面的保护。I/O 使用低权重竞争边界和固定时长的压力工具，不能把普通磁盘变成专用设备。全局内存、I/O、内核或根盘耗尽时，保护仍可能失效。

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

本地 disposable Ubuntu 的一次安装入口如下。默认命令不改系统；只有第二条命令会执行安装：

```bash
bash scripts/install-guardian-local.sh
sudo bash scripts/install-guardian-local.sh --apply --environment local-disposable
```

安装入口会创建或复用 `guardian`、`guardian-broker`、`guardian-shared` 账户和组，安装当前代码、observe 配置、systemd 资源边界及 journald drop-in，校验配置仍为 `observe` 且自动动作关闭，然后启用并启动 `guardian-runtime.service`。重复执行会先备份已管理的 unit/config 文件，再重新校验和启动；失败时保留备份路径，不会自动打开 Broker。

同时会创建 `guardian-maint` 维护账号、只读状态命令、限时压力命令和 `/var/lib/guardian/reserve/emergency-space.bin`。维护账号只获得固定诊断/压力命令和释放 Guardian 自有预留空间的受限入口；允许处理名单仍为空，Broker 仍关闭。

安装后应看到：

```text
runtime: active
enabled: enabled
readiness: runtime:ready:observe
broker: disabled (no marker, no socket)
```

随后重启 disposable VM，重新检查 `systemctl is-active guardian-runtime.service`、`/run/guardian-runtime/ready`、`/etc/guardian/broker.enabled` 和 `/run/guardian-broker/broker.sock`。这只能证明本地 Ubuntu 安装和开机恢复，不代表生产环境可用。

安装完成后，管理员可以直接运行只读状态命令：

```bash
sudo guardian-status
```

它会回答 Runtime/Collector 是否在线、是否开机启用、当前是否 `observe`、自动动作是否关闭、最近一条 Guardian 本地风险状态、当前候选数量/最高候选、候选保护原因、模拟目标及是否执行，以及 Broker 是否仍处于关闭边界。`Recent local risk` 来自 Guardian 本地审计；Beszel 的三类告警历史和通知仍以 Beszel 页面为准。

本地 disposable VM 的最终维护验收从宿主机执行真实 SSH：

```bash
bash scripts/accept-guardian-maintenance-ssh.sh \
  --host VM_IP --key /path/to/maintenance_ed25519 \
  --output /tmp/guardian-maintenance-acceptance.txt
```

它只启动固定、限时的 CPU、内存、磁盘和混合压力；每个场景重新建立 SSH 连接，读取 Guardian、Beszel、Docker、网络和磁盘状态，并在最后确认压力文件已清理。验收结果只代表本地 disposable VM；没有真实业务健康检查时，不报告“业务恢复”。

内存风险进入 observe/simulate 后，仍只有在明确的 `local-disposable`、完整容器 ID、稳定身份、允许资源和一次性授权都满足时，才会生成未来的 `graceful_stop` 计划；默认 allowlist 为空。磁盘容量风险没有写入者证据时，只能释放 Guardian 自有应急预留（若本地专用配置已授权），随后报告写入源待人工处理，不会扩大为容器停止。

## 升级

升级只允许在已确认的 disposable 环境或经过审批的非生产维护窗口执行：

1. 先确认 Broker 标记和 socket 都不存在，并保存当前 unit、配置、`/var/lib/guardian` 和审计备份。
2. 使用新版本仓库执行同一个本地安装入口；安装器会先备份已管理的 unit 和配置，校验默认仍为 `observe`，在本地 disposable VM 中重新加载维护链路 drop-in，并重启需要重新归属资源域的系统服务、Collector 和 Runtime；不会执行容器停止动作。
3. 用 `guardian-status`、preflight、Collector 只读快照和 `systemctl is-active` 检查升级结果；升级失败时保持 Broker 关闭并按回滚顺序恢复上一份备份。
4. 升级过程不自动停止、重启或删除 Beszel、Docker 容器、业务文件或审计数据。

如需逐项审查，也可以在 disposable VM 手工执行以下安装骨架；这不是生产安装授权：

```bash
sudo install -o root -g root -m 0644 deploy/guardian/rescue.slice /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/guardian/workload.slice /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/guardian/guardian-runtime.service /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/guardian/guardian.tmpfiles /etc/tmpfiles.d/guardian.conf
sudo install -d -o root -g root -m 0755 /etc/systemd/journald.conf.d
sudo install -o root -g root -m 0644 deploy/guardian/guardian-journald.conf /etc/systemd/journald.conf.d/guardian.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/guardian.conf
sudo systemd-analyze verify /etc/systemd/system/rescue.slice /etc/systemd/system/workload.slice /etc/systemd/system/guardian-runtime.service
sudo systemctl daemon-reload
sudo systemctl reload systemd-journald
sudo systemctl enable guardian-runtime.service
sudo systemctl start guardian-runtime.service
```

安装后至少检查 `systemctl status guardian-runtime.service`、`/run/guardian-runtime/ready`、`journalctl -u guardian-runtime.service`、共享 SQLite/WAL owner/mode、当前 cgroup 归属、SSH/session、网络/DNS、Docker/containerd 只读状态和根盘余量。Runtime 用户不得出现在 `docker` 组中。

## 默认 Broker 关闭

安装和重启后都必须确认 `/etc/guardian/broker.enabled` 不存在、Broker socket 不存在或不可用、Broker 没有 `[Install]` 自动入口。Runtime 缺少 Broker 时只能 observe/告警/人工接管；Beszel 告警、webhook、UI 和 SSH 登录都不能授予 capability。进入 `enforce` 前必须在配置中指定绝对路径的一次性授权文件，Runtime 会在启动时向 Broker 注册 capability；任何 disposable VM 的 Broker 验证都需要单独、当次、指定目标授权，本 Runbook 不提供自动开放命令。

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

`guardian_rescue_probe.py` 的 Multipass 管理 session 只能证明本地 guest 探针可用，不能证明生产外部 SSH、认证、网络故障或任意资源耗尽下的可登录性。根盘真实满盘、带外通道、外部 x86_64 主机和生产 Docker/AuthZ 策略仍需独立验证。本地维护通道本轮只在新的 disposable VM 应用和验证，未连接生产，也未执行容器停止动作；不再新增阈值 EXP。
