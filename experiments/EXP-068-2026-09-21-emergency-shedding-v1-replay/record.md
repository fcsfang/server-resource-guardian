# EXP-068：Emergency Shedding v1 离线回放

- 实验 ID：`EXP-068`
- 日期：2026-09-21
- 关联任务：`PG-P0-17 Emergency Shedding v1`
- 状态：`INCONCLUSIVE`
- 目的：将现有 EXP-063 真实 Docker 三资源原始 JSON 与 EXP-064 容量 loopback 原始 JSON 重新送入 v1 初级策略，比较旧联合/精确归因计划和新“整机危险确认 + 正向名单 + 资源 TOP + 身份完整”边界。

## 安全边界

- 仅离线读取仓库中已保存的 JSON；不连接 Docker、Multipass、Beszel、生产或通知渠道。
- 没有调用 action adapter、Docker/systemd stop/restart/kill，也没有修改原始输入；输出由 [`check_p017_replay.py`](../../scripts/check_p017_replay.py) 生成。
- 回放把每条历史记录的 workload target 作为“测试用 actionable label”，不把它写成真实 owner 批准的配置；保护/可处置资格不能从历史标签推断为生产授权。
- 历史数据缺少 v1 要求的 `cgroup_inode` 和 `emergency_shedding_v1` 正向清单字段时保持拒绝，不补造字段。

## 输入与结果

命令：

```bash
python3 scripts/check_p017_replay.py \
  experiments/EXP-063-2026-09-20-real-docker-p014/data/real-docker-v2.json \
  experiments/EXP-064-2026-09-21-real-docker-capacity-loopback/data/real-docker-capacity-v2.json \
  --output experiments/EXP-068-2026-09-21-emergency-shedding-v1-replay/data/p017-replay-v1.json
```

结果见 [`p017-replay-v1.json`](data/p017-replay-v1.json)：

| 输入 | Guardian 样本 | 旧 `graceful_stop` 计划 | 新 v1 计划 | 结论 |
| --- | ---: | ---: | ---: | --- |
| EXP-063 | 480 | 7 | 0 | 历史计划不满足 v1 身份/危险门，动作 0 |
| EXP-064 | 96 | 0 | 0 | 容量 writer ownership/危险门不足，动作 0 |

总体 `code_gate=PASS`、`status=INCONCLUSIVE`、新策略动作执行数 `0`、生成计划契约违规数 `0`。EXP-063 回放有 `56` 个样本的历史 registry 缺少 cgroup inode，EXP-064 有 `11` 个；该缺口被保留为拒绝证据。

## 差异解释

- 旧联合策略曾在 7 个样本中输出 `graceful_stop` 计划，但这些计划不能直接继承到 v1：v1 还要求显式 `CRITICAL_CONFIRMED`、完整 Docker identity（含 created/cgroup path/inode）、正向 `actionable_set`、资源贡献短窗和最多一个不可变计划。
- 回放没有把“旧计划存在”改写成新策略命中，也没有把“目标容器自然退出”改写成动作或恢复。
- EXP-064 的容量高水位仍不能形成 writer 计划；v1 继续要求目标挂载点 writer evidence，不能因为容量 TOP 看起来明显就删除文件或停止未知对象。

## 结论

离线回放证明新策略不会放宽现有 P0-14 的不完整证据：历史数据有旧计划时，缺少 v1 门禁字段仍保持 `action=none`/`execution=not_executed`；没有明确的危险确认、稳定身份或 writer ownership 时同样动作 0。这是策略边界回归证据，不是 P0-17 disposable VM 对照通过，也不授权 PG-P0-15A 或生产动作。
