# EXP-088:仿生产人工恢复验收(WSL 等效执行,宕机等效档)

- 日期:2026-09-22
- 状态:COMPLETED(待用户复核确认)
- 依据:`deploy/guardian-x86/PRODUCTION-LIKE-ACCEPTANCE.md`(1cc74de);用户逐级加码:"资源占用拉满,足够恶劣" → "完全占满一口气不剩,仿照宕机" → "数十个 hog 死一个顶一个持续爆满 + Guardian 开关对照"
- 执行者:Claude(Code 会话),用户通过飞书群同步接收全部告警并实时观察 Beszel
- 极端条件已固化为可复用工具包:`tools/collapse-lab/`

## 执行摘要

1. **人工维护通道在全部形态下可用** — 含最恶劣的 24-hog 持续 OOM 震荡 + swap 100%:11 步真实运维操作链全通,无一步永久挂死;纯 shell 诊断(登录/free/dmesg/journalctl/ps)即使在爆满相位也只要 0.4–4.9s。
2. **观察者效应(E10)**:同等爆满相位下,Guardian 开启使 docker 类人工操作合计 11.7s → 190.5s(单步最高 21 倍)— Collector 每 2s 的 daemon 全量快照与人工处置抢 daemon 内存预算。
3. **观测系统自损(E12)**:Swarm 持续钉死形态(与单 hog 的 45s 自救周期形态相反)下,Guardian runtime 12 分钟内崩溃 3 次(2×FAILURE + 1×SIGABRT,systemd 拉起),审计流出现 13 次 >10s 中断(最长 152s);Beszel agent↔hub SSH 断连重连。单 hog 周期形态下这些现象均未出现 — **持续钉死形态才是观测系统的杀手**。
4. **内核 OOM 自救在单 hog 形态下目标精准**(全部命中压力进程,关键服务 NRestarts=0),Swarm 形态的误伤面未测。
5. 告警链路(审计→飞书)在坍缩期降级但未失能:runtime 崩溃期间无事件(网关如实发 stale 告警),恢复后告警补报;网关自身(轻量 tail)全程存活,是坍缩期唯一持续履职的 Guardian 组件。

## 环境与等效性声明

| 项 | 文档要求 | 本次执行 | 差异 |
| --- | --- | --- | --- |
| TARGET | Ubuntu 22.04 x86_64 | WSL2 Ubuntu 26.04 x86_64,4 vCPU / 3.8Gi+2G swap | 版本不同;资源规模小,压力更易拉满 |
| CLIENT | 另一台机器 | Windows 宿主,SSH 密钥直连 WSL IP(192.168.225.53) | 非独立物理机,但为跨 OS TCP 通道 |
| 带外入口 | 云/虚拟机控制台 | `wsl.exe` root 直连 | 等效存在,未动用 |
| Guardian | observe、自动动作关闭 | observe、actions.enabled=false、allow=[]、两个 Broker 关闭 | 一致 |
| 告警通道 | Beszel/Guardian 显示风险 | Guardian 审计 + 飞书群实时消息 | 增强可观测 |

## 场景结果总表

