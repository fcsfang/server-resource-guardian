# 源代码目录

当前第一版原型使用 Python 标准库实现只读 `observe`：[`guardian_observer.py`](guardian_observer.py) 采集 `/proc`、PSI、cgroup v2 和 Docker stats，使用连续采样窗口去抖，并输出 JSONL 事件和可选快照/审计记录。它不包含停止、重启、kill 或资源变更代码。

当前也支持 `simulate`：它只根据风险状态、对象身份、保护状态和动作白名单生成计划，并明确标记 `execution=not_executed`；不包含停止、重启、kill 或资源变更代码。后续实现仍需拆分为：采样与快照、状态机、策略校验、受限动作执行、审计和控制通道；`enforce` 必须在 `observe`/`simulate` 证据稳定后单独实现。
