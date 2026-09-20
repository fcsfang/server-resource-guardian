# Guardian 配置 Schema 与启动门禁

更新时间：2026-09-20

状态：`LOCAL-MVP-IMPLEMENTED`

## 1. 运行格式

PG-P0-02 使用标准 JSON 和 Python 标准库校验，避免在本地原型阶段引入未锁定的 YAML 解析依赖：

- Schema：[`config/guardian.schema.json`](../config/guardian.schema.json)
- 本地样例：[`config/guardian.example.json`](../config/guardian.example.json)
- 校验实现：[`src/guardian_config.py`](../src/guardian_config.py)
- 历史讨论样例：[`config/guardian.example.yaml`](../config/guardian.example.yaml)，当前不作为运行时配置读取

加载方式：

```bash
python3 -m src.guardian_observer --once --config config/guardian.example.json
```

未提供 `--config` 时，Guardian 只使用内置安全默认值：`observe`、不允许动作、不写快照目录。配置文件不存在、JSON 无法解析或 schema 校验失败时，程序拒绝启动，不降级为危险默认。

## 2. 已实现的启动门禁

- 顶层和嵌套对象均拒绝未知字段和缺失字段。
- `schema`、`version` 必须匹配 `guardian.config.v1`/`1`。
- 内存 warning/critical 阈值必须满足 `0 < critical <= warning < 100`。
- dwell 窗口、采样周期、恢复窗口和动作超时必须在安全范围内。
- `enforce` 必须同时声明 `actions.enabled=true`、`require_approval=true` 和非空动作白名单。
- 自动配置禁止把 `terminate` 放入 `enforce` 白名单；强制终止不属于当前自动生产路线。
- 动作白名单只接受已知动作名，拒绝重复值。
- 远程审计导出当前未实现，`remote_export_enabled=true` 会拒绝配置。
- 每个事件输出配置 `config_digest`，用于把决策与配置版本绑定。

## 3. 当前未完成的配置化范围

本任务建立 schema 和启动门禁；PG-P0-03 已在本地 MVP 中接入组合风险和质量门禁，但以下生产能力仍未完成：

- 组合风险的生产阈值校准、长跑耐久和 x86_64 兼容性仍待后续阶段验证；
- 对象 registry、业务 owner 和动作合同仍由 PG-P0-04/05 接入；
- capability 一次性消费、原子 intent/result 和崩溃恢复仍由 PG-P0-05 接入；
- `guardian_enforce.py` 的恢复/冷却参数尚未全部从配置文件迁移；
- 当前 schema 只提供本地 MVP 配置，不代表生产阈值或生产授权。

## 4. 验收证据

- [`tests/test_guardian_config.py`](../tests/test_guardian_config.py) 覆盖默认安全模式、样例加载、digest、未知字段、阈值冲突、enforce 门禁、terminate 拒绝、远程导出拒绝和非法 JSON。
- PG-P0-02 当时的宿主机全量测试：`74 tests ... OK`；组合风险完成后的最新全量测试见 [`EXP-030`](../experiments/EXP-030-2026-09-20-composite-risk-engine/record.md)。
- observer 配置 smoke test：显式样例进入 `observe`，无配置进入内置 `observe`，两者均输出 `config_digest`；P0-03 另输出风险质量状态和 cgroup 路径证据。

## 5. 回滚

删除或移走配置文件不会启用动作；没有配置时只进入内置 observe-only 默认。要回滚本任务，只需让调用方不传 `--config`，但不能把旧 YAML 直接当作运行时配置。
