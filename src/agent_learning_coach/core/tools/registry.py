"""工具注册表。

注册表是「有哪些工具可用」的唯一权威来源。它只做三件事：

1. **注册与注销**：按名字登记工具实现。
2. **查找**：按名字找到工具，找不到时明确报错。
3. **导出**：产出给模型看的工具清单。

注册表**不执行工具**，也不校验参数——那是 Dispatcher 的职责。这个边界很关键：
如果注册表也管执行，注册表就会同时承担「可用性」与「行为」，两者变化的原因
完全不同（前者是配置，后者是运行时）。分开之后，Agent Loop 可以在不知道
任何工具实现细节的情况下，把注册表当作工具清单的来源。

重复注册的处理选择**报错而非静默覆盖**：同名工具意味着配置冲突，覆盖会让
问题在很晚才暴露。
"""

from __future__ import annotations

from agent_learning_coach.core.llm.models import ToolSpec
from agent_learning_coach.core.tools.errors import ToolNotFoundError
from agent_learning_coach.core.tools.types import Tool, ToolDefinition


class ToolRegistry:
    """工具注册表。

    按工具名登记工具实现。线程安全性由使用方保证（Agent Loop 单线程调用）。
    """

    def __init__(self) -> None:
        """初始化空注册表。"""
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool, *, overwrite: bool = False) -> ToolDefinition:
        """注册一个工具。

        Args:
            tool: 工具实现，需提供 ``definition`` 属性与 ``execute`` 方法。
            overwrite: 是否允许覆盖同名工具。默认 False，冲突时抛
                ``ValueError``。

        Returns:
            工具的静态契约。

        Raises:
            ValueError: 同名工具已存在且未指定 ``overwrite``。
        """
        definition = tool.definition
        existing = self._tools.get(definition.name)
        if existing is not None and not overwrite:
            raise ValueError(f"工具名 {definition.name!r} 已注册；如需覆盖请传 overwrite=True")
        self._tools[definition.name] = tool
        return definition

    def unregister(self, name: str) -> Tool:
        """注销一个工具。

        Args:
            name: 工具名。

        Returns:
            被注销的工具。

        Raises:
            KeyError: 工具名未注册。
        """
        try:
            return self._tools.pop(name)
        except KeyError:
            raise KeyError(f"工具 {name!r} 未注册") from None

    def get(self, name: str) -> Tool:
        """按名字查找工具。

        Args:
            name: 工具名。

        Returns:
            工具实现。

        Raises:
            ToolNotFoundError: 工具名未注册。
        """
        try:
            return self._tools[name]
        except KeyError:
            known = ", ".join(sorted(self._tools)) or "<无>"
            raise ToolNotFoundError(f"未注册的工具 {name!r}；已注册: {known}") from None

    def has(self, name: str) -> bool:
        """判断工具名是否已注册。

        Args:
            name: 工具名。

        Returns:
            是否已注册。
        """
        return name in self._tools

    def names(self) -> list[str]:
        """列出全部已注册的工具名，按字典序排序。"""
        return sorted(self._tools)

    def definitions(self) -> list[ToolDefinition]:
        """列出全部工具的静态契约，按工具名排序。"""
        return [self._tools[name].definition for name in sorted(self._tools)]

    def tools(self) -> list[Tool]:
        """列出全部工具实现，按工具名排序。

        与 ``definitions()`` 对称：前者给契约，后者给实现。需要**组合**注册表
        时（例如给某个注册表补一个工具）用它——直接改动原注册表会产生副作用。
        """
        return [self._tools[name] for name in sorted(self._tools)]

    def specs(self) -> list[ToolSpec]:
        """导出给模型的工具清单。

        Returns:
            可直接放入 ``LLMRequest.tools`` 的 ``ToolSpec`` 列表。
        """
        return [definition.to_spec() for definition in self.definitions()]

    def __len__(self) -> int:
        """已注册工具数量。"""
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        """判断工具名是否已注册。"""
        return isinstance(name, str) and name in self._tools


__all__ = ["ToolRegistry"]
