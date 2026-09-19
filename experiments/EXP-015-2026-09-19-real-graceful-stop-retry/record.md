# EXP-015：修正测试进程后的真实 graceful_stop 复测

- 实验 ID：`EXP-015`
- 状态：`PASSED`
- 创建日期：2026-09-19
- 最近更新：2026-09-19
- 关联 Goal：`Goal 4 / G4-T05、G4-T06`
- 实验负责人：当前 Agent

## 1. 实验目的

在 EXP-014 暴露强制 kill 后，使用能够响应 SIGTERM 的测试进程和修正后的 `docker stop --timeout` 参数，复测本地 disposable 容器的真实 `graceful_stop` 闭环。

## 2. 授权与安全边界

- 用户已授权本项目后续所需的本地测试动作。
- 实际范围：仅 Mac Multipass `guardian-ubuntu` 内的 `guardian-test-base:local` disposable 容器。
- 允许动作：创建、启动并执行一次 `graceful_stop`；读取状态和审计结果。
- 禁止动作：不连接生产；不执行 `restart`、`terminate` 或 `docker kill`；不修改宿主机资源配置；不删除历史实验容器或镜像。
- 测试进程：BusyBox shell 注册 SIGTERM trap，收到 TERM 后退出 0；最长运行时间由动作或实验流程控制。
- 停止条件：目标、授权、动作或恢复观察不匹配时立即停止，不扩大动作范围。

## 3. 预期验收

- 动作前目标为唯一 running 容器。
- 实际参数为 `docker stop --timeout 5 <target_id>`，不出现弃用参数警告。
- 动作后容器 `running=false`，退出码为 0 或 143，不是 137。
- Guardian 审计记录状态为 `recovered`，恢复原因能够区分 clean exit 或 SIGTERM。

## 4. 结果与核心数据

- 事件 ID：`45154c8b-7349-4a4c-8225-b45b4a9c477c`。
- 目标短 ID：`99f89c1df26e`；完整 ID：`99f89c1df26e59c3e47073ec09486907689ff42de3776707348edbf32b7aa8dc`。
- 动作前：`status=running`、`running=true`；事件为合成阈值触发的 `critical`，动作计划为 `graceful_stop`。
- 授权校验：通过；授权环境为 `local-disposable`，目标和动作匹配，执行器确认开关已提供。
- 实际动作：`docker stop --timeout 5 99f89c1df26e`，返回码 0；动作输出无 `--time` 弃用警告。
- 动作后只读检查：`status=exited`、`running=false`、`exit_code=0`、`OOMKilled=false`。
- Guardian 审计结果：`state=recovered`、`recovered=true`、`reason_codes=[target_stopped]`、`cooldown_state=recorded`、`failure_breaker_tripped=false`。
- 真实动作范围：只执行本次授权的 `graceful_stop`；未执行 restart、terminate 或 kill；未连接生产。

## 5. 结论

- 验收状态：通过（真实 `graceful_stop` 闭环）。
- 已验证：事件生成、稳定对象定位、授权/保护判断、参数数组动作、动作后恢复探测和 `guardian.enforce.v1` 审计记录完整串联。
- 与 EXP-014 的差异：通过 SIGTERM trap 让测试进程在优雅窗口内退出，避免把 exit 137 强制 kill 误判为成功。
- 尚未验证：恢复失败后的冷却、连续失败熔断、动作超时、多对象竞争和业务健康检查。

## 6. 更新记录

- 2026-09-19：用户确认后续不再重复询问本地测试授权；建立 EXP-015，准备复测。
- 2026-09-19：复测通过，目标 exit 0、OOMKilled=false，Guardian 返回 `recovered`；真实 graceful_stop 闭环完成。
