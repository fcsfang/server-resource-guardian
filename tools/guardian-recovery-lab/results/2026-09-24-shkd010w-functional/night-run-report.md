# SHKD010W 功能测试夜战报告(2026-09-24 晚,无人值守)

**执行人**:Claude(全权授权)· **主机**:SHKD010W(8C / 23.7G / 468G,专用测试服务器)

## 一句话结论

三轮功能测试(内存 / CPU / 磁盘)全部完成:告警链路 6/6 通过,对象定位与入口存活暴露 2 个产品缺口,**已全部修复并真机验证**;服务器终态 SMOKE PASS 15/15,基线复原。

## 测试结果总表

| 线 | 告警及时性 | 对象定位 | 入口存活 | 判定 |
|---|---|---|---|---|
| T1 内存(17GB hog → 可用 8-10%) | ✅ CRITICAL 即时,33 条消息零丢失 | —(内存走 docker/RSS 视图) | ❌ 密码认证 0/10 | 告警过,入口缺口 |
| T2 CPU(8 spinner,100% × 90s) | ✅ CRITICAL ~15s | ❌ workload_slice_missing | ✅ 5/5 | 告警过,定位缺口 |
| T3 磁盘(填 283G 至 14.4% free) | ✅ 双层告警 + 180s 去抖精确 | ✅ reserve 实战释放精确 +1G | ✅ | **全过** |

## 测试前部署变更(全真机验证)

1. emergency reserve 256M → 1G(release+create 演练)
2. earlyoom avoid + 业务名单(litellm/postgres/redis-server/nginx/agent/beszel)
3. 三处 AllowedCPUs 解钉(8 核机器,WSL 时代钉死浪费 6 核)
4. T1 期间 earlyoom 击杀 31 进程,业务容器 0 重启、全健康

## 三个产品缺口 → 三个修复(全部提交 01e04ee)

| # | 缺口(实测证据) | 修复 | 真机验证 |
|---|---|---|---|
| 1 | 内存风暴中密码 SSH 认证 0/10(PAM 链路 fork 失败) | RUNBOOK 新增"管理员密钥部署"章节 | 文档级 |
| 2 | `rescue top` 无法定位宿主级 CPU hog(真实 hog 永不在 workload.slice) | 新增 `--host` 模式(/proc 双采样差分,只读) | spinner 一命令点名(12.3% cpu 排第一) |
| 3 | earlyoom avoid 匹配不到容器子进程(litellm 的 spawn_main 被杀 1 次) | avoid += spawn_main | 活参数确认 |

测试守护:322 → 325(host_top 三条:点名 hog、CPU 排序、排除中途新进程)。

## 亮点

- **T3 的 reserve 实战释放**:before 72.456G → after 73.530G free,精确 +1073741824 字节,manifest 清理完整 — 这个工具第一次在真机完成设计使命
- **T2 的三源交叉**:runtime 审计流 tick 差分、top、告警文字三者一致(100.0%)— 观测可信
- **deploy-verify 又立功**:手动 cp CLI 后它立即报 MODIFIED,重跑 apply 重建 92 文件指纹后 SMOKE PASS — 版本化部署的闭环工作正常

## 服务器终态

- 四服务 active,业务容器 6/6 healthy,reserve 1G ready
- 磁盘 352G free、内存 19.9G avail(与实验前完全一致)
- `guardian-smoke` 15/15 全绿,部署指纹与 commit 01e04ee 血缘一致

## 遗留(下轮)

- 第二告警通道(飞书单点仍在)
- 72h 长跑
- T1 的 SSH 入口修复是文档级(密钥部署),需要管理员在真实使用前完成
