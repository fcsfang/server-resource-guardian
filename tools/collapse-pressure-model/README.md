# Collapse pressure model (EXP-088/收益实验验证版)

本目录保存经过两轮实验(EXP-088、2026-09-23 收益对照)验证的"宕机等效"压力模型。
在 4 vCPU / 3.8 GiB + 2 GiB swap 的 WSL2 环境实测达到的状态:

- **RAM 剩 ~44-67Mi、swap 100% 耗尽、连续全局 OOM kill**(死一个顶一个的钉死形态)
- **Beszel 上报时断时续**(agent↔hub SSH 断连重连)
- **SSH 命令行基本不可用**(诊断类操作 5-21s,`docker ps` 733s,盲找定位 18-19 分钟)
- **当前版 Guardian runtime 12 分钟内崩溃 3 次**,审计流 13 次 >10s 中断

即:这个压力水平 = **宿主上所有监控与运维工具同时降级/失效的临界区**。
作为标准实验环境复用;不要在生产或非一次性环境使用。

## 模型组成

| 文件 | 作用 |
| --- | --- |
| `hog_hold.py` | 持有型压力:占 N MiB 并保持(24×280MiB = Swarm 标准形态)|
| `hog_growth.py` | 增长型压力:阶梯增长直到内核拒绝(触发单点全局 OOM)|
| `swarm.sh` | Swarm 编排:start/topup/stop;restart=on-failure 死一个顶一个 |
| `guardian_ctl.sh` | Guardian 三件套开关(A-B-A 对照用)|
| `ops_chain.ps1` | CLIENT 侧 11 步真实运维操作链,每步 SSH 独立会话计时 |
| `emergency_cleanup.sh` | 应急清理:杀全部 hog + 恢复服务(实验中断时也先跑这个)|

## 标准复现步骤(WSL root)

```bash
# 0) 前置:python:3.12-alpine 镜像在本地;带外入口(wsl.exe/VM 控制台)确认可用
# 1) Swarm 钉死形态(24 容器 × 280MiB,需求 6.7GiB > 3.8+2 GiB)
bash swarm.sh start 24 280
#    ~40s 后进入钉死:RAM <70Mi、swap 100%、OOM kill 持续累计
# 2) 观察/操作/计时(实验窗口)
bash ops_chain.ps1 -Label run1            # Windows 侧
# 3) 应急清理(任何时刻可执行;docker daemon 极慢时转后台等)
bash emergency_cleanup.sh
```

## 已验证的关键参数(勿随意改)

- **24 × 280MiB + restart=on-failure:100**:钉死形态的最低稳定配比;减少数量会退化为
  "45-50s 周期自救"形态(观测系统可存活,见 EXP-088 E8/E12 的形态对照)
- **restart 退避会导致活跃容器数随时间衰减**(24→14),长窗口实验需 `topup` 补位
- **写盘类压力容器内存限额必须 ≥1Gi**(cgroup v2 把 page cache 记入容器账,128m 自毁)
- **`docker stop --time N` 在 `sh -c` 包装下必然等满 N 秒**(alpine sh 不转发 SIGTERM)
- docker daemon 自身被放进 rescue.slice 后,`--cpus` 校验视角 = 1 核(绕法:容器内多进程)

## 形态对照(实验结论速查)

| 形态 | 触发 | 观测系统命运 | 人工通道 |
| --- | --- | --- | --- |
| 单点周期自救(1 个 unbounded hog)| 内核每 45-50s 杀一次最大进程 | **零异常**(有喘息周期)| SSH 0.17-0.56s |
| **Swarm 钉死(本模型)** | 死一个顶一个,内核无喘息 | runtime 崩溃 3 次/12min,审计 13 次中断,Beszel 断续 | SSH 秒级但诊断 5-21s,docker 类 150-733s,盲找 18-19 分钟 |

## 三资源形态(2026-09-24 验证,新增)

在 Swarm 钉死基础上叠加 CPU 与磁盘压力(CPU 4×spin 每核一个 + dd 1GiB fdatasync 循环),
形成"三资源同时爆满"形态。两轮防御臂 + 一轮基线臂实测:

- **防御栈在位:进入能力 19/20×2(95%),docker 侧 ops 6.8s(0.58× off 基线),宿主不宕机**
- **基线(栈全关):进入能力 8/20(40%),docker_stats 241s,PSI-mem full 45.3** — 比内存-only
  基线(52.5%)更糟:换页与 fdatasync 抢同一条磁盘带宽,swap 碾轧被进一步加深
- **机制结论:三资源中只有内存达到 PSI-full(杀进程级);CPU/IO 只排队不封死** —
  内核 full: cpu 0.0 / io ~0.5 / mem 9.9(防御),故内存侧防御栈覆盖了三资源形态的致命轴
- 复现脚本模式见 `results/2026-09-24-triple-resource/`(cpu_spin.py +
  disk_churn.sh + ben_swarm.sh 三段启动,330s 看门狗保证清理与栈还原)

## 收益对照已有数据(2026-09-23,A2/B1 轮)

- 无 Guardian 盲找:1106s / 1160s,两次无法可靠指认真凶(一次误指 96Mi 无辜应用)
- 有 Guardian:真实告警 ≈41s(宿主内存 CRITICAL 可用 5.3%)+ `guardian-rescue top` 一步定位 0.34s(精确容器身份)≈ 42s

## 安全边界

- 只在一次性/可丢弃环境使用;禁止生产
- 只创建/清理 `ben-swarm-*` / `guardian-accept-*` / `guardian-oom-*` 前缀对象
- Windows 宿主会随 WSL CPU 拉满短暂卡顿;实验时长控制在 30 分钟内
- 任何时刻 `emergency_cleanup.sh` 是唯一出口;先于一切诊断执行
