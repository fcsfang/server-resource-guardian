# EXP-069：Emergency Shedding v1 disposable Docker identity / simulate 对照

- 实验 ID：`EXP-069`
- 日期：2026-09-21
- 关联任务：`PG-P0-17 Emergency Shedding v1`
- 状态：`INCONCLUSIVE`
- 代码/安全 gate：`PASS`
- 原始数据：[`p017-disposable-sim-v1.json`](data/p017-disposable-sim-v1.json)

## 目的

在一台新建的 ARM64 Ubuntu 22.04 disposable Multipass VM 中启动真实 Docker
容器，验证 P0-17 所需的完整对象身份可以从实际 daemon/cgroup 读出，并把这组
真实身份送入纯策略的 observe/simulate 场景。该实验补的是“真实 Docker 身份接入
和策略边界”证据，不把 idle 容器或显式场景标签写成真实 CPU/内存危机效果。

## 安全边界

- VM 名称为 `guardian-p017-sim`，仅在本地新建；实验完成后已执行精确
  `multipass delete guardian-p017-sim`，未触碰现有 `guardian-ubuntu`。
- 仅执行了本实验 fixture 所需的 `docker run` 和只读 `docker stats`、`docker inspect`、
  `/proc`/cgroup 读取；3 个 `idle` 容器按 45 秒 deadline 自然退出。
- harness 没有调用 Docker `stop`、`restart`、`kill`、`rm`，没有调用 Guardian action
  adapter，没有创建 capability，没有连接 Beszel、通知渠道或生产。
- `actionable_set`/`protected_set` 是本地测试标签，不是 owner 的生产批准；没有执行
  真实 `graceful_stop`。

## 方法与身份结果

命令：

```bash
python3 scripts/run_p017_disposable_sim.py \
  --vm guardian-p017-sim \
  --output experiments/EXP-069-2026-09-21-emergency-shedding-v1-disposable-sim/data/p017-disposable-sim-v1.json
```

Observer 在真实 Docker daemon 上返回 `object_registry.status=ok`，3/3 个容器均
包含 full 64-hex ID、`created_at`、cgroup path 和正整数 cgroup inode；3/3 最终自然
退出。完整原始身份和 registry 对象保存在 JSON 中，示例：

| 容器角色 | cgroup 形式 | inode | 结果 |
| --- | --- | ---: | --- |
| actionable | `/sys/fs/cgroup/system.slice/docker-<full-id>.scope` | 7594 | 采集成功 |
| protected | `/sys/fs/cgroup/system.slice/docker-<full-id>.scope` | 7704 | 采集成功 |
| unknown | `/sys/fs/cgroup/system.slice/docker-<full-id>.scope` | 7814 | 采集成功 |

## 策略场景

| 场景 | 预期 | 实际 |
| --- | --- | --- |
| 单资源 actionable TOP | 1 个 `graceful_stop` simulate plan | 通过，`execution=not_executed` |
| 混合资源 | 按 CPU 优先级最多 1 个 plan | 通过，1 个 plan |
| protected TOP | 动作 0 | 通过，`PROTECTED_TOP_CONSUMER` |
| unknown TOP | 动作 0 | 通过，`UNKNOWN_TOP_CONSUMER` |
| 贡献不足 | 动作 0 | 通过，`CONTRIBUTION_BELOW_MINIMUM` |
| identity churn | 动作 0 | 通过，`TARGET_IDENTITY_CHANGED` |
| normal | 动作 0 | 通过，`HOST_RISK_NOT_CONFIRMED` |
| observe | 动作 0、无计划 | 通过；该实验原始 JSON 记录为 `ACTION_NOT_ALLOWLISTED`，随后补充明确的 `OBSERVE_ONLY` reason code 并由单测验证 |

总计 8 个场景，`code_gate=PASS`，策略执行数为 0。

## 结论与限制

该实验确认了 P0-17 能在真实本地 Docker registry 上取得完整身份，并对正向名单、
资源优先级、保护/未知 TOP、贡献下限和身份变化保持预期 fail-closed；混合危险最多
一个 simulate 计划。整体仍为 `INCONCLUSIVE`，因为容器是有界 idle fixture，整机
`CRITICAL_CONFIRMED`、资源贡献和危险窗口均是显式策略场景标签，尚未证明真实压力下
的检测提前量、贡献测量、容器停止后的宿主缓解或业务恢复。该证据不授权
PG-P0-15A，也不改变生产门禁。
