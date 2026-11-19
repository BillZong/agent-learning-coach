"""工具权限策略。

**为什么需要它**：模型的意图不可信。工具参数可能被诱导（prompt injection）、
工具名可能被幻觉、模型可能只是想「试试看」。因此「这个工具能不能用」必须
由**运行时**说了算，而不是由模型在请求里声明。

**放在 Dispatcher 而不是 Runtime 的理由**（本项目的明确选择）：

- Dispatcher 是**唯一的执行边界**。本地工具、MCP 工具、结构化输出的
  ``final_answer`` 工具全部经过它。把检查放在这里，意味着「新增一种工具来源」
  不可能绕过检查——检查点只有一个，不会漏。
- 放在 Runtime 的话，Runtime 必须知道每个工具的来源与危险程度。一旦有人
  新写一条执行路径而忘了检查，检查就**默认放行**（fail-open）——这是安全
  系统里最危险的失败模式。
- Runtime 仍然有一份职责，但它是**可见性**而非**强制**：不给模型看它无权
  调用的工具，模型就不会浪费轮次去尝试。这是纵深防御的一层，不是唯一一层。

**作用域（scope）模型**：工具在自己的契约里声明「我需要哪些作用域」，
策略持有「本次运行授予了哪些作用域」，工具所需是授予的子集才放行。作用域是
能力（capability）而非角色（role）：角色模型要维护「角色 → 权限」的映射表，
能力模型只需要「授予哪些能力」这一份清单。

**未声明作用域的工具在任何作用域策略下都放行**。这是「声明式授权」的固有
代价：工具作者忘了声明，等于默认公开。因此凡是接入不可信来源（尤其是
MCP Server）的工具，都应当显式声明作用域——``discover_mcp_tools`` 会为远端
工具自动授予 ``mcp:<命名空间>`` 作用域，正是为了堵住这个口子。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from agent_learning_coach.core.tools.types import ToolDefinition


@dataclass(frozen=True)
class PermissionDecision:
    """一次权限判定的结果。

    判定结果**总是**带一句人类可读的说明：拒绝时这句话会回填给模型，让它
    知道自己该换个工具，而不是反复重试同一个。
    """

    allowed: bool
    """是否放行。"""

    reason: str = ""
    """判定依据。放行时通常为空；拒绝时说明为什么。"""

    missing_scopes: tuple[str, ...] = ()
    """缺失的作用域。仅在按作用域判定时有意义。"""

    @classmethod
    def allow(cls) -> PermissionDecision:
        """构造放行结果。"""
        return cls(allowed=True)

    @classmethod
    def deny(
        cls,
        reason: str,
        *,
        missing_scopes: tuple[str, ...] = (),
    ) -> PermissionDecision:
        """构造拒绝结果。

        Args:
            reason: 拒绝原因，会回填给模型。
            missing_scopes: 缺失的作用域。

        Returns:
            拒绝判定。
        """
        return cls(
            allowed=False,
            reason=reason,
            missing_scopes=tuple(missing_scopes),
        )


@runtime_checkable
class PermissionPolicy(Protocol):
    """工具权限策略。

    实现必须是**纯判定**：不执行工具、不产生副作用、结果只依赖入参。
    策略会被每次工具调用调用一次，副作用会放大成难以排查的问题。
    """

    def check(
        self,
        definition: ToolDefinition,
        *,
        caller: str | None = None,
    ) -> PermissionDecision:
        """判断某个工具是否允许被调用。

        Args:
            definition: 待判定工具的静态契约。
            caller: 调用方标识（如 ``"agent_runtime"``）。供按调用方区分授权
                的自定义策略使用；内置策略不区分。

        Returns:
            判定结果。
        """
        ...


@dataclass(frozen=True)
class AllowAllPolicy:
    """放行一切。

    默认策略：不配置权限时行为与引入权限机制之前完全一致。
    """

    def check(
        self,
        definition: ToolDefinition,
        *,
        caller: str | None = None,
    ) -> PermissionDecision:
        """放行。

        Args:
            definition: 待判定工具的契约。
            caller: 调用方标识。

        Returns:
            放行结果。
        """
        return PermissionDecision.allow()


@dataclass(frozen=True)
class DenyAllPolicy:
    """拒绝一切。

    存在的意义不是「常用」，而是**安全默认值**：在不确定该给哪些权限时，
    先全部拒绝，再按需放开。也可以用于只读地演练一次运行。
    """

    reason: str = "当前策略不允许调用任何工具"
    """拒绝时给出的说明。"""

    def check(
        self,
        definition: ToolDefinition,
        *,
        caller: str | None = None,
    ) -> PermissionDecision:
        """拒绝。

        Args:
            definition: 待判定工具的契约。
            caller: 调用方标识。

        Returns:
            拒绝结果。
        """
        return PermissionDecision.deny(self.reason)


@dataclass(frozen=True)
class ScopePolicy:
    """按作用域判定。

    工具在 ``ToolDefinition.scopes`` 里声明自己需要哪些作用域；策略持有本次
    运行授予的作用域集合。工具所需作用域是授予集合的**子集**时放行。

    未声明作用域的工具恒放行——见模块文档的说明。
    """

    granted: frozenset[str] = field(default_factory=frozenset)
    """已授予的作用域集合。"""

    def check(
        self,
        definition: ToolDefinition,
        *,
        caller: str | None = None,
    ) -> PermissionDecision:
        """按作用域判定。

        Args:
            definition: 待判定工具的契约。
            caller: 调用方标识（本策略不区分调用方）。

        Returns:
            判定结果。
        """
        missing = tuple(sorted(set(definition.scopes) - set(self.granted)))
        if missing:
            return PermissionDecision.deny(
                f"缺少权限作用域：{'、'.join(missing)}",
                missing_scopes=missing,
            )
        return PermissionDecision.allow()


__all__ = [
    "AllowAllPolicy",
    "DenyAllPolicy",
    "PermissionDecision",
    "PermissionPolicy",
    "ScopePolicy",
]
