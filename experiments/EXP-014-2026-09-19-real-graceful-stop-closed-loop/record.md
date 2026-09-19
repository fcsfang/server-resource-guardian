# EXP-014：本地可丢弃容器真实 graceful_stop 闭环

- 实验 ID：`EXP-014`
- 状态：`FAILED`
- 创建日期：2026-09-19
- 最近更新：2026-09-19
- 关联 Goal：`Goal 4 / G4-T05、G4-T06`
- 实验负责人：当前 Agent

## 1. 实验目的

在 Mac Multipass Ubuntu 22.04 ARM64 中，对由本地 `guardian-test-base:local` 镜像创建的单个 disposable 容器执行一次经明确授权的 `graceful_stop`，验证完整链路：事件生成 → 稳定对象定位 → 授权/保护判断 → Docker 动作 → 只读恢复探测 → 结构化审计。

## 2. 本次明确授权与边界

- 用户授权范围：仅本次 `guardian-ubuntu` 内的本地 disposable 容器，仅 `graceful_stop`。
- 测试对象：实验运行时创建的唯一容器，名称计划为 `guardian-enforce-disposable-20260919`。
- 镜像：本地 `guardian-test-base:local`，不连接 Docker Hub。
- 允许动作：创建、启动并停止上述测试容器；读取 Docker 状态；写入 VM `/tmp` 下事件、授权和审计文件。
- 禁止动作：不连接生产；不执行 `restart`、`terminate`、`docker kill`；不修改宿主机资源配置；不删除现有容器或镜像。
- 授权有效期：运行时生成短期授权文件，目标 ID 和动作完全绑定。
- 停止条件：动作返回非零、目标 ID/授权不匹配、保护/白名单校验失败、恢复探测超时，立即停止后续动作并保留证据。

## 3. 预期验收

- 动作前容器明确为 running，且仅有本实验目标被授权。
- Controller 通过稳定 ID、非保护对象、动作白名单、`local-disposable` 授权和 `enforce` 模式校验。
- 实际执行命令只能是参数数组形式的 `docker stop --time ... <target_id>`。
- Docker 返回成功，恢复探测确认 `running=false`，审计记录包含动作前事件和动作后结果。

## 4. 结果与核心数据

- 事件 ID：`c7e08648-5ba5-4c7e-8a6e-5172bb9bddb8`。
- 目标短 ID：`f0e5b5c145bb`；完整 ID：`f0e5b5c145bbbe866203aae01c7ea5d3e64c2a5707c7ee91a0e7f26746dceb45`。
- 动作前：`status=running`、`running=true`；事件状态为合成阈值触发的 `critical`，动作计划为 `graceful_stop`。
- 授权校验：通过；授权环境为 `local-disposable`，目标 ID 和动作完全匹配，执行器确认开关已提供。
- Docker 动作：实际调用一次 `docker stop --time 5 f0e5b5c145bb`，返回码为 0；Docker 输出提示 `--time` 已弃用。
- 动作后只读检查：`status=exited`、`running=false`、`exit_code=137`。
- 修正后的恢复判定：`state=failed`、`recovered=false`、`reason_codes=[target_force_killed]`。
- 原始运行桥接曾仅依据 `running=false` 返回 `recovered`；该误判已被本次代码修正和 30/30 双环境测试覆盖。
- 真实动作：已执行且仅执行一次；未执行 restart、terminate 或 kill；未连接生产。

## 5. 结论与后续

- 验收状态：失败（动作触发成功，但 graceful stop 在 5 秒超时后以 137 结束，未证明优雅退出）。
- 已验证：授权、稳定对象、动作参数调用和动作后状态读取链路真实可达。
- 关键缺口：当前 BusyBox 无限循环测试进程没有在 Docker stop 的优雅窗口内退出，Docker 最终强制终止；因此不能把“容器停止”当作“优雅恢复”。
- 代码修正：恢复观察现在读取 `ExitCode`，`137` 判定为 `target_force_killed`，其他未允许的非零退出也 fail-closed；Docker 参数改为 `--timeout`。
- 下一步：修正测试进程后，需重新获得本地 disposable `graceful_stop` 授权，再执行一次复测；本次授权不自动扩展到下一次动作。

## 6. 更新记录

- 2026-09-19：用户明确授权本地 disposable 容器的单次 `graceful_stop`，建立实验记录，准备执行。
- 2026-09-19：真实动作返回 0 但容器以 exit 137 结束；只读复核确认是强制 kill，实验标记为 FAILED。
- 2026-09-19：修正恢复判定和 Docker `--timeout` 参数；宿主机与 Ubuntu 单元测试均 30/30 通过，未重复真实动作。
