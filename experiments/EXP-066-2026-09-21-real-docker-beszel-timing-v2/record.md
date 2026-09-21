# EXP-066：真实 Docker + Beszel 时间差重跑

- 实验 ID：`EXP-066`
- 日期：2026-09-21
- 目的：验证 EXP-065 暴露的 Observer 55 秒等待边界修复，并在全新 disposable VM 中重跑真实 Docker 内存负载与 Beszel `container_stats` 同负载时间对齐。
- 状态：`INCONCLUSIVE`

本实验在真实 Docker 三轮控制目标均自然退出，控制探针 `36/36` 成功；但 runner 未将仓库 `src/` 同步到新 VM，三个 Observer 均在启动后以 `ModuleNotFoundError: No module named 'src'` 退出，Guardian 原始样本为 `0/96`。该 setup failure 已保留在 `data/real-docker-memory-v2.json`，不作为 Guardian 功能证据。EXP-065 的超时证据不覆盖或替代本实验；修复 runner 前置条件后另开 EXP-067。
