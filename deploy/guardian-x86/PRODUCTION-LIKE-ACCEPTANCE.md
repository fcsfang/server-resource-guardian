# x86 Ubuntu 仿生产人工恢复验收

本文件验收里程碑一：多个应用同时运行且宿主机资源紧张时，管理员能否从另一台机器新建 SSH，找到测试对象，人工温和停止它，并确认资源恢复。

Guardian 全程保持 `observe`，自动动作关闭。验收通过只能证明“本次压力下人工通道可用”，不代表任意资源耗尽下 SSH 都不会失效。

## 强制安全条件

- 只在可回滚的 x86_64 Ubuntu 22.04 非生产主机执行。
- 事先准备云控制台、虚拟机控制台或其他带外入口。
- 不在根盘或业务盘做满盘测试。
- 不执行宿主机 `kill -9`，不停 SSH、Docker、Beszel、Guardian、数据库或未知进程。
- 只允许停止本文件创建且名称以 `guardian-accept-` 开头的容器。
- SSH 无法新建连接、命令持续无响应或带外入口失效时，立即终止。

## 准备

`TARGET` 是 Ubuntu 目标机，`CLIENT` 是另一台发起 SSH 的机器。先在 `CLIENT` 设置：

```bash
export GUARDIAN_TARGET=<目标机IP或主机名>
export GUARDIAN_USER=<SSH管理员用户>
```

在 `TARGET` 安装并检查：

```bash
git clone https://github.com/fcsfang/server-resource-guardian.git
cd server-resource-guardian
git pull --ff-only
python3 scripts/guardian-x86-preflight.py --output /tmp/guardian-x86-preflight.json
sudo ./scripts/install-guardian-x86.sh --apply --environment x86-observe
sudo reboot
```

等待主机重新上线后，从 `CLIENT` 重新 SSH 登录，再在 `TARGET` 执行：

```bash
sudo guardian-status
systemctl show ssh.service systemd-logind.service systemd-journald.service docker.service -p Id -p Slice -p CPUWeight -p IOWeight
```

以上已安装的服务必须显示 `Slice=rescue.slice`。如果任何实际使用的 SSH、登录、日志、网络或 Docker 单元没有进入维护域，停止验收并先回滚或修正。

必须看到 Runtime 和 Collector 正在运行、`mode=observe`、自动动作关闭、两个 Broker 关闭、Beszel 中目标机在线。

在 `CLIENT` 记录无压力基线：

```bash
time ssh -o ConnectTimeout=10 "${GUARDIAN_USER}@${GUARDIAN_TARGET}" 'uptime; free -h; df -h /; sudo guardian-status'
```

## 创建多应用环境

在 `TARGET` 创建六个受限的模拟 Web 应用：

```bash
for n in 1 2 3 4 5 6; do
  docker run -d --name "guardian-accept-app-${n}" --label guardian.acceptance=true \
    --cgroup-parent workload.slice \
    --cpus 0.20 --memory 96m --pids-limit 64 nginx:alpine
done
docker ps --filter label=guardian.acceptance=true
docker inspect guardian-accept-app-1 --format '{{.HostConfig.CgroupParent}}'
```

先确认六个应用都是 `Up`，检查结果为 `workload.slice`，Beszel、Guardian 和 SSH 正常。

## CPU 场景

在 `TARGET` 启动受限的异常容器：

```bash
docker run -d --name guardian-accept-cpu --label guardian.acceptance=true \
  --cgroup-parent workload.slice \
  --cpus "$(nproc)" --memory 128m --pids-limit 128 alpine:3.20 \
  sh -c 'for i in 1 2 3 4 5 6 7 8; do while :; do :; done & done; wait'
```

等待 Beszel 或 Guardian 显示 CPU 风险，然后在 `CLIENT` 新建 SSH：

```bash
time ssh -o ConnectTimeout=10 "${GUARDIAN_USER}@${GUARDIAN_TARGET}" 'uptime; sudo guardian-status; docker stats --no-stream; docker ps --filter label=guardian.acceptance=true'
```

核对名称后手工温和停止：

```bash
ssh "${GUARDIAN_USER}@${GUARDIAN_TARGET}" 'docker stop --time 20 guardian-accept-cpu'
```

再次新建 SSH，确认 CPU 下降、告警恢复、其他应用仍在运行。

## 内存场景

