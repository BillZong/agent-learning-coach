"""成本核算与预算控制。

**价格表由调用方提供**，不内置在包里。原因：价格会变、随厂商与地区不同、
有时还随合同而变；把它写死在代码里等于制造一个必然过期的真相来源。运行时
只负责按给定价格表核算与执行预算。

单位统一为「每千 token」——这个量级下数字可读，且与主流厂商的报价口径一致。
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_learning_coach.core.llm.models import LLMUsage


@dataclass(frozen=True)
class TokenPrice:
    """某个模型的 token 单价。

    Attributes:
        input_per_1k: 每千输入 token 的价格。
        output_per_1k: 每千输出 token 的价格。
    """

    input_per_1k: float = 0.0
    """每千输入 token 的价格。"""

    output_per_1k: float = 0.0
    """每千输出 token 的价格。"""

    def cost_of(self, usage: LLMUsage) -> float:
        """计算一段用量的成本。

        Args:
            usage: token 用量。

        Returns:
            成本金额。
        """
        return (
            usage.input_tokens / 1000.0 * self.input_per_1k
            + usage.output_tokens / 1000.0 * self.output_per_1k
        )


__all__ = ["TokenPrice"]
