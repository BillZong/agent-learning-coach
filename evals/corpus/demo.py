"""示例模块：装饰器与闭包的最小实现。"""

import functools


def counter():
    """返回一个计数的闭包。"""
    count = 0

    def increment():
        nonlocal count
        count += 1
        return count

    return increment


def timed(fn):
    """打印被装饰函数的参数。"""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        print("calling", fn.__name__)
        return fn(*args, **kwargs)

    return wrapper


def double(fn):
    """把结果乘二。"""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs) * 2

    return wrapper


@timed
@double
def add(a, b):
    """两数相加。"""
    return a + b
