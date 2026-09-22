# Guardian 源代码

## 当前运行主线

```text
采集系统资源
  → 判断 CPU、内存或磁盘风险
  → 选择允许处理的候选容器
  → 记录告警和计划
  → 默认只观察或模拟
  → 获得授权后交给独立动作服务
  → 检查宿主机风险是否缓解
  → 如有真实业务健康检查，再单独确认业务恢复
```

## 主要模块

| 功能 | 文件 |
| --- | --- |
| 持续运行入口 | `guardian_orchestrator.py` |
| 主机和容器采集 | `guardian_observer.py` |
| CPU、内存、磁盘判断 | `guardian_cpu.py`、`guardian_risk.py`、`guardian_disk.py` |
| 多资源合并 | `guardian_multirisk.py` |
| 容器身份与贡献 | `guardian_attribution.py` |
| 保护名单和候选选择 | `guardian_emergency_shedding.py` |
| 事故编排 | `guardian_coordinator.py` |
| 动作前复查 | `guardian_revalidation.py` |
| 独立动作服务 | `guardian_broker_client.py`、`guardian_broker_service.py` |
| 持久状态和通知队列 | `guardian_state.py`、`guardian_notification_outbox.py` |
| 恢复检查 | `guardian_recovery.py` |
| Beszel 只读集成 | `beszel_adapter.py`、`guardian_beszel_bridge.py`、`guardian_beszel_alerts.py` |
| 页面数据模型 | `guardian_ui_model.py` |

## 当前重要缺口

- Beszel 页面和 Guardian 事故信息还没有形成完整的外部页面整合；当前先由 `guardian-status` 提供本地投影。
- 里程碑三功能代码已收口：CPU 已完成一次本地真实止损，磁盘已完成 Guardian 自有预留应急恢复；内存真实止损曾获得单次授权，但因动作前风险证据不足而安全拒绝，尚未成功验收。代码已接入一次性授权、root-only 固定执行槽、动作前复核、宿主机风险恢复检查、通知持久化和结果审计。`graceful_stop` 只发送一次 `SIGTERM`，超时不强杀。没有真实业务健康检查时，只报告宿主机风险已缓解，业务恢复待人工确认；Broker 仍默认关闭。
- 生产阈值、生产授权和 x86_64 非生产环境验证仍未完成。
- 本地真实 SSH 维护通道已经在独立 disposable VM 验收；这不是生产 SSH 保证。宿主机风险缓解和业务服务恢复仍是两个状态，没有真实业务健康检查时只能报告“业务恢复待人工确认”。
- 内存已经进入连续 Runtime 的 observe/simulate/enforce 主链，但真实止损证据仍缺；磁盘容量可以在一次性授权后通过默认关闭的固定作用域边界，只释放 Guardian 自有应急预留并验证空间增加。没有写入者证据时，磁盘不会生成容器停止计划；任何真实容器动作都仍需针对当次目标明确授权。

开发顺序只看根目录 [ROADMAP.md](../ROADMAP.md)。
