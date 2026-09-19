# 源代码目录

当前第一版原型使用 Python 标准库实现只读 `observe`：[`guardian_observer.py`](guardian_observer.py) 采集 `/proc`、PSI、cgroup v2 和 Docker stats，使用连续采样窗口去抖，并输出 JSONL 事件和可选快照/审计记录。它不包含停止、重启、kill 或资源变更代码。

当前也支持 `simulate`：它只根据风险状态、对象身份、保护状态和动作白名单生成计划，并明确标记 `execution=not_executed`。`guardian_actions.py` 提供授权校验、mock executor 和参数数组 Docker 适配器；没有显式的本地可丢弃环境授权时，不调用真实执行器。

`guardian_recovery.py` 提供纯函数式恢复验证、冷却和失败熔断判断；它不主动重试动作，必须由上层在策略允许时决定是否升级。

`guardian_controller.py` 是 `enforce` 的控制层：它要求事件显式标记为 `enforce`，在调用注入式执行器前再次校验授权、保护对象、稳定身份和风险状态，并串联冷却、失败熔断和恢复判断。当前控制层只通过 mock/fake executor 验证，真实 Docker 执行仍需单独授权。

`guardian_enforce.py` 提供单次运行桥接：读取事件快照和短期授权文件，默认走 mock；显式选择 Docker executor 并确认本地可丢弃环境后，才会调用动作适配器，随后使用只读 `docker inspect` 做恢复探测，并输出包含动作前事件与动作后结果的 `guardian.enforce.v1` 审计记录。

真实 Docker executor 还必须提供持久化 `--ledger-file`；ledger 保存冷却时间、动作窗口和连续失败次数，防止独立 CLI 进程绕过冷却或失败熔断。