| 场景 | 压力强度 | 明确告警(飞书) | 外部新 SSH | 诊断可用 | 仅停测试对象 | 资源恢复 | 其他应用存活 | 结论 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1a CPU 域内 | workload.slice 内 1 核拉满(100.19%) | 无(host 25% 未达线,见发现 E5) | 0.18–0.20s ×5 | ✓ | ✓ | ✓ | 6/6 | 通过 |
| 1b CPU 恶劣 | 裸容器 4 核全满(392.7%) | CRITICAL 99.9% @+7s | 0.26–0.31s ×5 | ✓(0.29s 含 docker stats×9) | ✓ | ✓ | 6/6 | 通过 |
| 2a 内存域内 | workload.slice 内 480MiB(逼近 512M 上限) | 无(域内 PSI full 80%,host available 71%) | — | ✓ | ✓ | ✓ | 6/6 | 通过(隔离生效) |
| 2b 内存文档档 | 2.3Gi(60%) | 无(available 24.2% > 15% 线,按文档记"未触发") | — | ✓ | ✓ | ✓ | 6/6 | 通过(按文档处理) |
| 2c 内存恶劣 | 3.2Gi,available 最低 4.4–5.2%,swap 启用 | CRITICAL 可用 4.4% @+9s | 0.17–0.54s ×5 | ✓ | ✓ 无 OOM | ✓ 3.3Gi | 6/6 | 通过 |
| 3 磁盘 100% | 4GiB loop 盘写满(剩 0.5%) | CRITICAL 剩余 0.5% @+秒级 | 0.20–0.46s ×5 | ✓ | ✓ + 文件删除 | ✓ 1%→94.4% 观测 4s | 6/6 | 通过 |
| 4 CPU+内存叠加 | 全核 + available 4.1% | CRITICAL ×2 同刻(99.5% / 4.5%) | 0.27–0.38s ×5(含诊断) | ✓ | ✓ 按序停止 | ✓ | 6/6 | 通过 |
| 5 三重+全 SSH 处置链 | 全核 + available 3.9% + IO 直写满载 | (复用 4 的告警态) | 全链 12 步见下 | ✓ 全步 | ✓ 全 SSH 停止 | ✓ | 6/6 | 通过 |

## 场景 5:全 SSH 人工处置链(三重极端窗口内,每步真实计时)

压力:CPU 8×busy-loop(全核,load 8.09)+ mem_hog 3.2GiB(available 147Mi)+ dd oflag=dsync(IO 打满)。
全部动作经 SSH(csfang@WSL)执行,每步独立会话:

| 步骤 | 耗时 | 结果 |
| --- | --- | --- |
| 1 登录+uptime | 0.30s | load 8.09 |
| 2 free | 0.28s | available 147Mi |
| 3 dmesg(内核日志) | 0.32s | 正常返回 |
| 4 journalctl(服务日志) | 0.82s | 正常返回 |
| 5 定位 CPU 源(ps sort=-%cpu) | 0.38s | 识别 busy-loop sh 进程 |
| 6 定位内存源(ps sort=-rss) | 0.28s | 正常返回 |
| 7 docker ps | 0.34s | 9 容器列表 |
| 8 docker stats --no-stream(重命令) | 1.76s | daemon 仍响应 |
| 9 停 CPU 源(docker stop --time 20) | 20.57s | 20s 为温和等待设计值(sh 不转发 SIGTERM,见 E6) |
| 10 停 IO 源 | 20.41s | 同上 |
| 11 停内存源 | 20.43s | 同上 |
| 12 验证恢复(sleep 5 + free) | 5.50s | load 8.09→3.63 回落 |
| **处置链合计** | **≈71s**(其中 60s 为温和停止设计等待) | 全部成功 |

诊断 4 步合计 <2s;真正"决策+执行+验证"开销 ≈11s。

## 场景 6:宕机等效 — 系统级全局 OOM(RAM+swap 完全耗尽)

用户要求"完全占满,一口气不剩,仿照可能宕机的情况"。压力改为**无界容器**(不设 `--memory`)+"分配到死" hog;分三段:突发双 hog、慢速挤兑(256MiB/3s,给 swap 回写时间)、OOM 震荡(`restart=on-failure`,被杀后自动回来再吃)。

**坍缩点实测**(慢速挤兑 t=7):available 130Mi、**swap 2047/2048Mi(100% 耗尽)**、该瞬间 SSH 0.56s(全程最慢,仍成功);数秒后内核全局 OOM 击杀 hog,available 跳回 3.4Gi — 内核在临界点亲自完成"处置"。

