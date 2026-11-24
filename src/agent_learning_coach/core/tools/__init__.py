"""工具系统（Tool System）。

为 Agent Runtime 提供工具调用能力：模型请求执行某个工具，系统按契约执行
它，并把结果归一化后回填给模型。

组成：

- **Tool Definition**（``types.ToolDefinition``）：工具的静态契约——名称、
  描述、参数 JSON Schema。既是注册项，也是发给模型的工具清单。
- **Tool Context**（``types.ToolContext``）：运行时上下文——会话、调用方、
  截止时间。工具据此决定能做什么。
- **Tool Result**（``types.ToolOutcome``）：归一化的执行结果——状态、内容、
  错误、耗时。无论工具返回什么类型，对外结构一致。
- **Tool Registry**（``registry.ToolRegistry``）：工具可用性的唯一权威来源。
- **Tool Dispatcher**（``dispatcher.ToolDispatcher``）：执行边界——查找、权限
  检查、校验、执行（带超时）、归一化。
- **Tool Permission**（``permissions``）：权限策略——工具声明所需作用域，策略
  决定放行与否。检查点唯一，因此不存在绕过检查的执行路径。
- **Schema Validation**（``schema.validate_arguments``）：按 JSON Schema 校验
  参数，保证「模型看到的契约」与「实际校验的契约」一致。
- **Tool Timeout**（``dispatcher.ToolDispatcher.resolve_timeout``）：超时优先
  级为工具定义 > Dispatcher 默认值。
- **Tool Result Normalization**（``normalization``）：把任意返回值收敛成统一
  的文本与结构。
- **Tool Callbacks**（``types.BeforeToolHook`` / ``types.AfterToolHook``）：
  执行前可短路、执行后可替换结果。挂在 Dispatcher 上，因此**所有**工具来源
  都受同一套回调约束。
- **MCP 协议适配**（``mcp``）：双向适配——把工具注册表暴露为 MCP Server，
  或把远端 MCP 工具接入本项目的注册表。

导入约定：调用方统一从 ``agent_learning_coach.core.tools`` 导入公开符号。
"""

from agent_learning_coach.core.tools.dispatcher import ToolDispatcher
from agent_learning_coach.core.tools.errors import (
    MCPConnectionError,
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolPermissionError,
    ToolSchemaError,
    ToolTimeoutError,
    ToolValidationError,
)
from agent_learning_coach.core.tools.mcp import (
    MCPTool,
    build_mcp_server,
    discover_mcp_tools,
    extract_text_content,
    from_mcp_tool,
    run_stdio_server,
    serve,
    stdio_session,
    to_mcp_tool,
)
from agent_learning_coach.core.tools.normalization import (
    normalize_content,
    normalize_data,
    normalize_error,
    normalize_outcome,
)
from agent_learning_coach.core.tools.permissions import (
    AllowAllPolicy,
    DenyAllPolicy,
    PermissionDecision,
    PermissionPolicy,
    ScopePolicy,
)
from agent_learning_coach.core.tools.registry import ToolRegistry
from agent_learning_coach.core.tools.schema import describe_schema, validate_arguments
from agent_learning_coach.core.tools.types import (
    AfterToolHook,
    BeforeToolHook,
    FunctionTool,
    Tool,
    ToolContext,
    ToolDefinition,
    ToolHandler,
    ToolOutcome,
)

__all__ = [
    "AfterToolHook",
    "AllowAllPolicy",
    "BeforeToolHook",
    "DenyAllPolicy",
    "FunctionTool",
    "MCPConnectionError",
    "MCPTool",
    "PermissionDecision",
    "PermissionPolicy",
    "ScopePolicy",
    "Tool",
    "ToolContext",
    "ToolDefinition",
    "ToolDispatcher",
    "ToolError",
    "ToolExecutionError",
    "ToolHandler",
    "ToolNotFoundError",
    "ToolOutcome",
    "ToolPermissionError",
    "ToolRegistry",
    "ToolSchemaError",
    "ToolTimeoutError",
    "ToolValidationError",
    "build_mcp_server",
    "describe_schema",
    "discover_mcp_tools",
    "extract_text_content",
    "from_mcp_tool",
    "normalize_content",
    "normalize_data",
    "normalize_error",
    "normalize_outcome",
    "run_stdio_server",
    "serve",
    "stdio_session",
    "to_mcp_tool",
    "validate_arguments",
]
