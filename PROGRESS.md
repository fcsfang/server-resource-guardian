# PROGRESS — 项目进度档案

> 用途：项目当前状态总账。每次完成阶段性工作后，在本文件追加条目并 commit 即可对齐进度。
> 约定：新条目写在对应小节末尾，格式 `- YYYY-MM-DD 内容`。

## 项目一句话

服务器宕机风险检测与自动处置：复用 Beszel 做历史监控和可视化，补充轻量 Guardian 做实时风险检测、对象定位、策略判断、分级处置和恢复验证。原则：先观测、再判断、后自动化；先取证、再分级处置；资源限制只按业务需要采用。

## 当前阶段

**阶段 5：本地生产仿真性能报告 — ✅ 已完成**（生产交接仍待外部条件；执行蓝图见 [docs/14-execution-roadmap.md](docs/14-execution-roadmap.md)，自动执行路线见 [docs/16-autonomous-execution-roadmap.md](docs/16-autonomous-execution-roadmap.md)）

- 阶段 0 需求与环境确认：✅ 已完成
- 阶段 1 只观测 PoC：✅ 本地已完成
- 阶段 2 救援韧性验证：✅ 本地已完成，生产复核待授权
- 阶段 3 自动风险处置评估：✅ 本地已完成，业务动作策略待确认
- 阶段 4 Guardian 最小实现与受控灰度：✅ 本地已完成
- 阶段 5 本地生产仿真性能报告：✅ 已完成，生产测试权限待申请

## 当前活动目标

按 [自动执行路线](docs/16-autonomous-execution-roadmap.md) 推进：Goal 1–3 的本地 WSL2 PoC、Goal 4 本地 Guardian 闭环和 Goal 5 本地生产仿真报告均已完成。当前等待非生产 x86_64 Ubuntu 22.04 测试机与最小授权范围，生产动作仍未执行。目标任务记录、接手入口和完成标准见目标文件；真实实验结果见 [`experiments/`](experiments/README.md)。

## 环境清单

| 项 | 生产环境 | 当前主测试环境（Mac Multipass） |
| --- | --- | --- |
| 用途 | 目标生产服务器 | 功能和指标 PoC、压测与阈值校准 |
| 版本 | Ubuntu 22.04.5 / systemd 249 / cgroup v2 / Docker 29.1.3 | Ubuntu 22.04.5 / systemd 249 / cgroup v2 / Docker 29.1.3 / ARM64 |
| 资源边界 | 68 个容器中 67 个无资源边界 | 2 vCPU / 4GB 内存 / 40GB 虚拟磁盘上限 |
| Beszel 0.19.0 | 待生产部署评估 | Mac Multipass Hub/Agent 已部署且认证连接通过；指标字段核验待完成；历史 WSL2 结果仍单独保留 |
| 数据边界 | 生产原始报告不入库 | 运行态容器、`.env`、指标数据不入库 |

> 历史 WSL2 实验环境：Ubuntu 26.04.1 / systemd 259 / 内核 6.18 / Docker 29.1.3 / 8C/12G/4G swap。Goal 1–3 的实验结果仍以该环境为准，不改写为 Mac 实验。

## 已完成事项时间线

