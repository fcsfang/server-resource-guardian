# EXP-085 — PG-P0-15A 本地 disposable 单次 graceful_stop

日期：2026-09-21

状态：`PASSED`

## 目的

在 PG-P0-18 本地功能收敛和 PG-P0-19 统一验收通过后，验证一次由用户当次授权触发的 local-disposable Docker 容器 `graceful_stop`。本实验只验证动作边界、身份复验、一次性 capability、持久 intent/result、动作后目标状态和人工接管记录；不验证生产救援 SLO、真实业务恢复或整机压力缓解效果。

## 明确授权范围

- 授权人：当前用户；授权语义：允许本实验对下列唯一目标执行一次 `graceful_stop`。
- 环境：本机 OrbStack local-disposable；不连接生产、远程主机或外部通知渠道。
- 允许动作：仅 `graceful_stop`；禁止 `restart`、`terminate`、裸 PID kill、批量动作和自动重试。
- 停止条件：目标身份、运行状态、actionable_set、授权 action 或持久化状态任一不匹配，立即拒绝；动作结果未知时不重试。

## 目标定义

目标由本实验新建，不复用任何已有容器：

- name：`guardian-p015a-disposable-20260921-01`
- image：本机已有 `ubuntu:24.04`
- full Docker ID：`ca6deff288030771a32a0eb62a9e5401b49bfd01f1f85563fafd66ec4faad600`
- created_at：`2026-09-21T06:00:13.082819164Z`
- cgroup：`/sys/fs/cgroup`，inode `467`（目标容器命名空间内的只读身份字段）
- 运行边界：`network=none`、只读根文件系统、64 MiB、0.1 CPU、pids limit 32、`no-new-privileges`
- 标签：`guardian.scope=local-disposable`、`guardian.purpose=p015a-graceful-stop`、`guardian.protected=false`、`guardian.action=graceful_stop`

本机既有的 `goal4i-netlab-receiver`、`goal4i-netlab-sender` 和 `infinite-canvas` 均保持原状，不属于本实验目标。

## 执行与证据

执行入口、授权文件、配置快照、事件、SQLite state/outbox、协调器结果和 Docker inspect 结果均写入本目录 `data/`。动作前必须先完成：

1. 重新 inspect 目标完整 ID、created_at、cgroup 字段、标签和 `running=true`。
2. 生成只包含该目标的 local-disposable actionable_set；保护既有容器。
3. 持久化通知 outbox 和 ActionIntent 后，注册并消费一次性 capability。
4. 通过 Docker adapter 发送一次 `docker stop --timeout <bounded> <full-id>`。
5. 只读复验目标已停止；不因业务未运行而声称 `BUSINESS_RECOVERED`，不自动重启。

## 结果

- `result.json` 状态：`PASS_ACTION`。
- 统一 coordinator 的 `ActionIntent`、一次性 capability、broker 和 Docker adapter 均走通；capability 记录为 `capability_consumed`。
- 实际 Docker 动作：1 次 `graceful_stop`，完整目标 ID 为 `ca6deff288030771a32a0eb62a9e5401b49bfd01f1f85563fafd66ec4faad600`，adapter return code 为 `0`。
- 目标停止复验：`running=false`、`status=exited`、exit code `0`；随后以非强制 `docker rm` 清理，未发送第二次 stop。
- 告警 outbox 审计：1 条记录，hash/audit 校验 `valid=true`；真实通知投递 `0`；外部连接 `0`。
- 既有 `goal4i-netlab-receiver`、`goal4i-netlab-sender`、`infinite-canvas` 均保持原状态。
- 宿主缓解和业务恢复：均未验证；协调器因此保留 `manual_handoff=true`、`BUSINESS_DEGRADED`/未缓解结论，不把“容器已停止”写成业务恢复。

## 结论

PG-P0-15A 的“单个明确登记目标、一次授权、一次真实 `graceful_stop`、停止后不自动升级或重试”动作边界通过。本结果只证明本机 OrbStack local-disposable 动作链，不证明生产环境、SSH 救援 SLO、宿主压力缓解、真实通知收件或业务恢复；PG-P0-15B 仍保持阻塞。

## 当前边界

本实验不是 CPU/内存压力实验，也不证明停止目标能缓解真实宿主压力。若 Docker daemon、目标身份、outbox、intent、capability、动作返回或结果对账任一环节不确定，结论必须为拒绝或人工对账，不得重复动作。
