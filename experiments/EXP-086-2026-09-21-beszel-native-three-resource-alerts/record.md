# EXP-086：G8-T12 Beszel 原生三资源告警

- 实验 ID：`EXP-086`
- 状态：`IN_PROGRESS`
- 创建日期：2026-09-21
- 关联任务：`G8-T12`

## 1. 已完成

- 在本地 Multipass `guardian-ubuntu` 的 Beszel `0.19.0` UI 中确认目标系统 `guardian-ubuntu`，system ID 为 `rv5biizeh0f67bs`。
- 配置并在 UI 中复核三条原生主机告警：CPU 85%/1 分钟、Memory 85%/1 分钟、Disk 90%/1 分钟。
- CPU I/O Wait、CPU Steal 均保持关闭。
- 通过 Beszel 通知设置页只读确认到已有邮件通知收件渠道；本次未修改该渠道，也未执行会触发真实外部通知的压力信号。
- 现有告警历史页可见此前本地 Memory 触发/恢复记录；本次 T12 三资源触发/恢复统一窗口尚未执行。

配置只用于本地验证，不代表生产阈值。未读取或保存登录凭据、通知目的地、Token 或 Key；未调用 Broker、未生成 capability、未执行 Docker/systemd 动作。

## 2. 待完成

1. 在隔离的本地通知接收条件下执行一次有界三资源信号窗口。
2. 读取 `alerts_history`，保留 CPU、Memory、Disk 的触发/恢复记录、持续时间、Hub 健康和重复抑制证据。
3. 同窗确认 Guardian 本地检测不依赖 Beszel，并在实验后关闭临时告警或恢复实验前状态。

当前门禁：已有邮件通知渠道仍处于配置状态。为避免 T12 实验误发外部通知，本次不自动删除、禁用或替换该渠道；需由通知渠道 owner 明确授权隔离后，才能继续 live trigger/recovery。真实管理员渠道归 G8-T06 管理。

## 3. 当前结论

T12 功能合同、现场规则配置和统一回归已完成，T12 仍不能标记 `DONE`；当前仅剩通知隔离后的统一 live readback 和恢复证据。