- 2026-09-16 生产环境基线采集完成：Ubuntu 22.04.5 / systemd 249 / cgroup v2 / Docker 29.1.3，68 容器中 67 个无资源边界（docs/09、docs/10）。
- 2026-09-16 WSL2 Ubuntu 26.04.1 环境初始化完成（8C/12G/4G swap），Docker + Compose 验证可用。
- 2026-09-16 隔离 Beszel 0.19.0 Hub/Agent 在 WSL2 Docker 上线，WebSocket 认证通过，容器 healthy；空载开销 Hub ≈13.3 MiB、Agent ≈6.5 MiB（docs/11）。
- 2026-09-16 调研与设计文档 12 篇就绪（docs/01–12：需求、选型、架构、实施计划、安全策略、PoC 蓝图、生产基线、leader 讨论研究）。
- 2026-09-17 完成汇报讲稿《服务器资源保护-现成策略优先-讲稿.md》（21 页，现成策略优先、自研仅作备选）。
- 2026-09-17 建立 git 仓库基线并首次提交，配置 GitHub 远程（github.com/fcsfang/server-resource-guardian）和 SSH key。
- 2026-09-17 将本地调研补充、最终汇报 PPT 与预览素材合并到仓库；远程仓库的工程文档、脚本和部署配置保持为主干。
- 2026-09-17 leader 最新汇报后要求围绕两层方案开始测试：第一层验证资源高压下的 SSH 与人工救援链路，第二层验证风险发现、对象定位和现成自动处置机制，再根据缺口评估 Guardian；交接与测试边界见 docs/13-leader-test-handoff.md。
- 2026-09-18 确定总执行路线蓝图（docs/14：以 Beszel 为监控基础，以 Guardian 风险检测和自动处置为主线，资源限制仅按业务需要采用），并统一为单一本地 WSL2 测试环境。
- 2026-09-18 完成 Goal 1 EXP-001：核验 Beszel 主机（CPU/内存/swap/磁盘/网络/load）、Docker 容器（2 个）和 systemd 服务（39 个含 docker/systemd-oomd）指标完整性；CPU 压测（4/8 核 x 120s）CPU 升至 50.20%、load 3.01；内存压测（1GB x 120s）内存升至 1.88 GB、swap 0.07 GB；Hub/Agent 负载开销可忽略（CPU <=0.03%，内存增量 <=2.3 MB）。结论和数据见 experiments/EXP-001-2026-09-18-beszel-metric-verification/record.md。
- 2026-09-18 Goal 1 全部完成（G1-T04）：基于 EXP-001 实测数据建立首轮本地告警阈值（CPU >70%/>90%、内存 >70%/>85%、Swap >100/>500 MB、Load 1m >4.0/>6.0），标注\u201c本地 PoC 校准值\u201d。Goal 1 状态改为 COMPLETED，下一步进入 Goal 2 第一层救援能力保障验证。
- 2026-09-18 完成 Goal 2 EXP-002：第一层救援能力保障验证。5 个场景（基线/CPU 无限制/CPU 限制 2 核/内存无限制/内存限制 512m）全部通过。核心发现：本地 Linux CFS 和内核内存管理在当前测试强度下保持 SSH 可用；Docker 资源限制可作为明确业务允许时的可选隔离手段，不是统一配置要求。生产复核清单已产出（G2-T06）。
- 2026-09-18 完成 Goal 3 EXP-003：第二层自动风险处置评估。核心发现：systemd-oomd 默认不 kill（ManagedOOM=auto）；Monit 适合已知服务固定规则；现成机制在保护名单、动作分级、动作前快照和动作后恢复验证方面存在缺口，构成 Guardian 最小开发范围。
- 2026-09-18 补充 EXP-004 极端压力测试：WSL2 减配至 4CPU/3.9GB/2GB，CPU 100% 持续、内存 92%+swap 98%（合计 95% 总内存资源）、CPU+内存同时极端、OOM 边界共 5 个场景。SSH 全程 0 失败（延迟峰值 488ms）。内核在资源耗尽时终止压力进程并恢复正常。结论和数据见 experiments/EXP-004-2026-09-18-extreme-stress-rescue/record.md。
- 2026-09-18 补充 EXP-005 真实故障模式验证（完整版）：多容器竞争、I/O、PID 耗尽测试 + 宿主机级内存耗尽导致 WSL2 整机崩溃（两次复现）。关键发现：sshd 被 OOM killer 保护（oom_score_adj=-1000）；SSH 失效的真实场景是宿主机内存耗尽导致系统崩溃。项目价值定位调整为提前发现风险并自动处置，资源限制仅作为按业务选择的可选防线。见 experiments/EXP-005-2026-09-18-realistic-failure-modes/record.md。
- 2026-09-18 完成 EXP-006 检测-定位-处置-恢复管道时效性测试：模拟 20MB/s 内存泄漏，完整管道（检测→定位→处置→恢复）可在 15 秒内完成（docker kill 可缩至 ~3s）。泄漏到临界有 141s 预警窗口。docker stats 定位即时（<1s），docker stop 耗时 13s（瓶颈），恢复 2s。管道速度远快于崩溃时间。见 experiments/EXP-006-2026-09-18-detection-response-pipeline/record.md。
- 2026-09-19 在 Mac Apple Silicon 上建立 `guardian-ubuntu` Multipass Ubuntu 22.04.5 ARM64 主测试机：2 vCPU、4GB 内存、40GB 虚拟磁盘上限；systemd、cgroup v2、Docker 29.1.3、Compose 2.40.3、systemd-oomd 和 PSI 验证通过。GitHub/Docker Hub 在虚拟机内出网不稳定，项目先通过宿主机文件传输同步；当前环境边界见 docs/16。
- 2026-09-19 完成 EXP-007 Mac Ubuntu 测试环境基线；完成 G4-T01 风险信号规范，定义 P0 内存风险、P1 对象定位、P2 辅助信号和风险状态机，设计稿见 docs/17-risk-signal-specification.md。
- 2026-09-19 完成 G4-T02 对象策略规范：定义稳定对象身份、永久保护名单、L0–L5 动作等级、冷却、熔断、执行前后检查和未知对象默认升级人工，设计稿见 docs/18-object-policy-specification.md。
- 2026-09-19 完成 G4-T03 第一版只读 Observer：采集 `/proc`、CPU/内存/I/O PSI、cgroup v2、Docker stats，使用连续窗口去抖并输出 JSONL；宿主机 5/5 单元测试、Ubuntu 虚拟机真实 `--once`、快照和审计验证通过，当前不执行任何动作。
- 2026-09-19 完成 G4-T04 `simulate`：根据风险状态、对象身份、保护状态和动作白名单生成动作计划；宿主机与 Ubuntu 虚拟机 7/7 测试通过，所有计划明确 `execution=not_executed`。
- 2026-09-19 完成 G4-T05 第一版受控动作适配器：实现授权、环境、目标 ID、保护名单、动作白名单和超时校验，提供 mock executor 与参数数组 Docker adapter；宿主机与 Ubuntu 虚拟机 12/12 测试通过，真实 enforce 等待本地可丢弃对象授权。
- 2026-09-19 完成 EXP-008：动作安全契约、mock executor、恢复状态、冷却窗口和失败熔断验证；宿主机与 Ubuntu 虚拟机 16/16 通过，真实 Docker 动作未执行。见 experiments/EXP-008-2026-09-19-guardian-safety-contract/record.md。
- 2026-09-19 完成 EXP-009：接入 `GuardianController`，串联 `enforce` 事件、授权门禁、对象保护、冷却、失败熔断和恢复判断；宿主机与 Ubuntu 虚拟机 23/23 通过，真实 Docker 动作未执行。见 experiments/EXP-009-2026-09-19-guardian-controller-integration/record.md。
- 2026-09-19 完成 EXP-010：新增 `guardian_enforce.py` 单次运行桥接，验证事件快照、授权文件、mock/真实适配器门禁和只读恢复探测；宿主机与 Ubuntu 虚拟机 28/28 通过，Ubuntu 实机 `--mode enforce` CLI smoke test 生成 `pending_controller` 快照，真实 Docker 动作未执行。见 experiments/EXP-010-2026-09-19-enforce-bridge-recovery/record.md。
- 2026-09-19 完成 EXP-011：Docker Hub 拉取超时后，使用 Ubuntu 自带静态 ARM64 BusyBox 构造 `guardian-test-base:local`；镜像已准备但未创建、启动或停止容器。见 experiments/EXP-011-2026-09-19-local-disposable-image-preparation/record.md。
- 2026-09-19 完成 EXP-012：在本地自动退出容器上完成 Ubuntu 实机 `observe/simulate`，成功定位稳定容器 ID，并在合成风险阈值下生成 `graceful_stop` 计划且保持 `execution=not_executed`；容器自然退出，真实动作未执行。见 experiments/EXP-012-2026-09-19-live-observe-simulate-object/record.md。
- 2026-09-19 完成 EXP-013：新增 `guardian.enforce.v1` 结构化审计记录，将动作前事件与动作后执行、恢复、冷却和失败熔断结果统一输出；宿主机与 Ubuntu 虚拟机 29/29 通过，真实 Docker 动作未执行。见 experiments/EXP-013-2026-09-19-enforce-audit-record/record.md。
- 2026-09-19 执行 EXP-014：在用户授权的本地 disposable 容器上真实调用一次 `graceful_stop`；Docker 返回 0 但目标以 exit 137 结束，修正后的恢复判定为 `target_force_killed`，实验 FAILED。随后修正 `ExitCode` fail-closed 判定和 `--timeout` 参数，宿主机与 Ubuntu 测试 30/30 通过；未重复真实动作。见 experiments/EXP-014-2026-09-19-real-graceful-stop-closed-loop/record.md。
- 2026-09-19 完成 EXP-015：在修正后的 SIGTERM trap 测试进程上再次执行真实 `graceful_stop`，目标 exit 0、OOMKilled=false，Guardian 审计返回 `recovered / target_stopped`；真实闭环通过。见 experiments/EXP-015-2026-09-19-real-graceful-stop-retry/record.md。
- 2026-09-19 完成 EXP-016：第一次真实动作成功写入持久化 ledger，第二次独立 CLI 调用在 Docker executor 前被 `cooldown_active` 拒绝；跨进程冷却验证通过。见 experiments/EXP-016-2026-09-19-persistent-cooldown-real-action/record.md。
- 2026-09-19 完成 EXP-017：复用已退出的本地 disposable 容器验证真实 Docker 恢复失败连续两次后升级，第三次请求在执行器前被失败熔断；注入 runner 验证动作超时与恢复窗口超时 fail-closed。见 experiments/EXP-017-2026-09-19-failure-escalation-timeout-contract/record.md。
- 2026-09-19 完成 EXP-018：双 disposable 容器实机 observe/simulate 将多对象竞争升级为 `ambiguous_object_identity`；无稳定 ID 和 unhealthy 健康状态测试均 fail-closed，临时容器已清理。见 experiments/EXP-018-2026-09-19-multi-object-health-observe/record.md。
- 2026-09-19 完成 EXP-019：在 Mac Multipass Ubuntu 空载测得 Guardian 单次 observe 峰值约 25.8 MiB、32 秒持续采样峰值约 26.5 MiB，CPU 累计约 0.12 秒；结果仅作为 0 容器基线。见 experiments/EXP-019-2026-09-19-guardian-local-resource-overhead/record.md。
- 2026-09-19 建立 Goal 5：在本地复刻生产关键运行时、容器角色和故障模式，完成无 Guardian/Guardian 对照及性能报告；不连接生产。
- 2026-09-19 完成 EXP-020/Goal 5：5 轮有效重复、CPU/IO/PID、churn、无 Guardian/Guardian 对照和压力下资源开销测量完成；老板报告见 experiments/EXP-020-2026-09-19-production-like-benchmark/report.md。
- 2026-09-19 完成 EXP-021/Goal 5：补足真实有效性对照。同一无界内存泄漏下，无 Guardian 复现 global OOM，健康探针、dockerd、sshd 和多个 systemd 服务受 OOM 影响；Guardian 在 critical 阈值执行授权 graceful_stop，目标退出 0、内存恢复、健康探针保持可用。主证据见 experiments/EXP-021-2026-09-19-failure-prevention-comparison/report.md。
- 2026-09-19 完成 EXP-022/Goal 5：补充多对象歧义、保护对象、CPU/IO 误报和恢复失败熔断边界；全部使用 observe/simulate 或纯 fixture，无新增真实动作。见 experiments/EXP-022-2026-09-19-policy-boundary-scenarios/record.md。
- 2026-09-19 完成 Mac Multipass Beszel 本地部署：Hub/Agent 0.19.0 镜像离线导入 `guardian-ubuntu`，Hub/Agent 均为 healthy；用户完成系统登记后，Agent 日志出现 WebSocket connected，连接后短窗口无新的 401/错误。当前运行状态和后续指标核验见 [docs/20](docs/20-local-beszel-multipass-deployment.md)。

