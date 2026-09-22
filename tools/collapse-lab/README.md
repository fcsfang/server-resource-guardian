# Collapse Lab — 极端资源条件实验工具包

EXP-088 验证过的可复用压力条件与测量方法。用于在受控一次性环境(WSL2 / disposable VM)重现"宕机等效"状态,测量人工维护通道与观测系统在极端条件下的表现。

## 安全边界(每次使用前重读)

- 只在可回滚的一次性环境执行;Windows/宿主侧会随 WSL CPU 拉满而短暂卡顿。
- 内存坍缩时内核 OOM killer 可能误杀任何进程(实验中曾波及无辜调用者);关键服务需 systemd 自愈能力。
- 磁盘场景只允许 loop 独立盘;禁止对根盘或业务盘。
- 只停止/删除 `guardian-accept-*` / `guardian-oom-*` 前缀的自建对象。
- 磁盘写满容器需要 ≥1Gi 内存限额(cgroup v2 把 page cache 记入容器账,128m 会自毁,见 EXP-088 E3)。
- 带外入口(wsl.exe / VM 控制台)必须先验证可用。

## 前置

- WSL2 Ubuntu(systemd 开启)或等效 disposable VM;Docker;cgroup v2。
- TARGET 上 root 执行 01/02;CLIENT(Windows)需 OpenSSH 客户端与密钥。
- Guardian observe 部署 + 飞书网关(可选,用于观测自损对照)。

## 用法速查(在 WSL root)

```bash
LAB=<repo>/tools/collapse-lab
bash $LAB/01_preflight.sh                     # 规格/服务/loop 支持检查
bash $LAB/02_ssh_client_setup.sh              # 授权 CLIENT 密钥(读 /mnt/c 密钥路径按需改)
bash $LAB/10_baseline_apps.sh                 # 6 个对照应用 + 压力镜像 + hog 助手
bash $LAB/11_cpu_pressure.sh workload         # 域内 1 核拉满(workload.slice)
bash $LAB/11_cpu_pressure.sh bare             # 全核拉满(无隔离)
bash $LAB/12_mem_pressure.sh bare 2300 600    # 60% 文档档
bash $LAB/12_mem_pressure.sh unbounded 3200 900  # 无界 → OOM 领域
bash $LAB/13_disk_pressure.sh setup           # 4GiB loop 盘 + 加入 Guardian 监控
bash $LAB/13_disk_pressure.sh fill            # 写满(100%)
bash $LAB/13_disk_pressure.sh recover         # 停写入者 + 删压力文件
bash $LAB/14_combo_triple.sh                  # CPU 全核 + 内存 3.2Gi + IO 直写
bash $LAB/15_oom_swarm.sh start 24 280        # 24×280MiB 阵列,OOM 震荡钉死
bash $LAB/15_oom_swarm.sh topup 8             # 补位(restart 退避导致衰减时)
bash $LAB/15_oom_swarm.sh stop                # 清理阵列
bash $LAB/20_guardian_ctl.sh off              # A-B-A:模拟未部署主机
bash $LAB/20_guardian_ctl.sh on
bash $LAB/40_evidence.sh                      # 审计空洞/网关告警/Beszel 证据
bash $LAB/50_cleanup.sh                       # 全量清理 + 恢复检查
```

CLIENT 侧(Windows PowerShell):

```powershell
powershell -File <repo>/tools/collapse-lab/30_ops_chain.ps1 -Label ON_run1 -StopTarget guardian-oom-swarm-3
```

## 实验形态 ↔ 已知结论(EXP-088)

| 形态 | 脚本 | 已测结论 |
| --- | --- | --- |
| 域内压力 | 11 workload / 12 slice | 隔离生效,host 无感;docker `--cpus` 校验受 cpuset 限制(E1/E2) |
| 单 hog 坍缩 | 12 unbounded + hog_growth | 6s 直达全局 OOM(突发)或 45s 走完 swap 100%(慢速),OOM 周期性自救(E8) |
| Swarm 持续钉死 | 15 swarm | 死一个顶一个,系统持续在 OOM 边缘;观测系统崩溃期(E12) |
| A-B-A 观察者效应 | 20 + 30 | docker 类人工操作 OFF 11.7s vs ON 190.5s(E10) |

## 文件

- `lib/hog_hold.py` — 持有 N MiB 后 sleep(持有型压力)
- `lib/hog_growth.py` — 阶梯增长直到 MemoryError(增长型/无界)
- `30_ops_chain.ps1` — CLIENT 侧 11 步真实运维操作链,每步 SSH 独立会话计时
