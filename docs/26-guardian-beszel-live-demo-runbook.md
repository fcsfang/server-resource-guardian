# Guardian × Beszel 现场演示 Runbook

更新时间：2026-09-20

目标：明早先由本地验收，再向组长展示 Guardian 的真实效果证据和 Beszel 集成边界。

## 1. 演示安全边界

- 默认演示使用 `demo/guardian-beszel-review/index.html` 的静态脱敏数据。
- 演示不需要 Beszel 登录、不读取 `.env`、不连接生产、不调用 Docker/systemd 变更接口。
- 页面中的 `graceful_stop` 是效果回放和 `simulate` 计划，不表示本次现场正在执行动作。
- 真实动作证据只引用已经完成的 EXP-015/EXP-021，不在组长演示过程中重复故障注入。

## 2. 启动方式

在仓库根目录执行：

```bash
./scripts/serve-review-demo.sh
```

等价命令：

```bash
python3 -m http.server 8765 --directory demo/guardian-beszel-review
```

打开：<http://127.0.0.1:8765/>

也可以直接双击 `demo/guardian-beszel-review/index.html` 离线打开。

## 3. 三分钟讲解顺序

### 3.1 效果对照

先指出页面顶部的核心问题：

> 同一类无界内存泄漏，没有 Guardian 时会触发 global OOM；加入 Guardian 后，在错误扩大前停止风险对象，并保持健康探针可用。

然后展示四张指标卡：

- 无 Guardian：`GLOBAL OOM`；
- 有 Guardian：`STOPPED`；
- 健康探针：`200 OK`；
- Guardian 检测：约 `6.1s`。

最后打开对照表，说明数据来自 EXP-021/EXP-028，不是页面自行推断。

### 3.2 Beszel 集成

按三个卡片解释：

1. Beszel：采集、历史、告警，是外部事实来源；
2. Guardian：本机重新确认风险、对象和策略；
3. View Model：只展示脱敏结果，不能直接把告警变成动作。

重点强调：Beszel 事件不会直接获得 Docker/systemd 动作权限。

### 3.3 现场回放

点击“播放现场回放”，按时间线讲解：

```text
观测 → critical → 唯一对象定位 → simulate 计划 → 恢复验证
```

回放结束后说明：失败、身份不明、多对象或保护对象命中时，Guardian 会进入 `escalated`，而不是继续自动升级。

## 4. 可选技术验收

页面演示后，在另一个终端运行：

```bash
python3 -m unittest discover -s tests -q
```

预期：宿主机全量测试 `61 tests ... OK`。

Multipass 验证：

```bash
multipass exec guardian-ubuntu -- bash -lc 'cd /home/ubuntu/server-resource-guardian && python3 -m unittest discover -s tests -q'
```

预期：虚拟机全量测试同样 `61 tests ... OK`。

运行态只读检查：

```bash
multipass exec guardian-ubuntu -- bash -lc 'docker ps --format "{{.Names}}\t{{.Status}}" && curl -fsS -o /dev/null -w "hub_http=%{http_code}\n" http://127.0.0.1:8090/api/health'
```

预期：`beszel-poc`、`beszel-agent-poc` healthy，`hub_http=200`。

## 5. 组长可能追问及回答

**问：这是不是把所有容器都限制内存？**

答：不是。项目目标是资源危机前发现风险并受控止损，资源限制只作为业务明确允许时的可选隔离手段。

**问：Beszel 能不能直接处理容器？**

答：Beszel 提供观测和告警；Guardian 负责本机对象确认、策略和动作。Beszel 告警本身不能授权动作。

**问：现在能不能上生产？**

答：不能直接上。当前是 ARM64 本地 PoC；还需要非生产 x86_64 Ubuntu 22.04、真实业务 health check、保护名单和明确授权。

**问：Guardian 是否真的有效？**

答：EXP-021 在相同无界内存泄漏下做了直接对照：无 Guardian 发生 global OOM，有 Guardian 在错误扩大前停止目标、回收内存并保住健康探针。
