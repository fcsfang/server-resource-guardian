# 源代码目录

当前第一版原型使用 Python 标准库实现只读 `observe`：[`guardian_observer.py`](guardian_observer.py) 采集 `/proc`、PSI、cgroup v2 和 Docker stats，并输出 JSONL 事件。它不包含停止、重启、kill 或资源变更代码。

后续实现仍需拆分为：采样与快照、状态机、策略校验、受限动作执行、审计和控制通道；`simulate` 和 `enforce` 必须在 `observe` 证据稳定后单独实现。
