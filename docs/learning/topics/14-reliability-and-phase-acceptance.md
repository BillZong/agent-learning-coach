# Topic: 可靠性与阶段验收

> Day 14 ｜ Phase 0 · Agent Engineering Foundation ｜ 主教材：Ch10：10.1～10.4；Ch8：8.3～8.5（选读） ｜ 外部参考：OpenTelemetry、OpenAI Agent Evaluation、NIST AI RMF ｜ 状态：已完成

## 1. Engineering Question

如何用 Trace / Metrics 定位 Agent 的失败？阶段验收需要哪些 Evaluation Cases，才算真正通过？

## 2. Primary Reading

- Book: Ch10：10.1～10.4；Ch8：8.3～8.5（选读）
- Chapter / Section: Ch10 10.1～10.4；Ch8 8.3～8.5
- 核心观点（读到的结构性观点，未逐句摘录）：
  - **评估围绕任务组织**：单看最终答案无法判断 Agent 好不好；失败往往发生在
    「第几步走偏」，需要执行过程才能解释。
  - **可观测性是评估的前提**：没有轨迹，评估只能看到结果；有轨迹，才能回答
    「为什么」。
  - **评估需要数据集与回归**：一次性的手工检查无法回答「改动之后有没有变好」；
    评估要能重复执行。
  - **选读部分（Ch8 8.3～8.5）** 没有深入：只作为「可验证的外部信号」这一思路
    的交叉参考，未纳入本次实现。

## 3. Cross References

| 来源 | 核心观点 | 与主教材的关系 |
|---|---|---|
| OpenTelemetry | 三种信号：Traces / Metrics / Logs。Trace 由 span 组成，span 带 attributes 与父子关系；采集与导出解耦（SDK → exporter → 后端）；支持采样，避免全量成本 | 给我们**信号分层**的骨架：事实（Logs/事件）、视图（Trace）、聚合（Metrics）。本项目的 Trace 字段刻意贴近 span 语义（execution_id、逐步序号、耗时、状态），将来接 OTel 只需加导出器——但现在不起管道，理由见 ADR-0014 |
| OpenAI Agent Evaluation | Eval 由 **dataset + grader** 组成；grader 有三类：字符串检查、模型评分（model grader）、代码断言（code grader）；trace grading 可以对执行轨迹评分 | 与主教材「围绕任务评估」一致。本项目选 **code grader**（确定性断言）做门禁，把 model grader 留给 Phase 2 的语义质量评估——因为判官不可回归 |
| NIST AI RMF | 四个功能：GOVERN / MAP / MEASURE / MANAGE；强调**持续测量**、风险记录与可追溯 | 与「指标用来比较、诊断给证据」同源。但 RMF 是组织级治理框架，本项目只落地 MEASURE 的一小部分：一组可解释的指标 + 一个可回归的评估集，不假装完成了合规 |
| 官方工程指南（Anthropic《Building effective agents》、OpenAI evals 指南） | 从最简单的方案开始；**评估驱动开发**——先有能跑的最小评估集，再迭代 | 与主教材一致，但都强调「简单」。本项目因此放弃了第一个版本里的两个想法：独立 trace 缓冲、LLM 判官 |
| 本项目 ADR-0005 / 0006 / 0009 / 0014 | 停止策略分层、预算只兜底；状态要能持久化；可观测性是投影不是第二套事实 | 把「可靠性」从文档变成可执行的东西：停止原因表 → 诊断提示 → 评估用例，三者共用同一套枚举 |

## 4. Compare and Challenge

- **不同资料有哪些一致观点？**
  全部一致认为：**没有轨迹就没有定位**；**评估必须可重复**；**指标要能指导
  下一步行动**，否则只是装饰。OpenTelemetry 与本项目的差异只在「采集在哪一层」，
  不在「要不要采集」。