## 待办事项（按优先级）

- [ ] 将 leader 最新反馈中的测试授权、保护名单和动作边界回填到 docs/05-open-questions.md。
- [x] Goal 4-T05：使用 `guardian_enforce.py` 完成真实 `graceful_stop` 闭环；EXP-014 的强制 kill 失败已修正并由 EXP-015 以 exit 0 验证成功。
- [x] Goal 4-T06：恢复、冷却、失败升级、超时、多对象竞争、误报边界和业务健康状态均已完成本地验证；证据见 EXP-014 至 EXP-018。
- [x] 在可丢弃测试对象上验证“检测 → 定位 → 保护判断 → 分级处置 → 恢复/升级”闭环；真实成功见 EXP-015，失败升级见 EXP-017，多对象升级见 EXP-018。
- [x] 补充多容器同时泄漏、误报、保护名单和恢复失败场景；证据见 EXP-022，不把统一 Docker 内存限制作为默认方案。
- [ ] 向公司确认仍未决的 P0/P1 问题：带外管理通道（Q-008）、SSH 失效实际表现（Q-006）、保护名单与可处置白名单（Q-009）、非生产测试机与故障注入授权（Q-010）。
- [ ] 生产兼容性复核：本地 WSL 版本高于生产（systemd 259 vs 249、内核 6.18 vs 6.8），结论需在 Ubuntu 22.04 测试机验证。


## 仓库同步备忘

```bash
# 开始工作前：拉取进度
git pull --rebase origin main

# 完成工作后：更新 PROGRESS.md，再提交推送
git add PROGRESS.md && git commit -m "progress: <一句话>" && git push origin main
```

注意：以下内容刻意不入库，只存在于本机：

- `deploy/beszel/.env`（本地 PoC 凭据）
- `reports/*`（生产环境采集产物，含生产信息）
- WSL2 内的 Docker 容器与 Beszel 指标数据（本地运行态，不属于仓库）
