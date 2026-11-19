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

职责边界：运行时只管「模型 ↔ 工具」的节奏与状态。模型调用委托给
``core.llm.LLMClient``，工具执行委托给 ``core.tools.ToolDispatcher``——
因此替换模型或替换工具来源都不需要改运行时。上下文管理、记忆、护栏与
可观测性是同层的其它能力，尚未实现。

导入约定：调用方统一从 ``agent_learning_coach.core.agent`` 导入公开符号。
"""

from agent_learning_coach.core.agent.cost import TokenPrice
from agent_learning_coach.core.agent.errors import AgentConfigError, AgentError
from agent_learning_coach.core.agent.output import (
    FINAL_ANSWER_TOOL_NAME,
    build_final_answer_tool,
    build_output_schema,
)
from agent_learning_coach.core.agent.runtime import AgentRuntime
from agent_learning_coach.core.agent.types import (
    AgentConfig,
    AgentResult,
    AgentStatus,
    AgentStep,
    Event,
    ExecutionContext,
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
    "Event",
    "ExecutionContext",
    "StopReason",
    "TokenPrice",
    "build_final_answer_tool",
    "build_output_schema",
]
