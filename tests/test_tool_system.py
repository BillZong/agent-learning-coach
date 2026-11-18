"""Tests for the tool system.

覆盖八个组成部分：Tool Definition、Tool Context、Tool Result、
Tool Registry、Tool Dispatcher、Schema Validation、Tool Timeout、
Tool Result Normalization。

打桩一律通过公开注入点（``FunctionTool`` 包裹替身函数），
不 monkeypatch 第三方模块，也不访问被测对象的私有字段。
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from typing import Any

import pytest
from pydantic import BaseModel

from agent_learning_coach.core.llm.models import LLMRequest
from agent_learning_coach.core.tools import (
    FunctionTool,
    Tool,
    ToolContext,
    ToolDefinition,
    ToolDispatcher,
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolOutcome,
    ToolRegistry,
    ToolTimeoutError,
    ToolValidationError,
    describe_schema,
    normalize_content,
    normalize_data,
    normalize_error,
    normalize_outcome,
    validate_arguments,
)

# --------------------------------------------------------------------------
# 测试替身
# --------------------------------------------------------------------------


class _EchoTool:
    """回显参数的工具替身。"""

    def __init__(self, name: str = "echo", **definition_kwargs: Any) -> None:
        self.definition = ToolDefinition(
            name=name,
            description="回显传入的参数",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            **definition_kwargs,
        )
        self.calls: list[dict[str, Any]] = []

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> Any:
        self.calls.append(arguments)
        return arguments.get("text", "")


class _DictTool:
    """返回 dict 的工具替身。"""

    def __init__(self) -> None:
        self.definition = ToolDefinition(name="lookup", description="返回字典")

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> Any:
        return {"query": arguments.get("query"), "hits": 3}


class _ModelTool:
    """返回 Pydantic 模型的工具替身。"""

    class Answer(BaseModel):
        value: int
        unit: str

    def __init__(self) -> None:
        self.definition = ToolDefinition(name="answer", description="返回模型")

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> Any:
        return self.Answer(value=2, unit="个")


class _BoomTool:
    """抛出异常的工具替身。"""

    def __init__(self, exc: BaseException | None = None) -> None:
        self.definition = ToolDefinition(name="boom", description="总是失败")
        self.exc = exc or RuntimeError("boom")

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> Any:
        raise self.exc


class _SlowTool:
    """耗时的工具替身，用于超时测试。"""

    def __init__(self, delay: float = 5.0) -> None:
        self.definition = ToolDefinition(name="slow", description="很慢")
        self.delay = delay
        self.cancelled = False

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> Any:
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return "done"


class _ContextTool:
    """回显上下文与截止时间的工具替身。"""

    def __init__(self) -> None:
        self.definition = ToolDefinition(name="ctx", description="回显上下文")
        self.seen: ToolContext | None = None

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> Any:
        self.seen = context
        return {"session_id": context.session_id}


# --------------------------------------------------------------------------
# Tool Definition
# --------------------------------------------------------------------------


def test_definition_converts_to_spec() -> None:
    definition = ToolDefinition(
        name="search",
        description="检索资料",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
    )

    spec = definition.to_spec()

    assert spec.name == "search"
    assert spec.description == "检索资料"
    assert spec.parameters == definition.parameters


def test_definition_defaults() -> None:
    definition = ToolDefinition(name="bare")

    assert definition.description == ""
    assert definition.parameters == {}
    assert definition.timeout is None
    assert definition.metadata == {}


def test_definition_is_serializable() -> None:
    """定义要能原样发给模型，因此必须可 JSON 序列化。"""
    import json

    definition = ToolDefinition(
        name="search",
        description="检索资料",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
        timeout=30.0,
    )

    payload = json.loads(definition.model_dump_json())

    assert payload["name"] == "search"
    assert payload["timeout"] == 30.0


def test_function_tool_satisfies_protocol() -> None:
    async def _handler(context: ToolContext, arguments: dict[str, Any]) -> Any:
        return "ok"

    tool = FunctionTool(
        definition=ToolDefinition(name="fn"),
        func=_handler,
    )

    assert isinstance(tool, Tool)
    assert isinstance(_EchoTool(), Tool)


# --------------------------------------------------------------------------
# Tool Context
# --------------------------------------------------------------------------


def test_context_is_immutable() -> None:
    context = ToolContext(session_id="s-1")

    with pytest.raises(dataclasses.FrozenInstanceError):
        context.session_id = "s-2"  # type: ignore[misc]


def test_context_remaining_seconds_without_deadline() -> None:
    assert ToolContext().remaining_seconds() is None


def test_context_remaining_seconds_counts_down() -> None:
    context = ToolContext(deadline=time.monotonic() + 10.0)

    remaining = context.remaining_seconds()

    assert remaining is not None
    assert 0 < remaining <= 10.0


def test_context_remaining_seconds_negative_when_expired() -> None:
    context = ToolContext(deadline=time.monotonic() - 5.0)

    remaining = context.remaining_seconds()

    assert remaining is not None
    assert remaining < 0


# --------------------------------------------------------------------------
# Tool Result
# --------------------------------------------------------------------------


def test_outcome_success_flag() -> None:
    assert ToolOutcome(tool_name="t", status="success").succeeded is True
    assert ToolOutcome(tool_name="t", status="error").succeeded is False


def test_outcome_converts_to_message_result() -> None:
    outcome = ToolOutcome(
        tool_name="search",
        status="success",
        content="找到 3 条结果",
    )

    message_result = outcome.to_message_result("call-1")

    assert message_result.tool_call_id == "call-1"
    assert message_result.name == "search"
    assert message_result.status == "success"
    assert message_result.content == "找到 3 条结果"


def test_outcome_error_converts_to_message_result() -> None:
    outcome = ToolOutcome(tool_name="search", status="error", content="失败", error="boom")

    message_result = outcome.to_message_result("call-1")

    assert message_result.status == "error"


# --------------------------------------------------------------------------
# Tool Result Normalization
# --------------------------------------------------------------------------


def test_normalize_content_from_text() -> None:
    assert normalize_content("hello") == "hello"
    assert normalize_content(42) == "42"
    assert normalize_content(True) == "true"


def test_normalize_content_from_none() -> None:
    assert normalize_content(None) == ""


def test_normalize_content_from_dict() -> None:
    assert normalize_content({"a": 1}) == '{"a": 1}'


def test_normalize_content_from_list() -> None:
    assert normalize_content([1, 2]) == "[1, 2]"


def test_normalize_content_from_model() -> None:
    class Sample(BaseModel):
        name: str

    assert normalize_content(Sample(name="x")) == '{"name": "x"}'


def test_normalize_content_from_unserializable_falls_back() -> None:
    """不可序列化的对象必须降级为 repr，而不是抛异常。"""

    class Weird:
        def __repr__(self) -> str:
            return "<weird>"

    assert normalize_content(Weird()) == "<weird>"


def test_normalize_content_never_raises() -> None:
    class Hostile:
        def __repr__(self) -> str:
            raise RuntimeError("repr failed")

    result = normalize_content(Hostile())

    assert isinstance(result, str)


def test_normalize_content_handles_circular_structure() -> None:
    """循环引用的结构必须降级，不能抛异常。"""
    payload: dict[str, Any] = {"a": 1}
    payload["self"] = payload

    result = normalize_content(payload)

    assert isinstance(result, str)


def test_normalize_content_falls_back_for_unserializable_member() -> None:
    """结构体中含不可序列化成员时降级为 repr，不整体失败。"""

    class Weird:
        def __repr__(self) -> str:
            return "<weird>"

    result = normalize_content({"item": Weird()})

    assert result == '{"item": "<weird>"}'


def test_normalize_data_from_dict() -> None:
    assert normalize_data({"a": 1}) == {"a": 1}


def test_normalize_data_from_model() -> None:
    class Sample(BaseModel):
        name: str

    assert normalize_data(Sample(name="x")) == {"name": "x"}


def test_normalize_data_from_non_dict() -> None:
    assert normalize_data([1, 2]) is None
    assert normalize_data("text") is None
    assert normalize_data(None) is None


def test_normalize_outcome_success() -> None:
    outcome = normalize_outcome("search", {"hits": 3}, duration_ms=12.0)

    assert outcome.tool_name == "search"
    assert outcome.status == "success"
    assert outcome.content == '{"hits": 3}'
    assert outcome.data == {"hits": 3}
    assert outcome.duration_ms == 12.0
    assert outcome.error is None


def test_normalize_error_outcome() -> None:
    outcome = normalize_error("search", ValueError("bad"), duration_ms=1.0)

    assert outcome.status == "error"
    assert outcome.succeeded is False
    assert outcome.error == "ValueError: bad"
    assert "bad" in outcome.content


# --------------------------------------------------------------------------
# Schema Validation
# --------------------------------------------------------------------------


def test_validate_arguments_accepts_valid() -> None:
    schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    validate_arguments(schema, {"text": "hi"})


def test_validate_arguments_empty_schema_accepts_all() -> None:
    validate_arguments({}, {"anything": 1})


def test_validate_arguments_rejects_missing_required() -> None:
    schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    with pytest.raises(ToolValidationError) as exc_info:
        validate_arguments(schema, {})

    assert exc_info.value.errors
    assert "text" in str(exc_info.value)


def test_validate_arguments_rejects_wrong_type() -> None:
    schema = {"type": "object", "properties": {"text": {"type": "string"}}}

    with pytest.raises(ToolValidationError):
        validate_arguments(schema, {"text": 123})


def test_validate_arguments_rejects_extra_property() -> None:
    schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "additionalProperties": False,
    }

    with pytest.raises(ToolValidationError):
        validate_arguments(schema, {"text": "hi", "extra": 1})


def test_validate_arguments_reports_multiple_errors() -> None:
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
    }

    with pytest.raises(ToolValidationError) as exc_info:
        validate_arguments(schema, {"a": 1, "b": "x"})

    assert len(exc_info.value.errors) == 2


def test_describe_schema() -> None:
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["query"],
    }

    text = describe_schema(schema)

    assert "query: string (必填)" in text
    assert "limit: integer" in text


def test_describe_schema_empty() -> None:
    assert describe_schema({}) == "<无参数>"


def test_describe_schema_without_properties() -> None:
    assert describe_schema({"type": "object"}) == "<无参数>"


def test_describe_schema_skips_malformed_property_spec() -> None:
    """非法的 property 定义必须被跳过，不能让描述函数崩溃。"""
    schema = {"type": "object", "properties": {"broken": "not-a-schema", "ok": {"type": "string"}}}

    text = describe_schema(schema)

    assert "ok: string" in text
    assert "broken" not in text


# --------------------------------------------------------------------------
# Tool Registry
# --------------------------------------------------------------------------


def test_registry_register_and_get() -> None:
    registry = ToolRegistry()
    tool = _EchoTool()

    definition = registry.register(tool)

    assert definition.name == "echo"
    assert registry.get("echo") is tool
    assert registry.has("echo") is True
    assert len(registry) == 1


def test_registry_rejects_duplicate_name() -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool())

    with pytest.raises(ValueError, match="已注册"):
        registry.register(_EchoTool())


def test_registry_overwrite_allowed() -> None:
    registry = ToolRegistry()
    first = _EchoTool()
    second = _EchoTool()
    registry.register(first)

    registry.register(second, overwrite=True)

    assert registry.get("echo") is second


def test_registry_unregister() -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool())

    removed = registry.unregister("echo")

    assert removed.definition.name == "echo"
    assert registry.has("echo") is False


def test_registry_unregister_unknown() -> None:
    with pytest.raises(KeyError):
        ToolRegistry().unregister("nope")


def test_registry_get_unknown_raises() -> None:
    with pytest.raises(ToolNotFoundError) as exc_info:
        ToolRegistry().get("nope")

    assert "nope" in str(exc_info.value)


def test_registry_lists_names_sorted() -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool(name="zeta"))
    registry.register(_DictTool())

    assert registry.names() == ["lookup", "zeta"]


def test_registry_exports_specs() -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool())

    specs = registry.specs()

    assert len(specs) == 1
    assert specs[0].name == "echo"
    assert specs[0].description == "回显传入的参数"
    assert specs[0].parameters["required"] == ["text"]


def test_registry_definitions() -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool())

    definitions = registry.definitions()

    assert len(definitions) == 1
    assert definitions[0].name == "echo"


def test_registry_contains_protocol() -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool())

    assert "echo" in registry
    assert "nope" not in registry


# --------------------------------------------------------------------------
# Tool Dispatcher
# --------------------------------------------------------------------------


def test_dispatcher_executes_tool() -> None:
    registry = ToolRegistry()
    tool = _EchoTool()
    registry.register(tool)
    dispatcher = ToolDispatcher(registry)

    outcome = asyncio.run(dispatcher.dispatch("echo", {"text": "hi"}, ToolContext()))

    assert outcome.status == "success"
    assert outcome.content == "hi"
    assert outcome.succeeded is True
    assert tool.calls == [{"text": "hi"}]


def test_dispatcher_passes_context_to_tool() -> None:
    registry = ToolRegistry()
    tool = _ContextTool()
    registry.register(tool)
    dispatcher = ToolDispatcher(registry)
    context = ToolContext(session_id="s-42", caller="agent_loop")

    asyncio.run(dispatcher.dispatch("ctx", {}, context))

    assert tool.seen is not None
    assert tool.seen.session_id == "s-42"
    assert tool.seen.caller == "agent_loop"


def test_dispatcher_normalizes_dict_result() -> None:
    registry = ToolRegistry()
    registry.register(_DictTool())
    dispatcher = ToolDispatcher(registry)

    outcome = asyncio.run(dispatcher.dispatch("lookup", {"query": "x"}, ToolContext()))

    assert outcome.content == '{"query": "x", "hits": 3}'
    assert outcome.data == {"query": "x", "hits": 3}


def test_dispatcher_normalizes_model_result() -> None:
    registry = ToolRegistry()
    registry.register(_ModelTool())
    dispatcher = ToolDispatcher(registry)

    outcome = asyncio.run(dispatcher.dispatch("answer", {}, ToolContext()))

    assert outcome.content == '{"value": 2, "unit": "个"}'


def test_dispatcher_returns_error_result_for_unknown_tool() -> None:
    dispatcher = ToolDispatcher(ToolRegistry())

    with pytest.raises(ToolNotFoundError):
        asyncio.run(dispatcher.dispatch("nope", {}, ToolContext()))


def test_dispatcher_safe_returns_error_result_for_unknown_tool() -> None:
    dispatcher = ToolDispatcher(ToolRegistry())

    outcome = asyncio.run(dispatcher.dispatch_safe("nope", {}, ToolContext()))

    assert outcome.status == "error"
    assert "nope" in outcome.content


def test_dispatcher_returns_error_result_for_invalid_arguments() -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool())
    dispatcher = ToolDispatcher(registry)

    outcome = asyncio.run(dispatcher.dispatch("echo", {"wrong": 1}, ToolContext()))

    assert outcome.status == "error"
    assert outcome.error is not None
    assert "ToolValidationError" in outcome.error


def test_dispatcher_does_not_execute_tool_on_invalid_arguments() -> None:
    registry = ToolRegistry()
    tool = _EchoTool()
    registry.register(tool)
    dispatcher = ToolDispatcher(registry)

    asyncio.run(dispatcher.dispatch("echo", {"wrong": 1}, ToolContext()))

    assert tool.calls == []


def test_dispatcher_returns_error_result_for_raising_tool() -> None:
    registry = ToolRegistry()
    registry.register(_BoomTool())
    dispatcher = ToolDispatcher(registry)

    outcome = asyncio.run(dispatcher.dispatch("boom", {}, ToolContext()))

    assert outcome.status == "error"
    assert "boom" in outcome.content


def test_dispatcher_wraps_unexpected_exception() -> None:
    registry = ToolRegistry()
    registry.register(_BoomTool(ValueError("bad input")))
    dispatcher = ToolDispatcher(registry)

    outcome = asyncio.run(dispatcher.dispatch("boom", {}, ToolContext()))

    assert outcome.status == "error"
    assert "bad input" in outcome.content


def test_dispatcher_records_duration() -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool())
    dispatcher = ToolDispatcher(registry)

    outcome = asyncio.run(dispatcher.dispatch("echo", {"text": "hi"}, ToolContext()))

    assert outcome.duration_ms >= 0.0


def test_dispatcher_dispatch_all_preserves_order() -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool())
    dispatcher = ToolDispatcher(registry)

    outcomes = asyncio.run(
        dispatcher.dispatch_all(
            [("echo", {"text": "a"}), ("nope", {}), ("echo", {"text": "b"})],
            ToolContext(),
        )
    )

    assert len(outcomes) == 3
    assert outcomes[0].content == "a"
    assert outcomes[1].status == "error"
    assert outcomes[2].content == "b"


def test_dispatcher_supports_function_tool() -> None:
    async def _handler(context: ToolContext, arguments: dict[str, Any]) -> Any:
        return f"got {arguments.get('x')}"

    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="fn",
                parameters={
                    "type": "object",
                    "properties": {"x": {"type": "string"}},
                    "required": ["x"],
                },
            ),
            func=_handler,
        )
    )
    dispatcher = ToolDispatcher(registry)

    outcome = asyncio.run(dispatcher.dispatch("fn", {"x": "1"}, ToolContext()))

    assert outcome.content == "got 1"


# --------------------------------------------------------------------------
# Tool Timeout
# --------------------------------------------------------------------------


def test_resolve_timeout_prefers_tool_definition() -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool(timeout=5.0))
    dispatcher = ToolDispatcher(registry, default_timeout=30.0)

    assert dispatcher.resolve_timeout("echo") == 5.0


def test_resolve_timeout_falls_back_to_default() -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool())
    dispatcher = ToolDispatcher(registry, default_timeout=30.0)

    assert dispatcher.resolve_timeout("echo") == 30.0


def test_resolve_timeout_none_when_unset() -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool())
    dispatcher = ToolDispatcher(registry)

    assert dispatcher.resolve_timeout("echo") is None


def test_dispatcher_times_out_slow_tool() -> None:
    registry = ToolRegistry()
    registry.register(_SlowTool(delay=5.0))
    dispatcher = ToolDispatcher(registry, default_timeout=0.05)

    outcome = asyncio.run(dispatcher.dispatch("slow", {}, ToolContext()))

    assert outcome.status == "error"
    assert outcome.error is not None
    assert "ToolTimeoutError" in outcome.error


def test_dispatcher_cancels_timed_out_tool() -> None:
    """超时必须真正取消工具协程，而不是仅仅放弃等待。"""
    registry = ToolRegistry()
    tool = _SlowTool(delay=5.0)
    registry.register(tool)
    dispatcher = ToolDispatcher(registry, default_timeout=0.05)

    asyncio.run(dispatcher.dispatch("slow", {}, ToolContext()))

    assert tool.cancelled is True


def test_dispatcher_timeout_from_tool_definition() -> None:
    registry = ToolRegistry()
    registry.register(_SlowTool(delay=5.0))
    tool = registry.get("slow")
    assert tool.definition.timeout is None

    registry.unregister("slow")
    registry.register(_SlowToolWithTimeout(delay=5.0, timeout=0.05))
    dispatcher = ToolDispatcher(registry, default_timeout=30.0)

    outcome = asyncio.run(dispatcher.dispatch("slow", {}, ToolContext()))

    assert outcome.status == "error"
    assert "ToolTimeoutError" in (outcome.error or "")


def test_dispatcher_no_timeout_runs_to_completion() -> None:
    registry = ToolRegistry()
    registry.register(_SlowTool(delay=0.01))
    dispatcher = ToolDispatcher(registry)

    outcome = asyncio.run(dispatcher.dispatch("slow", {}, ToolContext()))

    assert outcome.status == "success"
    assert outcome.content == "done"


class _SlowToolWithTimeout(_SlowTool):
    def __init__(self, delay: float = 5.0, timeout: float | None = None) -> None:
        super().__init__(delay=delay)
        self.definition = ToolDefinition(name="slow", description="很慢", timeout=timeout)


# --------------------------------------------------------------------------
# 端到端：注册表 -> 分发器 -> 回填模型
# --------------------------------------------------------------------------


def test_end_to_end_registry_to_message_result() -> None:
    from agent_learning_coach.core.llm.types import ToolCall

    registry = ToolRegistry()
    registry.register(_EchoTool())
    dispatcher = ToolDispatcher(registry)

    call = ToolCall(tool_call_id="call-1", name="echo", arguments={"text": "你好"})
    outcome = asyncio.run(dispatcher.dispatch(call.name, call.arguments, ToolContext()))
    message_result = outcome.to_message_result(call.tool_call_id)

    assert message_result.tool_call_id == "call-1"
    assert message_result.name == "echo"
    assert message_result.status == "success"
    assert message_result.content == "你好"


def test_end_to_end_specs_match_validation() -> None:
    """发给模型的 schema 与实际校验的 schema 必须是同一份。"""
    registry = ToolRegistry()
    registry.register(_EchoTool())
    dispatcher = ToolDispatcher(registry)

    spec = registry.specs()[0]

    # 模型看到的 schema 直接可用于校验
    outcome = asyncio.run(
        dispatcher.dispatch("echo", {"text": "x"}, ToolContext()),
    )
    assert outcome.status == "success"

    with pytest.raises(ToolValidationError):
        validate_arguments(spec.parameters, {})


def test_tool_errors_share_base_class() -> None:
    error_types = (
        ToolNotFoundError,
        ToolValidationError,
        ToolTimeoutError,
        ToolExecutionError,
    )
    for error_type in error_types:
        assert issubclass(error_type, ToolError)


def test_tool_call_and_result_are_paired_in_messages() -> None:
    """回填工具结果时必须同时带上原始 ToolCall。

    厂商要求 tool result 消息与 assistant 的 tool_calls 消息成对出现，
    否则返回 "tool result's tool id not found"。这条契约由本包保证：
    ToolOutcome 只负责结果，原始 ToolCall 由调用方保留并一起回填。
    """
    from agent_learning_coach.core.llm.messages import build_messages
    from agent_learning_coach.core.llm.types import ToolCall

    registry = ToolRegistry()
    registry.register(_EchoTool())
    dispatcher = ToolDispatcher(registry)

    call = ToolCall(tool_call_id="call-1", name="echo", arguments={"text": "hi"})
    outcome = asyncio.run(dispatcher.dispatch(call.name, call.arguments, ToolContext()))
    result = outcome.to_message_result(call.tool_call_id)

    messages = build_messages(LLMRequest(contents=[call, result]))

    assert messages[0]["role"] == "assistant"
    assert messages[0]["tool_calls"][0]["id"] == "call-1"
    assert messages[1]["role"] == "tool"
    assert messages[1]["tool_call_id"] == "call-1"
