# 源代码目录

Goal 6 新增的 beszel_adapter.py 是只读集成边界：只允许 HTTP GET，白名单化 Beszel payload，标准化为 guardian.beszel.v1，检查时间新鲜度和对象身份，并拒绝把外部事件直接转成动作授权。guardian_beszel_bridge.py 将标准化事件接入 Guardian 的 observe/simulate：Guardian 必须用本机观测重新判断风险，Beszel 对象不会直接成为动作目标，重复/过期/乱序/低置信度输入全部保持 fail-closed。

Adapter 还提供 alerts_history 活动/恢复记录的安全映射和 GET-only 分页读取入口；缺少资源、严重级别或稳定系统身份时保持 fail-closed。EXP-028 已在本地数据层确认实际 Memory 历史记录字段；GET-only 接口的实时登录态读取仍不保存或回显凭据。

当前第一版原型使用 Python 标准库实现只读 `observe`：[`guardian_observer.py`](guardian_observer.py) 采集 `/proc`、PSI、cgroup v2 和 Docker stats，使用连续采样窗口去抖，并输出 JSONL 事件和可选快照/审计记录。它不包含停止、重启、kill 或资源变更代码。

PG-P0-02 新增 [`guardian_config.py`](guardian_config.py) 和 `config/guardian.example.json`：配置使用严格 JSON schema、未知字段拒绝、危险 `enforce` 默认拒绝，并为每个事件输出 `config_digest`。未传配置时只使用 observe-only 安全默认；历史 `guardian.example.yaml` 不被运行时读取。

PG-P0-03 新增 [`guardian_risk.py`](guardian_risk.py)：组合 OOM 增量、可用内存、下降趋势、memory PSI、swap 和观测质量，输出候选状态、reason codes、质量标记和信号摘要。`guardian_observer.py` 使用单调时钟，并依据 `/proc/self/cgroup` 定位 cgroup v2 当前进程目录；历史计数不重复触发，计数回退或采集不完整时 fail-closed 为 `degraded_observability`。该层仍只产生风险证据，不授权动作。

PG-P0-04 新增 [`guardian_attribution.py`](guardian_attribution.py)：只读调用 Docker inspect，使用容器 PID 映射 full ID 到 cgroup v2，读取对象内存和 OOM 计数，并用连续样本计算贡献度、置信度和领先幅度。单目标未达到证据门槛、对象重建、ID/cgroup 不一致和候选接近时分别降级或输出 `AMBIGUOUS_TARGET`；未确认归因不能生成 simulate/enforce 目标计划。

PG-P0-05 新增 [`guardian_state.py`](guardian_state.py)：使用 SQLite WAL 持久化 capability、intent/result、审计、冷却和失败熔断状态。真实 Docker enforce 必须同时提供 `--ledger-file` 与 `--state-db`；意图或执行开始后崩溃会在下次启动进入 `RECONCILIATION_REQUIRED`，不会自动重试。

当前也支持 `simulate`：它只根据风险状态、对象身份、保护状态和动作白名单生成计划，并明确标记 `execution=not_executed`。`guardian_actions.py` 提供授权校验、mock executor 和参数数组 Docker 适配器；没有显式的本地可丢弃环境授权时，不调用真实执行器。

`guardian_recovery.py` 提供纯函数式恢复验证、冷却和失败熔断判断；它不主动重试动作，必须由上层在策略允许时决定是否升级。

`guardian_ui_model.py` 只生成脱敏的 `guardian.ui.v1` 展示模型，不提供 HTTP endpoint、不推断风险、不授权动作；UI 设计和字段约束见 [`docs/23-beszel-guardian-ui-integration-design.md`](../docs/23-beszel-guardian-ui-integration-design.md)。

`guardian_controller.py` 是 `enforce` 的控制层：它要求事件显式标记为 `enforce`，在调用注入式执行器前再次校验授权、保护对象、稳定身份和风险状态，并串联冷却、失败熔断和恢复判断。当前控制层只通过 mock/fake executor 验证，真实 Docker 执行仍需单独授权。

`guardian_enforce.py` 提供单次运行桥接：读取事件快照和短期授权文件，默认走 mock；显式选择 Docker executor 并确认本地可丢弃环境后，才会调用动作适配器，随后使用只读 `docker inspect` 做恢复探测，并输出包含动作前事件与动作后结果的 `guardian.enforce.v1` 审计记录。

真实 Docker executor 还必须提供持久化 `--ledger-file` 和 SQLite WAL `--state-db`；前者保存兼容的冷却 ledger，后者保存 capability、intent/result、审计和崩溃恢复状态，防止独立 CLI 进程绕过动作门禁。
CLI 可用 `--cooldown-seconds`、`--max-actions`、`--action-window-seconds` 和
`--max-consecutive-failures` 显式设置实验参数；动作执行器超时会形成
`action_timeout` 失败结果并计入 ledger，失败动作不会继续探测恢复状态。
