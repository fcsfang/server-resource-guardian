# EXP-012：本地临时容器的 observe/simulate 实机验证

- 实验 ID：`EXP-012`
- 状态：`PASSED`
- 创建日期：2026-09-19
- 最近更新：2026-09-19
- 关联 Goal：`Goal 4 / G4-T03、G4-T04`
- 实验负责人：当前 Agent

## 1. 实验目的

使用本地构造的 ARM64 BusyBox 镜像启动一个自动退出的临时容器，验证 Guardian 在 Ubuntu 实机上能读取 Docker stats、识别稳定容器 ID，并在 `observe/simulate` 模式下生成计划。该实验不进入真实 `enforce`，不对容器执行 stop/restart/kill。

## 2. 授权与安全边界

- 环境：Mac Multipass `guardian-ubuntu`，Ubuntu 22.04.5 ARM64。
- 测试对象：`guardian-disposable-observe`，生命周期由容器内部倒计时控制，最长约 60 秒。
- 允许动作：创建和启动上述临时测试容器、读取 Docker stats、生成 observe/simulate 快照。
- 禁止动作：不得调用 `docker stop`、`docker restart`、`docker kill`；不得连接生产或修改宿主机资源配置。
- 清理方式：容器自然退出；若异常残留，只记录状态并等待后续授权，不在本实验中强制清理。

## 3. 预期验收

- `docker ps` 能看到唯一测试对象及稳定短 ID。
- `guardian_observer --mode observe` 能采集 Docker stats。
- `guardian_observer --mode simulate --allow-action graceful_stop` 能输出动作计划或保护/风险升级原因，并保持 `execution=not_executed`。
- 容器自然退出后，读取最终状态并记录；不执行真实动作。

## 4. 结果与核心数据

- 测试对象：`guardian-disposable-observe`。
- 容器 ID：`0b2a3c4edeaf3594b5040e4fb3df1071dca9deba0e194452847c53dabe85ee3d`；Guardian 使用稳定短 ID `0b2a3c4edeaf`。
- `observe`：成功读取 Docker stats，识别 1 个对象；风险状态为 `normal`，输出 `execution=not_applicable`。快照保存在 VM 临时目录 `/tmp/guardian-exp012-observe/a32f0c68-9c45-45cc-89d6-81e5a2251dff.json`。
- `simulate`：使用合成本地测试阈值（available 100/95%、持续窗口 0 秒）触发 `critical`，成功生成 `graceful_stop` 计划；输出 `execution=not_executed` 和 `simulate_only`。快照保存在 VM 临时目录 `/tmp/guardian-exp012-simulate/d11eb903-4040-4214-ac9d-7e137a76f311.json`。
- 容器生命周期：按内部 60 秒倒计时自然退出，最终 `status=exited`、`exit=0`、`running=false`。
- 真实动作：未调用 `docker stop`、`docker restart` 或 `docker kill`。
- 首次命令因未切换到 VM 项目目录而失败，随后在正确目录重跑成功；该错误未改变容器状态。

## 5. 结论

- 验收状态：通过（Ubuntu 实机 observe/simulate 和稳定对象定位）。
- 已验证：Observer 能从真实 Docker stats 识别对象；simulate 能在合成风险条件下生成动作计划但不执行。
- 阈值边界：本次 100/95% 和 0 秒窗口仅为验证动作计划的合成值，不能作为生产阈值。
- 尚未验证：真实 `graceful_stop`、动作后恢复、失败升级和业务健康检查。
- 下一步：在明确授权后，为该本地镜像创建一次性运行对象，执行真实 `graceful_stop` 并记录 EXP-013。

## 5. 更新记录

- 2026-09-19：建立实验记录，准备启动自动退出的本地临时容器。
- 2026-09-19：临时容器自然退出；observe/simulate 实机验证通过，真实动作未执行。
