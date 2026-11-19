"""上下文工程的测试。

覆盖三层：

**Token 计数**——计数是估算，但必须**单调**且**可用**：内容越多 token 越多；
拿不到分词器时回退到字符估算而不是抛异常。

**裁剪策略**——Sliding Window 处理条数、Compaction 处理体积，两者都必须
保持「每条 tool_call 都有对应的 tool_result」这条不变量。这是本项目对参考
实现的一处修正：参考实现的滑窗直接切列表，会切断配对。

**Context Builder**——预算是**被执行**的，不是建议。以及存储与呈现分离：
裁剪只影响发给模型的内容，历史本身不受影响。
"""

from __future__ import annotations

import asyncio

import pytest

from agent_learning_coach.core.agent import AgentConfig, AgentRuntime
from agent_learning_coach.core.context import (
    Compaction,
    CompactionRules,
    ContextBuilder,
    SlidingWindow,
    TokenCounter,
    drop_unpaired,
    estimate_tokens,
)
from agent_learning_coach.core.llm.types import Message, ToolCall, ToolResult
from support import ScriptedClient, text_response


def _conversation(turns: int = 4) -> list:
    """构造一段普通的多轮对话。"""
    items: list = [Message(role="user", content="帮我学闭包")]
    for index in range(turns):
        items.append(Message(role="assistant", content=f"第 {index} 轮的回答"))
        items.append(Message(role="user", content=f"第 {index} 轮的追问"))
    return items


def _with_tool_exchange() -> list:
    """构造一段含工具调用的对话。"""
    return [
        Message(role="user", content="读一下笔记"),
        ToolCall(tool_call_id="c1", name="read_file", arguments={"path": "a.md"}),
        ToolResult(tool_call_id="c1", name="read_file", content="笔记内容"),
        Message(role="assistant", content="笔记里讲的是闭包"),
    ]


def _call_ids(items: list) -> tuple[set[str], set[str]]:
    """取出调用 id 与结果 id。"""
    calls = {item.tool_call_id for item in items if isinstance(item, ToolCall)}
    results = {item.tool_call_id for item in items if isinstance(item, ToolResult)}
    return calls, results


# ---------------------------------------------------------------------------
# Token 计数
# ---------------------------------------------------------------------------


def test_token_count_is_positive_for_content() -> None:
    """有内容的请求 token 数大于零。"""
    counter = TokenCounter("gpt-4")

    assert counter.count([Message(role="user", content="你好")]) > 0


def test_token_count_grows_with_content() -> None:
    """内容越多 token 越多——计数必须单调，否则预算没有意义。"""
    counter = TokenCounter("gpt-4")
    short = counter.count([Message(role="user", content="短")])
    long = counter.count([Message(role="user", content="短" * 100)])

    assert long > short


def test_tool_definitions_are_counted() -> None:
    """工具定义也占 token——它们每次请求都要发。"""
    from agent_learning_coach.core.llm.models import ToolSpec

    counter = TokenCounter("gpt-4")
    contents = [Message(role="user", content="你好")]
    tools = [
        ToolSpec(
            name="search",
            description="搜索资料",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
        )
    ]

    without = counter.count(contents)
    with_tools = counter.count(contents, tools=tools)

    assert with_tools > without


def test_estimate_tokens_handles_chinese() -> None:
    """中文按字符估算时，每个汉字算一个 token 量级。"""
    assert estimate_tokens("") == 0
    assert estimate_tokens("闭包") >= 2
    assert estimate_tokens("a" * 100) >= 25


def test_counter_works_without_tiktoken(monkeypatch: pytest.MonkeyPatch) -> None:
    """拿不到分词器时回退到估算，而不是抛异常。

    ``tiktoken`` 首次使用要联网下载 BPE 文件，离线环境（CI 容器）会失败。
    预算功能不能因此整体不可用。
    """
    import tiktoken

    import agent_learning_coach.core.context.tokens as tokens_module

    monkeypatch.setattr(tokens_module, "_ENCODINGS", {})
    monkeypatch.setattr(tokens_module, "_FAILED", set())

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("离线")

    # patch tiktoken 本身而不是本模块的加载函数——这样走的是真实的
    # 「捕获异常 -> 记入 _FAILED -> 回退估算」路径。
    monkeypatch.setattr(tiktoken, "get_encoding", _boom)
    monkeypatch.setattr(tiktoken, "encoding_for_model", _boom)

    counter = tokens_module.TokenCounter("some-unknown-model")

    assert counter.exact is False
    assert counter.count([Message(role="user", content="你好世界")]) > 0


