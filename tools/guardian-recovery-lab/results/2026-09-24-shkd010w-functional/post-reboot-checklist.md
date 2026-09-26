# SHKD010W 重启后恢复清单(现场重启后自动/手动执行)

## 第一波:自动(监控触发后我执行)

1. `uptime` + `free -m` + `df -h /` — 确认干净启动
2. 残留检查:`pgrep -f deep_storm_hog` / `bytearray(600` / `ls /tmp/deep_storm*` — hog 若自毁未完成会有残留
3. Guardian 四服务:`systemctl is-active guardian-runtime guardian-collector feishu-gateway earlyoom`(全部 enable 过,应自动回来)
4. 业务容器:`docker ps`(6 个应自动回来)
5. `guardian-smoke` 全量复检
6. earlyoom journal 取证:风暴期间它到底杀了几次、最后一条日志是什么时刻
7. runtime 审计流末尾:观测链死亡时刻
8. 补全 deep-storm-wedge-incident.md 的"待恢复后补全"一节

## 第二波:需要用户决策

- 深压实验是否重跑(带修正设计:单杀手/有界填充/判定边界即停/确认带外管理)
- 是否为这台服务器配置带外管理(下次失联的兜底)

## 风暴最终数据(已归档,不受失联影响)

- 密钥(主机内):70/90,失败全部在 <3.2%
- 密码(外部新登录):90/90,含 0.8% 可用时
- 建立会话新开 channel:<2% 时超时
- 结论:登录位置 > 认证方式;admin_reserve 是唯一硬保证
