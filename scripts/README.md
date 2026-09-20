# 实验辅助脚本

本目录的脚本用于环境采集、有限实验和本地演示。脚本必须明确适用环境、停止条件和副作用；不能把本地实验脚本当作生产部署入口。

## Guardian 资源采样器

[`guardian_resource_sampler.py`](guardian_resource_sampler.py) 对指定 PID 做只读采样，输出有界 TSV：RSS、CPU 百分比、FD 数和线程数。它不会向目标进程发送信号，也不会修改目标进程；目标消失、持续时间到达或输出达到 `--max-bytes` 时退出。

示例：

```bash
python3 scripts/guardian_resource_sampler.py \
  --pid <observer-pid> \
  --output /tmp/guardian-resource.tsv \
  --interval 10 \
  --duration 300 \
  --max-bytes 10485760
```

该脚本只用于本地 disposable 实验和证据重算，不提供生产监控、动作授权或资源限制能力。
