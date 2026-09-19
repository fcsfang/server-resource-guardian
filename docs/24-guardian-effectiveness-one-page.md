# Guardian × Beszel 组长评审一页纸

更新时间：2026-09-20

状态：`LOCAL-REVIEW-PACK-READY`

范围：本地 Mac Multipass `guardian-ubuntu`，仅使用脱敏证据和可丢弃测试对象；不代表生产 SLA 或生产兼容性结论。

## 一句话结论

在同一类无界内存泄漏条件下，无 Guardian 会造成宿主机 global OOM、健康服务和 SSH/Docker 相关进程受影响；Guardian 能在错误扩大前识别风险、定位唯一对象、执行受控 `graceful_stop`，并验证内存和健康探针恢复。

项目目标不是给所有 Docker 容器统一设置内存上限，而是：

```text
发现宕机风险 → 定位风险对象 → 核对保护/授权 → 受控止损 → 验证恢复或升级人工
```

## 直接效果对照：EXP-021

| 指标 | 无 Guardian | Guardian | 证据 |
| --- | --- | --- | --- |
| 宿主机 global OOM | 发生 | 未发生 | 内核日志、对照摘要 |
| 健康探针 | 被 OOM killer 杀死 | 保持 `health-ok` | 探针日志 |
| Docker/SSH 等系统服务 | 受到 OOM 影响 | 未出现同类影响 | 内核日志、systemd 状态 |
| 风险对象 | 无处置 | 唯一稳定容器 ID | Guardian 事件 |
| 动作结果 | 无 | `graceful_stop`，目标 exit 0 | Guardian 审计 |
| 资源恢复 | 无 | 可用内存回升 | 处置后观测 |
| 结论 | 系统级错误 | 提前止损并保住健康服务 | EXP-021 |

完整证据：[EXP-021 report](../experiments/EXP-021-2026-09-19-failure-prevention-comparison/report.md)。

## Guardian 效果指标

### 必须达成的效果

1. **故障预防：**同一故障条件下，有 Guardian 不发生无 Guardian 组的宿主机级错误。
2. **检测及时性：**在系统进入不可救援状态前发现风险，并记录检测窗口。
3. **对象定位：**能够定位稳定的容器、cgroup 或进程组；身份不明时拒绝自动处置。
4. **动作安全：**保护名单、动作白名单、授权、冷却和熔断全部通过后才允许动作。
5. **恢复验证：**动作后确认目标状态、资源回收和业务健康；失败时升级人工，不无限重试。
6. **人工救援保留：**高压时 SSH、诊断命令和停止测试任务的链路仍有可用窗口。

### 当前本地证据

- Guardian 在 EXP-021 中提前发现 `critical`，并成功止损。
- Guardian 检测窗口在 EXP-028 的两次运行中约为 6.15/6.18 秒。
- Beszel 同窗告警约为 16.378/33.159 秒；它承担监控和历史，Guardian 负责低延迟本机判断。
- EXP-015 验证真实 `graceful_stop` 恢复；EXP-016/017/018 验证冷却、熔断、多对象和未知对象边界。

## Beszel 集成效果指标

Beszel 是否有效，不能只看页面是否有曲线，要看它是否提供了可靠的证据来源，并且不会越权触发动作：

| 集成指标 | 当前结果 |
| --- | --- |
| 主机、Docker 指标 | 已完成本地字段核验 |
| systemd 数据缺失 | 已记录为当前本地缺失项，不伪装成正常 |
| 采集周期 | 约 60 秒，不能替代 Guardian 秒级危机检测 |
| 告警路径 | 已完成同窗压力告警和恢复历史对照 |
| Adapter | GET-only、白名单化、过期/重复/乱序 fail-closed |
| Guardian 桥接 | Beszel 事件仅作为证据，不能直接授权动作 |
| Hub 不可用 | 保留脱敏错误，不把数据缺失解释为安全 |
| 页面展示 | 已形成本地离线静态演示；Beszel 上游未修改 |

## 当前展示物

- [离线可点击演示页](../demo/guardian-beszel-review/index.html)：展示效果对照、Beszel/Guardian 分工和现场回放。
- [UI 集成设计](23-beszel-guardian-ui-integration-design.md)：冻结 `guardian.ui.v1`、降级状态和人工确认边界。
- [现场演示 Runbook](26-guardian-beszel-live-demo-runbook.md)：明早验收和组长演示步骤。
- [交付前执行路线](25-leader-review-delivery-roadmap.md)：交付物、验收标准和剩余边界。

## 必须明确的限制

- 证据来自本地 ARM64 Multipass，不是生产 x86_64 验证。
- 只验证 disposable 泄漏对象和本地健康探针，不代表真实业务自动恢复。
- 当前不接生产、不读取生产凭据、不默认执行重启或强制终止。
- 真实生产接入仍需要非生产 x86_64 测试机、保护名单、业务 health check 和明确动作授权。

## 希望组长评审并决定的事项

1. 是否认可“提前止损、保住健康服务”作为 Guardian 第一阶段效果标准。
2. 是否认可 Beszel 负责观测与历史、Guardian 负责本机决策与处置的分工。
3. 是否批准下一步在非生产 x86_64 Ubuntu 22.04 上先做 `observe` 验证。
4. 是否指定一个无状态、可重建的测试对象，作为后续一次性 `graceful_stop` 灰度对象。
