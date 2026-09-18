# PROGRESS — 项目进度档案

> 用途：项目当前状态总账。每次完成阶段性工作后，在本文件追加条目并 commit 即可对齐进度。
> 约定：新条目写在对应小节末尾，格式 `- YYYY-MM-DD 内容`。

## 项目一句话

服务器资源监控、危机预警与受控处置：复用 Beszel 做监控主干，补充轻量 Guardian 做危机取证与受控处置。原则：先观测、再建议、后自动化；先取证、再处置；先限流、再终止。

## 当前阶段

**阶段 1：只观测 PoC — ✅ 已完成**（执行蓝图见 [docs/14-execution-roadmap.md](docs/14-execution-roadmap.md)，五阶段计划见 [docs/04-delivery-plan.md](docs/04-delivery-plan.md)）

- 阶段 0 需求与环境确认：✅ 已完成
- 阶段 1 只观测 PoC：✅ 已完成
- 阶段 2 Guardian 与人工处置：⚪ 未开始（src/ 为空；下一阶段）
- 阶段 3 有限自动化 / 阶段 4 灰度推广：⚪ 远期

## 当前活动目标

按 [执行蓝图](docs/14-execution-roadmap.md) 推进：[Goal 1：只观测 PoC 收尾](goals/resource-protection.md#goal-1只观测-poc-收尾)（`IN_PROGRESS`），随后进入 Goal 2、Goal 3。目标任务记录、接手入口和完成标准见目标文件；真实实验结果见 [`experiments/`](experiments/README.md)。

## 环境清单

| 项 | 生产环境 | 本地 PoC（当前电脑 WSL2） |
| --- | --- | --- |
| 用途 | 目标生产服务器 | 功能和指标 PoC、压测与阈值校准 |
| 版本 | Ubuntu 22.04.5 / systemd 249 / cgroup v2 / Docker 29.1.3 | Ubuntu 26.04.1 / systemd 259 / 内核 6.18 / Docker 29.1.3 |
| 资源边界 | 68 个容器中 67 个无资源边界 | 8C/12G/4G swap |
| Beszel 0.19.0 | 待生产部署评估 | Hub/Agent 已上线，认证通过 |
| 数据边界 | 生产原始报告不入库 | 运行态容器、`.env`、指标数据不入库 |

## 已完成事项时间线

- 2026-09-16 生产环境基线采集完成：Ubuntu 22.04.5 / systemd 249 / cgroup v2 / Docker 29.1.3，68 容器中 67 个无资源边界（docs/09、docs/10）。
- 2026-09-16 WSL2 Ubuntu 26.04.1 环境初始化完成（8C/12G/4G swap），Docker + Compose 验证可用。
- 2026-09-16 隔离 Beszel 0.19.0 Hub/Agent 在 WSL2 Docker 上线，WebSocket 认证通过，容器 healthy；空载开销 Hub ≈13.3 MiB、Agent ≈6.5 MiB（docs/11）。
- 2026-09-16 调研与设计文档 12 篇就绪（docs/01–12：需求、选型、架构、实施计划、安全策略、PoC 蓝图、生产基线、leader 讨论研究）。
- 2026-09-17 完成汇报讲稿《服务器资源保护-现成策略优先-讲稿.md》（21 页，现成策略优先、自研仅作备选）。
- 2026-09-17 建立 git 仓库基线并首次提交，配置 GitHub 远程（github.com/fcsfang/server-resource-guardian）和 SSH key。
- 2026-09-17 将本地调研补充、最终汇报 PPT 与预览素材合并到仓库；远程仓库的工程文档、脚本和部署配置保持为主干。
- 2026-09-17 leader 最新汇报后要求围绕两层方案开始测试：第一层验证资源高压下的 SSH 与人工救援链路，第二层先验证现成自动资源保护策略，再根据缺口评估 Guardian；交接与测试边界见 docs/13-leader-test-handoff.md。
- 2026-09-18 确定总执行路线蓝图（docs/14：以 Beszel 为监控基础、现成机制优先、Guardian 仅补缺口），并统一为单一本地 WSL2 测试环境，移除旧文档中两台机器的分工描述。
- 2026-09-18 完成 Goal 1 EXP-001：核验 Beszel 主机（CPU/内存/swap/磁盘/网络/load）、Docker 容器（2 个）和 systemd 服务（39 个含 docker/systemd-oomd）指标完整性；CPU 压测（4/8 核 x 120s）CPU 升至 50.20%、load 3.01；内存压测（1GB x 120s）内存升至 1.88 GB、swap 0.07 GB；Hub/Agent 负载开销可忽略（CPU <=0.03%，内存增量 <=2.3 MB）。结论和数据见 experiments/EXP-001-2026-09-18-beszel-metric-verification/record.md。
- 2026-09-18 Goal 1 全部完成（G1-T04）：基于 EXP-001 实测数据建立首轮本地告警阈值（CPU >70%/>90%、内存 >70%/>85%、Swap >100/>500 MB、Load 1m >4.0/>6.0），标注\u201c本地 PoC 校准值\u201d。Goal 1 状态改为 COMPLETED，下一步进入 Goal 2 第一层救援能力保障验证。
- 2026-09-18 完成 Goal 2 EXP-002：第一层救援能力保障验证。5 个场景（基线/CPU 无限制/CPU 限制 2 核/内存无限制/内存限制 512m）全部通过。核心发现：Docker --cpus=2 完全保护 SSH（响应与基线无差异）；Docker --memory=512m 成功通过 cgroup OOM killer 保护宿主机；即使无资源限制，Linux CFS 在当前测试强度下仍保持 SSH 可用。生产复核清单已产出（G2-T06）。Goal 2 状态改为 COMPLETED，下一步进入 Goal 3。
- 2026-09-18 完成 Goal 3 EXP-003：第二层现成自动资源保护策略评估。核心发现：systemd-oomd 默认不 kill（ManagedOOM=auto）；Docker --memory 限制配合内核 OOM killer 是最可靠的自动保护（EXP-002 S4 已验证）；Monit 适合已知服务固定规则。缺口分析确认 4 个缺口（保护名单/动作分级/动作前快照/动作后验证），构成 Guardian 最小开发范围。Goal 3 状态改为 COMPLETED。
- 2026-09-18 补充 EXP-004 极端压力测试：WSL2 减配至 4CPU/3.9GB/2GB，CPU 100% 持续、内存 92%+swap 98%（合计 95% 总内存资源）、CPU+内存同时极端、OOM 边界共 5 个场景。SSH 全程 0 失败（延迟峰值 488ms）。内核在资源耗尽时终止压力进程并恢复正常。结论和数据见 experiments/EXP-004-2026-09-18-extreme-stress-rescue/record.md。

## 待办事项（按优先级）

- [ ] 将 leader 最新反馈中的测试授权、保护名单和动作边界回填到 docs/05-open-questions.md。
- [ ] 根据执行蓝图开始 Goal 3 第二层现成自动资源保护策略评估：systemd-oomd / Monit 只读与模拟验证。
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





