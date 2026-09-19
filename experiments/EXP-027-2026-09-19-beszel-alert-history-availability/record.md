# EXP-027：Beszel 告警历史记录可用性

- 实验 ID：`EXP-027`
- 状态：`PASSED`
- 创建日期：2026-09-19
- 最近更新：2026-09-19
- 关联 Goal：`Goal 6 / G6-T03`
- 实验目的：在已登录的本地 Beszel 会话中只读查询 `alerts_history`，确认是否存在可脱敏的真实告警 payload；没有记录时验证 Adapter 的空数据 fail-closed 结论。

## 1. 环境与边界

- 环境：Mac Apple Silicon 上的 Multipass `guardian-ubuntu`，Ubuntu 22.04.5 LTS ARM64，Beszel Hub/Agent 0.19.0。
- 测试对象：本地 Hub 的 `alerts_history` 只读接口和仓库中的 GET-only Adapter。
- 允许动作：本地 GET、读取脱敏 JSON、运行已有 Adapter 单元测试。
- 禁止动作：开启告警、修改通知设置、写入/伪造历史记录、读取或回显凭据、连接生产、调用 Docker/systemd 变更接口。

## 2. 方法与结果

已登录本地 Chrome 访问：

```text
/api/collections/alerts_history/records?perPage=5&sort=-created&expand=system&fields=id,name,value,state,created,resolved,expand.system.name
```

页面返回：

```json
{"page":1,"perPage":5,"totalItems":0,"totalPages":0,"items":[]}
```

因此当前用户范围没有可读取的真实告警记录，也就没有可提交仓库的真实 payload。Adapter 对空数据不生成事件、不授权动作；EXP-024 中已通过静态 bundle 对应的脱敏 fixture 覆盖活动、恢复、分页和缺失映射边界。

## 3. 结论

1. G6-T03 的只读获取、白名单标准化、时间窗口、防重复/乱序/过期、Hub 不可用和空数据 fail-closed 均有测试或本地探针证据。
2. `alerts_history` 的真实用户范围当前为空，不能声称已完成真实告警 payload 映射；真实 payload 仍是数据缺口，而不是 Adapter 可以猜测的字段。
3. 不开启告警、不伪造记录，避免把通知设置变化或 fixture 误写成 Beszel 真实告警证据。
4. G6-T04 仍需单独授权的本地告警配置和同一故障场景对照；本实验不进入告警触发路径。

## 4. 数据质量与证据

- 观测时间：2026-09-19 23:59（Asia/Shanghai）。
- 查询结果只包含分页元数据和空数组，不包含凭据或业务数据。
- `totalItems=0` 证明当前用户范围无可见记录，但不能区分尚未产生告警、告警开关关闭、Agent 未上报或保留策略；这些原因不在本实验中猜测。
- 组合证据：[`EXP-024 Adapter 契约`](../EXP-024-2026-09-19-beszel-adapter-contract/record.md)、[`Adapter 实现`](../../src/beszel_adapter.py)。

