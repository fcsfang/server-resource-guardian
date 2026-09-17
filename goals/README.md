# 目标与任务目录

这是项目的执行目标总入口。它记录“要做什么、做到什么算完成、当前由谁接手、下一步从哪里开始”，用于跨电脑和跨 agent 交接。

## 目录规则

- 目标编号全局递增，从 `Goal 1` 开始；目标完成、暂停或废弃后，编号不复用。
- 一个文件按一个稳定主题组织，可以包含多个 Goal；只有目标主题不同或生命周期明显分离时才新建文件。
- 文件名使用主题名，不使用会因新增 Goal 而变化的文件名，例如 `resource-protection.md`。
- 每个 Goal 内的任务使用 `G<目标编号>-T<两位序号>`，例如 `G1-T01`。
- Goal 必须写清状态：`PLANNED`、`IN_PROGRESS`、`BLOCKED`、`DONE` 或 `ABANDONED`。
- 完成或阻塞时更新 Goal 文件，并在 `PROGRESS.md` 写一条摘要；不要只更新聊天记录。
- Goal 是执行计划，不是实验事实。实验结果必须进入 [`experiments/`](../experiments/README.md)。

## 当前目标索引

| Goal | 分类文件 | 状态 | 目标摘要 | 首要参考 |
| --- | --- | --- | --- | --- |
| [Goal 1：两层资源保护方案测试](resource-protection.md#goal-1-两层资源保护方案测试) | [resource-protection.md](resource-protection.md) | `PLANNED` | 验证第一层人工救援能力与第二层自动资源保护策略 | [测试交接](../docs/13-leader-test-handoff.md)、[PoC 蓝图](../docs/07-poc-blueprint.md) |

## 新增 Goal 的最小模板

```markdown
## Goal N：<目标名称>

- 状态：`PLANNED`
- 创建日期：YYYY-MM-DD
- 最近更新：YYYY-MM-DD
- 负责人：<人或协作方式>
- 来源：<需求、leader 反馈或 issue>

### 目标结果

<可观察、可验收的结果>

### 任务清单

- [ ] GN-T01 <任务>

### 接手入口

<下一位 agent 先读什么、先做什么、不要做什么>

### 完成标准

- [ ] <验收条件>

### 更新记录

- YYYY-MM-DD：<变化>
```

## Agent 接手原则

接手时按以下顺序读取：

```text
README.md → PROGRESS.md → goals/README.md → 当前 Goal 文件 → 相关 docs → 相关 experiments
```

执行前先确认当前 Goal 状态、未完成任务、依赖和授权边界；执行后更新任务勾选、实验编号、证据链接和 `PROGRESS.md`。