# ---------------------------------------------------------------------------
# 配对不变量
# ---------------------------------------------------------------------------


def test_drop_unpaired_removes_orphan_call() -> None:
    """孤立的 tool_call 被丢弃——厂商 API 不接受没有结果的调用。"""
    contents = [
        ToolCall(tool_call_id="c1", name="read_file", arguments={}),
        Message(role="assistant", content="我读一下"),
    ]

    kept = drop_unpaired(contents)

    assert not any(isinstance(item, ToolCall) for item in kept)


def test_drop_unpaired_removes_orphan_result() -> None:
    """孤立的 tool_result 被丢弃。"""
    contents = [ToolResult(tool_call_id="c1", name="read_file", content="内容")]

    assert drop_unpaired(contents) == []


def test_drop_unpaired_keeps_complete_pairs() -> None:
    """配对完整的原样保留。"""
    contents = _with_tool_exchange()

    assert len(drop_unpaired(contents)) == len(contents)


# ---------------------------------------------------------------------------
# Sliding Window
# ---------------------------------------------------------------------------


def test_sliding_window_keeps_recent_items() -> None:
    """窗口只保留最近的若干条。"""
    contents = _conversation(turns=10)

    kept = SlidingWindow(keep_recent=4).apply(contents)

    assert len(kept) < len(contents)
    assert kept[-1] == contents[-1]


def test_sliding_window_keeps_leading_user_message() -> None:
    """最早的用户消息（原始诉求）被保留。"""
    contents = _conversation(turns=10)

    kept = SlidingWindow(keep_recent=4).apply(contents)

    assert isinstance(kept[0], Message)
    assert kept[0].role == "user"
    assert kept[0].content == "帮我学闭包"


def test_sliding_window_can_drop_leading_user_message() -> None:
    """可以关掉「保留原始诉求」的行为。"""
    contents = _conversation(turns=10)

    kept = SlidingWindow(keep_recent=4, keep_leading_user=False).apply(contents)

    assert not any(isinstance(item, Message) and item.content == "帮我学闭包" for item in kept)


def test_sliding_window_preserves_tool_pairing() -> None:
    """滑窗切断配对时，成对的调用与结果一起被保留或一起被丢弃。

    参考实现直接切列表，会留下没有结果的 tool_call——厂商 API 会拒绝整个
    请求。本项目在这里修正。
    """
    contents = [
        Message(role="user", content="读一下"),
        ToolCall(tool_call_id="c1", name="read_file", arguments={}),
        ToolResult(tool_call_id="c1", name="read_file", content="内容"),
        Message(role="assistant", content="读完了"),
        ToolCall(tool_call_id="c2", name="search", arguments={}),
        ToolResult(tool_call_id="c2", name="search", content="结果"),
    ]

    for keep in range(1, len(contents) + 1):
        kept = SlidingWindow(keep_recent=keep).apply(contents)
        calls, results = _call_ids(kept)
        assert calls == results, f"keep_recent={keep} 时配对断裂"


def test_sliding_window_is_a_noop_when_short_enough() -> None:
    """内容不足窗口大小时原样返回。"""
    contents = _conversation(turns=2)

    assert SlidingWindow(keep_recent=100).apply(contents) == contents


def test_sliding_window_does_not_mutate_input() -> None:
    """策略不修改入参——否则存储侧的历史会被裁剪掉。"""
    contents = _conversation(turns=5)
    snapshot = list(contents)

    SlidingWindow(keep_recent=2).apply(contents)

    assert contents == snapshot


# ---------------------------------------------------------------------------
# Compaction
# ---------------------------------------------------------------------------


def test_compaction_replaces_large_result() -> None:
    """超长的工具结果被截断。"""
    contents = [
        ToolCall(tool_call_id="c1", name="read_file", arguments={}),
        ToolResult(tool_call_id="c1", name="read_file", content="内容" * 500),
    ]

    compacted = Compaction(CompactionRules(max_result_chars=50)).apply(contents)

    result = next(item for item in compacted if isinstance(item, ToolResult))
    assert len(result.content) < 100
    assert "已截断" in result.content


def test_compaction_uses_template_with_call_arguments() -> None:
    """结果模板可以用这次调用的参数做占位符。"""
    contents = [
        ToolCall(tool_call_id="c1", name="read_file", arguments={"path": "notes.md"}),
        ToolResult(tool_call_id="c1", name="read_file", content="很长的内容" * 100),
    ]
    rules = CompactionRules(result_templates={"read_file": "已读取 {path}，需要时重新读取。"})

    compacted = Compaction(rules).apply(contents)

    result = next(item for item in compacted if isinstance(item, ToolResult))
    assert result.content == "已读取 notes.md，需要时重新读取。"


