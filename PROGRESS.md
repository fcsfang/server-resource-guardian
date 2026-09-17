# PROGRESS — 双机进度同步档案

> 用途：公司电脑与本地电脑共同维护的对账单。每次完成阶段性工作后，在本文件追加条目并 commit，另一台电脑 pull 后即可对齐进度。
> 约定：新条目写在对应小节末尾，格式 `- YYYY-MM-DD [电脑名] 内容`。电脑名用 `公司电脑` / `本地电脑`。

## 项目一句话

服务器资源监控、危机预警与受控处置：复用 Beszel 做监控主干，补充轻量 Guardian 做危机取证与受控处置。原则：先观测、再建议、后自动化；先取证、再处置；先限流、再终止。

## 当前阶段

**阶段 1：只观测 PoC — 进行中**（五阶段计划见 docs/04-delivery-plan.md）

- 阶段 0 需求与环境确认：✅ 已完成
- 阶段 1 只观测 PoC：🟡 进行中
- 阶段 2 Guardian 与人工处置：⚪ 未开始（src/ 为空）
- 阶段 3 有限自动化 / 阶段 4 灰度推广：⚪ 远期

## 电脑分工与环境差异

| 项 | 公司电脑（当前操作机，D:\Project_Codex） | 本地电脑（Windows + WSL2） |
| --- | --- | --- |
| 主要工作 | 生产环境采集、汇报、与 leader 沟通、调研与文档 | Beszel PoC、文档整理 |
| 生产基线采集 | ✅ reports/production-environment.txt（不入库，本机在） | — |
| WSL2 + Docker PoC 环境 | ❌ 无 WSL2/Docker，公司电脑不做 PoC | ✅ Ubuntu 26.04.1 / Docker 29.1.3 |
| Beszel 0.19.0 Hub/Agent | ❌ 未部署 | ✅ 已上线，认证通过 |
| deploy/beszel/.env | ❌ 无（PoC 只在本地电脑跑，无需重建） | 已配置（不入库） |

## 已完成事项时间线

- 2026-09-16 [公司电脑] 生产环境基线采集完成：Ubuntu 22.04.5 / systemd 249 / cgroup v2 / Docker 29.1.3，68 容器中 67 个无资源边界（docs/09、docs/10）。
- 2026-09-16 [本地电脑] WSL2 Ubuntu 26.04.1 环境初始化完成（8C/12G/4G swap），Docker + Compose 验证可用。
- 2026-09-16 [本地电脑] 隔离 Beszel 0.19.0 Hub/Agent 在 WSL2 Docker 上线，WebSocket 认证通过，容器 healthy；空载开销 Hub ≈13.3 MiB、Agent ≈6.5 MiB（docs/11）。
- 2026-09-16 [双机] 调研与设计文档 12 篇就绪（docs/01–12：需求、选型、架构、实施计划、安全策略、PoC 蓝图、生产基线、leader 讨论研究）。
- 2026-09-17 [公司电脑] 完成汇报讲稿《服务器资源保护-现成策略优先-讲稿.md》（21 页，现成策略优先、自研仅作备选）。
- 2026-09-17 [公司电脑] 建立 git 仓库基线并首次提交，配置 GitHub 远程（github.com/fcsfang/server-resource-guardian），配好公司电脑 SSH key（id_ed25519_github）。
- 2026-09-17 [公司电脑] 修正 PROGRESS.md：当前操作机为公司电脑，明确两机分工（PoC 只在本地电脑，公司电脑负责生产采集/汇报/调研文档）。
- 2026-09-17 [本地电脑] 将本地调研补充、最终汇报 PPT 与预览素材合并到仓库；远程仓库的工程文档、脚本和部署配置保持为主干。

## 待办事项（按优先级）

- [ ] 向 leader 汇报（讲稿已就绪），记录反馈并更新 docs/05-open-questions.md。
- [ ] 阶段 1 收尾：Beszel 页面核验主机/Docker/systemd 指标完整性（hello-world 容器历史、systemd 服务列表）。
- [ ] 阶段 1 收尾：受控压测验证指标完整性与 Agent 开销，建立首轮本地告警阈值。
- [ ] 向公司确认仍未决的 P0/P1 问题：带外管理通道（Q-008）、SSH 失效实际表现（Q-006）、保护名单与可处置白名单（Q-009）、非生产测试机与故障注入授权（Q-010）。
- [ ] 生产兼容性复核：本地 WSL 版本高于生产（systemd 259 vs 249、内核 6.18 vs 6.8），结论需在 Ubuntu 22.04 测试机验证。
- [ ] 阶段 1 验收后，确认是否进入阶段 2（Guardian 开发的进入条件见 docs/04 末尾）。

## 两台电脑同步操作备忘

```bash
# 开始工作前：拉取对方进度
git pull --rebase origin main

# 完成工作后：更新 PROGRESS.md，再提交推送
git add PROGRESS.md && git commit -m "progress: <一句话>" && git push origin main
```

注意：以下内容刻意不入库，且只存在于对应电脑上：
- `deploy/beszel/.env`（本地电脑的 PoC 凭据；公司电脑无 WSL2/Docker，不跑 PoC，无需此文件）
- `reports/*`（公司电脑的生产环境采集产物，含生产信息，仅公司电脑保留）
- WSL2 内的 Docker 容器与 Beszel 指标数据（本地电脑运行态，不属于仓库）
