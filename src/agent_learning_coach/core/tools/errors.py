"""工具系统异常类型。

工具系统的异常分两类：

- **调用级**：某个工具调用的失败（参数不合法、超时、执行报错）。
  这些错误会被归一化成 ``ToolResult`` 回填给模型，属于**可预期的**失败。
- **系统级**：工具系统自身的配置错误（注册表里没有这个工具）。
  这些错误直接抛出，因为模型无法通过重试修复。

异常继承 ``LLMError`` 的兄弟类 ``ToolError``，不与模型调用错误混在一起，
让调用方能区分「模型调用失败」与「工具调用失败」。
"""

from __future__ import annotations


class ToolError(Exception):
    """工具系统错误的基类。"""


class ToolNotFoundError(ToolError):
    """注册表中不存在指定的工具。

    属于系统级错误：模型请求了未注册的工具，重试无意义。
    """


class ToolValidationError(ToolError):
    """工具参数不满足 JSON Schema。

    属于调用级错误：会被归一化成错误结果回填给模型，让模型有机会修正参数。
    """

    def __init__(self, message: str, *, errors: list[str] | None = None) -> None:
        """初始化校验错误。

        Args:
            message: 人类可读的失败原因。
            errors: 逐条的校验明细。
        """
        super().__init__(message)
        self.errors = list(errors or [])


class ToolTimeoutError(ToolError):
    """工具执行超时。

    属于调用级错误：会被归一化成错误结果回填给模型。
    """


class ToolExecutionError(ToolError):
    """工具执行过程中抛出异常。

    属于调用级错误：会被归一化成错误结果回填给模型。
    """

    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        """初始化执行错误。

        Args:
            message: 人类可读的失败原因。
            cause: 原始异常。
        """
        super().__init__(message)
        self.cause = cause


class MCPConnectionError(ToolError):
    """与远端 MCP Server 建立会话失败。

    属于系统级错误：远端进程没起来、握手没完成或超时——都是环境或配置问题，
    模型无法通过调整参数修复，因此直接抛出而不是归一化成工具结果。

    SDK 内部的并发原语会把真实原因包进 ``ExceptionGroup``（层层嵌套，最外层
    只显示 "unhandled errors in a TaskGroup"）。本错误类型把根因摊平到消息里，
    让调用方一眼看出问题出在哪。
    """

    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        """初始化连接错误。

        Args:
            message: 人类可读的失败原因。
            cause: 原始异常。
        """
        super().__init__(message)
        self.cause = cause


__all__ = [
    "MCPConnectionError",
    "ToolError",
    "ToolExecutionError",
    "ToolNotFoundError",
    "ToolTimeoutError",
    "ToolValidationError",
]
