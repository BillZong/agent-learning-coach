"""长期记忆的核心类型。

**什么值得记？** 这是长期记忆唯一真正的问题。本模块给出的答案是三个条件，
缺一不可：

1. **归属**——是关于**谁**的？长期记忆按主体隔离（LangGraph 的 namespace）。
   关于学习者的事进长期记忆，关于工具输出、模型推测、一次性任务的事不进。
2. **耐久**——下次会话还成立吗？「我在闭包」会变，「我喜欢先看代码」不会。
   一次性的事属于会话（短期记忆），不属于长期记忆。
3. **可覆盖**——它属于哪一类事实？同一类事实**只保留最新的一条**（水平从
   初级变成中级，旧的那条应当被取代而不是并存）。

第 3 条是抑制噪声的关键机制：一条记忆有 ``(主体, 类型, 类别, 作用域)``
这样的唯一键，写入时按键 upsert。于是「学习者水平变了」表现为**一条记忆被
更新**，而不是「多了一条矛盾的记忆」。

**类型沿用 CoALA / LangGraph 的三分法**（不是本项目自创）：

- ``semantic``：**事实**。关于学习者的事实——目标、水平、强项、弱点、偏好。
- ``episodic``：**经历**。发生过的事——「上周三学了闭包」。
- ``procedural``：**规则**。怎么教这个人——「他需要先看代码再讲原理」。

注意与「语义搜索」的区别：这里说的是心理学意义上的记忆分类，与用嵌入做
相似度检索是两回事。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

MemoryType = Literal["semantic", "episodic", "procedural"]
"""记忆类型（CoALA 分类，LangGraph 文档采用同一套）。

- ``semantic``：事实。
- ``episodic``：经历。
- ``procedural``：规则。
"""

LEARNER_MEMORY_KINDS: tuple[str, ...] = (
    "goal",
    "level",
    "strength",
    "weakness",
    "preference",
    "schedule",
    "progress",
)
"""学习者记忆的类别词表。

