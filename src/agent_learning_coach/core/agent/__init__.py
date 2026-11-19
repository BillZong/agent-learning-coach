"""Agent 运行时（Agent Runtime）。

提供可复用的 Agent 执行能力，位于应用层与学习领域层之下：

- **Agent Loop**（``loop.AgentLoop``）：ReAct 执行循环——「模型 -> 工具 ->
  结果回填 -> 模型」。负责循环控制与停止条件。
- **Agent Config**（``types.AgentConfig``）：运行参数——预算与停止阈值。
- **Agent Run / Agent Step**（``types``）：运行结果与逐步记录。

职责边界：循环只管「模型 ↔ 工具」的节奏。模型调用委托给
``core.llm.LLMClient``，工具执行委托给 ``core.tools.ToolDispatcher``——
因此替换模型或替换工具来源都不需要改循环。上下文管理、记忆、护栏与
可观测性是同层的其它能力，尚未实现。

导入约定：调用方统一从 ``agent_learning_coach.core.agent`` 导入公开符号。
"""

from agent_learning_coach.core.agent.loop import AgentLoop
from agent_learning_coach.core.agent.types import (
    AgentConfig,
    AgentRun,
    AgentStep,
    StopReason,
)

__all__ = [
    "AgentConfig",
    "AgentLoop",
    "AgentRun",
    "AgentStep",
    "StopReason",
]
