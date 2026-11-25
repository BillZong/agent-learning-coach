"""文本切分工具。

分词这件事看起来小，但**中英文的处理方式不同**：

- 英文与数字有天然的词边界，按词切即可。
- 中文**没有词边界**——按空格切会得到整句话，检索时几乎命中不了。

因此中文按**二元组**切（「闭包」切成「闭」「包」）：既能命中子串，又不至于把
单字当成噪声（「闭」几乎出现在任何中文文本里，会让相关度失去区分度）。

这个模块是 ``core.memory`` 与 ``core.rag`` 共用的唯一分词实现——两处各写一
份必然漂移。
"""

from __future__ import annotations

import re

_ASCII_TOKEN = re.compile(r"[a-z0-9_]+")
_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]+")


def token_counts(text: str) -> dict[str, int]:
    """把文本切成小块并统计出现次数。

    Args:
        text: 待切分的文本。

    Returns:
        小块到次数的映射。大小写不敏感。
    """
    lowered = text.lower()
    counts: dict[str, int] = {}

    def bump(token: str) -> None:
        counts[token] = counts.get(token, 0) + 1

    for token in _ASCII_TOKEN.findall(lowered):
        bump(token)
    for run in _CJK.findall(lowered):
        if len(run) == 1:
            bump(run)
            continue
        for index in range(len(run) - 1):
            bump(run[index : index + 2])
    return counts


def tokenize(text: str) -> set[str]:
    """把文本切成用于匹配的小块。

    Args:
        text: 待分词的文本。

    Returns:
        小块集合。
    """
    return set(token_counts(text))


def normalize_for_match(text: str) -> str:
    """把文本归一化成便于**精确比对**的形式。

    折叠空白、忽略大小写与标点，因此「闭包的概念」与「闭包  的概念！」会
    被判为同一个字符串。这比 :func:`tokenize` 更严格——它是「相等」而不是
    「相似」。

    Args:
        text: 原始文本。

    Returns:
        归一化后的文本。
    """
    return re.sub(r"[\s\W_]+", "", text.casefold(), flags=re.UNICODE)


__all__ = ["normalize_for_match", "token_counts", "tokenize"]