def test_compaction_tolerates_missing_template_argument() -> None:
    """模板里的占位符没有对应参数时不抛异常。"""
    contents = [
        ToolCall(tool_call_id="c1", name="read_file", arguments={}),
        ToolResult(tool_call_id="c1", name="read_file", content="内容"),
    ]
    rules = CompactionRules(result_templates={"read_file": "已读取 {path}。"})

    compacted = Compaction(rules).apply(contents)

    result = next(item for item in compacted if isinstance(item, ToolResult))
    assert "unknown" in result.content


def test_compaction_replaces_large_arguments() -> None:
    """超长的调用参数被截断。"""
    contents = [
        ToolCall(tool_call_id="c1", name="write", arguments={"content": "x" * 500}),
    ]

    compacted = Compaction(CompactionRules(max_argument_chars=50)).apply(contents)

    call = next(item for item in compacted if isinstance(item, ToolCall))
    assert len(call.arguments["content"]) < 100


def test_compaction_uses_argument_placeholders() -> None:
    """按参数名替换成占位符。"""
    contents = [
        ToolCall(
            tool_call_id="c1",
            name="write",
            arguments={"content": "很长的正文", "path": "a.md"},
        ),
    ]
    rules = CompactionRules(argument_placeholders={"write": {"content": "[已保存]"}})

    compacted = Compaction(rules).apply(contents)

    call = next(item for item in compacted if isinstance(item, ToolCall))
    assert call.arguments["content"] == "[已保存]"
    assert call.arguments["path"] == "a.md"


def test_compaction_protects_configured_tools() -> None:
    """受保护的工具不参与压缩。"""
    contents = [
        ToolCall(tool_call_id="c1", name="final_answer", arguments={"output": "x" * 500}),
        ToolResult(tool_call_id="c1", name="final_answer", content="y" * 500),
    ]
    rules = CompactionRules(
        max_result_chars=10,
        max_argument_chars=10,
        protected_tools=frozenset({"final_answer"}),
    )

    compacted = Compaction(rules).apply(contents)

    assert compacted == contents


def test_compaction_preserves_pairing() -> None:
    """压缩只换内容，不增删条目，因此配对天然不变。"""
    contents = _with_tool_exchange()

    compacted = Compaction(CompactionRules(max_result_chars=1)).apply(contents)

    calls, results = _call_ids(compacted)
    assert calls == results
    assert len(compacted) == len(contents)


# ---------------------------------------------------------------------------
# Context Builder
# ---------------------------------------------------------------------------


def test_builder_returns_full_history_when_under_budget() -> None:
    """没超预算时原样返回。"""
    contents = _conversation(turns=3)

    view = ContextBuilder(max_tokens=100000).build(contents)

    assert list(view.contents) == contents
    assert view.dropped_items == 0
    assert view.applied == ()


def test_builder_without_limit_does_not_truncate() -> None:
    """不设预算就不裁剪。"""
    contents = _conversation(turns=3)

    view = ContextBuilder().build(contents)

    assert list(view.contents) == contents


def test_builder_enforces_budget_with_compaction() -> None:
    """预算被真正执行——压缩后必须装得下。"""
    contents = [
        ToolCall(tool_call_id="c1", name="read_file", arguments={"path": "a.md"}),
        ToolResult(tool_call_id="c1", name="read_file", content="内容" * 2000),
    ]

    view = ContextBuilder(
        max_tokens=100,
        strategies=(Compaction(CompactionRules(max_result_chars=20)),),
    ).build(contents)

    assert view.final_tokens <= 100
    assert view.original_tokens > view.final_tokens
    assert view.saved_tokens > 0


def test_builder_enforces_budget_with_sliding_window() -> None:
    """压缩不够时滑窗接手，最终仍然装得下。"""
    contents = _conversation(turns=40)

    view = ContextBuilder(
        max_tokens=60,
        strategies=(Compaction(), SlidingWindow(keep_recent=3)),
    ).build(contents)

    assert view.final_tokens <= 60
    assert view.dropped_items > 0
    assert "sliding_window" in view.applied


def test_builder_keeps_pairing_after_trimming() -> None:
    """兜底裁剪之后配对仍然完整。"""
    contents = [
        Message(role="user", content="读一下" * 50),
        ToolCall(tool_call_id="c1", name="read_file", arguments={}),
        ToolResult(tool_call_id="c1", name="read_file", content="内容" * 50),
    ]

    view = ContextBuilder(max_tokens=40).build(contents)

    calls, results = _call_ids(list(view.contents))
    assert calls == results


