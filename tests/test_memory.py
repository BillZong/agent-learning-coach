"""长期记忆的测试。

对应 Topic 10「Long-term Memory」，主线是**「什么值得记、什么算噪声」**：

- **准入**——空内容、低置信度、超长、未知类别的候选一律不写入。
- **去重**——同类别同内容的重复不写入。
- **覆盖**——同唯一键的新事实**取代**旧事实，而不是并存两条矛盾的。
- **召回**——按条件筛选与按相关度排序都是确定性的，不需要嵌入服务。
- **不打扰**——「没有值得记的东西」是合法结果，必须被尊重。

以及一个可观测的产物：学习者画像。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agent_learning_coach.core.llm.mock import MockLLMClient
from agent_learning_coach.core.llm.types import Message, ToolCall, ToolResult
from agent_learning_coach.core.memory import (
    ExtractedMemory,
    InMemoryMemoryStore,
    JsonFileMemoryStore,
    LearnerProfile,
    LLMMemoryExtractor,
    Memory,
    MemoryBatch,
    MemoryManager,
    MemoryPolicy,
    format_memories,
    format_transcript,
    tokenize,
)

SUBJECT = "learner-1"


def _memory(
    *,
    kind: str = "weakness",
    scope: str = "闭包",
    content: str = "闭包掌握不好",
    subject: str = SUBJECT,
    memory_type: str = "semantic",
    confidence: float = 1.0,
) -> Memory:
    """构造一条记忆。"""
    return Memory(
        id=f"m-{kind}-{scope}-{content}",
        memory_type=memory_type,  # type: ignore[arg-type]
        kind=kind,
        subject=subject,
        scope=scope,
        content=content,
        confidence=confidence,
        created_at=1.0,
        updated_at=1.0,
    )


def _batch(*items: dict) -> str:
    """把候选记忆渲染成模型会返回的 JSON。"""
    return json.dumps(
        {"memories": [ExtractedMemory(**item).model_dump() for item in items]},
        ensure_ascii=False,
    )


def _manager(text: str, *, policy: MemoryPolicy | None = None) -> tuple[MemoryManager, object]:
    """构造一个返回固定抽取结果的管理器。"""
    store = InMemoryMemoryStore()
    client = MockLLMClient(text=text)
    extractor = LLMMemoryExtractor(client)
    return MemoryManager(store, extractor, policy=policy), store


# ---------------------------------------------------------------------------
# 抽取
# ---------------------------------------------------------------------------


def test_extraction_returns_candidates() -> None:
    """抽取把模型的结构化输出变成记忆。"""
    extractor = LLMMemoryExtractor(
        MockLLMClient(
            text=_batch(
                {
                    "memory_type": "semantic",
                    "kind": "weakness",
                    "scope": "闭包",
                    "content": "闭包掌握不好",
                    "confidence": 0.9,
                }
            )
        )
    )

    memories = asyncio.run(
        extractor.extract([Message(role="user", content="闭包不会")], subject=SUBJECT)
    )

    assert len(memories) == 1
    memory = memories[0]
    assert memory.kind == "weakness"
    assert memory.scope == "闭包"
    assert memory.content == "闭包掌握不好"
    assert memory.subject == SUBJECT
    assert memory.confidence == 0.9


def test_empty_extraction_is_a_valid_result() -> None:
    """「没有值得记的东西」是合法结果，必须被尊重。"""
    extractor = LLMMemoryExtractor(MockLLMClient(text=json.dumps({"memories": []})))

    memories = asyncio.run(
        extractor.extract([Message(role="user", content="你好")], subject=SUBJECT)
    )

    assert memories == []


def test_unknown_memory_type_falls_back_to_default() -> None:
    """模型给出不认识的类型时用默认值，而不是让整批抽取失败。"""
    extractor = LLMMemoryExtractor(
        MockLLMClient(text=_batch({"memory_type": "sensory", "kind": "fact", "content": "x"})),
        default_type="semantic",
    )

    memories = asyncio.run(extractor.extract([], subject=SUBJECT))

    assert memories[0].memory_type == "semantic"


def test_confidence_is_clamped() -> None:
    """置信度被限制在 0 到 1。"""
    extractor = LLMMemoryExtractor(
        MockLLMClient(text=_batch({"kind": "fact", "content": "x", "confidence": 5.0})),
    )

    memories = asyncio.run(extractor.extract([], subject=SUBJECT))

    assert memories[0].confidence == 1.0


def test_transcript_formatting_only_keeps_readable_text() -> None:
    """转录渲染成人与模型能读的文本，工具调用只留名字。"""
    transcript = [
        Message(role="user", content="闭包不会"),
        ToolCall(tool_call_id="c1", name="read_notes", arguments={"path": "a.md"}),
        ToolResult(tool_call_id="c1", name="read_notes", content="很长的笔记内容"),
        Message(role="assistant", content="我们来看一下"),
    ]

    text = format_transcript(transcript)

    assert "[user]: 闭包不会" in text
    assert "[assistant]: 我们来看一下" in text
    assert "read_notes" in text
    # 工具结果不进转录——它是执行细节，不是学习者的事实
    assert "很长的笔记内容" not in text


# ---------------------------------------------------------------------------
# 准入：噪声的第一道闸
# ---------------------------------------------------------------------------


def test_empty_content_is_rejected() -> None:
    """空内容不写入。"""
    manager, store = _manager(_batch({"kind": "weakness", "content": "   "}))

    written = asyncio.run(manager.capture([], subject=SUBJECT))

    assert written == []
    assert asyncio.run(store.find(subject=SUBJECT)) == []


def test_low_confidence_is_rejected() -> None:
    """置信度不达标的候选不写入。"""
    manager, store = _manager(
        _batch({"kind": "weakness", "content": "可能是闭包", "confidence": 0.2})
    )

    written = asyncio.run(manager.capture([], subject=SUBJECT))

    assert written == []
    assert asyncio.run(store.find(subject=SUBJECT)) == []


def test_unknown_kind_is_rejected() -> None:
    """模型随手发明的类别不写入——否则记忆会被搅乱。"""
    manager, store = _manager(_batch({"kind": "astrology", "content": "他是双子座"}))

    written = asyncio.run(manager.capture([], subject=SUBJECT))

    assert written == []
    assert asyncio.run(store.find(subject=SUBJECT)) == []


def test_oversized_content_is_rejected() -> None:
    """超长的内容不写入。"""
    manager, store = _manager(
        _batch({"kind": "preference", "content": "偏好" * 500}),
        policy=MemoryPolicy(max_content_chars=100),
    )

    written = asyncio.run(manager.capture([], subject=SUBJECT))

    assert written == []
    assert asyncio.run(store.find(subject=SUBJECT)) == []


def test_policy_can_allow_any_kind() -> None:
    """词表可以放开——策略由调用方掌握。"""
    manager, _store = _manager(
        _batch({"kind": "astrology", "content": "他是双子座"}),
        policy=MemoryPolicy(allowed_kinds=frozenset()),
    )

    written = asyncio.run(manager.capture([], subject=SUBJECT))

    assert len(written) == 1


# ---------------------------------------------------------------------------
# 去重与覆盖：噪声的第二道闸
# ---------------------------------------------------------------------------


def test_duplicate_content_is_not_written_twice() -> None:
    """同类别同内容不重复写入。"""
    manager, store = _manager(
        _batch({"kind": "weakness", "scope": "闭包", "content": "闭包掌握不好"})
    )

    asyncio.run(manager.capture([], subject=SUBJECT))
    asyncio.run(manager.capture([], subject=SUBJECT))

    assert len(asyncio.run(store.find(subject=SUBJECT))) == 1


def test_duplicate_is_detected_across_scopes() -> None:
    """内容一样但作用域不同——仍然算重复。"""
    manager, store = _manager(
        _batch({"kind": "weakness", "scope": "闭包", "content": "闭包掌握不好"})
    )

    asyncio.run(manager.capture([], subject=SUBJECT))
    manager.extractor = LLMMemoryExtractor(
        MockLLMClient(
            text=_batch({"kind": "weakness", "scope": "闭包基础", "content": "闭包掌握不好"})
        )
    )
    asyncio.run(manager.capture([], subject=SUBJECT))

    assert len(asyncio.run(store.find(subject=SUBJECT))) == 1


def test_same_key_updates_instead_of_duplicating() -> None:
    """同唯一键的新事实取代旧事实——水平变了不会留下两条矛盾的记忆。"""
    manager, store = _manager(_batch({"kind": "level", "content": "初级"}))

    asyncio.run(manager.capture([], subject=SUBJECT))
    manager.extractor = LLMMemoryExtractor(
        MockLLMClient(text=_batch({"kind": "level", "content": "中级"}))
    )
    asyncio.run(manager.capture([], subject=SUBJECT))

    memories = asyncio.run(store.find(subject=SUBJECT))
    assert len(memories) == 1
    assert memories[0].content == "中级"


def test_update_preserves_identity_and_creation_time() -> None:
    """更新保留原 id 与创建时刻——「这条记忆被改过」而不是「多了一条」。"""
    store = InMemoryMemoryStore()
    original = _memory(content="初级", kind="level")
    stored = asyncio.run(store.add(original))

    updated = asyncio.run(store.add(_memory(content="中级", kind="level")))

    assert updated.id == stored.id
    assert updated.created_at == stored.created_at


def test_different_scopes_coexist() -> None:
    """不同作用域的同类事实可以并存——多个弱点、多个目标。"""
    manager, store = _manager(
        _batch({"kind": "weakness", "scope": "闭包", "content": "闭包掌握不好"})
    )

    asyncio.run(manager.capture([], subject=SUBJECT))
    manager.extractor = LLMMemoryExtractor(
        MockLLMClient(text=_batch({"kind": "weakness", "scope": "递归", "content": "递归掌握不好"}))
    )
    asyncio.run(manager.capture([], subject=SUBJECT))

    assert len(asyncio.run(store.find(subject=SUBJECT))) == 2


def test_memories_of_different_subjects_are_isolated() -> None:
    """不同学习者的记忆互不干扰。"""
    store = InMemoryMemoryStore()
    stored_a = asyncio.run(store.add(_memory(subject="a", content="A 的弱点")))
    asyncio.run(store.add(_memory(subject="b", content="B 的弱点")))

    assert stored_a.subject == "a"
    assert len(asyncio.run(store.find(subject="a"))) == 1
    assert len(asyncio.run(store.find(subject="b"))) == 1
    assert asyncio.run(store.find(subject="a"))[0].content == "A 的弱点"


def test_reusing_an_id_with_a_different_key_does_not_leave_a_stale_mapping() -> None:
    """id 复用但键不同时不留陈旧映射——否则按键查找会捞出别的记忆。"""
    store = InMemoryMemoryStore()
    asyncio.run(store.add(_memory(subject="a", scope="闭包", content="闭包掌握不好")))

    asyncio.run(store.add(_memory(subject="b", scope="闭包", content="闭包掌握不好")))

    assert asyncio.run(store.find(subject="a")) == []
    assert len(asyncio.run(store.find(subject="b"))) == 1


# ---------------------------------------------------------------------------
# 召回
# ---------------------------------------------------------------------------


def test_recall_by_kind() -> None:
    """按类别召回。"""
    store = InMemoryMemoryStore()
    asyncio.run(store.add(_memory(kind="weakness", scope="闭包", content="闭包掌握不好")))
    asyncio.run(store.add(_memory(kind="preference", scope="", content="喜欢先看代码")))
    manager = MemoryManager(store, LLMMemoryExtractor(MockLLMClient(text="{}")))

    memories = asyncio.run(manager.recall(subject=SUBJECT, kind="weakness"))

    assert len(memories) == 1
    assert memories[0].kind == "weakness"


def test_recall_by_query_ranks_by_relevance() -> None:
    """按查询召回，相关度高的排前面。"""
    store = InMemoryMemoryStore()
    asyncio.run(store.add(_memory(kind="weakness", scope="闭包", content="闭包掌握不好")))
    asyncio.run(store.add(_memory(kind="weakness", scope="递归", content="递归掌握不好")))
    asyncio.run(store.add(_memory(kind="preference", scope="", content="喜欢先看代码再读原理")))
    manager = MemoryManager(store, LLMMemoryExtractor(MockLLMClient(text="{}")))

    memories = asyncio.run(manager.recall("闭包", subject=SUBJECT))

    assert memories
    assert "闭包" in memories[0].content


def test_recall_ignores_unrelated_memories() -> None:
    """没有匹配的记忆不返回——召回噪声和写入噪声一样有害。"""
    store = InMemoryMemoryStore()
    asyncio.run(store.add(_memory(kind="preference", scope="", content="喜欢先看代码再读原理")))
    manager = MemoryManager(store, LLMMemoryExtractor(MockLLMClient(text="{}")))

    memories = asyncio.run(manager.recall("闭包", subject=SUBJECT))

    assert memories == []


def test_recall_respects_subject_boundary() -> None:
    """召回不会跨主体——A 的记忆不该出现在 B 的上下文里。"""
    store = InMemoryMemoryStore()
    asyncio.run(store.add(_memory(subject="a", content="A 的弱点")))
    asyncio.run(store.add(_memory(subject="b", content="B 的弱点")))
    manager = MemoryManager(store, LLMMemoryExtractor(MockLLMClient(text="{}")))

    memories = asyncio.run(manager.recall("弱点", subject="a"))

    assert len(memories) == 1
    assert memories[0].subject == "a"


def test_recall_without_query_returns_all_for_subject() -> None:
    """不带查询时按主体返回全部记忆。"""
    store = InMemoryMemoryStore()
    asyncio.run(store.add(_memory(kind="weakness", scope="闭包", content="闭包掌握不好")))
    asyncio.run(store.add(_memory(kind="level", scope="", content="初级")))
    manager = MemoryManager(store, LLMMemoryExtractor(MockLLMClient(text="{}")))

    memories = asyncio.run(manager.recall(subject=SUBJECT))

    assert len(memories) == 2


def test_tokenizer_handles_chinese_and_english() -> None:
    """分词对中文按二元组、对英文按词。

    中文没有词边界，按空格切会得到整句话；按二元组既能命中子串，又不至于把
    单字当成噪声。
    """
    tokens = tokenize("闭包 Closure")

    assert "闭包" in tokens
    assert "closure" in tokens
    assert "包掌" in tokenize("闭包掌握不好")


# ---------------------------------------------------------------------------
# 学习者画像
# ---------------------------------------------------------------------------


def test_profile_aggregates_memories_by_kind() -> None:
    """画像把散落的记忆聚合成可读的结构。"""
    store = InMemoryMemoryStore()
    asyncio.run(store.add(_memory(kind="goal", scope="agent", content="学 Agent Engineering")))
    asyncio.run(store.add(_memory(kind="level", scope="", content="初级")))
    asyncio.run(store.add(_memory(kind="weakness", scope="闭包", content="闭包掌握不好")))
    asyncio.run(store.add(_memory(kind="preference", scope="", content="喜欢先看代码")))
    manager = MemoryManager(store, LLMMemoryExtractor(MockLLMClient(text="{}")))

    profile = asyncio.run(manager.profile(subject=SUBJECT))

    assert profile.level == "初级"
    assert profile.goals == ("学 Agent Engineering",)
    assert profile.weaknesses == ("闭包掌握不好",)
    assert profile.preferences == ("喜欢先看代码",)


def test_profile_renders_to_prompt_text() -> None:
    """画像能渲染成放进提示词的文本。"""
    store = InMemoryMemoryStore()
    asyncio.run(store.add(_memory(kind="level", scope="", content="初级")))
    asyncio.run(store.add(_memory(kind="weakness", scope="闭包", content="闭包掌握不好")))

    profile = LearnerProfile.from_memories(asyncio.run(store.find(subject=SUBJECT)))
    text = profile.to_prompt()

    assert "当前水平：初级" in text
    assert "闭包掌握不好" in text


def test_empty_profile_renders_to_empty_text() -> None:
    """没有记忆时画像渲染成空字符串——不给模型塞一段空话。"""
    assert LearnerProfile.from_memories([]).to_prompt() == ""


# ---------------------------------------------------------------------------
# 端到端
# ---------------------------------------------------------------------------


def test_capture_then_recall_round_trip() -> None:
    """抽取 → 写入 → 召回，一条完整链路。"""
    manager, _store = _manager(
        _batch(
            {
                "memory_type": "semantic",
                "kind": "weakness",
                "scope": "闭包",
                "content": "闭包掌握不好",
                "confidence": 0.9,
            },
            {
                "memory_type": "semantic",
                "kind": "level",
                "scope": "",
                "content": "初级",
                "confidence": 0.9,
            },
        )
    )

    written = asyncio.run(
        manager.capture([Message(role="user", content="闭包不会")], subject=SUBJECT)
    )

    assert len(written) == 2
    recalled = asyncio.run(manager.recall("闭包", subject=SUBJECT))
    assert len(recalled) == 1
    assert recalled[0].kind == "weakness"


def test_structured_batch_model_round_trips() -> None:
    """抽取结果的结构能原样还原——形状稳定是下游机制的前提。"""
    batch = MemoryBatch(memories=[ExtractedMemory(kind="goal", content="学 Python")])

    assert MemoryBatch.model_validate(batch.model_dump()).memories[0].kind == "goal"


def test_memory_dict_round_trips() -> None:
    """记忆能序列化成字典并还原。"""
    memory = _memory()

    restored = Memory(**memory.to_dict())

    assert restored.kind == memory.kind
    assert restored.key == memory.key


# ---------------------------------------------------------------------------
# 存储的基础操作
# ---------------------------------------------------------------------------


def test_store_get_returns_the_memory() -> None:
    """按 id 读取。"""
    store = InMemoryMemoryStore()
    stored = asyncio.run(store.add(_memory()))

    found = asyncio.run(store.get(stored.id))

    assert found is not None
    assert found.id == stored.id


def test_store_get_returns_none_for_unknown_id() -> None:
    """不存在的 id 返回 None。"""
    store = InMemoryMemoryStore()

    assert asyncio.run(store.get("nope")) is None


def test_store_delete_removes_the_memory() -> None:
    """删除记忆，并把键映射一并清掉。"""
    store = InMemoryMemoryStore()
    stored = asyncio.run(store.add(_memory()))

    asyncio.run(store.delete(stored.id))

    assert asyncio.run(store.get(stored.id)) is None
    assert asyncio.run(store.find(subject=SUBJECT)) == []


def test_store_delete_is_forgiving() -> None:
    """删除不存在的记忆不报错。"""
    store = InMemoryMemoryStore()

    asyncio.run(store.delete("nope"))


def test_search_with_empty_query_returns_nothing() -> None:
    """空查询不召回任何东西——没有查询就没有相关度可言。"""
    store = InMemoryMemoryStore()
    asyncio.run(store.add(_memory()))

    assert asyncio.run(store.search("", subject=SUBJECT)) == []


def test_tokenizer_handles_a_single_cjk_character() -> None:
    """单个汉字按单字切。"""
    assert "闭" in tokenize("闭")


def test_profile_renders_every_bucket() -> None:
    """画像的每个分类都渲染进提示词文本。"""
    store = InMemoryMemoryStore()
    asyncio.run(store.add(_memory(kind="goal", scope="agent", content="学 Agent Engineering")))
    asyncio.run(store.add(_memory(kind="level", scope="", content="初级")))
    asyncio.run(store.add(_memory(kind="strength", scope="代码", content="代码能力强")))
    asyncio.run(store.add(_memory(kind="weakness", scope="闭包", content="闭包掌握不好")))
    asyncio.run(store.add(_memory(kind="preference", scope="", content="喜欢先看代码")))
    asyncio.run(store.add(_memory(kind="schedule", scope="", content="每周三五各一小时")))
    asyncio.run(store.add(_memory(kind="progress", scope="闭包", content="上周学了闭包")))

    profile = LearnerProfile.from_memories(asyncio.run(store.find(subject=SUBJECT)))
    text = profile.to_prompt()

    assert "学习目标：学 Agent Engineering" in text
    assert "强项：代码能力强" in text
    assert "薄弱点：闭包掌握不好" in text
    assert "学习偏好：喜欢先看代码" in text
    assert "学习节奏：每周三五各一小时" in text
    assert "其它：上周学了闭包" in text


def test_transcript_ending_with_a_tool_result() -> None:
    """转录以工具结果结尾时也能正常渲染。"""
    transcript = [
        Message(role="user", content="闭包不会"),
        ToolResult(tool_call_id="c1", name="read_notes", content="笔记内容"),
    ]

    text = format_transcript(transcript)

    assert "[工具结果]: read_notes" in text


def test_long_term_memory_example_runs() -> None:
    """长期记忆示例可运行——它是这个能力对外的可见形态。"""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "examples" / "long_term_memory.py")],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=root,
    )

    assert result.returncode == 0, result.stderr
    assert "噪声被挡住" in result.stdout


# ---------------------------------------------------------------------------
# 持久化
# ---------------------------------------------------------------------------


def test_json_file_store_round_trips(tmp_path: Path) -> None:
    """文件存储能存能取，跨「进程」还在。"""
    store = JsonFileMemoryStore(tmp_path / "memories")
    memory = _memory()

    asyncio.run(store.add(memory))

    other = JsonFileMemoryStore(tmp_path / "memories")
    found = asyncio.run(other.get(memory.id))
    assert found is not None
    assert found.content == memory.content


def test_json_file_store_upserts_by_key(tmp_path: Path) -> None:
    """文件存储同样按键 upsert。"""
    store = JsonFileMemoryStore(tmp_path / "memories")
    asyncio.run(store.add(_memory(kind="level", scope="", content="初级")))
    asyncio.run(store.add(_memory(kind="level", scope="", content="中级")))

    memories = asyncio.run(store.find(subject=SUBJECT))

    assert len(memories) == 1
    assert memories[0].content == "中级"


def test_json_file_store_isolates_subjects(tmp_path: Path) -> None:
    """不同主体写进不同文件，互不干扰。"""
    store = JsonFileMemoryStore(tmp_path / "memories")
    asyncio.run(store.add(_memory(subject="a", content="A 的弱点")))
    asyncio.run(store.add(_memory(subject="b", content="B 的弱点")))

    assert len(asyncio.run(store.find(subject="a"))) == 1
    assert len(asyncio.run(store.find(subject="b"))) == 1
    assert len(asyncio.run(store.find())) == 2


def test_json_file_store_searches(tmp_path: Path) -> None:
    """文件存储的召回与内存实现一致。"""
    store = JsonFileMemoryStore(tmp_path / "memories")
    asyncio.run(store.add(_memory(kind="weakness", scope="闭包", content="闭包掌握不好")))
    asyncio.run(store.add(_memory(kind="preference", scope="", content="喜欢先看代码")))

    memories = asyncio.run(store.search("闭包", subject=SUBJECT))

    assert len(memories) == 1
    assert "闭包" in memories[0].content


def test_json_file_store_deletes(tmp_path: Path) -> None:
    """文件存储能删除记忆。"""
    store = JsonFileMemoryStore(tmp_path / "memories")
    memory = _memory()
    asyncio.run(store.add(memory))

    asyncio.run(store.delete(memory.id))

    assert asyncio.run(store.get(memory.id)) is None
    assert asyncio.run(store.find(subject=SUBJECT)) == []


def test_json_file_store_survives_a_manager_restart(tmp_path: Path) -> None:
    """记忆落盘后，换一个存储对象仍能召回——长期记忆的定义就是跨会话。"""
    store = JsonFileMemoryStore(tmp_path / "memories")
    manager = MemoryManager(
        store,
        LLMMemoryExtractor(
            MockLLMClient(
                text=_batch({"kind": "weakness", "scope": "闭包", "content": "闭包掌握不好"})
            )
        ),
    )
    asyncio.run(manager.capture([], subject=SUBJECT))

    other_store = JsonFileMemoryStore(tmp_path / "memories")
    other_manager = MemoryManager(other_store, LLMMemoryExtractor(MockLLMClient(text="{}")))

    memories = asyncio.run(other_manager.recall(subject=SUBJECT))

    assert len(memories) == 1


# ---------------------------------------------------------------------------
# 上下文集成
# ---------------------------------------------------------------------------


def test_build_context_returns_the_profile() -> None:
    """不带查询时给出学习者画像。"""
    store = InMemoryMemoryStore()
    asyncio.run(store.add(_memory(kind="level", scope="", content="初级")))
    asyncio.run(store.add(_memory(kind="weakness", scope="闭包", content="闭包掌握不好")))
    manager = MemoryManager(store, LLMMemoryExtractor(MockLLMClient(text="{}")))

    context = asyncio.run(manager.build_context(subject=SUBJECT))

    assert "当前水平：初级" in context
    assert "闭包掌握不好" in context


def test_build_context_with_query_returns_relevant_memories() -> None:
    """带查询时只给相关的记忆。"""
    store = InMemoryMemoryStore()
    asyncio.run(store.add(_memory(kind="weakness", scope="闭包", content="闭包掌握不好")))
    asyncio.run(store.add(_memory(kind="preference", scope="", content="喜欢先看代码")))
    manager = MemoryManager(store, LLMMemoryExtractor(MockLLMClient(text="{}")))

    context = asyncio.run(manager.build_context("闭包", subject=SUBJECT))

    assert "闭包掌握不好" in context
    assert "喜欢先看代码" not in context


def test_build_context_is_empty_without_memories() -> None:
    """没有记忆时不注入任何东西——不给模型塞空话。"""
    store = InMemoryMemoryStore()
    manager = MemoryManager(store, LLMMemoryExtractor(MockLLMClient(text="{}")))

    assert asyncio.run(manager.build_context(subject=SUBJECT)) == ""


def test_memories_actually_reach_the_model() -> None:
    """记忆真的进到模型请求里——这是「上下文集成」的可验证形态。"""
    from agent_learning_coach.core.agent import AgentConfig, AgentRuntime
    from agent_learning_coach.core.llm.mock import MockLLMClient
    from agent_learning_coach.core.tools import ToolDispatcher, ToolRegistry

    store = InMemoryMemoryStore()
    asyncio.run(store.add(_memory(kind="weakness", scope="闭包", content="闭包掌握不好")))
    manager = MemoryManager(store, LLMMemoryExtractor(MockLLMClient(text="{}")))
    context = asyncio.run(manager.build_context(subject=SUBJECT))

    runtime = AgentRuntime(
        MockLLMClient(text="好的"),
        ToolDispatcher(ToolRegistry()),
        config=AgentConfig(model="mock-model", instructions=["你是学习教练。", context]),
    )

    asyncio.run(runtime.run("我该从哪开始？"))

    request = runtime.client.requests[0]  # type: ignore[attr-defined]
    assert any("闭包掌握不好" in text for text in request.instructions)


def test_format_memories_renders_labels() -> None:
    """记忆渲染时带类别与作用域。"""
    text = format_memories([_memory(kind="weakness", scope="闭包", content="闭包掌握不好")])

    assert text == "- [weakness:闭包] 闭包掌握不好"


def test_format_memories_is_empty_for_no_memories() -> None:
    """没有记忆时渲染成空字符串。"""
    assert format_memories([]) == ""


def test_json_file_store_get_scans_all_subjects(tmp_path: Path) -> None:
    """按 id 读取会扫描各主体——主体是文件维度，id 不是。"""
    store = JsonFileMemoryStore(tmp_path / "memories")
    first = _memory(subject="a", content="A 的弱点")
    second = _memory(subject="b", content="B 的弱点")
    asyncio.run(store.add(first))
    asyncio.run(store.add(second))

    assert asyncio.run(store.get(second.id)) is not None
    assert (await_first := asyncio.run(store.get(second.id))).content == "B 的弱点"
    assert await_first is not None


def test_json_file_store_filters_by_type_and_kind(tmp_path: Path) -> None:
    """文件存储支持按类型与类别筛选。"""
    store = JsonFileMemoryStore(tmp_path / "memories")
    asyncio.run(store.add(_memory(kind="weakness", scope="闭包", content="闭包掌握不好")))
    asyncio.run(
        store.add(
            _memory(
                kind="schedule",
                scope="",
                content="每周三学",
                memory_type="episodic",
            )
        )
    )

    assert len(asyncio.run(store.find(subject=SUBJECT, kind="weakness"))) == 1
    assert len(asyncio.run(store.find(subject=SUBJECT, memory_type="episodic"))) == 1
    assert len(asyncio.run(store.find(subject=SUBJECT, kind="schedule"))) == 1


def test_json_file_store_search_with_empty_query(tmp_path: Path) -> None:
    """空查询不召回任何东西。"""
    store = JsonFileMemoryStore(tmp_path / "memories")
    asyncio.run(store.add(_memory()))

    assert asyncio.run(store.search("", subject=SUBJECT)) == []


def test_json_file_store_delete_of_a_missing_memory_is_forgiving(tmp_path: Path) -> None:
    """删除不存在的记忆不报错。"""
    store = JsonFileMemoryStore(tmp_path / "memories")
    asyncio.run(store.add(_memory()))

    asyncio.run(store.delete("nope"))

    assert len(asyncio.run(store.find(subject=SUBJECT))) == 1


def test_format_memories_omits_empty_scope() -> None:
    """作用域为空时不加后缀——单例记忆（如水平）渲染成裸类别。"""
    text = format_memories([_memory(kind="level", scope="", content="初级")])

    assert text == "- [level] 初级"
