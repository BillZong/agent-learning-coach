"""结构化输出：把最终答案作为一个工具产出。

做法来自主教材 4.7「Tools as output formatters」：给模型一个额外的
``final_answer`` 工具，它的参数 schema 就是目标模型的 JSON Schema。模型调用
它，即表示给出了最终答案。

为什么走工具这条通道，而不是在运行时里另开一条「结构化输出」路径：

1. **契约一致**。``final_answer`` 就是普通工具——同一套参数校验、同一套超时、
   同一套结果归一化。模型看到的工具清单里它和其它工具平级，模型不需要学新
   的交互方式。
2. **失败可自愈**。模型给出的 JSON 不符合目标模型时，校验错误被归一化成错误
   结果回填给模型，它可以改一份再交。这与「调用级失败回填」是同一条语义。
3. **不需要为结构化单独适配厂商**。厂商的 function calling 能力被
   ``core/llm`` 的适配器隔离，结构化输出因此不需要厂商专属代码。

这与 OpenAI Agents SDK 的 ``output_type`` 是同一套做法。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from agent_learning_coach.core.tools.types import FunctionTool, ToolContext, ToolDefinition

FINAL_ANSWER_TOOL_NAME = "final_answer"
"""结构化输出工具的名字。"""


def build_output_schema(output_model: type[BaseModel]) -> dict[str, Any]:
    """构造 ``final_answer`` 工具的参数 schema。

    目标模型的 schema 被包在一个 ``output`` 字段里，与模型约定「最终答案放在
    ``output`` 下」一致。

    **$defs 的处理**：Pydantic 生成的嵌套模型 schema 用 ``$ref: "#/$defs/X"``
    引用其它定义。``$ref`` 是相对**根 schema** 解析的，因此把模型 schema 包进
    另一层时，必须把 ``$defs`` 提升到外层——否则引用会解析失败，嵌套模型
    直接校验不过。

    Args:
        output_model: 目标模型。

    Returns:
        工具参数 JSON Schema。
    """
    model_schema = dict(output_model.model_json_schema())
    definitions = model_schema.pop("$defs", {})
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {"output": model_schema},
        "required": ["output"],
    }
    if definitions:
        schema["$defs"] = definitions
    return schema


def build_final_answer_tool(output_model: type[BaseModel]) -> FunctionTool:
    """构造 ``final_answer`` 工具。

    工具执行时用目标模型校验参数。校验失败会抛异常，被 Dispatcher 归一化成
    错误结果回填给模型——模型因此可以自己纠正，而不是让整次运行失败。

    Args:
        output_model: 目标模型。

    Returns:
        工具实现。
    """

    async def _final_answer(context: ToolContext, arguments: dict[str, Any]) -> Any:
        """校验并返回最终答案。"""
        return output_model.model_validate(arguments.get("output"))

    return FunctionTool(
        definition=ToolDefinition(
            name=FINAL_ANSWER_TOOL_NAME,
            description=(
                f"提交最终答案。必须调用本工具返回结果，内容需符合 {output_model.__name__} 的结构。"
            ),
            parameters=build_output_schema(output_model),
        ),
        func=_final_answer,
    )


__all__ = [
    "FINAL_ANSWER_TOOL_NAME",
    "build_final_answer_tool",
    "build_output_schema",
]