def test_builder_truncates_single_item_that_exceeds_budget() -> None:
    """单条内容就超预算时裁它的文本——预算是被执行的，不是建议。

    宁可让模型看到一段被截断的输入，也不要发出一个会被 API 拒绝的请求。
    """
    contents = [Message(role="user", content="很长的输入" * 5000)]

    view = ContextBuilder(max_tokens=50).build(contents)

    assert view.truncated is True
    assert view.final_tokens <= 50
    assert view.over_budget is False
    assert len(view.contents) == 1
    message = view.contents[0]
    assert isinstance(message, Message)
    assert "已截断" in message.content


def test_builder_reports_over_budget_when_even_empty_content_does_not_fit() -> None:
    """连空内容都装不下时如实报告。"""
    contents = [Message(role="user", content="很长的输入" * 5000)]

    view = ContextBuilder(max_tokens=1).build(contents)

    assert view.over_budget is True
    assert view.truncated is True


def test_builder_does_not_mutate_input() -> None:
    """构建过程不修改传入的历史——存储侧必须保持完整。"""
    contents = _conversation(turns=20)
    snapshot = list(contents)

    ContextBuilder(max_tokens=30, strategies=(SlidingWindow(keep_recent=2),)).build(contents)

    assert contents == snapshot


# ---------------------------------------------------------------------------
# 运行时接入
# ---------------------------------------------------------------------------


def test_runtime_applies_context_budget() -> None:
    """运行时按预算裁剪发给模型的内容，但存储侧保持完整。"""
    client = ScriptedClient([text_response("好的")])
    runtime = AgentRuntime(
        client,
        _dispatcher(),
        config=AgentConfig(
            model="gpt-4",
            max_steps=2,
            context_builder=ContextBuilder(max_tokens=20),
        ),
    )

    result = asyncio.run(runtime.run("很长的用户输入" * 500))

    assert result.status == "complete"
    view = result.context.last_context_view
    assert view is not None
    assert view.final_tokens <= 20
    # 存储侧仍然完整：用户输入原文还在，裁剪只发生在呈现层
    assert any(
        isinstance(item, Message) and "很长的用户输入" * 500 in item.content
        for item in result.context.contents
    )


def test_runtime_without_builder_keeps_full_history() -> None:
    """不配置构建器时行为不变——完整历史照发。"""
    runtime = AgentRuntime(
        ScriptedClient([text_response("好的")]),
        _dispatcher(),
        config=AgentConfig(model="gpt-4", max_steps=2),
    )

    result = asyncio.run(runtime.run("很长的用户输入" * 500))

    assert result.context.last_context_view is None


def _dispatcher() -> object:
    """构造一个空注册表的调度器。"""
    from agent_learning_coach.core.tools import ToolDispatcher, ToolRegistry

    return ToolDispatcher(ToolRegistry())


# ---------------------------------------------------------------------------
# 边界情形
# ---------------------------------------------------------------------------


def test_sliding_window_with_zero_keeps_nothing() -> None:
    """窗口为 0 时什么都不留。"""
    assert SlidingWindow(keep_recent=0).apply(_conversation(turns=3)) == []


def test_sliding_window_without_any_user_message() -> None:
    """没有用户消息时不会误判起点。"""
    contents = [
        Message(role="assistant", content="模型自说自话"),
        Message(role="assistant", content="继续"),
        Message(role="assistant", content="再说"),
    ]

    kept = SlidingWindow(keep_recent=2, keep_leading_user=True).apply(contents)

    assert len(kept) == 2
    assert kept[0].content == "继续"


def test_compaction_returns_original_when_nothing_to_do() -> None:
    """内容本来就不超限时不产生新对象。"""
    contents = [
        ToolCall(tool_call_id="c1", name="read_file", arguments={"path": "a.md"}),
        ToolResult(tool_call_id="c1", name="read_file", content="短"),
    ]

    compacted = Compaction(CompactionRules(max_result_chars=1000)).apply(contents)

    assert compacted[0] is contents[0]
    assert compacted[1] is contents[1]


def test_compaction_survives_a_broken_template() -> None:
    """模板本身写坏时不抛异常——压缩失败不能中断整个运行。"""
    contents = [
        ToolCall(tool_call_id="c1", name="read_file", arguments={"path": "a.md"}),
        ToolResult(tool_call_id="c1", name="read_file", content="内容"),
    ]
    rules = CompactionRules(result_templates={"read_file": "路径是 {path[nonexistent]}"})

    compacted = Compaction(rules).apply(contents)

    result = next(item for item in compacted if isinstance(item, ToolResult))
    assert result.content == "[结果已压缩]"


