"""完整学习流程的端到端演示。

**一条命令跑完整个闭环**::

    uv run python examples/learning_journey.py            # 离线（脚本化模型）
    uv run python examples/learning_journey.py --live     # 真实模型（需要 .env 凭证）

演示的八步就是 Phase 2 的验收清单：

1. **导入课程材料**——文件 → 课程文档 → 切分 → 向量库（Day 27–30）。
2. **建立学习状态**——目标、进度、画像（Phase 1 的领域对象）。
3. **检索材料**——按学习者的问题检索，带引用标记（Day 29）。
4. **装配上下文**——画像 / 目标 / 进度 / 材料 / 问题，按优先级取舍（Day 32）。
5. **直接教学**——结构化输出：讲解 + 分步示例 + 引导问题（Day 33）。
6. **引用校验**——答案里的引用逐条查，编造的会被拦下（Day 31）。
7. **苏格拉底式追问**——学习者答错 → 判断状态 → 调整策略（Day 34）。
8. **兜底话术**——被拦下时学习者看到的是「没找到依据」，不是带假引用的答案。

**为什么要有这个脚本**：零件都齐了，但**没有一条把它们串起来的路**。演示的价值不
在于「跑通」，而在于**让人看见每一步的产物**——材料、状态、上下文、引用、判定，
都在输出里 ✓。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from agent_learning_coach.core.application import (
    DirectTeacher,
    DocumentRequest,
    GroundingPolicy,
    MaterialQuery,
    MaterialSearch,
    SocraticTeacher,
    TeachingRequest,
    TutorContextBuilder,
    TutorContextRequest,
    choose_next_mode,
    ingest_course_documents,
)
from agent_learning_coach.core.learning.domain import (
    Course,
    CourseId,
    KnowledgePoint,
    KnowledgePointId,
    KnowledgeStructure,
    LearnerId,
)
from agent_learning_coach.core.learning.goal import (
    CapabilityRequirement,
    DailyEffort,
    GoalId,
    LearningGoal,
    TimeWindow,
)
from agent_learning_coach.core.learning.progress import (
    LearningProgress,
    PointProgress,
    PointTransition,
)
from agent_learning_coach.core.llm.mock import MockLLMClient
from agent_learning_coach.core.llm.protocol import LLMClient
from agent_learning_coach.core.memory.types import LearnerProfile
from agent_learning_coach.core.rag import (
    Chunk,
    Citation,
    CitationSet,
    HashingEmbedder,
    InMemoryVectorStore,
    SearchCourseMaterialTool,
    VectorRetriever,
    default_chunking_policy,
    load_source,
)
from agent_learning_coach.core.storage import InMemoryCourseDocumentRepository
from agent_learning_coach.core.tools.types import ToolContext

ROOT = Path(__file__).resolve().parents[1]
MATERIALS = ROOT / "tests" / "data" / "materials"

K_CLOSURE = KnowledgePointId("k-closure")
K_SCOPE = KnowledgePointId("k-scope")

TEACHING_OUTPUT = {
    "explanation": ("闭包是函数加上它定义时所处的环境[1]。即使函数已经返回，变量绑定仍然保留[1]。"),
    "examples": [
        {
            "title": "一个计数的闭包",
            "steps": [
                "定义内部函数 increment，它修改外层的 count",
                "内部函数用 nonlocal 声明 count 是外层的变量",
            ],
            "code": (
                "def counter():\n"
                "    count = 0\n"
                "    def inc():\n"
                "        nonlocal count\n"
                "        count += 1\n"
                "        return count\n"
                "    return inc"
            ),
            "question": "为什么这里要写 nonlocal？",
        },
    ],
    "citations": ["1"],
    "follow_up": "闭包和作用域是什么关系？",
}

ASSESSMENT_OUTPUT = {
    "understanding": "partial",
    "reasoning": "学习者说对了「函数记住环境」，但没说清捕获的是什么",
    "next_step": "probe",
    "follow_up_question": "那它捕获的是值，还是名字？",
}


@dataclass
class Stage:
    """演示的一个阶段。"""

    title: str
    """阶段标题。"""

    lines: tuple[str, ...] = ()
    """这个阶段的输出。"""

    def show(self) -> None:
        """打印这个阶段。"""
        print(f"=== {self.title} ===")
        for line in self.lines:
            print(line)
        print()


def _course() -> Course:
    """构造一门课。

    Returns:
        课程。
    """
    return Course(
        id=CourseId("course-js-core"),
        title="Python 核心",
        structure=KnowledgeStructure(
            points=(
                KnowledgePoint(id=K_CLOSURE, title="闭包"),
                KnowledgePoint(id=K_SCOPE, title="作用域"),
            ),
        ),
    )


def _goal() -> LearningGoal:
    """构造学习目标。

    Returns:
        目标。
    """
    return LearningGoal(
        id=GoalId("goal-closures"),
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-js-core"),
        statement="两周内能讲清楚闭包与作用域的关系",
        outcomes=("能解释闭包捕获了什么",),
        requirements=(CapabilityRequirement(point_id=K_CLOSURE, outcome="能解释闭包捕获了什么"),),
        time_window=TimeWindow(start=date(2026, 1, 1), end=date(2026, 1, 14)),
        daily_effort=DailyEffort(minutes_per_day=45),
    )


def _progress() -> LearningProgress:
    """构造学习进度：闭包已学会，作用域在学。

    Returns:
        进度。
    """
    return LearningProgress(
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-js-core"),
        points=(
            PointProgress(
                point_id=K_CLOSURE,
                transitions=(
                    PointTransition(
                        source="not_started",
                        target="learned",
                        source_kind="learned",
                        reason="练习通过",
                        at=1000.0,
                    ),
                ),
            ),
            PointProgress(
                point_id=K_SCOPE,
                transitions=(
                    PointTransition(
                        source="not_started",
                        target="in_progress",
                        source_kind="learned",
                        reason="正在学",
                        at=2000.0,
                    ),
                ),
            ),
        ),
    )


def _profile() -> LearnerProfile:
    """构造学习者画像。

    Returns:
        画像。
    """
    return LearnerProfile(
        subject="learner-1",
        level="入门",
        strengths=("动手写代码",),
        weaknesses=("作用域",),
        preferences=("先看例子",),
    )


async def _ingest() -> list[Chunk]:
    """导入课程材料并建立向量库。

    **两条线**：课程侧（课程文档实体 + 知识点关联）与检索侧（切片 + 向量）——
    它们读同一批文件，但用途不同 ✓。

    Returns:
        切片。
    """
    repository = InMemoryCourseDocumentRepository()
    requests = [
        DocumentRequest.from_path(path, point_ids=(K_CLOSURE,))
        for path in sorted(MATERIALS.glob("*.md"))
    ]
    report = await ingest_course_documents(requests, _course(), repository=repository)
    print(
        f"课程侧导入：{len(report.imported)} 份新建、{len(report.updated)} 份更新、"
        f"{len(report.unchanged)} 份未变、{len(report.failed)} 份失败"
    )
    for failure in report.failed:
        print(f"  失败：{failure.source} —— {failure.reason}")

    embedder = HashingEmbedder(dimension=256)
    chunks: list[Chunk] = []
    for path in sorted(MATERIALS.glob("*.md")):
        documents, reason = load_source(str(path))
        if not documents:
            print(f"  跳过：{path.name} —— {reason}")
            continue
        for document in documents:
            chunks.extend(default_chunking_policy().split(document))
    store = InMemoryVectorStore()
    await store.add(chunks, await embedder.embed([chunk.text for chunk in chunks]))
    print(f"检索侧入库：{len(chunks)} 块（维度 {embedder.dimension}）")
    return chunks


def _client(live: bool, payload: Mapping[str, Any]) -> LLMClient:
    """构造模型客户端。

    Args:
        live: 是否用真实供应商。
        payload: 脚本化模式下要返回的结构化输出。

    Returns:
        客户端。
    """
    if not live:
        return MockLLMClient(text=json.dumps(payload, ensure_ascii=False))
    from dotenv import load_dotenv

    from agent_learning_coach.core.llm import create_client

    load_dotenv(ROOT / ".env")
    return create_client("minimax-cn")


async def run(live: bool) -> None:
    """跑完整流程。

    Args:
        live: 是否用真实模型。
    """
    print("=" * 68)
    print("Agent Learning Coach —— 完整学习流程演示")
    print("=" * 68)
    print()

    # --- 1. 导入材料 ---------------------------------------------------
    chunks = await _ingest()
    print()

    # --- 2. 建立学习状态 -----------------------------------------------
    goal = _goal()
    progress = _progress()
    profile = _profile()
    Stage(
        "2. 学习状态",
        (
            f"目标：{goal.statement}",
            f"时间：{goal.time_window.render() if goal.time_window else '未定'}"
            f"｜投入：{goal.daily_effort.render() if goal.daily_effort else '未定'}",
            "进度：闭包 已学会｜作用域 学习中",
            "画像：入门｜薄弱点 作用域｜偏好 先看例子",
        ),
    ).show()

    # --- 3. 检索材料 ----------------------------------------------------
    embedder = HashingEmbedder(dimension=256)
    store = InMemoryVectorStore()
    await store.add(chunks, await embedder.embed([chunk.text for chunk in chunks]))
    retriever = VectorRetriever(store, embedder, top_k=3)
    search = MaterialSearch(retriever=retriever)
    question = "闭包为什么能记住外部变量？"

    hits = await search.search(MaterialQuery(query=question, top_k=3))
    Stage(
        "3. 检索材料",
        tuple(
            [f"问题：{question}"]
            + [f"  {hit.score:.3f}  {hit.chunk.source}#{hit.chunk.index}" for hit in hits]
        ),
    ).show()

    # --- 4. 装配上下文 --------------------------------------------------
    tool = SearchCourseMaterialTool(retriever)
    outcome = await tool.execute(ToolContext(), {"query": question, "top_k": 3})
    data = outcome.data or {}
    citations = tuple(Citation.from_dict(item) for item in data.get("citations", []))
    context_request = TutorContextRequest(
        question=question,
        profile=profile,
        goal=goal,
        progress=progress,
        citations=CitationSet(citations=citations),
    )
    context = TutorContextBuilder(max_tokens=2000).build(context_request)
    Stage(
        "4. 装配上下文",
        tuple(
            [
                f"{section.name}（优先级 {section.priority}，{section.tokens} token）"
                for section in context.sections
            ]
            + [f"共 {context.total_tokens} token；丢掉 {list(context.dropped_names)}"],
        ),
    ).show()

    # --- 5 & 6. 教学 + 引用校验 -------------------------------------------
    teacher = DirectTeacher(
        client=_client(live, TEACHING_OUTPUT),
        policy=GroundingPolicy(),
    )
    turn = await teacher.teach(
        TeachingRequest(
            question=question,
            context=context_request,
        ),
    )
    Stage(
        "5. 直接教学",
        (
            turn.output.explanation,
            "",
            *(turn.output.examples[0].steps if turn.output.examples else []),
            "",
            f"想一想：{turn.output.examples[0].question}" if turn.output.examples else "",
            "",
            f"下一步：{turn.output.follow_up}",
        ),
    ).show()

    Stage(
        "6. 引用校验",
        (
            f"判定：{turn.grounding.summary()}",
            f"说明：{turn.grounding.note}",
            "",
            "--- 学习者看到的 ---",
            turn.render(),
        ),
    ).show()

    # --- 7. 苏格拉底式追问 -----------------------------------------------
    socratic = SocraticTeacher(client=_client(live, ASSESSMENT_OUTPUT))
    socratic_request = TeachingRequest(
        question=question,
        context=context_request,
        mode="socratic",
    )
    assessment = await socratic.assess(
        socratic_request,
        "闭包就是函数记住了外面的变量",
    )
    decision = choose_next_mode(assessment)
    Stage(
        "7. 苏格拉底式追问",
        (
            "学习者回答：闭包就是函数记住了外面的变量",
            f"判断：{assessment.understanding}——{assessment.reasoning}",
            f"下一步：{assessment.next_step}",
            f"模式：{decision.mode}（{decision.reason}）",
        ),
    ).show()

    # --- 8. 兜底话术 -----------------------------------------------------
    # 把教学输出换成**声明了不存在标记**的版本：门禁会拦下它 ✓。
    bad_output = dict(TEACHING_OUTPUT)
    bad_output["explanation"] = "闭包是函数加上它定义时所处的环境[9]。"
    bad_output["citations"] = ["9"]
    bad_teacher = DirectTeacher(
        client=_client(live, bad_output),
        policy=GroundingPolicy(),
    )
    bad_turn = await bad_teacher.teach(
        TeachingRequest(question=question, context=context_request),
    )
    Stage(
        "8. 兜底：编造引用会被拦下",
        (
            "教学输出声明了 [9]，但本次可用的引用只有 1、2、3——",
            f"判定：{bad_turn.grounding.summary()}",
            f"说明：{bad_turn.grounding.note}",
            "",
            "--- 学习者看到的（不是带假引用的原文）---",
            bad_turn.render(),
            "",
            "重试时给模型的指令：",
            f"  {bad_turn.grounding.retry_instruction}",
        ),
    ).show()

    print("=" * 68)
    print("演示结束。材料、状态、上下文、引用、判定——每一步的产物都在上面。")
    print("=" * 68)


def main() -> None:
    """入口。"""
    parser = argparse.ArgumentParser(description="完整学习流程演示")
    parser.add_argument(
        "--live",
        action="store_true",
        help="用真实模型（需要 .env 里的供应商凭证）",
    )
    args = parser.parse_args()
    asyncio.run(run(args.live))


if __name__ == "__main__":
    main()
