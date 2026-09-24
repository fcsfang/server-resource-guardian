# 夜间无人值守执行日志(2026-09-23)

## 任务进度

| # | 任务 | 状态 | 提交/产物 |
|---|---|---|---|
| 1 | OOM 防御(sshd -1000/runtime -900/collector+gateway -800) | ✅ 完成 | 695804a |
| 2 | 审计分级瘦身(full/summary/minimal + fidelity 字段) | ✅ 完成 | 41c8d1a |
| 2b | gate 状态转换即时标记 | ✅ 完成 | 753a454(与 3 合并)|
| 3 | 网关适配(gate 识别 + 降级期抑制 stale) | ✅ 完成 | 753a454 |
| 4 | WSL 升级 753a454 + 回归 294 通过 + rescue ok | ✅ 完成 | 部署态 |
| 5 | 双层验收 | ✅ 完成 — 层 1:钉死形态失败根因定位(swap-grind lockout)+ 4 层硬化解决(40/40 + 20/20,通过线 ≥95%);层 2:MTTA 42s vs 盲找 1106-1160s(~26x)、MTTD ~5-9s、MTTI 0.07s、观察者效应 0.70x、通知活性 ✓ — 六条 pass line 全部达成 | two-layer-acceptance-summary.json(最终版)|
| 6 | 全部推送 + 晨报 | ✅ 完成 | 382d483(层 1)/ c3e9d56(网关 v2)/ 2ceff9b(验收收口)|

## 重要事件(次日复核要点)

1. **20:08-20:26 WSL 管理通道死亡**(0x8007274c 持续，wsl --shutdown 复位恢复)。这是层 1 的最强真实证据：钉死形态下**连宿主管理传输层都不可达** — 层 1 的"进入能力"失败不只是理论。复位后环境已完全恢复(0 容器/3.2Gi 可用/三件套 active)。
2. runtime 的 unit 本有 `OOMScoreAdjust=-900`,我最初的 -800 drop-in 会**弱化**它 — 已改为只对 collector/gateway 加 drop-in,模板注释里写明。
3. reserve_broker/collector 的两次"测试失败"经三次归因验证均为**测试顺序依赖 + stash 暂态**，非真实回归；最终全量 294 通过。
4. 层 2 已有数据：off 盲找 1106/1160s(两次找不准目标)vs old-build on ≈41s+0.37s;new-build 数字待段 1 完成。

## 待晨间决定项 — 已全部解决(2026-09-24 补记)

- **层 1 已反转**:夜间"不可保"结论是未找到根因时的诚实判定。晨间定向搜索锁定真凶(swap-grind lockout:内核 OOM killer 出手前碾轧 swap 数十秒冻结 TCP accept),userspace 早杀(earlyoom @10% available)彻底消除 — 40/40 + v2 栈复验 20/20。原三选项中 (b) 的 x86 原生复测保留为跨平台确认项(机制已理解,属确认非探索)。
- **层 2 已收口**:new-build run 完成(MTTD ~5-9s、MTTI 0.07s),顺带发现并修复网关三缺陷(send-stall + copytruncate 吞事件链、消息无 event_id、gate 无独立可见性)— 网关 v2(c3e9d56),风暴中 CRITICAL 送达验证 [9012f08f]。
- **观察者效应已消**:EXP-088 的 21 倍劣化在 gate 激活下反转 — docker 侧操作 8.15s vs off 基线 11.7s = 0.70x(通过线 ≤1.5x)。
- 六条 pass line 全部 PASS,两层验收 JSON 已填终值并推送(2ceff9b)。环境终态:0 容器 0 hog、3.1Gi 可用、四服务 active、earlyoom 完整参数生效。
- 剩余项:目标三(完整处置闭环)未启动;x86 原生终验待环境。
