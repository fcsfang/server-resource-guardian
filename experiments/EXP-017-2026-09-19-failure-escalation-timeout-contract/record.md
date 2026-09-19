# EXP-017：真实 Docker 失败升级与动作超时 fail-closed 验证

- 实验 ID：`EXP-017`
- 状态：`PASSED`
- 创建日期：2026-09-19
- 最近更新：2026-09-19
- 关联 Goal：`Goal 4 / G4-T06`
- 实验负责人：当前 Agent

## 1. 实验目的

验证动作执行失败或超时时，Guardian 能够形成可审计失败结果、持久化失败次数，并在达到阈值后升级人工；验证动作失败不会继续执行恢复探针，避免二次超时掩盖原始故障。

## 2. 授权与安全边界

- 用户已授权本项目后续所需的本地测试动作。
- 环境：Mac Multipass `guardian-ubuntu`，仅使用本地 `guardian-test-base:local` ARM64 disposable 容器。
- 真实 Docker 失败场景复用 EXP-014 已退出的容器 `guardian-enforce-disposable-20260919`（exit 137）；不启动、不重启、不 kill，不删除容器或镜像。
- 注入 runner 场景只使用内存中的 fake runner，不连接 Docker，不改变系统状态。
- 禁止动作：不连接生产；不执行 restart、terminate 或新的 kill；不修改资源限制。
- 停止条件：发现目标重新运行、Docker 命令发生非预期变更、ledger 未能持久化或出现运行中容器时立即停止并记录。

## 3. 预期验收

- 两次独立真实 CLI 调用均完成动作后恢复失败，第二次因连续失败阈值返回升级状态；第三次请求在执行器前被熔断。
- ledger 在两次进程之间累计 `consecutive_failures=2`。
- 动作超时被转换为 `action_timeout`，并计入失败熔断，不继续执行恢复探针。
- 恢复探针超出窗口返回 `recovery_window_expired`。
- 实验结束时运行中容器为 0。

## 4. 执行记录

- 复用目标：`f0e5b5c145bb`（`guardian-enforce-disposable-20260919`），实验前状态为 `exited / exit=137 / OOMKilled=false`，运行中容器为 0。
- 第一次独立 CLI 调用：真实 `docker stop --timeout 5 f0e5b5c145bb` 返回 0；只读恢复探测发现 `ExitCode=137`，结果为 `failed / target_force_killed`，ledger 写入 `consecutive_failures=1`。
- 第二次独立 CLI 调用：同一已退出 disposable 目标再次执行真实 graceful_stop，恢复仍为 `target_force_killed`；结果为 `escalated`，`failure_breaker_tripped=true`，ledger 写入 `consecutive_failures=2`。
- 第三次独立 CLI 调用：返回 `escalated / failure_breaker_tripped`，`action_result=null`，未再次调用 Docker。
- 注入 runner 测试：Docker runner 抛出 `TimeoutExpired` 时转换为 `action_timeout`；控制器不再调用恢复探针，失败计数可在第二次超时后触发熔断。
- 注入 runner 测试：目标持续运行超过恢复窗口时返回 `recovery_window_expired`。
- 结束检查：运行中容器为 0；未启动、重启、kill 或删除任何容器；未连接生产。

## 5. 结论

- 验收状态：通过。
- 已验证：真实动作后的恢复失败能够累计到持久化 ledger，并在阈值后升级人工；后续请求在执行器前被熔断。
- 已验证：动作执行超时和恢复窗口超时均 fail-closed，且动作超时不会被后续恢复探针覆盖。
- 尚未验证：多对象竞争、误报率和带业务语义的健康检查。

## 6. 更新记录

- 2026-09-19：完成实验记录、停止条件和代码验收条件；开始真实本地失败升级验证。
- 2026-09-19：真实 Docker 失败恢复连续两次后升级，第三次请求被失败熔断；动作超时与恢复窗口超时注入测试通过；实验通过。
