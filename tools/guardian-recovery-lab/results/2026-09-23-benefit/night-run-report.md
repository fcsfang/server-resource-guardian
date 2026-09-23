# 夜间无人值守执行日志(2026-09-23)

## 任务进度

| # | 任务 | 状态 | 提交/产物 |
|---|---|---|---|
| 1 | OOM 防御(sshd -1000/runtime -900/collector+gateway -800) | ✅ 完成 | 695804a |
| 2 | 审计分级瘦身(full/summary/minimal + fidelity 字段) | ✅ 完成 | 41c8d1a |
| 2b | gate 状态转换即时标记 | ✅ 完成 | 753a454(与 3 合并)|
| 3 | 网关适配(gate 识别 + 降级期抑制 stale) | ✅ 完成 | 753a454 |
| 4 | WSL 升级 753a454 + 回归 294 通过 + rescue ok | ✅ 完成 | 部署态 |
| 5 | 双层验收 | 🔄 进行中(层 1 idle 基线 40/40;钉死跑因 VM 复位重启，段 1 进行中)| results/2026-09-23-benefit/ |
| 6 | 全部推送 + 晨报 | 🔄 随任务 5 收尾 | — |

## 重要事件(次日复核要点)

1. **20:08-20:26 WSL 管理通道死亡**(0x8007274c 持续，wsl --shutdown 复位恢复)。这是层 1 的最强真实证据：钉死形态下**连宿主管理传输层都不可达** — 层 1 的"进入能力"失败不只是理论。复位后环境已完全恢复(0 容器/3.2Gi 可用/三件套 active)。
2. runtime 的 unit 本有 `OOMScoreAdjust=-900`,我最初的 -800 drop-in 会**弱化**它 — 已改为只对 collector/gateway 加 drop-in,模板注释里写明。
3. reserve_broker/collector 的两次"测试失败"经三次归因验证均为**测试顺序依赖 + stash 暂态**，非真实回归；最终全量 294 通过。
4. 层 2 已有数据：off 盲找 1106/1160s(两次找不准目标)vs old-build on ≈41s+0.37s;new-build 数字待段 1 完成。

## 待晨间决定项

- 层 1 钉死数据落地后，若 loopback 探针全过而外网(Windows CLIENT)探针失败，说明"外部进入"与"本机自测"存在本质差异 — 契约要求 CLIENT 侧补测，届时需要从 Windows 发起(数据文件已备好 entry_probe2.py,PowerShell 一条命令即可)。
