"""检索 + 引用 + 校验的完整闭环。

跑法::

    uv run python examples/rag_citation_agent.py

脚本演示四件事：

1. **摄取**——把课程材料切分、嵌入、写入向量库。
2. **检索**——Agent 调用检索工具，工具返回带 ``[1]`` 标记的段落。
3. **引用**——Agent 的答案里复述这些标记。
4. **校验**——拿答案去比对引用集合，报告「编造的引用」与「不支撑的引用」。

**为什么要有最后一步**：模型会编造引用。它写 ``[3]`` 时，那个编号可能指向一段
它根本没读到的内容。引用一旦不可核对，RAG 的可追溯性就只是幻觉的一种形式。

这个脚本用脚本化的模型客户端，因此**不联网**也能跑。
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from agent_learning_coach.core.agent import AgentConfig, AgentRuntime, StateCollector
from agent_learning_coach.core.llm.models import LLMResponse, LLMUsage
from agent_learning_coach.core.llm.types import Message, ToolCall
from agent_learning_coach.core.rag import (
    CitationSet,
    CitationValidator,
    HashingEmbedder,
    InMemoryVectorStore,
    RetrieveKnowledgeTool,
    SearchCourseMaterialTool,
    VectorRetriever,
    ingest_directory,
)
from agent_learning_coach.core.tools import ToolDispatcher, ToolRegistry

MATERIALS = {
    "closures.md": (
        "# 闭包\n\n"
        "闭包是函数连同它引用的外部变量的组合。\n\n"
        "闭包让函数可以携带自己的环境，不必依赖全局状态。这是函数式编程里最重要的"
        "特性之一。\n"
    ),
    "recursion.md": (
        "# 递归\n\n递归是函数调用自己来解决问题。递归需要一个终止条件，否则会一直展开直到栈溢出。\n"
    ),
    "pets.md": "# 猫\n\n猫喜欢在温暖的沙发上睡觉，呼噜声据说有治愈作用。\n",
}


class ScriptedClient:
    """按脚本依次返回预设响应的模型客户端。"""

    def __init__(self, responses: list[LLMResponse]) -> None:
        """初始化脚本化客户端。

        Args:
            responses: 依次返回的响应。
        """
        self.responses = list(responses)

    async def generate(self, request: object) -> LLMResponse:
        """返回脚本中的下一个响应。"""
        return self.responses.pop(0)

    async def generate_structured(self, request: object, output_model: type) -> object:
        """本示例不使用结构化输出。"""
        raise NotImplementedError


def _tool_call(name: str, arguments: dict[str, object], call_id: str) -> LLMResponse:
    """构造一个只含工具调用的响应。

    Args:
        name: 工具名。
        arguments: 工具参数。
        call_id: 调用 id。

    Returns:
        模型响应。
    """
    return LLMResponse(
        model="mock-model",
        contents=[ToolCall(tool_call_id=call_id, name=name, arguments=arguments)],
        usage=LLMUsage(input_tokens=10, output_tokens=5),
    )


def _text(text: str) -> LLMResponse:
    """构造一个只含文本的响应。

    Args:
        text: 文本内容。

    Returns:
        模型响应。
    """
    return LLMResponse(
        model="mock-model",
        contents=[Message(role="assistant", content=text)],
        usage=LLMUsage(input_tokens=20, output_tokens=10),
    )


async def main() -> None:
    """跑一遍完整闭环。"""
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        for name, text in MATERIALS.items():
            (directory / name).write_text(text, encoding="utf-8")

        store = InMemoryVectorStore()
        embedder = HashingEmbedder(dimension=256)
        ingested = await ingest_directory(directory, store, embedder)
        print(f"1. 摄取：{ingested.summary()}")
        print(f"   来源：{await store.sources()}")

        retriever = VectorRetriever(store, embedder, top_k=2)
        registry = ToolRegistry()
        registry.register(SearchCourseMaterialTool(retriever, top_k=2))
        registry.register(RetrieveKnowledgeTool(store))

        # 把检索到的引用收进执行上下文的草稿本——它是运行时状态，模型看不到。
        dispatcher = ToolDispatcher(
            registry,
            after_hooks=[StateCollector("citations", data_key="citations")],
        )
        runtime = AgentRuntime(
            ScriptedClient(
                [
                    _tool_call("search_course_material", {"query": "闭包 外部变量"}, "call-1"),
                    _text(
                        "闭包是函数连同它引用的外部变量的组合[1]。"
                        "它让函数可以携带自己的环境，不必依赖全局状态[1]。"
                    ),
                ]
            ),
            dispatcher,
            config=AgentConfig(model="mock-model", max_steps=4),
        )

        result = await runtime.run("按教材说说闭包是什么")

        print("\n2. 检索（模型调用 search_course_material）：")
        for event in result.context.events:
            for item in event.contents:
                if item.__class__.__name__ == "ToolResult":
                    print(f"   {item.content.splitlines()[0]}")

        # 回调失败只落在 outcome.metadata 里——转录看不到，得主动查。
        errors = [
            error
            for step in result.steps
            for outcome in step.outcomes
            for error in outcome.metadata.get("callback_errors", [])
        ]
        if errors:
            print(f"   ⚠️ 回调出错：{errors}")

        answer = ""
        for event in result.context.events:
            for item in event.contents:
                if isinstance(item, Message) and item.role == "assistant":
                    answer = item.content

        print(f"\n3. 答案：{answer}")

        citations = CitationSet.from_list(result.context.state.get("citations", []))
        print(f"\n4. 引用校验（{len(citations)} 条可用引用）：")
        for citation in citations.citations:
            print(f"   {citation.reference_line()}")

        report = CitationValidator().validate(answer, citations)
        print(f"   结论：{'通过' if report.valid else '不通过'}（引用了 {list(report.cited)}）")
        for issue in report.issues:
            print(f"   - [{issue.kind}] {issue.detail}")

        print("\n5. 如果模型编造了引用呢？")
        fabricated = "闭包是函数连同它引用的外部变量[1]，而且会自动优化[7]。"
        fake_report = CitationValidator().validate(fabricated, citations)
        print(f"   结论：{'通过' if fake_report.valid else '不通过'}")
        for issue in fake_report.issues:
            print(f"   - [{issue.kind}] {issue.detail}")

        print("\n6. 草稿本里的引用（模型看不到，但可持久化）：")
        print("   " + json.dumps(result.context.state.get("citations", [])[:1], ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
