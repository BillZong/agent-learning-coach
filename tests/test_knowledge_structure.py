"""知识结构（知识点 + 关系）的测试（Topic 16）。

覆盖四组关注点：

**关系建模**——``requires`` 决定顺序，``part_of`` 提供主题层级；两者分开校验。

**不变量**——知识点 id 唯一、关系端点存在、不自指、不重复；``requires`` 无环；
``part_of`` 是树（一子一父）且无环。错误信息给出环路径或多父节点。

**派生查询**——``order`` / ``entry_points`` / ``prerequisites_of`` /
``prerequisite_closure`` / ``dependent_closure`` / ``parent_of`` /
``children_of``：全部按确定顺序返回，且每次重算。

**边界**——查询未知知识点会失败；``part_of`` 不影响学习顺序。
"""

from __future__ import annotations

import pytest

from agent_learning_coach.core.learning import (
    DomainError,
    KnowledgePoint,
    KnowledgePointId,
    KnowledgeRelation,
    KnowledgeStructure,
)


def _kp(point_id: str, title: str, summary: str = "") -> KnowledgePoint:
    """构造一个知识点。"""
    return KnowledgePoint(id=KnowledgePointId(point_id), title=title, summary=summary)


def _rel(kind: str, source: str, target: str) -> KnowledgeRelation:
    """构造一条关系。"""
    return KnowledgeRelation(
        kind=kind,  # type: ignore[arg-type]
        source=KnowledgePointId(source),
        target=KnowledgePointId(target),
    )


def _js_structure() -> KnowledgeStructure:
    """构造一个带前置链与主题层级的知识结构。

    顺序：k1 函数与作用域 → k2 闭包 → k3 高阶函数；k4 模块化无前置。
    层级：k2、k3 属于 t2（函数式编程），t2 属于 t1（语言基础）。
    """
    return KnowledgeStructure(
        points=(
            _kp("k3", "高阶函数"),
            _kp("k2", "闭包"),
            _kp("k1", "函数与作用域"),
            _kp("k4", "模块化"),
            _kp("t2", "函数式编程"),
            _kp("t1", "语言基础"),
        ),
        relations=(
            _rel("requires", "k3", "k2"),
            _rel("requires", "k2", "k1"),
            _rel("part_of", "k2", "t2"),
            _rel("part_of", "k3", "t2"),
            _rel("part_of", "t2", "t1"),
        ),
    )


# --- 派生查询 -------------------------------------------------------------


def test_order_follows_requires_and_keeps_declaration_order() -> None:
    """顺序由 requires 决定；并列知识点保持声明顺序，因此确定。"""
    structure = _js_structure()

    assert [point.id.value for point in structure.order()] == [
        "k1",
        "k4",
        "t2",
        "t1",
        "k2",
        "k3",
    ]


def test_part_of_does_not_affect_learning_order() -> None:
    """主题层级不是学习顺序：t2 依赖 t1 才改变顺序，part_of 不改变。"""
    without_hierarchy = KnowledgeStructure(
        points=(_kp("a", "A"), _kp("b", "B")),
    )
    with_hierarchy = KnowledgeStructure(
        points=(_kp("a", "A"), _kp("b", "B")),
        relations=(_rel("part_of", "b", "a"),),
    )

    assert [p.id.value for p in with_hierarchy.order()] == [
        p.id.value for p in without_hierarchy.order()
    ]


def test_entry_points_are_points_without_prerequisites() -> None:
    """学习起点 = 没有前置的知识点。"""
    structure = _js_structure()

    assert [point.id.value for point in structure.entry_points()] == [
        "k1",
        "k4",
        "t2",
        "t1",
    ]


def test_prerequisites_of_returns_direct_prerequisites_in_declaration_order() -> None:
    """直接前置只取一跳，顺序与声明一致。"""
    structure = KnowledgeStructure(
        points=(_kp("k3", "高阶函数"), _kp("k2", "闭包"), _kp("k1", "函数与作用域")),
        relations=(
            _rel("requires", "k3", "k2"),
            _rel("requires", "k3", "k1"),
            _rel("requires", "k2", "k1"),
        ),
    )

    assert [p.id.value for p in structure.prerequisites_of(KnowledgePointId("k3"))] == [
        "k2",
        "k1",
    ]


def test_prerequisite_closure_is_transitive_and_ordered() -> None:
    """传递前置按学习顺序返回，不含自身——补课顺序。"""
    structure = _js_structure()

    assert [point.id.value for point in structure.prerequisite_closure(KnowledgePointId("k3"))] == [
        "k1",
        "k2",
    ]


def test_dependent_closure_is_transitive_and_ordered() -> None:
    """传递后继按学习顺序返回，不含自身——影响范围。"""
    structure = _js_structure()

    assert [point.id.value for point in structure.dependent_closure(KnowledgePointId("k1"))] == [
        "k2",
        "k3",
    ]


def test_hierarchy_queries() -> None:
    """主题层级：父节点与直接子节点。"""
    structure = _js_structure()

    parent = structure.parent_of(KnowledgePointId("k2"))
    assert parent is not None
    assert parent.id.value == "t2"
    assert structure.parent_of(KnowledgePointId("t1")) is None
    assert [p.id.value for p in structure.children_of(KnowledgePointId("t2"))] == [
        "k3",
        "k2",
    ]


def test_point_lookup() -> None:
    """按 id 取知识点；不存在时失败而不是返回 None。"""
    structure = _js_structure()

    assert structure.point(KnowledgePointId("k1")).title == "函数与作用域"
    with pytest.raises(DomainError, match="没有知识点 k9"):
        structure.point(KnowledgePointId("k9"))


