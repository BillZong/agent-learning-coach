"""嵌入适配。

**嵌入把文本变成向量**，检索因此从「关键词匹配」变成「按语义相近程度排序」。
主教材 5.3.1 用的是 OpenAI 的 embeddings 接口——但那需要 API key，离线环境与
CI 都跑不了。

本包提供两个实现：

- ``HashingEmbedder``：**确定性、离线、零依赖**。用词袋 + 哈希投影把文本
  映射成固定维度的向量。它捕捉的是**词汇相似**而不是语义相似——同义词
  （「闭包」与「函数式闭包」）可能匹配不上。**它是占位级的嵌入**，用途是让
  整条流水线可离线、可测试、可演示；生产环境应当换成真正的语义嵌入模型。
- ``OpenAICompatibleEmbedder``：**生产路径**。调用任何 OpenAI 兼容的
  ``/embeddings`` 接口（OpenAI、Azure OpenAI、以及兼容该协议的其它服务）。

两者都实现同一个 ``EmbeddingClient`` 协议，因此替换时上层代码不用改。
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

import httpx

from agent_learning_coach.core.text import token_counts


@runtime_checkable
class EmbeddingClient(Protocol):
    """嵌入客户端。"""

    @property
    def dimension(self) -> int:
        """向量维度。

        同一套存储里的向量必须维度一致——维度不同的向量无法比较。
        """
        ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """把一批文本转成向量。

        Args:
            texts: 待嵌入的文本。

        Returns:
            与输入一一对应的向量列表。
        """
        ...


class HashingEmbedder:
    """确定性的哈希嵌入（离线、无依赖）。

    **它不是语义嵌入。** 用词袋 + 哈希投影，捕捉的是词汇层面的重合：包含
    相同词的两段文本会得到相近的向量，但同义不同词的两段文本可能完全不搭。

    它的价值在于**让整条流水线可离线跑通**：测试、示例、CI 都不需要任何
    外部服务。生产环境请换成 ``OpenAICompatibleEmbedder`` 或自建的嵌入服务。

    哈希用 SHA-256 而不是 Python 内置 ``hash()``——后者每个进程的随机种子
    不同，会让「同样的文本得到同样的向量」这条性质失效。
    """

    def __init__(self, dimension: int = 256) -> None:
        """初始化嵌入器。

        Args:
            dimension: 向量维度。越大冲突越少，但内存与计算也越贵。

        Raises:
            ValueError: 维度非正。
        """
        if dimension <= 0:
            raise ValueError("dimension 必须为正数")
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        """向量维度。"""
        return self._dimension

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """把一批文本转成向量。

        Args:
            texts: 待嵌入的文本。

        Returns:
            与输入一一对应的向量列表。每个向量已做 L2 归一化，因此点积即余弦
            相似度。
        """
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        """把一段文本转成一个向量。

        Args:
            text: 待嵌入文本。

        Returns:
            L2 归一化后的向量。
        """
        vector = [0.0] * self._dimension
        for token, count in token_counts(text).items():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self._dimension
            # 符号位取自哈希的另一位：带符号的哈希投影能减少不同词落到同一
            # 桶时的相互抵消。
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign * (1.0 + math.log(count))
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [value / norm for value in vector]


class OpenAICompatibleEmbedder:
    """OpenAI 兼容的嵌入适配器。

    调用 ``POST {base_url}/embeddings``，请求体 ``{"input": [...], "model": ...}``，
    解析 ``{"data": [{"embedding": [...], "index": n}]}``。

    **维度由第一次调用推断**，之后固定；后续响应维度不一致时抛错——同一套
    存储里混着不同维度的向量，检索结果会毫无意义且难以察觉。
    """

    def __init__(
        self,
        *,
        base_url: str = "https://api.openai.com/v1",
        api_key: str,
        model: str = "text-embedding-3-small",
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """初始化适配器。

        Args:
            base_url: 接口根地址。
            api_key: API key。
            model: 嵌入模型名。
            timeout: 请求超时（秒）。
            transport: 可选的 httpx 传输层。测试用它替换真实网络。
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._transport = transport
        self._dimension: int | None = None

    @property
    def dimension(self) -> int:
        """向量维度。

        Raises:
            RuntimeError: 还没有调用过 ``embed``，维度未知。
        """
        if self._dimension is None:
            raise RuntimeError("尚未调用过 embed，向量维度未知")
        return self._dimension

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """把一批文本转成向量。

        Args:
            texts: 待嵌入的文本。

        Returns:
            与输入一一对应的向量列表。

        Raises:
            LLMError: 请求失败或响应无法解析。
        """
        from agent_learning_coach.core.llm.errors import LLMError

        if not texts:
            return []
        async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/embeddings",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"input": list(texts), "model": self.model},
                )
                response.raise_for_status()
                payload = response.json()
            except httpx.HTTPError as exc:
                raise LLMError(f"嵌入接口调用失败：{exc}") from exc
            except ValueError as exc:
                raise LLMError(f"嵌入接口返回的不是合法 JSON：{exc}") from exc

        data = payload.get("data")
        if not isinstance(data, list) or not data:
            raise LLMError("嵌入接口响应缺少 data 字段")

        vectors: list[list[float]] = [[] for _ in texts]
        for item in data:
            vector = item.get("embedding")
            if not isinstance(vector, list) or not vector:
                raise LLMError("嵌入接口响应缺少 embedding 字段")
            index = int(item.get("index", len(vectors) - 1))
            vectors[index] = [float(value) for value in vector]

        width = len(vectors[0])
        if self._dimension is None:
            self._dimension = width
        elif width != self._dimension:
            raise LLMError(f"嵌入维度不一致：期望 {self._dimension}，实际 {width}")
        return vectors


__all__ = ["EmbeddingClient", "HashingEmbedder", "OpenAICompatibleEmbedder"]
