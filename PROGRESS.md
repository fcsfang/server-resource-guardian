# 当前进度

更新时间：2026-09-22

这份文件只回答三个问题：现在能做什么、还不能做什么、下一步做什么。历史过程保存在 Git 和 `experiments/`，不再堆入进度总账。

## 总体判断

当前已完成本地产品的观察、告警、候选识别、模拟建议和一次受控真实止损闭环；里程碑三本地整体验收已完成，下一步是整理非生产服务器测试申请与回滚方案。

已经完成本地维护通道验证；当前已能让普通管理员在本地 Ubuntu 安装并自动启动 Guardian，状态命令也能展示候选、模拟结果和动作恢复结果。真实动作仍默认关闭。

## 已经完成

- Guardian 已能读取 CPU、内存、磁盘和系统压力数据。
- 已有资源危险判断、保护名单、候选容器排名、模拟建议和日志模块。
- 在 `guardian-t11-matrix` 的统一验收中，持续 Runtime 对真实 CPU 压力容器完成一次明确授权的 `graceful_stop`；审计记录为 `execution=REAL`、`action_completed`、`target_stopped`，没有处理第二个对象。
- 本地维护通道已经完成安装、重启、停用恢复和回滚验证。
- 在 CPU、内存、I/O、磁盘容量和进程数压力下，本地维护探针全部通过。
- Beszel 已配置本地 CPU、内存、磁盘三类告警规则。
- Guardian 已提供一次可重复的本地 Ubuntu 安装入口；默认 observe、自动动作关闭，安装后已在本地 disposable VM 重复执行并重启验证。
- Guardian 持续运行服务已经接入安装入口，并在本地 disposable VM 重启后自动恢复。
- 安装流程已提供 `guardian-status` 只读命令，可查看运行状态、observe 模式、最近本地风险和 Broker 关闭边界。
- Guardian 已接入独立只读 Collector；Runtime 通过 Unix socket 获取容器身份、状态和资源数据，Runtime 账户不在 `docker` 组。
- 候选排名、保护原因和模拟计划已经写入持续 Runtime 的审计投影；`guardian-status` 可展示候选数量、最高候选、保护对象及原因、模拟目标和执行边界。
- 在 local-disposable t11 上完成 bounded simulate 演示：真实 Collector 连续读取 4 个容器，风险进入 warning，目标无法确认时自动升级人工，结果为 `escalate/not_executed`，真实动作数为零。
- 在 local-disposable t11 上完成里程碑二统一演示：制造真实 CPU 压力后，连续服务观测到 100% CPU，并以约 99.998% 贡献确认压力容器为第一候选；Beszel 控制面被列入保护对象，模拟计划为 `graceful_stop/not_executed`，真实动作数为零，演示容器已清理。
- 里程碑三本地验收已验证一次性授权文件、enforce capability 注册、动作前 Collector 复核、动作后主机恢复检查和运行结果审计；本次结果为宿主机风险 `MITIGATED`，业务恢复待人工确认。

## 还没有完成

- 尚未进入 x86_64 非生产服务器，更没有进入生产。

## 当前里程碑

里程碑一、里程碑二和里程碑三 [本地版本交付](ROADMAP.md) 已完成整体验收，本地版本已收口；当前不再继续增加零碎测试或 EXP 编号。

下一项交付是：

> 形成 x86_64 非生产服务器测试申请清单、明确回滚方案和只观测起步边界；在审批前不连接生产机、不打开生产自动动作。

## 当前本地运行事实

- `guardian-t11-matrix` 已完成维护通道的本地综合验证。
- `guardian-t11-matrix` 已通过 Guardian 首次安装、重复安装和重启后持续运行验证；代码目录归 root，readiness 为 `runtime:ready:observe`。
- `guardian-g8-runtime` 曾成功以只观察模式持续处理样本，随后安全停止。
- 当前自动动作服务默认关闭。
- 里程碑二统一演示和里程碑三真实动作统一验收均已在 `guardian-t11-matrix` 完成；验收结束后安装态恢复为 observe，自动动作服务默认关闭，Broker inactive 且 socket absent。
- 原有本地 Beszel 存在真实邮件通知配置；已新增并完成独立 8091 隔离 Hub 演示，未修改原有 8090 实例。
- 没有连接生产服务器，没有读取或保存真实凭据。

## 面向用户的完成口径

- 写完代码：不算完成。
- 测试通过：不算完成。
- 文档写好：不算完成。
- 能在全新本地环境安装、运行，并让用户看到预期结果：才算当前功能完成。

详细任务只看 [ROADMAP.md](ROADMAP.md)，不要从历史实验编号中领取工作。