- **哪些观点存在分歧？**
  - **判官 vs 断言**：OpenAI Agent Evaluation 把 model grader 列为正式手段，
    NIST AI RMF 强调「测量要覆盖难以量化的风险」；本项目却明确**不用**判官做
    门禁。分歧不在「判官有没有用」，而在「它能不能当门禁」——不能，因为不可
    回归。
  - **现在接不接 OTel**：OTel 的默认建议是尽早标准化埋点；本项目选择先用
    内置 Trace，理由是**先有可解释的数据，再谈导出**。
- **分歧来自应用场景、设计目标还是技术演进？**
  - 判官的分歧来自**设计目标**：本项目要的是「改完代码知道自己没退步」，判官
    做不到确定性，因此只能做监控，不能做门禁。
  - OTel 的分歧来自**应用场景**：分布式追踪的收益随服务数量增长；Phase 0 是
    单进程、单运行时，收益接近零，成本（依赖 + 运维）却是实打实的。
- **哪些结论缺少充分证据？**
  - 「诊断能加速定位失败」——本项目只验证了诊断**包含正确的证据**（工具名、
    重复次数、错误信息），**没有做过「有诊断 vs 无诊断」的对照实验**。这是
    一个合理的断言，但没有被测量。
  - 「评估集覆盖失败路径就够了」——覆盖的是机制层；语义质量完全没覆盖。
  - 「p50/p95 指标能反映改动的影响」——样本量只有个位数，百分位只是参考。
- **哪些观点不适用当前项目？**
  - **完整的 OTel 管道**：没有跨进程、跨服务的真实需求。
  - **NIST AI RMF 的组织级流程**：本项目没有合规义务，照搬只会制造文档负担。
  - **LLM-as-judge 作为门禁**：不可回归，会把 CI 变成抽奖。

## 5. Engineering Practice

- **实现了什么？**
  - **`core/agent/observability.py`**：
    - `RunTrace` / `StepTrace` / `ToolSpan`：从 `AgentResult` **投影**出一次
      运行的结构化轨迹——不新增事实，不另开事件缓冲。
    - `compute_metrics`：成功率、停止原因分布、工具失败率、步数与耗时百分位、
      token 与成本，全部对有限集合做聚合。
    - `diagnose`：停止原因 → 检查方向 + 从 Trace 取出的证据（失败工具与错误、
      被重复的动作与次数）。成功与 `pending_approval` 不诊断。
  - **`core/agent/evaluation.py`**：
    - `ScriptedLLMClient`：确定性驱动器（同时供测试复用，替身只有一份实现）。
    - `EvalCase` / `ScriptedTurn` / `EvalResult` / `EvalReport`：用例是数据，
      断言是行为契约（停止原因、工具序列、失败工具、输出子串）。
    - `EvalHarness` / `load_cases` / `build_eval_tools`：套件运行、JSON 加载、
      自带最小工具集（`echo` / `boom`）。
  - **`AgentStep.think_ms`**：运行时的唯一新增插桩——模型调用耗时无法从已有
    事实推导，因此必须测。
  - **`evals/phase0.json`**：7 条基础用例，覆盖成功、工具失败自愈、未知工具、
    步数兜底、原地打转、连续失败。
  - **`examples/reliability_report.py`**：Trace → Metrics → 诊断 → 评估套件
    的可运行报告；套件不通过时非零退出，可直接当门禁。
- **为什么这样设计？**
  - **Trace 是投影**：两份事实迟早对不上；调试时「以哪份为准」在故障现场没有
    答案。
  - **模型耗时记录在步上**：能用推导解决的不新增记录；不能推导的（耗时）就
    在产生它的地方测一次。
  - **指标是聚合**：没有 exporter、没有滑动窗口——先回答「改动前后」，实时
    管道留给有线上流量之后。
  - **评估用 code grader**：判官不确定、不可回归；门禁只放确定性层。
  - **用例是数据**：加用例改 JSON，不改代码；数据集进仓库、进评审、进 CI。