@pytest.mark.parametrize("method", ["order", "entry_points"])
def test_queries_on_empty_structure_are_empty(method: str) -> None:
    """空结构没有知识点，也没有顺序。"""
    structure = KnowledgeStructure()

    assert getattr(structure, method)() == ()


def test_queries_reject_unknown_point() -> None:
    """所有需要知识点的查询都拒绝未知 id。"""
    structure = _js_structure()
    missing = KnowledgePointId("k9")

    with pytest.raises(DomainError, match="没有知识点 k9"):
        structure.prerequisites_of(missing)
    with pytest.raises(DomainError, match="没有知识点 k9"):
        structure.prerequisite_closure(missing)
    with pytest.raises(DomainError, match="没有知识点 k9"):
        structure.dependent_closure(missing)
    with pytest.raises(DomainError, match="没有知识点 k9"):
        structure.parent_of(missing)
    with pytest.raises(DomainError, match="没有知识点 k9"):
        structure.children_of(missing)


# --- 不变量 ---------------------------------------------------------------


def test_knowledge_point_rejects_blank_text() -> None:
    """知识点名称与摘要都不能是空白。"""
    with pytest.raises(DomainError, match="知识点名称不能为空"):
        KnowledgePoint(id=KnowledgePointId("k1"), title=" ")
    with pytest.raises(DomainError, match="知识点摘要不能为空"):
        KnowledgePoint(id=KnowledgePointId("k1"), title="函数", summary=" ")


def test_duplicate_knowledge_point_ids_are_rejected() -> None:
    """知识点 id 在结构内唯一。"""
    with pytest.raises(DomainError, match="知识点 id 重复：k1"):
        KnowledgeStructure(points=(_kp("k1", "函数"), _kp("k1", "作用域")))


def test_relation_endpoint_must_exist() -> None:
    """关系两端都必须是结构内的知识点。"""
    with pytest.raises(DomainError, match="引用了不存在的知识点：k9"):
        KnowledgeStructure(
            points=(_kp("k1", "函数"),),
            relations=(_rel("requires", "k1", "k9"),),
        )
    with pytest.raises(DomainError, match="引用了不存在的知识点：t9"):
        KnowledgeStructure(
            points=(_kp("k1", "函数"),),
            relations=(_rel("part_of", "k1", "t9"),),
        )


def test_self_relation_is_rejected() -> None:
    """知识点不能与自己建立关系。"""
    with pytest.raises(DomainError, match="requires 关系不能指向自身"):
        _rel("requires", "k1", "k1")


def test_duplicate_relation_is_rejected() -> None:
    """同一条关系重复声明是建模错误，不静默去重。"""
    with pytest.raises(DomainError, match="重复的关系：requires k1 -> k2"):
        KnowledgeStructure(
            points=(_kp("k1", "A"), _kp("k2", "B")),
            relations=(
                _rel("requires", "k1", "k2"),
                _rel("requires", "k1", "k2"),
            ),
        )


def test_requires_cycle_reports_path() -> None:
    """requires 成环时错误信息给出环本身，而不是只说「有环」。"""
    with pytest.raises(DomainError, match="requires 依赖成环：k1 -> k2 -> k1"):
        KnowledgeStructure(
            points=(_kp("k1", "A"), _kp("k2", "B")),
            relations=(
                _rel("requires", "k1", "k2"),
                _rel("requires", "k2", "k1"),
            ),
        )


def test_part_of_must_be_a_tree() -> None:
    """一个知识点至多一个 part_of 父节点——层级要能回答「归到哪个主题」。"""
    with pytest.raises(DomainError, match="有多个 part_of 父节点：t1、t2"):
        KnowledgeStructure(
            points=(_kp("k1", "闭包"), _kp("t1", "语言"), _kp("t2", "范式")),
            relations=(
                _rel("part_of", "k1", "t1"),
                _rel("part_of", "k1", "t2"),
            ),
        )


def test_part_of_cycle_is_rejected() -> None:
    """part_of 层级也必须无环。"""
    with pytest.raises(DomainError, match="part_of 层级成环") as captured:
        KnowledgeStructure(
            points=(_kp("a", "A"), _kp("b", "B")),
            relations=(
                _rel("part_of", "a", "b"),
                _rel("part_of", "b", "a"),
            ),
        )

    message = str(captured.value)
    assert "a" in message and "b" in message


def test_requires_and_part_of_are_validated_independently() -> None:
    """两种关系各自校验：requires 无环不代表 part_of 合法，反之亦然。"""
    structure = KnowledgeStructure(
        points=(_kp("a", "A"), _kp("b", "B"), _kp("t", "主题")),
        relations=(
            _rel("requires", "a", "b"),
            _rel("part_of", "a", "t"),
            _rel("part_of", "b", "t"),
        ),
    )

    assert [p.id.value for p in structure.order()] == ["b", "t", "a"]
    assert [p.id.value for p in structure.children_of(KnowledgePointId("t"))] == [
        "a",
        "b",
    ]


def test_structure_is_immutable_and_value_compared() -> None:
    """结构是值对象：内容相同即相等，改动只能重建。"""
    first = KnowledgeStructure(points=(_kp("k1", "函数"),))
    second = KnowledgeStructure(points=(_kp("k1", "函数"),))

    assert first == second
    with pytest.raises(AttributeError):
        first.points = ()  # type: ignore[misc]
