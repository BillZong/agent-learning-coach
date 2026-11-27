"""结构化输出的测试。

覆盖主教材 4.7「Tools as output formatters」的做法：最终答案通过
``final_answer`` 工具产出，走的是与普通工具调用完全相同的通道。

关注点：

- 最终答案是**已校验的模型实例**，不是文本。
- 校验失败时错误回填给模型，它可以自己纠正——运行不失败。
- 嵌套模型的 schema（``$ref`` / ``$defs``）能正确透出并通过校验。
- 结构化输出工具不污染调用方的注册表。
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from agent_learning_coach.core.agent import (
    AgentConfig,
    AgentRuntime,
    build_output_schema,
)
from agent_learning_coach.core.agent.output import FINAL_ANSWER_TOOL_NAME
from agent_learning_coach.core.llm.types import ToolResult
from agent_learning_coach.core.tools import ToolDispatcher, ToolRegistry
from support import ScriptedClient, tool_call_response


class StudyStep(BaseModel):
    """学习步骤。"""

    title: str = Field(description="步骤标题")
    minutes: int = Field(description="预计分钟数")


class LearningPlan(BaseModel):
    """学习计划。"""

    topic: str = Field(description="学习主题")
    steps: list[StudyStep] = Field(description="学习步骤")


class SimpleAnswer(BaseModel):
    """没有嵌套模型的简单答案。"""

    answer: str = Field(description="答案")


def build_registry() -> ToolRegistry:
    """构造一个普通工具注册表。"""
    return ToolRegistry()


def final_answer_call(payload: dict[str, Any], call_id: str = "c1") -> Any:
    """构造一个调用 ``final_answer`` 的模型响应。"""
    return tool_call_response(
        FINAL_ANSWER_TOOL_NAME,
        {"output": payload},
        call_id=call_id,
    )


# --------------------------------------------------------------------------
# Schema 构造
# --------------------------------------------------------------------------


def test_output_schema_wraps_model_schema() -> None:
    """目标模型的 schema 被包在 ``output`` 字段下。"""
    schema = build_output_schema(LearningPlan)

    assert schema["type"] == "object"
    assert schema["required"] == ["output"]
    assert "topic" in schema["properties"]["output"]["properties"]


def test_output_schema_promotes_defs_for_nested_models() -> None:
    """嵌套模型的 ``$defs`` 必须提升到外层，否则 ``$ref`` 解析失败。"""
    schema = build_output_schema(LearningPlan)

    # $ref 在模型 schema 内是相对根解析的，因此 $defs 必须在根
    assert "$defs" in schema
    assert "$defs" not in schema["properties"]["output"]
    ref = schema["properties"]["output"]["properties"]["steps"]["items"]["$ref"]
    assert ref.startswith("#/$defs/")
    assert ref.removeprefix("#/$defs/") in schema["$defs"]


# --------------------------------------------------------------------------
# 运行时的结构化输出
# --------------------------------------------------------------------------


def test_final_answer_produces_validated_instance() -> None:
    """模型调用 ``final_answer`` 后，输出是已校验的模型实例。"""
    client = ScriptedClient(
        [
            final_answer_call(
                {
                    "topic": "闭包",
                    "steps": [{"title": "理解词法作用域", "minutes": 20}],
                }
            )
        ]
    )
    registry = build_registry()
    runtime = AgentRuntime(
        client,
        ToolDispatcher(registry),
        config=AgentConfig(output_model=LearningPlan),
    )

    run = asyncio.run(runtime.run("安排闭包的学习计划"))

    assert run.stop_reason == "completed"
    assert isinstance(run.output, LearningPlan)
    assert run.output.topic == "闭包"
    assert run.output.steps[0].minutes == 20


def test_final_answer_tool_is_offered_to_the_model() -> None:
    """``final_answer`` 必须出现在给模型的工具清单里。"""
    client = ScriptedClient([final_answer_call({"topic": "x", "steps": []})])
    runtime = AgentRuntime(
        client,
        ToolDispatcher(build_registry()),
        config=AgentConfig(output_model=LearningPlan),
    )

    asyncio.run(runtime.run("测试"))

    names = {spec.name for spec in client.requests[0].tools}
    assert FINAL_ANSWER_TOOL_NAME in names


def test_invalid_final_answer_is_fed_back_and_loop_continues() -> None:
    """结构化输出校验失败时，错误回填给模型让它纠正——运行不失败。"""
    client = ScriptedClient(
        [
            final_answer_call({"topic": "闭包"}),  # 缺 steps
            final_answer_call(
                {"topic": "闭包", "steps": [{"title": "复习", "minutes": 15}]},
                call_id="c2",
            ),
        ]
    )
    runtime = AgentRuntime(
        client,
        ToolDispatcher(build_registry()),
        config=AgentConfig(output_model=LearningPlan),
    )

    run = asyncio.run(runtime.run("测试"))

    assert run.stop_reason == "completed"
    assert isinstance(run.output, LearningPlan)
    assert run.output.steps[0].title == "复习"

    # 第一次失败被回填，模型看到了错误
    assert len(client.requests) == 2
    second_request = client.requests[1]
    assert any(
        isinstance(item, ToolResult) and item.status == "error" for item in second_request.contents
    )


def test_caller_registry_is_not_polluted() -> None:
    """组合出的注册表是新的——调用方的注册表不能被塞进工具。"""
    registry = build_registry()
    runtime = AgentRuntime(
        ScriptedClient([final_answer_call({"topic": "x", "steps": []})]),
        ToolDispatcher(registry),
        config=AgentConfig(output_model=LearningPlan),
    )

    asyncio.run(runtime.run("测试"))

    assert registry.names() == []
    assert FINAL_ANSWER_TOOL_NAME in runtime.dispatcher.registry.names()


def test_without_output_model_no_final_answer_tool() -> None:
    """没设 ``output_model`` 时不补工具——纯文本回答是默认路径。"""
    client = ScriptedClient([])
    registry = build_registry()
    runtime = AgentRuntime(client, ToolDispatcher(registry))

    assert FINAL_ANSWER_TOOL_NAME not in runtime.dispatcher.registry.names()


def test_plain_text_output_stays_text() -> None:
    """未设置 ``output_model`` 时，输出仍是文本。"""
    from support import text_response

    client = ScriptedClient([text_response("纯文本回答")])
    runtime = AgentRuntime(client, ToolDispatcher(build_registry()))

    run = asyncio.run(runtime.run("测试"))

    assert run.output == "纯文本回答"
    assert isinstance(run.output, str)


def test_flat_model_schema_has_no_defs() -> None:
    """没有嵌套模型时不需要 $defs。"""
    schema = build_output_schema(SimpleAnswer)

    assert "$defs" not in schema
    assert "answer" in schema["properties"]["output"]["properties"]


def test_existing_tools_are_kept_when_composing() -> None:
    """组合注册表时，调用方原有的工具必须保留。"""
    from agent_learning_coach.core.tools import (
        FunctionTool,
        ToolContext,
        ToolDefinition,
    )

    async def _noop(context: ToolContext, arguments: dict[str, Any]) -> str:
        return "ok"

    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            definition=ToolDefinition(name="noop", description="空操作"),
            func=_noop,
        )
    )
    runtime = AgentRuntime(
        ScriptedClient([final_answer_call({"topic": "x", "steps": []})]),
        ToolDispatcher(registry),
        config=AgentConfig(output_model=LearningPlan),
    )

    run = asyncio.run(runtime.run("测试"))

    assert set(runtime.dispatcher.registry.names()) == {"noop", FINAL_ANSWER_TOOL_NAME}
    assert run.stop_reason == "completed"
