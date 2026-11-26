# Python 参考

`functools.wraps` 把被包装函数的 `__name__`、`__doc__` 等属性复制到包装函数。

`inspect.signature` 取得函数的参数签名。

`copy.copy` 与 `copy.deepcopy` 的区别在于是否递归复制嵌套对象。

`inspect.getsource` 取出函数的源代码。

`typing.get_type_hints` 取出函数注解里的类型（在 `from __future__ import annotations`
下尤其有用，因为注解此时是字符串）。

`contextlib.contextmanager` 把一个生成器函数变成上下文管理器。

`dataclasses.dataclass` 自动生成 `__init__`、`__repr__` 与比较方法。