**关键数字**:
- 全局 OOM(CONSTRAINT_NONE)击杀 ×3,burst 轮 ×2;OOM 周期 ≈45–50s
- 23 次 SSH 探测(挤兑 14 + 震荡 9)**全部成功**,最慢 0.56s 出现在 swap 100% 瞬间
- 被杀者全部是压力进程本身(oom_score 选中最大 anon-rss);chronyd 两次作为无辜分配者触发 oom-killer 调用但幸存
- **零附带损伤**:guardian-runtime / guardian-collector / feishu-gateway / ssh 的 `NRestarts=0`,全程无中断
- 数据:`data/oom-collapse.txt`

**结论**:即使仿照宕机(RAM+swap 全耗尽、内核 OOM 反复开杀),外部 SSH 人工通道全程可用,Guardian 观测与飞书告警链路无中断。维护域的 `AllowedCPUs=0` 硬隔离对内存型坍缩无效(内存无核界),SSH 幸存靠:sshd 的 oom_score 较低(常驻、RSS 小)、每次探测都是短生命周期轻进程、以及内核 OOM 自救周期短(秒级)。这印证文档边界:是"提高可用概率",不是保证 — 若压力源是**大量中等进程**(而非单个大 hog),OOM 误伤面会更大,该形态未在本轮覆盖。

## 场景 7:24-hog Swarm 持续爆满 + Guardian 开/关 A-B-A 对照(观察者效应)

按用户要求升级恶劣程度:24 个 280MiB 持有型 hog 容器(`restart=on-failure:100`),内核杀掉几个、restart 立即补上,系统**全程钉在 OOM 震荡边缘**(RAM 剩 44Mi、swap 2048/2048Mi、13+ 次 kill)。在同一爆满状态下执行同一套 11 步真实运维操作链(全部经 SSH),对比 Guardian 三件套开/关:

| 操作 | ON(风暴初期) | OFF(成熟相位) | ON(成熟相位) |
| --- | --- | --- | --- |
| login+uptime | 20.78s | 1.71s | 2.68s |
| free | 10.06s | 0.79s | 2.07s |
| df | 10.06s | 0.65s | 3.87s |
| dmesg tail | 15.97s | 0.98s | 1.22s |
| journalctl | 15.49s | 1.25s | 4.86s |
| top-mem proc | 5.63s | 0.36s | 2.14s |
| docker ps | **733.93s** | 0.51s | 21.77s |
| docker stats | 16.79s | 7.16s | **150.15s** |
| STOP 一个 hog(t=5) | 5.98s | 4.07s | 18.55s |
| verify: free | 0.29s | 2.18s | 3.36s |
| verify: docker ps | 0.30s | 3.50s | 54.06s |
| **合计** | **835.3s** | **23.2s** | **265.7s** |

**分组结论**(同相位的 OFF vs ON):
- 纯 shell 诊断(login/free/df/dmesg/journalctl/ps):合计 4.7s → 16.8s,单步 2–4 倍、绝对值秒级 — 人工通道依然好用
- **docker daemon 类操作(ps/stats/stop):合计 11.7s → 190.5s,单步最高 21 倍** — 机制:Collector 每 2s 对 docker daemon 发全量快照请求,而 daemon 自身正被 OOM 震荡饿着,人工诊断请求与观测快照直接竞争 daemon 的内存分配

**观察者效应**:Guardian 在坍缩期通过 Collector 周期快照挤占 docker daemon 预算,放大了"通过 docker 定位/处置压力源"的人工操作延迟 — 这正是管理员最需要 daemon 的时刻。候选产品改进:**Collector 观测退避** — 主机内存 PSI 超阈值时自动降频/暂停 docker 快照,把 daemon 预算让给人工处置;恢复后自动回到 2s。

**诚实声明**:单次 A-B-A、无重复、风暴相位不可完全控制(Run1 与 Run3 相差 3 倍);方向与机制自洽,统计置信需多重复实验。

