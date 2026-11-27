"""Token 计数。

上下文预算是**估算**，不是精确值——这一点必须先说清楚，否则预算会给人
「精确控制」的错觉：

- 分词器本身是**厂商相关**的。OpenAI 系的模型用 ``tiktoken``，Anthropic 系
  用自己的分词器。本项目是厂商无关的，因此这里用 ``tiktoken`` 作为**通用
  近似**：它对英文接近准确，对中文与代码偏保守。
- ``tiktoken`` 首次使用需要**下载 BPE 文件**。离线环境（CI 容器、受限网络）
  下会失败，因此这里必须有无网络的回退路径，而不是让预算功能整体不可用。
- 每条消息还有固定开销（角色、分隔符），本模块按 4 token 估算，与
  ``tiktoken`` 官方 cookbook 的做法一致。

回退策略是**按字符估算**：中日韩字符按 1 token 计，其余按 4 字符 1 token 计。
它比真实分词保守（通常高估），因此**不会**让人以为内容比实际更小。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from agent_learning_coach.core.llm.models import ToolSpec
from agent_learning_coach.core.llm.types import ContentItem, Message, ToolCall, ToolResult

DEFAULT_ENCODING = "o200k_base"
"""默认分词器。取不到模型专属分词器时使用。"""

MESSAGE_OVERHEAD_TOKENS = 4
"""每条消息的固定开销（角色标记、分隔符等）。"""

_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x3000, 0x303F),  # CJK 标点
    (0x3400, 0x4DBF),  # 扩展 A
    (0x4E00, 0x9FFF),  # 基本汉字
    (0xF900, 0xFAFF),  # 兼容汉字
    (0x20000, 0x2A6DF),  # 扩展 B
    (0x3040, 0x30FF),  # 假名
)


def _is_cjk(char: str) -> bool:
    """判断字符是否属于中日韩文字。"""
    code = ord(char)
    return any(start <= code <= end for start, end in _CJK_RANGES)


def estimate_tokens(text: str) -> int:
    """按字符估算 token 数。

    这是 ``tiktoken`` 不可用时的回退路径。估算偏保守（高估），因此不会
    让预算被低估。

    Args:
        text: 待估算的文本。

    Returns:
        估算的 token 数。
    """
    if not text:
        return 0
    cjk = 0
    for char in text:
        if _is_cjk(char):
            cjk += 1
    others = len(text) - cjk
    return cjk + (others + 3) // 4


_ENCODINGS: dict[str, Any] = {}
"""已成功加载的分词器，按名称缓存。"""

_FAILED: set[str] = set()
"""加载失败的名称。

记录下来是为了**不重复下载**：``tiktoken`` 首次加载要联网取 BPE 文件，离线时
每次重试都要等一次超时。一个进程里失败一次就够了，之后直接走估算路径。
"""


def _load_encoding(name: str) -> Any | None:
    """按名称加载分词器。

    Args:
        name: 分词器名称，如 ``"o200k_base"``。

    Returns:
        分词器；不可用时返回 None。
    """
    if name in _ENCODINGS:
        return _ENCODINGS[name]
    if name in _FAILED:
        return None
    try:
        import tiktoken

        encoding = tiktoken.get_encoding(name)
    except Exception:
        # 缺依赖、离线、名称不认识——都归为「不可用」，走估算回退。
        _FAILED.add(name)
        return None
    _ENCODINGS[name] = encoding
    return encoding


def _load_model_encoding(model: str) -> Any | None:
    """加载模型专属的分词器。

    Args:
        model: 模型名。

    Returns:
        分词器；该模型没有专属分词器或加载失败时返回 None。
    """
    key = f"model:{model}"
    if key in _ENCODINGS:
        return _ENCODINGS[key]
    if key in _FAILED:
        return None
    try:
        import tiktoken

        encoding = tiktoken.encoding_for_model(model)
    except Exception:
        _FAILED.add(key)
        return None
    _ENCODINGS[key] = encoding
    return encoding


class TokenCounter:
    """按模型估算一段上下文占多少 token。

    计数对象是一次**请求**的全部输入：对话内容 + 工具定义。工具定义也要
    计入——工具越多、描述越长，每次请求的开销越大，这部分开销不受历史长度
    影响，但会挤压真正能放内容的空间。
    """

    def __init__(self, model: str | None = None) -> None:
        """初始化计数器。

        Args:
            model: 模型名。用于选择分词器；为 None 时用默认分词器。
        """
        self.model = model
        self._encoding = self._resolve(model)

    @staticmethod
    def _resolve(model: str | None) -> Any | None:
        """解析分词器。"""
        if model:
            encoding = _load_model_encoding(model)
            if encoding is not None:
                return encoding
        return _load_encoding(DEFAULT_ENCODING)

    @property
    def exact(self) -> bool:
        """计数是否精确。

        ``False`` 表示当前走的是字符估算回退——离线环境下 ``tiktoken`` 拿不到
        BPE 文件时的正常状态。
        """
        return self._encoding is not None

    def count_text(self, text: str) -> int:
        """统计一段文本的 token 数。

        Args:
            text: 待统计文本。

        Returns:
            token 数。
        """
        if not text:
            return 0
        if self._encoding is None:
            return estimate_tokens(text)
        return len(self._encoding.encode(text))

    def count(
        self,
        contents: Sequence[ContentItem],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> int:
        """统计一次请求的 token 总数。

        Args:
            contents: 对话内容。
            tools: 工具定义。为 None 或空列表时不计入工具开销。

        Returns:
            token 总数。
        """
        total = 0
        for item in contents:
            total += self._count_item(item)
        if tools:
            total += self.count_tools(tools)
        return total

    def count_tools(self, tools: Sequence[ToolSpec]) -> int:
        """统计工具定义占用的 token。

        Args:
            tools: 工具定义。

        Returns:
            token 数。
        """
        total = 0
        for tool in tools:
            total += self.count_text(_tool_payload(tool))
        return total

    def _count_item(self, item: ContentItem) -> int:
        """统计单条内容的 token 数。

        Args:
            item: 一条对话内容。

        Returns:
            token 数。
        """
        if isinstance(item, Message):
            return MESSAGE_OVERHEAD_TOKENS + self.count_text(item.content)
        if isinstance(item, ToolCall):
            total = MESSAGE_OVERHEAD_TOKENS
            total += self.count_text(item.name)
            total += self.count_text(json.dumps(item.arguments, ensure_ascii=False))
            return total
        if isinstance(item, ToolResult):
            return MESSAGE_OVERHEAD_TOKENS + self.count_text(item.content)
        return MESSAGE_OVERHEAD_TOKENS  # pragma: no cover - 联合类型已穷尽


def _tool_payload(tool: ToolSpec) -> str:
    """把工具定义序列化成用于计数的文本。

    Args:
        tool: 工具定义。

    Returns:
        JSON 文本。
    """
    return json.dumps(
        {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
        ensure_ascii=False,
    )


__all__ = [
    "DEFAULT_ENCODING",
    "MESSAGE_OVERHEAD_TOKENS",
    "TokenCounter",
    "estimate_tokens",
]
