"""Agent 运行时（Agent Runtime）。

提供可复用的 Agent 执行能力，位于应用层与学习领域层之下：

- **Agent Runtime**（``runtime.AgentRuntime``）：ReAct 执行循环与运行时状态。
  负责循环控制、停止条件与状态记录。
- **Execution Context**（``types.ExecutionContext``）：一次执行的运行时状态
  ——事件历史、步数、草稿本、用量与成本。它让运行可测试、可复现、可续接。
- **Event**（``types.Event``）：执行过程中发生的一件事。
- **Agent Config**（``types.AgentConfig``）：运行参数——预算与停止阈值。
- **Agent Result**（``types.AgentResult``）：运行结果，携带执行上下文。
- **Token Price**（``cost.TokenPrice``）：模型 token 单价，由调用方提供。
- **结构化输出**（``output``）：把最终答案作为一个工具产出（``final_answer``）。
- **Session Context**（``session.SessionContext``）：跨多次运行持续存在的会话
  ——学习者与教练的多轮对话，累积在同一份执行上下文里。
- **Session State**（``session.SessionState``）：可持久化的会话快照——恢复一个
  运行所需的全部状态。
- **Session Store**（``session``）：会话存储协议与两种实现——内存与 JSON 文件。
- **Tool Hooks**（``hooks.StateCollector``）：把工具产出的结构化数据收进执行
  上下文的草稿本。通用机制，不认识具体数据格式。

职责边界：运行时只管「模型 ↔ 工具」的节奏与状态。模型调用委托给
``core.llm.LLMClient``，工具执行委托给 ``core.tools.ToolDispatcher``——
因此替换模型或替换工具来源都不需要改运行时。上下文的**预算裁剪**委托给
``core.context.ContextBuilder``（存储与呈现分离）；记忆、护栏与可观测性是
同层的其它能力，尚未实现。

导入约定：调用方统一从 ``agent_learning_coach.core.agent`` 导入公开符号。
"""

from agent_learning_coach.core.agent.cost import TokenPrice
from agent_learning_coach.core.agent.errors import AgentConfigError, AgentError
from agent_learning_coach.core.agent.hooks import StateCollector
from agent_learning_coach.core.agent.output import (
    FINAL_ANSWER_TOOL_NAME,
    build_final_answer_tool,
    build_output_schema,
)
from agent_learning_coach.core.agent.runtime import AgentRuntime
from agent_learning_coach.core.agent.session import (
    InMemorySessionStore,
    JsonFileSessionStore,
    SessionContext,
    SessionState,
    SessionStore,
)
from agent_learning_coach.core.agent.types import (
    AgentConfig,
    AgentResult,
    AgentStatus,
    AgentStep,
    ApprovalDecision,
    Event,
    ExecutionContext,
    PendingApproval,
    StopReason,
)

__all__ = [
    "FINAL_ANSWER_TOOL_NAME",
    "AgentConfig",
    "AgentConfigError",
    "AgentError",
    "AgentResult",
    "AgentRuntime",
    "AgentStatus",
    "AgentStep",
    "ApprovalDecision",
    "Event",
    "ExecutionContext",
    "InMemorySessionStore",
    "JsonFileSessionStore",
    "PendingApproval",
    "SessionContext",
    "SessionState",
    "SessionStore",
    "StateCollector",
    "StopReason",
    "TokenPrice",
    "build_final_answer_tool",
    "build_output_schema",
]
