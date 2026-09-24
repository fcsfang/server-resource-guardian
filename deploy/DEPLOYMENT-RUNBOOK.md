# Guardian 部署演习手册(傻瓜式)

目标:在一台全新的 Ubuntu 一次性主机上,把 Guardian + 防御栈 + 飞书告警全部装好并验证,总共 **3 条命令**。

> 本手册只适用于**一次性/实验室主机**。生产主机部署是另一个流程(需要 x86 admission check),不要用本手册。

---

## 第 0 步:准备(一台全新 Ubuntu 主机)

需要确认的只有三件事:

1. 你能以 root 或 sudo 登录这台主机
2. 系统是 Ubuntu 且有 systemd(`systemctl` 命令存在)
3. 如果装了 Docker:`getent group docker` 能查到(没有的话 `apt install docker.io` 或跳过只读采集器)

把仓库弄到主机上(任选其一):

```bash
# 方式 A:git 拉取
git clone <你的仓库地址> /root/server-resource-guardian
cd /root/server-resource-guardian

# 方式 B:从 Windows 工作机传包(在 Windows 侧执行)
# tar --exclude=.git -czf guardian.tgz -C D:\Project_Codex server-resource-guardian
# scp guardian.tgz user@主机:/tmp/ && ssh user@主机 "tar -xzf /tmp/guardian.tgz -C /root/"
```

## 第 1 步:装 Guardian 核心(约 1 分钟)

```bash
sudo bash scripts/install-guardian-local.sh            # 先看计划,不改任何东西
sudo bash scripts/install-guardian-local.sh --apply --environment local-disposable
```

装好的东西:guardian 用户体系、observe-only 运行时 + 只读采集器、rescue/workload 边界、维护账号、构建指纹(build manifest)。**不包含** earlyoom 和飞书网关(下一步)。

## 第 2 步:装防御栈 + 飞书网关(约 1 分钟)

```bash
sudo bash scripts/guardian-host-setup.sh               # 先看计划
sudo bash scripts/guardian-host-setup.sh --apply --environment local-disposable
```

装好的东西:earlyoom(参数已调好,含包管理单元的 ExecStart 覆盖)、传输防御 sysctl、三层 oom_score 盾、用户会话 OOM 平衡、飞书网关单元 + journal 读权限。

**第一次会自动装 earlyoom(需要网络)。** 如果主机连不上外网,提前 `apt install earlyoom` 再跑本步。

**关于飞书凭据**:如果 `/etc/guardian/feishu.json` 还不存在,脚本会装好单元但不启动,并打印下一步命令。凭据文件从 `config/feishu.example.json` 复制后填入你的 app_id/app_secret/chat_id,然后:

```bash
sudo chown root:guardian-shared /etc/guardian/feishu.json
sudo chmod 0640 /etc/guardian/feishu.json
sudo systemctl enable --now feishu-gateway
```

(凭据从飞书开放平台拿:建一个企业自建应用,开通机器人能力,把机器人拉进群,`chat_id` 从群设置 API 拿。凭据只存主机上,绝不提交进 git。)

## 第 3 步:验证(约 10 秒)

```bash
sudo guardian-smoke
```

看到 `SMOKE PASS: all checks green` 就部署完了。任何一行 FAIL 都会直接写明是哪项、期望值是什么。

想看部署状态全景:

```bash
sudo guardian-status
```

## 演习确认:发一条真实告警(可选,约 1 分钟)

想确认飞书消息链路真的通(不只是服务 active),灌一个内存 hog,等 earlyoom 杀它,你会在群里收到两条消息:

```bash
# 起 hog:吃 60% 内存,60 秒后自动退出
python3 -c "
import time, os
total = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_NPROCESSORS_ONLN')
buf = bytearray(int(total * 0.6))
for i in range(0, len(buf), 4096): buf[i] = 1
time.sleep(60)
" &
# 15~25 秒内观察飞书群:应收到 WARNING/CRITICAL 告警 + earlyoom 击杀事件 + 恢复消息
sudo journalctl -u feishu-gateway --since "-2 min" --no-pager | grep sent
```

**清理**:`kill %1`(hog 若被 earlyoom 杀掉就已消失)。

## 常见问题

| 症状 | 原因与修复 |
|---|---|
| `guardian-smoke` 报 `earlyoom:args FAIL` | 包管理单元没展开参数。检查 `/etc/systemd/system/earlyoom.service.d/override.conf` 存在,然后 `systemctl daemon-reload && systemctl restart earlyoom` |
| 飞书群没消息,`journalctl -u feishu-gateway` 报凭据错误 | `/etc/guardian/feishu.json` 内容错或权限错(要 0640 root:guardian-shared) |
| 恢复消息里没有"earlyoom 击杀"摘要 | guardian 用户不在 systemd-journal 组:`sudo usermod --append --groups systemd-journal guardian && systemctl restart feishu-gateway` |
| `deploy-verify FAIL: MODIFIED ...` | 主机上的文件被改过。在仓库里重新 `install-guardian-local.sh --apply` 一次即可对齐 |
| 收到告警说"stale" | runtime 停了或审计流断了:`systemctl status guardian-runtime` |

## 部署后再做一次(建议)

- `sudo bash tools/guardian-recovery-lab/layer1-smoke.sh`(如果这是 WSL/有 SSH 探针环境的实验室主机):两分钟防御栈全检
- 把 `guardian-deploy-verify` 挂 cron(每天一次):指纹漂移第一时间发现

## 三条命令总结

```bash
sudo bash scripts/install-guardian-local.sh --apply --environment local-disposable
sudo bash scripts/guardian-host-setup.sh   --apply --environment local-disposable
sudo guardian-smoke
```
