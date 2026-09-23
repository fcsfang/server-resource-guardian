# Guardian 配置 Schema 与启动门禁

## 1. 运行格式

当前配置使用标准 JSON 和 Python 标准库校验，避免引入未锁定的 YAML 解析依赖：

- Schema：[`config/guardian.schema.json`](../config/guardian.schema.json)
- 本地样例：[`config/guardian.example.json`](../config/guardian.example.json)
- 校验实现：[`src/guardian_config.py`](../src/guardian_config.py)

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
- `enforce` 必须同时声明 `actions.enabled=true`、`require_approval=true`、非空动作白名单和绝对路径的 `actions.authorization_file`；授权文件无效、缺失或无法向 Broker 注册 capability 时必须拒绝启动。
- `actions.authorization_file` 在 `observe`/`simulate` 中可以为 `null`；旧的 observe/simulate 配置缺少该字段时会按安全默认值补齐，enforce 不会因此放宽门禁。
- 自动配置禁止把 `terminate` 放入 `enforce` 白名单；强制终止不属于当前自动生产路线。
- 动作白名单只接受已知动作名，拒绝重复值。
- 远程审计导出当前未实现，`remote_export_enabled=true` 会拒绝配置。
- 每个事件输出配置 `config_digest`，用于把决策与配置版本绑定。

## 3. 运行与环境边界

配置校验通过不代表生产阈值、授权或环境已获批准。当前未验证边界见根目录 [`PROGRESS.md`](../PROGRESS.md)。

## 4. Implementation

配置校验实现位于 [`src/guardian_config.py`](../src/guardian_config.py)，运行样例和约束以 JSON schema 与当前代码为准。

## 5. Recovery

删除或移走配置文件不会启用动作；没有配置时只进入内置 observe-only 默认。恢复内置默认值时让调用方不传 `--config`；不能把旧 YAML 直接当作运行时配置。
