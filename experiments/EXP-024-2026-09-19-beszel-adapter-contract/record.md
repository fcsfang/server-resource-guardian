# EXP-024：Beszel → Guardian 只读 Adapter 契约验收

- 状态：PASSED
- 日期：2026-09-19
- 关联 Goal：Goal 6 / G6-T02、G6-T03
- 目的：验证 Beszel 输入标准化、时间窗口、重复/乱序/过期处理、传输失败降级和 GET-only 边界。

## 1. 授权与边界

- 范围：仓库中的只读 Python Adapter、脱敏 fixture，以及本机 Multipass 中已运行的 Beszel Hub 健康接口。
- 允许动作：运行单元测试；向本地 Hub 发起 GET /api/health。
- 禁止动作：读取或回显 token、登录控制台、写入 Beszel、调用 Docker/systemd 变更接口、连接生产。
- 停止条件：发现 Adapter 构造 POST/PATCH/DELETE/命令执行路径，或本地 Hub 查询需要扩大权限。

## 2. 执行摘要

- normalize_beszel_event 仅复制白名单字段，并将事件标准化为 guardian.beszel.v1。
- 缺少时间戳、时间反转、未知对象/资源/严重级别和不安全来源 URL 均 fail-closed。
- BeszelEventWindow 对重复事件返回 duplicate_event，对乱序事件返回 out_of_order_event，对过期事件返回 stale_event；这些结果都不能直接触发动作。
- HTTP 客户端只构造 GET 请求；传输错误统一为不含原始异常内容的 beszel_get_failed。
- 宿主机测试 48/48 通过；Multipass Ubuntu 内 Adapter 测试 11/11 通过；真实本地 Hub 健康 GET 返回 200。
- 根据本地前端 bundle 已知的 alerts_history 字段，增加了活动告警、恢复告警和缺少映射字段的 fixture 处理；缺少稳定系统 ID 时仍只产生低置信度观测。
- 新增 alerts_history 分页 GET 入口；对本地 Hub 的无凭据只读轮询返回 0 条可见事件，没有尝试绕过权限。

## 3. 验收结果

| 检查项 | 结果 |
| --- | --- |
| 白名单标准化与敏感字段不复制 | 通过 |
| 时间戳和 TTL | 通过 |
| 低置信度身份不进入 actionable observation | 通过 |
| 重复事件去重 | 通过 |
| 乱序事件拒绝转发 | 通过 |
| Hub/API 传输失败降级 | 通过 |
| GET-only HTTP 请求 | 通过 |
| alerts_history 活动/恢复/缺少映射字段 | 通过 |
| alerts_history 分页 GET 与事件标准化 | 通过 |
| 本地 Hub /api/health | 200，API is healthy |

## 4. 结论与限制

本实验可以证明 Adapter 的输入安全契约和本地 fixture 行为满足当前设计，并覆盖了已从前端 bundle 识别的 alerts_history 记录形状和分页 GET 方式，但不能证明 Beszel 0.19.0 的真实告警 payload 字段已经完成映射。真实 payload、用户范围内的记录和控制台告警路径仍需在取得本地 Beszel 登录会话后补齐；在此之前不进入 G6-T04 的告警路径对照。

Adapter 的 accepted 只表示“可进入 Guardian 本机重新核验”，不表示动作已授权。Guardian 仍需独立确认本机风险、对象身份、保护名单、动作白名单和恢复条件。

## 5. 可复查入口

- 实现：src/beszel_adapter.py
- 测试：tests/test_beszel_adapter.py
- 事件契约：docs/21-beszel-guardian-event-contract.md
- 脱敏数据：data/test-summary.json

## 6. 数据质量

- 测试时间：2026-09-19，Asia/Shanghai。
- 宿主机和虚拟机测试均为可重复的单元测试，不包含压力注入。
- 本地 Hub 健康 GET 是一次运行态探针，不代表告警 payload 或 UI 指标已验收。
