"""MCP（Model Context Protocol）协议适配。

MCP 把「工具」标准化为「带 JSON Schema 的可调用能力」，通过 ``tools/list``
发现、``tools/call`` 调用。本项目的 ``ToolDefinition`` 恰好就是同样的形状
（名称 + 描述 + JSON Schema），因此适配是**双向**的：

- **服务端适配**（``build_mcp_server``）：把本项目的工具注册表暴露为 MCP
  Server，让其它 MCP 客户端可以发现并调用我们的工具。
- **客户端适配**（``MCPTool`` / ``discover_mcp_tools``）：把远端 MCP Server
  的工具包装成本项目的 ``Tool``，注册进 ``ToolRegistry``。

**为什么客户端适配更有价值**：适配进来的 MCP 工具进入我们的注册表后，直接
复用现有的 ``ToolDispatcher``——Schema 校验在**本地**完成（参数不合法不必
走一趟网络）、超时由本地调度器统一控制、结果归一化与本地工具完全一致。
第三方 MCP Server 因此不需要写任何适配代码就能接入。

**为什么服务端适配不是默认选择**：MCP 的价值在于「能力独立部署、独立归属、
跨客户端复用」。本项目自己的工具（检索、生成练习）与 Agent Loop 同属一个
进程、同一团队，套一层 MCP 只增加进程边界与协议开销而没有收益。因此服务端
适配以协议参考实现（PoC）的形式提供，用于验证映射的正确性并保留未来迁移
路径。

实现刻意停留在**协议层**（``mcp`` SDK 的低层 Server / ClientSession），
不依赖 FastMCP 的便捷封装：适配器应当直接说协议，而不是说某个框架。

**为什么用低层 ``Server`` 而不是官方 SDK 的中层 ``MCPServer``**：``MCPServer``
的注册接口只接受可调用对象，参数校验依据的是**从 Python 签名推导的
``arg_model``**，与我们显式声明的 ``definition.parameters`` 不是同一份。实测
（真实协议路径）：``additionalProperties: False`` 与 ``minLength`` 均不生效
——多传参数被静默丢弃、短字符串被接受。``**kwargs`` 绕过方案也不可行，arg
binding 会要求一个名为 ``kwargs`` 的参数并直接拒绝调用。

低层 Server 让「模型看到的契约」与「实际强制的契约」保持同一份，这是
ADR-0003 的硬约束。代价是 stdio 运行器与测试夹具要自己写，且低层 API 在
SDK 大版本间更易变动——我们接受这个代价。推导见 ADR-0004。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import mcp.types as types
from mcp.client.session import ClientSession
from mcp.server.lowlevel import Server
from mcp.shared.exceptions import MCPError
from mcp_types import CONNECTION_CLOSED

from agent_learning_coach.core.tools.dispatcher import ToolDispatcher
from agent_learning_coach.core.tools.errors import MCPConnectionError
from agent_learning_coach.core.tools.registry import ToolRegistry
from agent_learning_coach.core.tools.types import (
    ToolContext,
    ToolDefinition,
    ToolOutcome,
)


def to_mcp_tool(definition: ToolDefinition) -> types.Tool:
    """把 ``ToolDefinition`` 转换为 MCP 工具描述。

    映射是三字段的一一对应：名称、描述、参数 JSON Schema。这正是「MCP 的
    工具形状与我们的工具契约同构」的体现。

    Args:
        definition: 工具定义。

    Returns:
        MCP 协议的工具描述。
    """
    return types.Tool(
        name=definition.name,
        description=definition.description,
        input_schema=definition.parameters or {"type": "object", "properties": {}},
    )


def from_mcp_tool(mcp_tool: types.Tool) -> ToolDefinition:
    """把 MCP 工具描述转换为 ``ToolDefinition``。

    除三字段核心契约（名称、描述、参数 Schema）外，远端的其余契约信息会被
    保留在 ``metadata`` 中：结构化输出契约 ``output_schema``、标题 ``title``
    与行为注解 ``annotations``。丢弃它们会让远端声明的输出契约在发现阶段
    就失传——调用方无从知道工具会返回什么形状的数据。

    Args:
        mcp_tool: MCP 协议的工具描述。

    Returns:
        工具定义。
    """
    schema = _field(mcp_tool, "input_schema", "inputSchema")
    metadata: dict[str, Any] = {}
    output_schema = _field(mcp_tool, "output_schema", "outputSchema")
    if output_schema is not None:
        metadata["output_schema"] = output_schema
    title = _field(mcp_tool, "title")
    if title is not None:
        metadata["title"] = title
    annotations = _field(mcp_tool, "annotations")
    if annotations is not None:
        metadata["annotations"] = annotations
    return ToolDefinition(
        name=mcp_tool.name,
        description=mcp_tool.description or "",
        parameters=dict(schema or {}),
        metadata=metadata,
    )


def _field(item: Any, *names: str) -> Any:
    """按候选名读取属性，兼容 SDK 的 Python 字段名与 wire 格式别名。

    MCP SDK 的模型用 snake_case 作为 Python 字段名（``mime_type``），而协议
    上是 camelCase（``mimeType``）。不同 SDK 版本还可能直接用 wire 名做字段
    名，因此两个都试。

    Args:
        item: 对象。
        names: 候选属性名，按优先级排列。

    Returns:
        第一个存在的属性值；都不存在时返回 None。
    """
    for name in names:
        value = getattr(item, name, None)
        if value is not None:
            return value
    return None


def _describe_content_block(item: Any) -> str:
    """描述一个无法转成文本的 MCP 内容块。

    用于非文本内容（图片、音频、资源）。**不能直接丢弃**：空内容与「工具
    确实没有输出」无法区分，模型会误判工具行为。占位描述至少让模型知道
    「有东西回来了，但不是文本」。

    Args:
        item: MCP 内容块。

    Returns:
        占位描述文本。
    """
    kind = _field(item, "type") or "unknown"
    details: list[str] = []
    mime = _field(item, "mime_type", "mimeType")
    if mime:
        details.append(f"mimeType={mime}")
    uri = _field(item, "uri")
    if uri:
        details.append(f"uri={uri}")
    name = _field(item, "name")
    if name:
        details.append(f"name={name}")
    suffix = "".join(f", {d}" for d in details)
    return f"[MCP 内容块:{kind}{suffix}]"


def extract_text_content(result: types.CallToolResult) -> str:
    """从 MCP 调用结果中提取可回填给模型的文本。

    MCP 的调用结果不止文本一种形态：

    - **文本块**（``TextContent``）：直接取文本。
    - **结构化输出**（``structured_content``）：按 JSON 序列化后保留，机器可读
      的数据不能因为「不是文本」而被丢掉。
    - **其它内容块**（图片、音频、资源）：以占位描述代替，不静默丢弃。

    只要远端返回了任何东西，本函数就不会返回空字符串——空内容与「工具确实
    没有输出」无法区分，会让模型误判。远端确实什么都没返回时，返回空串是
    诚实的答案。

    Args:
        result: MCP 调用结果。

    Returns:
        拼接后的文本内容。
    """
    parts: list[str] = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text is not None:
            parts.append(text)
        else:
            parts.append(_describe_content_block(item))

    structured = getattr(result, "structured_content", None)
    if structured is not None:
        parts.append(json.dumps(structured, ensure_ascii=False, default=str))

    return "\n".join(parts)


def _is_error(result: types.CallToolResult) -> bool:
    """判断 MCP 调用结果是否为错误。

    MCP SDK v2 把字段改名为 ``is_error``，旧版本是 ``isError``。两种都探测，
    避免适配器被 SDK 的字段改名打断。

    Args:
        result: MCP 调用结果。

    Returns:
        是否为错误结果。
    """
    return bool(getattr(result, "is_error", None) or getattr(result, "isError", None))


class MCPTool:
    """远端 MCP 工具的本地适配。

    把远端 MCP 工具包装成本项目的 ``Tool``，因此可以直接注册进
    ``ToolRegistry``，由 ``ToolDispatcher`` 统一调度。

    **本地名与远端名分离**：本地名可能带命名空间前缀（避免多个 MCP Server
    的同名工具冲突），而协议调用必须使用远端的原始名。两者混用会导致远端
    找不到工具。
    """

    def __init__(
        self,
        mcp_tool: types.Tool,
        session: ClientSession,
        *,
        local_name: str | None = None,
    ) -> None:
        """初始化适配器。

        Args:
            mcp_tool: 远端 MCP 工具描述。
            session: 已建立的 MCP 客户端会话。
            local_name: 本地使用的工具名。为 None 时沿用远端名。
        """
        self._remote_name = mcp_tool.name
        self._definition = from_mcp_tool(mcp_tool)
        if local_name is not None:
            self._definition = self._definition.model_copy(update={"name": local_name})
        self._session = session

    @property
    def definition(self) -> ToolDefinition:
        """本地工具定义。"""
        return self._definition

    @property
    def remote_name(self) -> str:
        """远端 MCP Server 上的工具名。"""
        return self._remote_name

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> Any:
        """调用远端工具并返回文本结果。

        远端工具的**生命周期绑定在会话上**：会话关闭后再调用会失败。这种失败
        容易被误读成「远端坏了」，因此按错误码单独识别并给出明确提示。

        Args:
            context: 运行时上下文（远端工具不消费，仅满足协议）。
            arguments: 已通过本地 Schema 校验的参数。

        Returns:
            远端返回的文本内容。
        """
        try:
            result = await self._session.call_tool(self._remote_name, arguments)
        except MCPError as exc:
            if exc.code == CONNECTION_CLOSED:
                raise RuntimeError(
                    f"MCP 会话已关闭，无法调用远端工具 {self._remote_name}——"
                    "远端工具的生命周期绑定在会话上，必须在会话存活期间使用"
                ) from exc
            raise
        if _is_error(result):
            detail = extract_text_content(result)
            raise RuntimeError(f"MCP 工具 {self._remote_name} 返回错误：{detail}")
        return extract_text_content(result)


async def discover_mcp_tools(
    session: ClientSession,
    *,
    namespace: str | None = None,
    max_pages: int = 100,
) -> list[MCPTool]:
    """发现远端 MCP Server 的工具并适配成本项目的工具。

    **翻页**：MCP 的 ``tools/list`` 是分页协议，响应带 ``next_cursor`` 时表示
    还有下一页。只取第一页会让工具**静默丢失**——调用方看到的工具清单不完整，
    却没有任何报错。因此这里必须翻完所有页。

    ``max_pages`` 是防御性上限：远端若反复返回同一个游标，翻页会不收敛，
    上限保证发现过程一定终止。

    Args:
        session: 已建立的 MCP 客户端会话。
        namespace: 工具名前缀。用于避免多个 MCP Server 的同名工具冲突，
            形如 ``"web"`` 会把 ``search`` 改名为 ``web_search``。
        max_pages: 最多翻取的页数。

    Returns:
        适配后的工具列表。
    """
    tools: list[MCPTool] = []
    cursor: str | None = None
    for _ in range(max_pages):
        params = types.PaginatedRequestParams(cursor=cursor) if cursor is not None else None
        response = await session.list_tools(params=params)
        for mcp_tool in response.tools:
            local_name = f"{namespace}_{mcp_tool.name}" if namespace else None
            tools.append(MCPTool(mcp_tool, session, local_name=local_name))
        cursor = response.next_cursor
        if not cursor:
            break
    return tools


DEFAULT_HANDSHAKE_TIMEOUT = 30.0
"""握手超时默认值（秒）。远端无响应时不能无限期挂起。"""


def _root_cause(exc: BaseException) -> BaseException:
    """从嵌套 ``ExceptionGroup`` 里找出最有信息量的异常。

    MCP SDK 用 anyio 任务组管理子进程与协议读写，失败时会把根因包进层层
    嵌套的 ``ExceptionGroup``，最外层只显示 "unhandled errors in a TaskGroup"。
    真正有用的信息（连接关闭、进程退出码）在里面。

    查找规则：优先返回我们自己抛出的 ``MCPConnectionError``——它的消息已经
    写清了原因；没有则逐层下钻取最内层异常。异常组只有一个成员时，容器
    没有额外信息，直接下钻即可——调用方拿到的仍是原本的异常类型。

    Args:
        exc: 异常。

    Returns:
        最有信息量的异常。
    """
    current = exc
    while isinstance(current, BaseExceptionGroup) and current.exceptions:
        for sub in current.exceptions:
            if isinstance(sub, MCPConnectionError):
                return sub
        current = current.exceptions[0]
    return current


def _as_connection_error(exc: BaseException, command: str) -> BaseException:
    """把建立连接阶段的异常转换成可读错误。

    Args:
        exc: 原始异常。
        command: 启动命令。

    Returns:
        ``MCPConnectionError``，或已经是它的异常本身。
    """
    cause = _root_cause(exc)
    if isinstance(cause, MCPConnectionError):
        return cause
    if isinstance(exc, FileNotFoundError) or isinstance(cause, FileNotFoundError):
        return MCPConnectionError(f"无法启动 MCP Server：找不到可执行文件 {command!r}")
    return MCPConnectionError(
        f"无法与 MCP Server 建立会话（{command}）：{type(cause).__name__}: {cause}"
    )


async def _initialize_session(
    session: ClientSession,
    command: str,
    timeout: float,
) -> None:
    """完成 MCP 握手，失败时抛出可读错误。

    Args:
        session: 客户端会话。
        command: 启动命令，仅用于错误消息。
        timeout: 握手超时（秒）。

    Raises:
        MCPConnectionError: 握手超时。
    """
    try:
        async with asyncio.timeout(timeout):
            await session.initialize()
    except TimeoutError as exc:
        raise MCPConnectionError(f"MCP Server 握手超时（{timeout}s）：{command}") from exc


@asynccontextmanager
async def stdio_session(
    command: str,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    *,
    timeout: float = DEFAULT_HANDSHAKE_TIMEOUT,
) -> AsyncIterator[ClientSession]:
    """通过 stdio 启动 MCP Server 并建立会话。

    建立连接的过程有三类可预期的失败，都收敛成 ``MCPConnectionError``：

    - 启动命令不存在（``FileNotFoundError``）。
    - 握手超时（远端无响应）。
    - 连接中断（远端进程退出或不是 MCP Server）。

    这三类若原样抛出，调用方只能看到 ``ExceptionGroup`` 的
    "unhandled errors in a TaskGroup"，真实原因被埋在里面。摊平之后，错误
    消息直接指出是哪个命令、什么原因。

    转换只作用于**建立阶段**：握手完成后的调用方代码抛出的异常原样透传。

    Args:
        command: 启动命令。
        args: 命令参数。
        env: 环境变量。
        timeout: 握手超时（秒）。

    Yields:
        已完成初始化的 MCP 客户端会话。

    Raises:
        MCPConnectionError: 启动失败、握手失败或握手超时。
    """
    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=command, args=args or [], env=env)
    ready = False
    try:
        async with AsyncExitStack() as stack:
            read_stream, write_stream = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await _initialize_session(session, command, timeout)
            ready = True
            yield session
    except BaseException as exc:
        cause = _root_cause(exc)
        if isinstance(cause, (KeyboardInterrupt, SystemExit)):
            # 中断信号必须原样透传
            raise cause from exc.__cause__
        if ready:
            # 握手已完成：这是调用方代码的异常，不该被改写成连接错误
            raise cause from exc.__cause__
        raise _as_connection_error(cause, command) from exc.__cause__


def build_mcp_server(
    registry: ToolRegistry,
    *,
    name: str = "agent-learning-coach-tools",
    dispatcher: ToolDispatcher | None = None,
    version: str = "0.1.0",
) -> Server:
    """把工具注册表暴露为 MCP Server。

    服务端复用 ``ToolDispatcher`` 执行工具，因此 Schema 校验、超时与结果
    归一化与本地调用完全一致——不会出现「本地调一套、MCP 调另一套」的
    行为漂移。

    Args:
        registry: 工具注册表。
        name: Server 名称。
        dispatcher: 调度器。为 None 时用注册表新建一个默认调度器。
        version: Server 版本。

    Returns:
        可运行的 MCP Server。
    """
    engine = dispatcher if dispatcher is not None else ToolDispatcher(registry)

    async def on_list_tools(
        context: Any,
        params: types.PaginatedRequestParams | None,
    ) -> types.ListToolsResult:
        """处理 ``tools/list``：返回全部可用工具。"""
        return types.ListToolsResult(tools=[to_mcp_tool(d) for d in registry.definitions()])

    async def on_call_tool(
        context: Any,
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        """处理 ``tools/call``：执行工具并返回内容块。"""
        outcome = await engine.dispatch(
            params.name,
            dict(params.arguments or {}),
            ToolContext(caller="mcp"),
        )
        return _outcome_to_call_result(outcome)

    return Server(
        name,
        version=version,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


def _outcome_to_call_result(outcome: ToolOutcome) -> types.CallToolResult:
    """把归一化结果转换为 MCP 调用结果。

    Args:
        outcome: 归一化执行结果。

    Returns:
        MCP 调用结果。失败时置 ``is_error``。
    """
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=outcome.content)],
        is_error=not outcome.succeeded,
    )


async def serve(server: Server, read_stream: Any, write_stream: Any) -> None:
    """在给定的流上运行 MCP Server。

    与 ``run_stdio_server`` 分开是为了让「运行服务端」这一步能在进程内测试：
    stdio 的建流部分只能在子进程中执行，无法被覆盖率工具测量。

    Args:
        server: MCP Server。
        read_stream: 读取流。
        write_stream: 写入流。
    """
    await server.run(
        read_stream,
        write_stream,
        server.create_initialization_options(),
    )


async def run_stdio_server(server: Server) -> None:
    """在 stdio 传输上运行 MCP Server。

    本函数只在**子进程**中执行（stdio 传输要求 Server 独占进程的 stdin/stdout），
    因此覆盖率工具无法测量这几行。行为本身由 ``tests/test_mcp_tools.py`` 中
    以子进程方式运行示例 Server 的用例覆盖。

    Args:
        server: MCP Server。
    """
    from mcp.server.stdio import stdio_server  # pragma: no cover - 子进程执行

    async with stdio_server() as (read_stream, write_stream):  # pragma: no cover - 子进程执行
        await serve(server, read_stream, write_stream)


__all__ = [
    "MCPTool",
    "build_mcp_server",
    "discover_mcp_tools",
    "extract_text_content",
    "from_mcp_tool",
    "run_stdio_server",
    "serve",
    "stdio_session",
    "to_mcp_tool",
]