**通道可用性**:三跑中没有任何一步永久挂死 — 最恶劣单步(733.93s docker ps)也最终返回。即使 daemon 冻结,纯 shell 路径(`ps` 定位 PID → 杀容器内进程)可完全绕开 daemon 完成处置。

数据:`data/aba-guardian-onoff.txt`

## 场景 8:观测系统自损评估(24-hog 持续爆满期,三方取证)

用户在坍缩窗口实时观察:Beszel 上报时断时续、审计多次中断 >10s、"Guardian 已不能正常工作且起反作用"。取证结果全部证实:

**Guardian runtime 崩溃时间线**(journalctl,全部发生在 Swarm 窗口 17:59–18:09):
| 时刻 | 事件 | systemd 恢复 |
| --- | --- | --- |
| 18:00:46 | Main process exited, status=1/FAILURE | 18:03:06 Started(退避 2m20s) |
| 18:04:56 | SIGABRT(status=6/ABRT) | 18:05:13 Started |
| 18:08:43 | Main process exited, status=1/FAILURE | 18:09:01 Started |

即:**12 分钟内崩溃 3 次,观测中断累计 ≈4.5 分钟**。崩溃间的采样循环也被换出拖慢(见下)。

**审计流中断**(飞书网关 stale 检测,窗口 18:00–18:12):**13 次中断告警** + 13 次恢复,全部集中在 Swarm 窗口;最长单次空洞 152s(18:05:14→18:07:46)。对照:场景 1–6(单 hog / 三重叠加,45–50s OOM 自救周期)**零中断** — 说明持续钉死形态(死一个顶一个、内核永不喘息)才是观测系统的致命形态。网关自身 NRestarts=0 全程存活(18:13/18:16 的停起是 A-B-A 实验操作,非故障),是坍缩期唯一持续履职的 Guardian 组件。

**Beszel 断续**(agent 容器日志):agent↔hub 的 SSH 通道在 18:08:38、18:12:06 两次断线重连,窗口内 5 个错误/失败行 — 平台监控同样降级。此时服务器的"真实受影响程度"恰由这两个监控系统的自损如实反映。

**坍缩深度读数**(网关 18:16:36):宿主内存可用 **1.6%**、交换空间已用 **100.0%** — 全实验最深。

**结论**:用户的判断成立 — 在 Swarm 持续钉死形态下,Guardian observe (a) runtime 反复崩溃、(b) 审计流大面积断续(告警链路降级)、(c) Collector 与人工处置竞争 daemon(E10),此时 Guardian"不能正常工作"且净效应为负。改进候选(按优先级):① Collector 观测退避(E10,PSI 触发降频 docker 快照);② runtime 资源自适应(自身 RSS 高压时降采样频率、削减审计体积);③ systemd watchdog + StartLimitInterval 调优,让崩溃重启更快、退避不叠加;④ 飞书网关的 stale 告警表现良好,保持现状。

**取证方法局限**:对 events.jsonl 做 gap 分析不可靠(logrotate copytruncate + 轮转),网关 journalctl 日志是权威证据源(40_evidence.sh 已固化此方法)。

## 环境发现(回填上游候选)

