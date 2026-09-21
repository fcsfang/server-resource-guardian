# 脚本目录

脚本只用于安装、检查、演示或核对历史证据，不能自己定义项目路线。

## 当前产品开发会使用

- `guardian_rescue_plan.py`：生成维护通道的安装、诊断、停用和回滚计划。
- `install-guardian-local.sh`：在显式 `local-disposable` 标记下安装并启动 observe-only Guardian Runtime；默认只打印计划。
- `guardian_status.py`：只读输出 Runtime、Collector、observe 模式、候选排名、保护原因、模拟目标和 Broker 边界；安装流程会提供 `guardian-status` 命令。
- `guardian_rescue_probe.py`：执行固定的只读维护探针。
- `guardian_rescue_matrix.py`：汇总维护通道综合结果。
- `beszel_alerts.py`：生成 Beszel 三资源告警计划或只读回读告警状态。
- `check-repository.sh`：运行当前代码的统一回归检查。

## 历史工具

只服务于旧任务编号和零碎实验的脚本已从当前工作区移除，可在 Git 历史中恢复。历史实验数据仍保留在 `experiments/`，但不参与当前回归和任务调度。

新功能应接入 `src/` 的实际服务入口，不能只增加一个脚本就宣布完成。
