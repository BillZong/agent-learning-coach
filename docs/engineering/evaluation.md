# 评估（Evaluation）

> 状态：草稿 ｜ 对应 Topic 14 ｜ 决策依据：ADR-0014

这个项目里有两类评估，**不要混着看**：

| 类型 | 回答的问题 | 代码 | 指标 |
| ---- | ---- | ---- | ---- |
| 检索评估 | 检索排得对不对？ | `core/rag/evaluation.py` | hit@k、MRR |
| 行为评估 | 运行时行为对不对？ | `core/agent/evaluation.py` | 用例通过率 |

本文讲第二类。第一类见 [RAG 接入说明](rag.md)。

## 1. 评估对象

评估的是**机制**，不是措辞：

- 模型请求工具时，工具是否按预期被调用（名字、顺序）；
- 工具失败时，错误是否被归一化回填、运行是否继续；
- 停止原因是否符合预期（自然结束 / 步数兜底 / 原地打转 / 连续失败）；
- 最终输出是否交付。

**为什么不评估答案质量**：Phase 0 的 Agent 还没有真实业务场景；而且答案质量
评估需要判官，判官本身不确定，用它给「机制」打分等于用不确定验证不确定。
语义质量评估推迟到 Phase 2，有真实教学场景与人工标注之后再做。

## 2. 数据集构建

用例是**数据**，住在 `evals/phase0.json`：

```json
{
  "cases": [
    {
      "name": "工具失败被归一化后自愈",
      "prompt": "调用 boom 工具；如果它失败，就说明情况并给出替代方案。",
      "turns": [
        { "tool": "boom", "arguments": {} },
        { "text": "工具不可用，我改用讲解的方式回答。" }
      ],
      "expected_tools": ["boom"],
      "expected_tool_errors": ["boom"],
      "expected_output_contains": "工具不可用"
    }
  ]
}
```

| 字段 | 含义 |
| ---- | ---- |
| `name` | 用例名，报告里用它定位 |
| `prompt` | 用户输入 |
| `turns` | 脚本化的模型响应：`text`（回答）或 `tool`（工具调用），二选一 |
| `expected_stop_reason` | 期望的停止原因，默认 `completed` |
| `expected_tools` | 期望按顺序执行的工具名 |
| `expected_tool_errors` | 期望失败的工具名（按顺序） |
| `expected_output_contains` | 期望输出包含的子串 |
| `max_steps` | 本次运行的步数上限 |

**为什么用脚本化模型**：行为断言需要确定性。脚本化响应让「模型做了什么」变成
输入的一部分——用例失败时，失败原因只可能来自运行时，不来自模型的随机性。
这也意味着数据集可以进 CI，不消耗额度、不需要凭证。

**脚本驱动的工具**：数据集自带最小工具集 `build_eval_tools()`（`echo` 成功、
`boom` 故意失败），因此用例不依赖网络、文件或任何外部环境。

## 3. 指标体系

Behavior 层的指标只有三个，都是**覆盖**而不是**质量**：

1. **通过率**：`EvalReport.summary()`。
2. **停止原因覆盖**：数据集必须包含 `completed` 之外的路径——`max_steps`、
   `repeated_call`、`consecutive_errors`。
3. **失败路径覆盖**：未知工具（`missing_tool`）、工具抛异常（`boom`）。

`tests/test_evaluation.py::test_suite_covers_failure_paths` 把第 2、3 条钉住：
**一个只覆盖成功路径的数据集，全绿也说明不了什么。**

## 4. 回归与门禁

三层，从快到慢：

| 层 | 命令 | 用途 |
| ---- | ---- | ---- |
| 单元测试 | `uv run pytest tests/test_observability.py tests/test_evaluation.py` | 机制本身的正确性 |
| 评估套件 | `make eval`（`uv run python examples/reliability_report.py`） | 数据集全量回归；不通过时非零退出，CI 已接入 |
| 全量检查 | `make check`（ruff + mypy + pytest + eval） | 提交前门禁 |

加一条用例：往 `evals/phase0.json` 的 `cases` 里加一项，跑一次评估套件。不需要
改代码——这是「用例是数据」的意义。

**失败报告要能定位**：`EvalResult.failures` 逐条给出「期望 vs 实际」，例如:

```
工具调用：期望 ['另一个工具']，实际 ['echo']
```

而不是只给一个 `assert False`。

## 5. 与 Trace 的关系

评估跑完的每一次运行都会产出一条 `RunTrace`，因此用例失败时可以直接看：

```python
result.trace.steps  # 每步的 think_ms、token、工具调用
result.trace.stop_reason  # 实际停在哪
```

脚本耗尽等用例错误不会产出 Trace（`trace=None`）——运行根本没开始，没有轨迹
可言。

## 6. 已知限制

| 限制 | 说明 |
| ---- | ---- |
| 不评估语义质量 | 回答讲不讲得清楚，这套机制答不上来 |
| 脚本化模型不是真实模型 | 验证的是「给定这些响应，运行时行为对不对」，不是「真实模型会不会这样响应」 |
| 真实模型的回归需要抽样 | 接真实模型后要允许一定容差（例如通过率区间），不能要求 100% 复现 |
| 数据集不随包发布 | `evals/` 属于仓库资产，不打包进 wheel |

## 相关文档

- 可观测性：[可靠性（Reliability）](reliability.md)
- 检索评估：[RAG 接入说明](rag.md)
- 决策依据：[ADR-0014](../architecture/decisions/0014-observability-and-evaluation.md)
- 学习笔记：[Topic 14 · 可靠性与阶段验收](../learning/topics/14-reliability-and-phase-acceptance.md)