def test_compaction_without_limit_keeps_values() -> None:
    """阈值为 0 表示不限制。"""
    contents = [
        ToolCall(tool_call_id="c1", name="write", arguments={"content": "x" * 500}),
    ]

    compacted = Compaction(CompactionRules(max_argument_chars=0)).apply(contents)

    call = next(item for item in compacted if isinstance(item, ToolCall))
    assert len(call.arguments["content"]) == 500


def test_compaction_truncates_structured_arguments() -> None:
    """字典与列表参数按 JSON 序列化后截断。"""
    contents = [
        ToolCall(
            tool_call_id="c1",
            name="write",
            arguments={"meta": {"k": "v" * 500}, "items": [1, 2, 3]},
        ),
    ]

    compacted = Compaction(CompactionRules(max_argument_chars=60)).apply(contents)

    call = next(item for item in compacted if isinstance(item, ToolCall))
    assert isinstance(call.arguments["meta"], str)
    assert len(call.arguments["meta"]) < 100


def test_builder_truncates_a_remaining_tool_call_result() -> None:
    """剩下的工具结果装不下时裁它的文本——配对仍然完整。"""
    contents = [
        ToolCall(tool_call_id="c1", name="write", arguments={"content": "x" * 500}),
        ToolResult(tool_call_id="c1", name="write", content="y" * 500),
    ]

    view = ContextBuilder(max_tokens=80).build(contents)

    assert view.truncated is True
    assert view.final_tokens <= 80
    calls, results = _call_ids(list(view.contents))
    assert calls == results


def test_builder_truncates_a_remaining_tool_result() -> None:
    """超长的工具结果被裁到预算内。"""
    contents = [
        ToolCall(tool_call_id="c1", name="read_file", arguments={}),
        ToolResult(tool_call_id="c1", name="read_file", content="内容" * 500),
    ]

    view = ContextBuilder(max_tokens=40).build(contents)

    assert view.truncated is True
    assert view.final_tokens <= 40
    result = view.contents[-1]
    assert isinstance(result, ToolResult)
    assert "已截断" in result.content


def test_encoding_failure_is_remembered(monkeypatch: pytest.MonkeyPatch) -> None:
    """分词器加载失败后不再重试——避免每次调用都等一次下载超时。"""
    import tiktoken

    import agent_learning_coach.core.context.tokens as tokens_module

    monkeypatch.setattr(tokens_module, "_ENCODINGS", {})
    monkeypatch.setattr(tokens_module, "_FAILED", set())

    calls: list[str] = []

    def _boom(*args: object, **kwargs: object) -> None:
        calls.append("boom")
        raise RuntimeError("离线")

    monkeypatch.setattr(tiktoken, "get_encoding", _boom)
    monkeypatch.setattr(tiktoken, "encoding_for_model", _boom)

    first = tokens_module.TokenCounter("gpt-4")
    after_first = len(calls)
    second = tokens_module.TokenCounter("gpt-4")

    assert first.exact is False
    assert second.exact is False
    # 第一次：模型专属分词器与默认分词器各试一次；第二次不再重试。
    assert after_first == 2
    assert len(calls) == after_first


def test_compaction_keeps_non_text_arguments_untouched() -> None:
    """数字、布尔这类参数原样保留——它们本来就短。"""
    contents = [
        ToolCall(
            tool_call_id="c1",
            name="write",
            arguments={"count": 42, "flag": True, "content": "x" * 500},
        ),
    ]

    compacted = Compaction(CompactionRules(max_argument_chars=20)).apply(contents)

    call = next(item for item in compacted if isinstance(item, ToolCall))
    assert call.arguments["count"] == 42
    assert call.arguments["flag"] is True
    assert isinstance(call.arguments["content"], str)


def test_builder_reports_when_tool_definitions_alone_exceed_budget() -> None:
    """工具定义本身就超预算时如实报告——内容全部放不下。"""
    from agent_learning_coach.core.llm.models import ToolSpec

    contents = [Message(role="user", content="你好")]
    tools = [
        ToolSpec(
            name="search",
            description="很长的工具描述" * 100,
            parameters={"type": "object", "properties": {}},
        )
    ]

    view = ContextBuilder(max_tokens=10).build(contents, tools=tools)

    assert view.over_budget is True
    assert view.final_tokens > 10


def test_context_budget_example_runs() -> None:
    """上下文预算示例可运行——它是这个能力对外的可见形态。"""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "examples" / "context_budget.py")],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=root,
    )

    assert result.returncode == 0, result.stderr
    assert "预算是被执行的" in result.stdout
