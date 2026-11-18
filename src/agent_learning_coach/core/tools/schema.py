"""工具参数 Schema 校验。

校验发生在**调用前**，而不是让工具自己在函数体里检查参数。原因：工具函数
往往由业务方随手写成，让每个工具各自校验参数必然导致校验逻辑重复且不一致；
把校验集中在一处，工具只需声明「我接受什么」，Dispatcher 负责保证
「传进来的一定是合法的」。

校验用 JSON Schema（draft 2020-12）描述参数结构，与发给模型的
``ToolSpec.parameters`` 使用同一份 schema——模型看到的契约与实际校验的契约
因此必然一致，不会出现「告诉模型 A、实际按 B 校验」的错位。

使用 ``jsonschema`` 库做校验：它是 JSON Schema 的参考实现，完整覆盖
draft 2020-12 规范；自行实现子集校验器会在边界情况上给出错误的通过/拒绝
结论，而这种错误只在生产数据形态下才暴露。
"""

from __future__ import annotations

from typing import Any

from agent_learning_coach.core.tools.errors import ToolValidationError

try:
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
except ImportError as exc:  # pragma: no cover - 依赖缺失时的显式错误
    raise ImportError("工具系统需要 jsonschema 依赖；请执行 `uv sync` 安装依赖") from exc


def validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
    """按 JSON Schema 校验工具参数。

    Args:
        schema: 参数 JSON Schema。空 schema 表示接受任意参数。
        arguments: 待校验的参数。

    Raises:
        ToolValidationError: 参数不满足 schema 时抛出，附带逐条校验明细。
    """
    if not schema:
        return

    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(arguments), key=lambda err: list(err.absolute_path))
    if not errors:
        return

    details = [_format_error(error) for error in errors]
    raise ToolValidationError(
        f"工具参数校验失败（{len(details)} 项）：" + "；".join(details),
        errors=details,
    )


def _format_error(error: JsonSchemaValidationError) -> str:
    """把单个校验错误格式化为可读文本。

    Args:
        error: jsonschema 的校验错误。

    Returns:
        含参数路径与失败原因的可读描述。
    """
    path = ".".join(str(part) for part in error.absolute_path)
    location = path or "<root>"
    return f"{location}: {error.message}"


def describe_schema(schema: dict[str, Any]) -> str:
    """生成 schema 的简短描述，用于错误信息与日志。

    Args:
        schema: 参数 JSON Schema。

    Returns:
        形如 ``"query: string (必填), limit: integer"`` 的描述。
    """
    if not schema:
        return "<无参数>"

    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return "<无参数>"

    required = set(schema.get("required", []) or [])
    parts: list[str] = []
    for name, spec in properties.items():
        if not isinstance(spec, dict):
            continue
        type_name = spec.get("type", "any")
        suffix = " (必填)" if name in required else ""
        parts.append(f"{name}: {type_name}{suffix}")
    return ", ".join(parts)


__all__ = ["describe_schema", "validate_arguments"]
