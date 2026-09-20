# Guardian systemd 本地 MVP 部署基线

本目录只提供可审查的 unit/slice 模板，不代表已经安装或启用服务。

## 文件

- `guardian-observer.service`：非 root、只读 observer 常驻服务，使用 systemd notify/watchdog 和 readiness 文件。
- `guardian-observer.slice`：独立 cgroup 资源边界。`MemoryMin/Low/High/Max` 是基于 EXP-019 空载约 26.5 MiB RSS 的本地起始值，必须在多容器、快照和压力场景取得 P99+余量后才能调整或用于外部主机。

## 安全边界

- unit 只启动 `guardian_observer`；源码不含 Docker stop/restart/kill 或 systemd 写动作。
- `SupplementaryGroups=docker` 仅为只读 stats/inspect 原型提供 Docker socket 访问；Docker group 具有高权限，真实动作必须继续留在独立、显式授权的 action broker 中。
- `ProtectSystem=strict`、`NoNewPrivileges`、空 Capability 集、`PrivateTmp`、日志限流和 `UMask=0077` 是默认安全边界。
- 没有配置文件、Python 源码、状态目录和 audit 目录的明确安装/权限校验前，不运行 `systemctl enable --now`。

## 只读校验示例

```bash
systemd-analyze verify deploy/guardian/guardian-observer.service deploy/guardian/guardian-observer.slice
```

在当前 Mac Multipass 实验中，优先将 unit 复制到临时目录做静态解析或使用 `systemd-analyze verify`；本 Goal 不安装到系统、不开机自启、不执行真实动作。