内存压力不得超过宿主机内存的 60%。在 `TARGET` 执行：

```bash
MEM_MIB=$(awk '/MemTotal:/ {print int($2 / 1024 * 0.60)}' /proc/meminfo)
test "$MEM_MIB" -ge 512
docker run -d --name guardian-accept-memory --label guardian.acceptance=true \
  --cgroup-parent workload.slice \
  --memory "${MEM_MIB}m" --memory-swap "${MEM_MIB}m" --pids-limit 64 \
  python:3.12-alpine python3 -c "import time; n=${MEM_MIB}*1024*1024*9//10; b=bytearray(n); time.sleep(900)"
```

等待风险显示后，从 `CLIENT` 重复 CPU 场景的新 SSH 和诊断命令，然后执行：

```bash
ssh "${GUARDIAN_USER}@${GUARDIAN_TARGET}" 'docker stop --time 20 guardian-accept-memory'
```

确认可用内存回升、无 OOM、其他应用仍在运行。如果 60% 压力未达配置风险线，记录“未触发”，不得继续提高到不受控水平。

## 磁盘场景

必须使用单独可销毁的测试盘，挂载到 `/mnt/guardian-acceptance`，并将该挂载点加入 Guardian 和 Beszel 监控范围。禁止对根盘执行。

在 `TARGET` 检查挂载并写入最多 85% 当前可用空间：

```bash
test "$(findmnt -no TARGET /mnt/guardian-acceptance)" = /mnt/guardian-acceptance
AVAILABLE_KIB=$(df --output=avail /mnt/guardian-acceptance | tail -1)
WRITE_MIB=$(( AVAILABLE_KIB * 85 / 100 / 1024 ))
test "$WRITE_MIB" -gt 0
docker run -d --name guardian-accept-disk --label guardian.acceptance=true \
  --cgroup-parent workload.slice \
  --cpus 0.25 --memory 128m --pids-limit 64 \
  -v /mnt/guardian-acceptance:/acceptance alpine:3.20 \
  sh -c "dd if=/dev/zero of=/acceptance/guardian-pressure.bin bs=1M count=${WRITE_MIB}; sleep 900"
```

告警后从 `CLIENT` 新建 SSH 并确认写入者。先温和停止容器，再删除仅属于本验收的文件：

```bash
docker stop --time 20 guardian-accept-disk
sudo rm -f -- /mnt/guardian-acceptance/guardian-pressure.bin
df -h /mnt/guardian-acceptance
```

## CPU + 内存混合场景

在 `TARGET` 重新启动两个压力容器，但不做磁盘写入：

```bash
docker start guardian-accept-cpu guardian-accept-memory
```

只保持 60 秒。在 `CLIENT` 新建 SSH，查看状态，手工先停止占用更高的测试容器。如果一次停止后未恢复，先记录，再停另一个。不允许批量停止。

## 清理

在 `TARGET` 执行：

```bash
for name in guardian-accept-cpu guardian-accept-memory guardian-accept-disk guardian-accept-app-1 guardian-accept-app-2 guardian-accept-app-3 guardian-accept-app-4 guardian-accept-app-5 guardian-accept-app-6; do
  docker rm -f "$name" 2>/dev/null || true
done
sudo guardian-status
systemctl is-active guardian-runtime.service guardian-collector.service
test ! -e /etc/guardian/broker.enabled
test ! -e /etc/guardian/reserve-broker.enabled
```

## 验收记录

| 场景 | 明确告警 | 外部新 SSH | 诊断可用 | 仅停测试对象 | 资源恢复 | 其他应用存活 | 结论 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| CPU |  |  |  |  |  |  |  |
| 内存 |  |  |  |  |  |  |  |
| 独立测试盘 |  |  |  |  |  |  |  |
| CPU + 内存 |  |  |  |  |  |  |  |

同时记录 SSH 耗时、告警和恢复时间、手工停止的容器、是否 OOM、自动动作关闭证据和其他服务异常。任何场景无法新建 SSH、诊断命令不可用、误停非测试对象或自动动作开启，均判定失败。

安装器会按实际主机内存生成维护域，并提高 SSH、登录、日志、网络、Docker 控制链和新登录会话的资源权重；不固定 CPU 核号。配置需在安装后重启才完整生效，因此本验收必须在重启后开始。这仍只是提高维护通道在资源竞争下的可用概率，不是 SSH 绝对保证。
