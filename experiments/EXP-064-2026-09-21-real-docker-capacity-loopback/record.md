# EXP-064：真实 Docker bind-mount 容量高水位补证

- 实验 ID：`EXP-064`
- 状态：`INCONCLUSIVE`
- 日期：2026-09-21
- 关联 Goal：`Goal 7 / PG-P0-14`
- 前置结果：[`EXP-063`](../EXP-063-2026-09-20-real-docker-p014/record.md)
- 目的：在全新 disposable Multipass VM 中创建受控 128 MiB loopback 文件系统，将它 bind-mount 到真实 Docker workload 的 `/work`，验证容器写入导致的容量高水位信号、full ID/cgroup 证据和无法确认 writer ownership 时的 fail-closed 行为。

## 1. 安全边界

- 只创建并删除本实验专用的 `guardian-p014-capacity-loopback` VM；不操作现有 `guardian-ubuntu`，不连接生产。
- 根盘只增加约 128 MiB loopback 文件；不向宿主根盘做填满实验，不删除任意业务文件。
- Guardian 只运行 `--mode simulate`；不创建 capability，不调用 action adapter，不执行 Docker/systemd stop、restart 或 kill。
- 只使用 `docker run` 创建 workload/churn fixture；容器自然退出，VM 删除是清理边界。
- 无论容量风险是否被检测，原始 JSON 和失败信息均保留。

## 2. 预置验收条件

1. capacity 场景完成三轮，每轮包含 unprotected、rescue、guardian 三组。
2. Guardian 的 config 同时观察 `/` 和 `/mnt/p014-capacity`，原始数据记录 loopback 挂载点的 free bytes/free ratio。
3. 目标 full ID、PID、cgroup path、health 和 workload 写入命令可重算。
4. capacity writer ownership 未登记时，最多输出 observe/simulate 证据或放弃原因，不生成可执行动作。

## 3. 执行与环境

使用 runner 的聚焦场景参数，在两台全新 disposable VM 上分别保留 v1 夹具失败并完成 v2 重跑：

```bash
python3 scripts/run_p014_real_docker.py \
  --vm guardian-p014-capacity-loopback --rounds 3 \
  --scenario capacity --capacity-loopback --experiment-id EXP-064 \
  --output experiments/EXP-064-2026-09-21-real-docker-capacity-loopback/data/real-docker-capacity-v2.json
python3 scripts/check_p014_real_docker.py \
  experiments/EXP-064-2026-09-21-real-docker-capacity-loopback/data/real-docker-capacity-v2.json \
  --output experiments/EXP-064-2026-09-21-real-docker-capacity-loopback/data/real-docker-capacity-v2-check.json
```

VM 为 Ubuntu 22.04.5 LTS、ARM64、2 vCPU、约 3 GiB RAM、12 GiB 磁盘；Docker 29.1.3、overlayfs、cgroup v2/systemd controller。实验结束后两台 VM 均已精确删除，`guardian-ubuntu` 未修改。

## 4. 结果

### 4.1 失败夹具（v1，保留）

`real-docker-capacity-v1-fixture-failure.json` 保留了第一版结果：128 MiB raw image 实际约 104 MiB ext4 可用空间，第一 workload 请求 120 MiB 后 exit 1，并使后续共享挂载点失去独立重复条件。该数据没有被当作有效通过证据。

### 4.2 修正后 v2

- capacity 场景三轮、每轮 unprotected/rescue/guardian 三组，控制探针 `36/36` 成功，Guardian `3` 个窗口、`96/96` 样本。
- 每轮只在已知 fixture 挂载点清理自身 `/mnt/p014-capacity/capacity.bin`，该路径和清理命令写入每个 target 记录；没有清理未知文件，也没有执行 Docker/systemd stop/restart/kill。
- 目标容器按 100 MiB 写入受控 loopback mount；Guardian 观察到 `/mnt/p014-capacity` 最低 free ratio `1.289%`，三轮均形成 `disk_capacity` warning。
- 三轮 Guardian 目标均出现真实 full ID/cgroup 证据，健康 probe 在窗口前达到 `healthy`；三轮均有 3 个不同 churn ID；所有 decision 均为 `not_executed`。
- capacity writer ownership 明确不可用，三轮在 capacity attribution 上均输出 `capacity_writer_ownership_unavailable`，没有生成可执行目标；这验证了容量高水位命中时的 fail-closed 边界。
- Guardian 开销原始样本 `217`，审计字节约 `714,786～717,563`；没有 owner 批准的开销 SLO。该窗口仍有 CPU/IO 背景 warning，整体联合状态多次进入歧义/升级，因此不能把 capacity 命中写成完整三资源通过。

重算结果：`code_gate=PASS`、`status=INCONCLUSIVE`。剩余缺口为 Beszel 同负载时间差、owner Rescue SLO、稳定恢复/业务恢复语义和背景资源误触发边界。

## 5. 结论

EXP-064 补齐了真实 Docker bind-mount 容量高水位与 fail-closed writer ownership 证据，但不能单独改变 PG-P0-14 的整体状态；容量补证仍是本地 ARM64 disposable 证据，不能替代真实设备、overlay/writeback、x86_64、owner Rescue SLO 或生产 Observe/Simulate 准入。