这是**本项目当前的默认词表**，不是通用标准。Phase 1 的学习领域层会扩展它
（届时可能加入「知识点掌握度」「学习障碍」等）。词表是准入策略的一部分——
不在词表里的类别会被拒绝写入，避免模型随手发明类别把记忆搅乱。
"""


@dataclass(frozen=True)
class Memory:
    """一条长期记忆。

    **不可变**：更新一条记忆就是用同键的新记忆取代它。不可变让「历史」与
    「现状」分得清——想知道学习者上周什么水平，翻旧记录；想知道现在什么水平，
    看当前这条。
    """

    id: str
    """记忆标识。"""

    memory_type: MemoryType
    """记忆类型：事实 / 经历 / 规则。"""

    kind: str
    """类别。词表见 :data:`LEARNER_MEMORY_KINDS`。"""

    subject: str
    """归属主体（学习者标识）。相当于 LangGraph 的 namespace 第一层。"""

    scope: str = ""
    """作用域。区分「同一类别里不同的那一条」。

    留空表示该类别**只有一条**（单例），例如水平；填上具体值表示该类别下
    可以有多条，例如弱点按知识点区分（``scope="闭包"``）。
    """

    content: str = ""
    """记忆内容。一句话，陈述一个事实。"""

    metadata: dict[str, Any] = field(default_factory=dict)
    """附加信息。不参与检索。"""

    confidence: float = 1.0
    """置信度。低于准入阈值的记忆不写入。"""

    created_at: float = 0.0
    """创建时刻（``time.time()`` 基准）。"""

    updated_at: float = 0.0
    """最近更新时刻。"""

    @property
    def key(self) -> tuple[str, str, str, str]:
        """唯一键。

        同一 ``(主体, 类型, 类别, 作用域)`` 只有一条记忆——写入时按键 upsert，
        于是「新事实取代旧事实」而不是「两条矛盾的事实并存」。

        Returns:
            唯一键。
        """
        return (self.subject, self.memory_type, self.kind, self.scope)

    def to_dict(self) -> dict[str, Any]:
        """序列化成字典。

        Returns:
            字典。
        """
        return {
            "id": self.id,
            "memory_type": self.memory_type,
            "kind": self.kind,
            "subject": self.subject,
            "scope": self.scope,
            "content": self.content,
            "metadata": dict(self.metadata),
            "confidence": self.confidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class MemoryPolicy:
    """记忆准入策略。

    这是**抑制噪声的执行点**。抽取出来的东西不等于该写进长期记忆——准入
    策略在这里做最后一道确定性检查：

    - 内容非空（模型有时会返回空字符串）。
    - 置信度达标（模型自己都不确定的事实不值得记）。
    - 长度不超标（超长的是噪声还是摘要，取决于场景；默认给个上限）。
    - 类别在词表内（防止模型随手发明类别）。

    策略是**确定性**的：同样的输入永远得到同样的判断。这一点很重要——
    交给模型判断「这条算不算噪声」就等于没有判断。
    """

    allowed_kinds: frozenset[str] = frozenset(LEARNER_MEMORY_KINDS)
    """允许的类别。空集合表示不限制类别。"""

    min_confidence: float = 0.5
    """最低置信度。"""

    max_content_chars: int = 500
    """内容最大字符数。"""

    def admits(self, memory: Memory) -> bool:
        """判断一条记忆是否准入。

        Args:
            memory: 待判断的记忆。

        Returns:
            准入返回 True。
        """
        return (
            bool(memory.content.strip())
            and memory.confidence >= self.min_confidence
            and (not self.max_content_chars or len(memory.content) <= self.max_content_chars)
            and (not self.allowed_kinds or memory.kind in self.allowed_kinds)
        )


@dataclass(frozen=True)
class LearnerProfile:
    """学习者画像。

    是长期记忆的一种**视图**：把散落的记忆按类别聚合成一份可以读的画像。
    它不额外存数据——画像随时可以从记忆重新算出来。
    """

    subject: str
    """学习者标识。"""

    goals: tuple[str, ...] = ()
    """学习目标。"""

    level: str | None = None
    """当前水平。"""

    strengths: tuple[str, ...] = ()
    """强项。"""

    weaknesses: tuple[str, ...] = ()
    """薄弱点。"""

    preferences: tuple[str, ...] = ()
    """学习偏好。"""

    schedule: tuple[str, ...] = ()
    """学习节奏。"""

    notes: tuple[str, ...] = ()
    """其它语义事实。"""

    @classmethod
    def from_memories(cls, memories: list[Memory]) -> LearnerProfile:
        """从记忆集合生成画像。

        Args:
            memories: 记忆集合。通常是按主体过滤后的结果。

        Returns:
            画像。
        """
        subject = memories[0].subject if memories else ""
        buckets: dict[str, list[str]] = {}
        for memory in memories:
            buckets.setdefault(memory.kind, []).append(memory.content)
        level_values = buckets.get("level", [])
        return cls(
            subject=subject,
            goals=tuple(buckets.get("goal", [])),
            level=level_values[0] if level_values else None,
            strengths=tuple(buckets.get("strength", [])),
            weaknesses=tuple(buckets.get("weakness", [])),
            preferences=tuple(buckets.get("preference", [])),
            schedule=tuple(buckets.get("schedule", [])),
            notes=tuple(buckets.get("progress", []) + buckets.get("fact", [])),
        )

    def to_prompt(self) -> str:
        """渲染成可以放进提示词的文本。

        Returns:
            多行文本。没有内容时返回空字符串。
        """
        lines: list[str] = []
        if self.level:
            lines.append(f"- 当前水平：{self.level}")
        if self.goals:
            lines.append("- 学习目标：" + "；".join(self.goals))
        if self.strengths:
            lines.append("- 强项：" + "、".join(self.strengths))
        if self.weaknesses:
            lines.append("- 薄弱点：" + "、".join(self.weaknesses))
        if self.preferences:
            lines.append("- 学习偏好：" + "、".join(self.preferences))
        if self.schedule:
            lines.append("- 学习节奏：" + "、".join(self.schedule))
        if self.notes:
            lines.append("- 其它：" + "、".join(self.notes))
        if not lines:
            return ""
        return "关于这个学习者的已知信息：\n" + "\n".join(lines)


def format_memories(memories: list[Memory]) -> str:
    """把一组记忆渲染成可以放进提示词的文本。

    每条一行，带类别与作用域——模型据此能区分「这是关于闭包的薄弱点」与
    「这是关于递归的薄弱点」。

    Args:
        memories: 记忆列表。

    Returns:
        多行文本。没有记忆时返回空字符串。
    """
    if not memories:
        return ""
    lines = []
    for memory in memories:
        label = memory.kind
        if memory.scope:
            label = f"{label}:{memory.scope}"
        lines.append(f"- [{label}] {memory.content}")
    return "\n".join(lines)


def now() -> float:
    """当前时刻。

    Returns:
        ``time.time()``。
    """
    return time.time()


__all__ = [
    "LEARNER_MEMORY_KINDS",
    "LearnerProfile",
    "Memory",
    "MemoryPolicy",
    "MemoryType",
    "format_memories",
    "now",
]
