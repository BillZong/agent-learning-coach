"""Agent 运行时抛出的异常。

与 ``core/llm/errors.py``、``core/tools/errors.py`` 保持同构：每个包有自己的
错误基类，上层能据此区分「模型调用失败」「工具调用失败」「运行时配置错误」，
不必靠异常消息文本判断。

运行时错误分两类：

- **可预期的运行时失败**（模型调用失败、超时、预算耗尽）：不抛异常，写进
  ``AgentResult`` 的 ``stop_reason`` 与 ``error``，因为调用方需要知道「走到
  哪一步」以及「为什么停」。
- **配置错误**（预算无法执行、参数自相矛盾）：抛异常，因为模型无法修复它，
  重试也没有意义。
"""

from __future__ import annotations


class AgentError(Exception):
    """Agent 运行时错误的基类。"""


class AgentConfigError(AgentError):
    """运行时配置错误。

    属于**配置错误**：模型无法通过调整参数修复，重试也没有意义。典型场景是
    设了成本上限但价格表没有覆盖实际使用的模型——预算因此无法被执行。
    """


__all__ = [
    "AgentConfigError",
    "AgentError",
]
