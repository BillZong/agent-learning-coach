"""知识表示：关系、路径与薄弱点归因的原语。

跑法::

    uv run python examples/knowledge_structure.py

Topic 16 的问题是：知识点与关系怎么建模，才能同时支撑**路径计算**与**薄弱点
归因**？脚本演示四件事：

1. **两种关系**——``requires`` 决定学习顺序；``part_of`` 提供主题层级。
2. **路径**——``order()`` 给出学习顺序，``entry_points()`` 给出起点。
3. **归因**——``prerequisite_closure()`` 回答「先补哪些」，
   ``dependent_closure()`` 回答「后续哪些受影响」。
4. **校验**——成环、多父、端点缺失都会被拒绝，且错误信息可直接定位。

脚本不联网。
"""

from __future__ import annotations

from agent_learning_coach.core.learning import (
    DomainError,
    KnowledgePoint,
    KnowledgePointId,
    KnowledgeRelation,
    KnowledgeStructure,
)


def _kp(point_id: str, title: str) -> KnowledgePoint:
    """构造一个知识点。"""
    return KnowledgePoint(id=KnowledgePointId(point_id), title=title)


def _rel(kind: str, source: str, target: str) -> KnowledgeRelation:
    """构造一条关系。"""
    return KnowledgeRelation(
        kind=kind,  # type: ignore[arg-type]
        source=KnowledgePointId(source),
        target=KnowledgePointId(target),
    )


def build_structure() -> KnowledgeStructure:
    """课程「JavaScript 核心」的知识结构。"""
    return KnowledgeStructure(
        points=(
            _kp("k1", "函数与作用域"),
            _kp("k2", "闭包"),
            _kp("k3", "高阶函数"),
            _kp("k4", "异步与 Promise"),
            _kp("k5", "模块化"),
            _kp("t1", "语言基础"),
            _kp("t2", "函数式编程"),
        ),
        relations=(
            # 前置依赖：决定「能不能开始」
            _rel("requires", "k2", "k1"),
            _rel("requires", "k3", "k2"),
            _rel("requires", "k4", "k2"),
            _rel("requires", "k5", "k4"),
            # 主题层级：决定「归到哪个主题」
            _rel("part_of", "k2", "t2"),
            _rel("part_of", "k3", "t2"),
            _rel("part_of", "t2", "t1"),
        ),
    )


def show_path(structure: KnowledgeStructure) -> None:
    """演示学习顺序与起点。"""
    print("=== 1. 路径：先学什么 ===\n")
    for index, point in enumerate(structure.order(), 1):
        prerequisites = "，".join(str(item.id) for item in structure.prerequisites_of(point.id))
        print(f"{index}. {point.id} {point.title}（直接前置：{prerequisites or '无'}）")
    print(
        "\n起点：" + "，".join(f"{p.id} {p.title}" for p in structure.entry_points()) + "\n",
    )


def show_attribution(structure: KnowledgeStructure) -> None:
    """演示薄弱点归因的两个方向。"""
    print("=== 2. 归因：失败之后往哪看 ===\n")
    failed = KnowledgePointId("k3")
    remediation = structure.prerequisite_closure(failed)
    print("k3（高阶函数）没掌握 → 先补（按顺序）：")
    for point in remediation:
        print(f"  - {point.id} {point.title}")
    if not remediation:
        print("  （没有前置，直接补 k3 本身）")

    root = KnowledgePointId("k1")
    impact = structure.dependent_closure(root)
    print("\n反过来，k1（函数与作用域）没掌握 → 后续受影响：")
    for point in impact:
        print(f"  - {point.id} {point.title}")
    print("\n两条查询都是派生视图：每次重算，不落库。\n")


def show_hierarchy(structure: KnowledgeStructure) -> None:
    """演示主题层级。"""
    print("=== 3. 层级：归到哪个主题 ===\n")
    for point_id in ("k3", "t2", "t1"):
        point = structure.point(KnowledgePointId(point_id))
        parent = structure.parent_of(point.id)
        print(f"{point.id} {point.title} → 父主题：{parent.title if parent else '无'}")
    children = structure.children_of(KnowledgePointId("t2"))
    print("t2 的直接子节点：" + "，".join(f"{p.id} {p.title}" for p in children) + "\n")


def show_rejections() -> None:
    """演示被拒绝的结构。"""
    print("=== 4. 违反不变量会被拒绝 ===\n")
    attempts = [
        (
            "requires 成环",
            lambda: KnowledgeStructure(
                points=(_kp("a", "A"), _kp("b", "B")),
                relations=(_rel("requires", "a", "b"), _rel("requires", "b", "a")),
            ),
        ),
        (
            "一个知识点有两个主题父节点",
            lambda: KnowledgeStructure(
                points=(_kp("k1", "闭包"), _kp("t1", "语言"), _kp("t2", "范式")),
                relations=(_rel("part_of", "k1", "t1"), _rel("part_of", "k1", "t2")),
            ),
        ),
        (
            "关系端点不存在",
            lambda: KnowledgeStructure(
                points=(_kp("k1", "闭包"),),
                relations=(_rel("requires", "k1", "k9"),),
            ),
        ),
        (
            "重复声明同一条关系",
            lambda: KnowledgeStructure(
                points=(_kp("a", "A"), _kp("b", "B")),
                relations=(_rel("requires", "a", "b"), _rel("requires", "a", "b")),
            ),
        ),
    ]
    for label, attempt in attempts:
        try:
            attempt()
        except DomainError as exc:
            print(f"[{label}] -> {exc}")
    print()


def main() -> None:
    """跑一遍知识结构演示。"""
    structure = build_structure()
    show_path(structure)
    show_attribution(structure)
    show_hierarchy(structure)
    show_rejections()
    print("=== 设计要点 ===")
    print("relations 是结构的事实，不是知识点的字段：改关系不必复制知识点。")
    print("requires 决定顺序；part_of 只决定归属——两者分开校验，互不影响。")


if __name__ == "__main__":
    main()
