# x86_64 部署

本目录是 Ubuntu 22.04 x86_64 的 Guardian 只观察部署包。它不使用本地虚拟机的固定 CPU 编号，不修改 SSH、Docker、网络和登录服务，也不安装或启用自动动作服务。

## 下载与安装

```bash
git clone https://github.com/fcsfang/server-resource-guardian.git
cd server-resource-guardian

# 只显示计划，不改系统
./scripts/install-guardian-x86.sh

# 可选：单独运行只读环境检查
python3 scripts/guardian-x86-preflight.py --output /tmp/guardian-x86-preflight.json

# 安装只观察版本
sudo ./scripts/install-guardian-x86.sh --apply --environment x86-observe

# 查看结果
sudo guardian-status
```

安装程序会自动再次执行环境检查。只有 Linux x86_64、Ubuntu 22.04、systemd、cgroup v2、Docker 可读、至少 2 vCPU、2 GiB 内存和 2 GiB 根盘可用空间全部通过时才会继续。

成功后的关键结果：

- `guardian-runtime.service` 与 `guardian-collector.service` 为 `active`、`enabled`；
- readiness 为 `runtime:ready:observe`；
- 自动动作关闭，允许处理名单为空；
- 动作 Broker 和磁盘预留 Broker 均未安装、未启用；
- `/opt/server-resource-guardian/DEPLOYED_VERSION` 记录部署版本；
- 命令结尾打印本次备份位置和精确回滚命令。

Beszel 保持独立：Guardian 不修改已有 Beszel。CPU、内存和磁盘的平台告警继续在 Beszel 中配置和查看。

## 升级

在仓库中拉取经过确认的新版本，然后重复执行同一条安装命令。安装器先备份当前程序、服务文件及原服务状态，再替换程序并重启 Guardian，不重启服务器和业务容器。

```bash
git pull --ff-only
sudo ./scripts/install-guardian-x86.sh --apply --environment x86-observe
sudo guardian-status
```

## 回滚

使用安装成功时打印的备份目录：

```bash
sudo ./scripts/install-guardian-x86.sh \
  --rollback /var/backups/guardian-x86-installer/<安装时间> \
  --environment x86-observe
```

回滚只恢复安装器管理的程序和服务文件，并恢复安装前的启用/运行状态。`/etc/guardian`、`/var/lib/guardian`、审计和快照会保留，避免丢失排障证据。

## 当前边界

- 该入口只安装观察和只读采集功能，不开放自动停止容器。
- Collector 的服务账号可以读取 Docker socket，但对外只提供固定快照操作；Guardian Runtime 本身不属于 Docker 组。
- 无 Beszel 时 Guardian 仍可本地观察并通过 `guardian-status` 查看，但不会凭空创建 Beszel 页面。
- 首次 x86 现场安装后仍需核对开机恢复、Beszel 页面和现有业务健康。