- **有哪些替代方案？**
  - **自建 trace 缓冲 / 接 OpenTelemetry**：前者制造第二份事实，后者引入依赖
    与运维。都否决，接入顺序写进 ADR-0014。
  - **LLM-as-judge 评估**：不可回归，只能做监控。推迟到 Phase 2。
  - **评估打 0～1 分**：单分数会隐藏失败原因；改为逐条断言 + 「期望 vs 实际」。
  - **真实模型跑评估集**：不确定、要凭证、有成本；确定性层用脚本化模型，
    真实模型层留给监控。
- **测试结果如何？**
  - 新增 `tests/test_observability.py`（9）与 `tests/test_evaluation.py`（8）：
    全量 **594 passed**；ruff / mypy 全绿。
  - `examples/reliability_report.py` 跑通：3 条 Trace、聚合指标、1 条诊断、
    评估集 **7/7 通过**。
- **实践结果是否支持阅读材料中的观点？**
  - 支持「没有轨迹就没有定位」：同一个 `repeated_call`，有了 Trace 能直接说
    「echo 被调用 3 次」，没有它只能说「模型可能卡住了」。
  - 支持「评估必须可重复」：脚本化模型让「用例失败」只可能来自运行时。
  - **部分支持**「指标能指导行动」：指标能回答「哪类失败变多了」，但样本量
    太小，还不足以支撑阈值告警。

## 6. Reflection

1. **今天最重要的知识是什么？**
   可靠性的边界是**可观测**，而不是「不出错」。四类不可靠来源里，只有模型的
   非确定性无法消除；其余三类都能被工程手段约束。因此真正要设计的是「出错时
   有没有痕迹、能不能恢复、会不会继续烧预算」。

2. **哪个观点改变或修正了原来的理解？**
   最初以为「可观测性 = 接一个 tracing 后端」。读完 OTel 与本项目的实现之后
   修正为：**可观测性是数据模型问题，不是管道问题**。先把事实组织成可解释的
   Trace 与 Metrics，导出只是最后一公里；反过来先接管道，会得到一个字段含义
   没人说得清的仪表盘。

3. **当前实现还有哪些局限？**
   - 无分布式追踪：MCP 子进程内部不可见。
   - 评估不覆盖语义质量，也没有真实模型的行为回归。
   - 指标样本小，p95 只是参考；告警阈值需要基线。
   - 诊断的「检查方向」是人工维护的映射表（新增停止原因要补）。
   - 没有「诊断能否加速定位」的对照证据。

## 7. Output

- Code / PR:
  - `src/agent_learning_coach/core/agent/observability.py`
  - `src/agent_learning_coach/core/agent/evaluation.py`
  - `src/agent_learning_coach/core/agent/types.py`（`AgentStep.think_ms`）
  - `src/agent_learning_coach/core/agent/runtime.py`（测量模型耗时）
  - `examples/reliability_report.py`
  - `evals/phase0.json`
- Tests: `tests/test_observability.py`（9）、`tests/test_evaluation.py`（8）；
  全量 594 passed，ruff / mypy 全绿
- ADR: [ADR-0014](../../architecture/decisions/0014-observability-and-evaluation.md)
  ——可观测性与评估的边界
- Article Notes: [docs/articles/reliable-agent-system.md](../../articles/reliable-agent-system.md)、
  [docs/articles/agent-evaluation.md](../../articles/agent-evaluation.md)
- Unresolved Questions:
  - 语义质量评估的锚点从哪里来？Phase 2 的哪些标注能当标准？
  - 真实模型的行为回归怎么做抽样与容差？
  - 诊断的价值怎么证明？需要一个「有诊断 vs 无诊断」的对照实验。
  - 告警阈值怎么定？需要先积累多少条运行才算基线？
  - 跨进程追踪（MCP 子进程）什么时候必须做？
