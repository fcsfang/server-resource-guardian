# EXP-037：Observer 依赖故障 fixture

- 实验 ID：`EXP-037`
- 日期：2026-09-20
- 关联目标：Goal 7 / `PG-P0-07`
- 状态：`PASSED`
- 实验负责人：当前 Agent

## 1. 实验目的

验证 Observer 面对 Docker 只读采集超时、快照目录不可写等依赖故障时不会抛出未处理异常，也不会把不完整数据转成可执行动作。

## 2. 环境与边界

- 主机：macOS，Python 标准库 fixture 测试。
- VM：Multipass `guardian-ubuntu` 的 `/tmp/guardian-p0-03-check` 隔离目录。
- 依赖 runner 使用 mock；快照路径使用临时文件作为不可用目录；没有连接生产或写入凭据。
- 未执行 Docker stop/restart/kill、systemd 安装/启用/启动或资源变更。

## 3. 结果

| 故障场景 | 预期 | 结果 |
| --- | --- | --- |
| `docker stats` runner 抛出 `TimeoutExpired` | 返回 `available=false` 和错误，不抛出到 Observer 外层 | 通过 |
| 快照目标是普通文件而非目录 | 返回空路径，由上层标记降级，不抛出 | 通过 |
| 审计目标路径不可写 | 返回失败，由上层禁止继续动作 | 已由 EXP-035 覆盖，回归通过 |
| 主机完整测试 | 所有现有行为不回归 | `116/116` 通过 |
| VM 隔离相关测试 | Ubuntu 运行结果一致 | `73/73` 通过 |

结构化摘要见 [`data/verification.json`](data/verification.json)。

## 4. 结论与限制

Observer 的依赖读取和本地快照路径故障已有 fixture 级 fail-closed 证据，故障不会直接升级为 Docker 或 systemd 动作。

这不是对真实 Docker socket 超时、真实磁盘满、journald 故障或 Hub 网络中断的端到端注入；真实环境故障仍需本地可丢弃运行态或后续非生产授权。Guardian Observer 本身不依赖 Beszel Hub 才能做本机采样，Hub 不可用的旁路展示降级由 Goal 6 的 Adapter/Bridge 测试单独覆盖。

## 5. 更新记录

- 2026-09-20：新增 Docker stats timeout 和快照路径故障测试；主机 116/116、VM 73/73 通过。