- **E1** workload.slice 设 AllowedCPUs=1 时,Docker 校验 `--cpus` 上限为该 cpuset 核数(拒绝 `--cpus 4`,"range of CPUs is from 0.01 to 1.00")。
- **E2** docker.service 进入 rescue.slice(AllowedCPUs=0)后,daemon 的 Go runtime CPU 视角为 1 核,所有 `--cpus` 校验按 1 核执行 — 与 E1 是同一机制在两个位置的表现;容器内多进程仍可超配(无 quota 时)。
- **E3** cgroup v2 将容器写盘产生的 page cache 计入该容器内存账:`--memory 128m` 的 dd 写盘容器在 ~745M 未回写 cache 处被 OOM kill(exit 137)。写满盘场景容器内存限额需 ≈1Gi 或用 oflag=dsync。
- **E4** WSL2 available 含可回收 cache,60% 文档档(2.3Gi)压到 available 24.2%,不触发 15% 告警线 — 与文档"未触发则记录,不得加压"一致;恶劣档 3.2Gi 才进入 CRITICAL。
- **E5** 域内单核拉满(host aggregate ≈25%)不触发 host 级 CPU 告警 — host 级告警语义是"整机利用率",单核饱和在多核机上属正常;容器级告警需对象归因(当前 fail-closed 不升级)。
- **E6** alpine `sh -c` 包装不转发 SIGTERM 给子进程,`docker stop --time 20` 全部等满 20s 后 SIGKILL — "温和停止"在 shell 包装场景退化为"等待+强杀";压力进程应直连 PID 1 或用 exec 形态。
- **E7** 第一轮写满后残留文件使第二轮"截断重写同名文件"产生空间复用错觉 — 写满脚本应写新文件或先清理。
- **E8** 内存坍缩路径由压力形态决定:无界激进分配 6 秒内直达全局 OOM(swap 几乎未启用,~190Mi);慢速分配(256MiB/3s)才走完"swap 100% → 全局 OOM"完整路径。生产内存告警/验收应区分突发与渐变两种形态。
- **E9** 全局 OOM 的目标选择完全正确(全部命中最大 anon-rss 的压力进程),小 RSS 常驻服务(ssh/Guardian)在单一大 hog 形态下未被误伤;但该结论不可外推到多进程中等体量的泄漏形态。
- **E10(观察者效应)** 坍缩期 Collector 每 2s 的 docker daemon 全量快照与人工处置的 daemon 请求直接竞争:同等爆满相位下 docker 类人工操作 11.7s(OFF)→ 190.5s(ON),最高单步 21 倍;纯 shell 操作仅 2–4 倍。观测系统在极端期应具备自适应退避(PSI 触发降频),把 daemon 预算让给人工处置。
- **E11** docker restart 的 on-failure 退避会让 swarm 阵列的活跃容器数随时间衰减(24→14),长爆满实验需周期性补位或用更小的 restart 退避。
- **E12(观测系统自损)** 持续钉死形态(Swarm,死一个顶一个,内核无喘息)下:Guardian runtime 12 分钟崩溃 3 次(FAILURE×2 + SIGABRT),审计流 13 次 >10s 中断(最长 152s),Beszel agent↔hub 断连重连;单 hog 周期自救形态(45–50s)下同样组件零异常。**观测系统的存活判据不是压力峰值,而是内核是否给观测组件留出喘息周期** — 这与 E8(坍缩路径由压力形态决定)互为印证。轻量旁路组件(飞书网关,只 tail 文件)全程存活。

## 安全边界遵守情况

- 磁盘测试全部在 4GiB loop 盘(/var/tmp/guardian-acceptance.img → /mnt/guardian-acceptance),根盘仅 +4GB(0.4%),未触碰业务数据。
- 仅停止/删除 `guardian-accept-*` 前缀的自建对象;Guardian/Beszel/SSH/飞书网关全程存活。
- 自动动作全程关闭,审计 `EMERGENCY_SHEDDING_DISABLED` / action=none 持续在案。
- 测试后清理:压力容器与压力文件全部移除,Guardian 配置中新增的 `/mnt/guardian-acceptance` 监控路径保留(供复测)。

## 与文档结论的一致性

"验收通过只能证明本次压力下人工通道可用"。本次在 WSL 等效环境、恶劣档(整机饱和)下:外部新建 SSH(0.17–0.54s)、日志查看、进程定位、Docker 查询、温和停止、恢复验证全链可用;维护域(rescue.slice AllowedCPUs=0 硬隔离)是 SSH 稳定的关键护栏,但内存型压力(available 4%)不区分核,SSH 仍靠内核回收与轻量命令存活 — 与文档"提高可用概率,不是绝对保证"的表述一致。
