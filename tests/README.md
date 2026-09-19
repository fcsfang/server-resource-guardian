# 测试目录

后续测试包括：

- 单元测试：规则状态机、保护名单、请求过期、防重放、冷却和熔断。
- 集成测试：systemd/cgroup、Docker、信号处理、快照和审计。
- 故障演练：CPU、内存、PSI、I/O、磁盘满、中心失联和动作失败。
- 安全测试：未授权请求、任意命令注入、PID 重用和保护对象误操作。

当前单元测试使用 Python 标准库 `unittest`，可运行：

```bash
python3 -m unittest discover -s tests -v
```

所有资源耗尽和终止测试必须在获得授权的非生产环境执行。`observe` 和 `simulate` 的测试不得执行 Docker stop、restart、kill 或资源变更。
