# 习题

1. 下面这段代码的输出是什么？

```python
def outer():
    x = 1
    def inner():
        return x
    return inner
print(inner())
```

2. 用装饰器写一个函数，打印它被调用的次数。

3. 闭包与全局变量在修改变量时有什么区别？

4. 用闭包实现一个计数器：每次调用返回的函数都让计数加一。

5. 下面这个装饰器为什么没有生效？

```python
def double(fn):
    def wrapper(*args):
        return fn(*args) * 2
    return wrapper

@double
def f(x):
    return x + 1

print(f(3))
```

6. `nonlocal` 与 `global` 在闭包里的区别是什么？
