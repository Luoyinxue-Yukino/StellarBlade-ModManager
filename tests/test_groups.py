"""分组树的单元测试。

重点覆盖那些「手工改坏文件也不能崩」的路径：悬空父级、循环引用、
失效归属记录——这些在真实使用里迟早会遇到。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stellar_mod_manager.core import paths
from stellar_mod_manager.core.groups import (
    DEFAULT_GROUPS,
    UNGROUPED_LABEL,
    GroupError,
    GroupTree,
    ModGroup,
)


@pytest.fixture
def tree(isolated_app_data: Path) -> GroupTree:
    """隔离应用数据目录下的空分组树。"""
    return GroupTree()


@pytest.fixture
def seeded(tree: GroupTree) -> GroupTree:
    tree.ensure_defaults()
    return tree


# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------


def test_ungrouped_label_is_stable() -> None:
    # 界面里直接显示这个字符串，改动会打乱截图与文档
    assert UNGROUPED_LABEL == "未分组"


def test_defaults_created_once(seeded: GroupTree) -> None:
    assert [g.name for g in seeded.roots()] == list(DEFAULT_GROUPS)
    # 第二次不该重复建
    assert seeded.ensure_defaults() is False
    assert len(seeded) == len(DEFAULT_GROUPS)


def test_create_trims_and_rejects_blank(seeded: GroupTree) -> None:
    group = seeded.create("  上装  ")
    assert group.name == "上装"
    with pytest.raises(GroupError, match="不能为空"):
        seeded.create("   ")


def test_duplicate_name_rejected_at_same_level(seeded: GroupTree) -> None:
    seeded.create("外套")
    with pytest.raises(GroupError, match="已经有叫"):
        seeded.create("外套")
    # 大小写不同也算重名
    with pytest.raises(GroupError, match="已经有叫"):
        seeded.create("外套".upper() if "外套".upper() != "外套" else "外套")
    # 不同层级可以同名
    parent = seeded.find_by_name("服装")
    assert parent is not None
    assert seeded.create("外套", parent.id).name == "外套"


def test_rename_validates(seeded: GroupTree) -> None:
    group = seeded.create("待定")
    other = seeded.create("已定")
    seeded.rename(group.id, "改好了")
    assert group.name == "改好了"
    with pytest.raises(GroupError, match="已经有叫"):
        seeded.rename(group.id, other.name)
    with pytest.raises(GroupError, match="不能为空"):
        seeded.rename(group.id, " ")
    with pytest.raises(GroupError, match="不存在"):
        seeded.rename("nope", "x")


# ---------------------------------------------------------------------------
# 层级
# ---------------------------------------------------------------------------


def test_nesting_and_breadcrumb(seeded: GroupTree) -> None:
    dress = seeded.find_by_name("服装")
    assert dress is not None
    top = seeded.create("上装", dress.id)
    inner = seeded.create("衬衫", top.id)

    assert top.parent_id == dress.id
    assert seeded.depth(top.id) == 1
    assert seeded.depth(inner.id) == 2
    assert seeded.breadcrumb(inner.id) == "服装 / 上装 / 衬衫"
    assert seeded.descendants(dress.id) == {top.id, inner.id}
    assert [g.id for g in seeded.children(dress.id)] == [top.id]


def test_move_reparents_and_keeps_order(seeded: GroupTree) -> None:
    dress = seeded.find_by_name("服装")
    weapon = seeded.find_by_name("武器")
    assert dress and weapon
    child = seeded.create("上装", dress.id)

    seeded.move(child.id, weapon.id)
    assert child.parent_id == weapon.id
    assert [g.id for g in seeded.children(weapon.id)] == [child.id]

    seeded.move(child.id, None)
    assert child.parent_id is None


def test_move_rejects_cycles(seeded: GroupTree) -> None:
    dress = seeded.find_by_name("服装")
    assert dress is not None
    top = seeded.create("上装", dress.id)

    with pytest.raises(GroupError, match="它自己里面"):
        seeded.move(dress.id, dress.id)
    with pytest.raises(GroupError, match="子分组里"):
        seeded.move(dress.id, top.id)
    # 顶层不能塞进自己
    with pytest.raises(GroupError, match="它自己里面"):
        seeded.move(top.id, top.id)


def test_move_rejects_duplicate_name(seeded: GroupTree) -> None:
    dress = seeded.find_by_name("服装")
    weapon = seeded.find_by_name("武器")
    assert dress and weapon
    seeded.create("同名", dress.id)
    mover = seeded.create("同名2")
    seeded.rename(mover.id, "同名")
    with pytest.raises(GroupError, match="已经有同名"):
        seeded.move(mover.id, dress.id)


def test_sorted_groups_is_parents_before_children(seeded: GroupTree) -> None:
    dress = seeded.find_by_name("服装")
    assert dress is not None
    seeded.create("上装", dress.id)
    order = [g.name for g in seeded.sorted_groups()]
    assert order.index("服装") < order.index("上装")
    assert len(order) == len(seeded)


# ---------------------------------------------------------------------------
# 删除
# ---------------------------------------------------------------------------


def test_delete_promotes_children_and_unassigns_mods(seeded: GroupTree) -> None:
    dress = seeded.find_by_name("服装")
    assert dress is not None
    child = seeded.create("上装", dress.id)
    seeded.assign("A", dress.id)
    seeded.assign("B", child.id)

    seeded.delete(dress.id)

    assert seeded.get(dress.id) is None
    # 子分组上提一级，结构不丢
    assert seeded.get(child.id).parent_id is None
    # 组内 Mod 变未分组，而不是被连带删除
    assert seeded.group_of("A") is None
    # 子分组里的 Mod 归属不变（它属于子分组，不属于被删的那个）
    assert seeded.group_of("B") == child.id


def test_delete_missing_raises(tree: GroupTree) -> None:
    with pytest.raises(GroupError, match="不存在"):
        tree.delete("nope")


def test_clear_wipes_everything(seeded: GroupTree) -> None:
    seeded.assign("A", seeded.roots()[0].id)
    seeded.clear()
    assert len(seeded) == 0
    assert seeded.ungrouped() == []


# ---------------------------------------------------------------------------
# 归属
# ---------------------------------------------------------------------------


def test_assign_and_ungroup(seeded: GroupTree) -> None:
    dress = seeded.find_by_name("服装")
    assert dress is not None
    seeded.assign("A", dress.id)
    assert seeded.group_of("A") == dress.id

    seeded.assign("A", None)
    assert seeded.group_of("A") is None
    # 再取消一次不应该报错
    seeded.assign("A", None)


def test_assign_to_missing_group_raises(seeded: GroupTree) -> None:
    with pytest.raises(GroupError, match="不存在"):
        seeded.assign("A", "ghost")


def test_assign_many_counts_only_real_changes(seeded: GroupTree) -> None:
    dress = seeded.find_by_name("服装")
    weapon = seeded.find_by_name("武器")
    assert dress and weapon

    assert seeded.assign_many(["A", "B", "C"], dress.id) == 3
    # 重复赋值同一个分组不算改动
    assert seeded.assign_many(["A", "B"], dress.id) == 0
    assert seeded.assign_many(["A"], weapon.id) == 1
    assert seeded.group_of("A") == weapon.id


def test_mods_in_is_recursive_on_demand(seeded: GroupTree) -> None:
    dress = seeded.find_by_name("服装")
    assert dress is not None
    child = seeded.create("上装", dress.id)
    seeded.assign("A", dress.id)
    seeded.assign("B", child.id)

    assert seeded.mods_in(dress.id) == {"A"}
    assert seeded.mods_in(dress.id, recursive=True) == {"A", "B"}


def test_ungrouped_respects_known_mods(seeded: GroupTree) -> None:
    dress = seeded.find_by_name("服装")
    assert dress is not None
    seeded.assign("A", dress.id)
    seeded.assign("幽灵", dress.id)

    # 传入已知 Mod 列表时，已不存在的「幽灵」不会出现
    assert seeded.ungrouped(["A", "C"]) == ["C"]
    # 归属指向已删除分组时也算未分组
    seeded.delete(dress.id)
    assert seeded.ungrouped(["A", "幽灵"]) == ["A", "幽灵"]


def test_counts_include_descendants(seeded: GroupTree) -> None:
    dress = seeded.find_by_name("服装")
    assert dress is not None
    child = seeded.create("上装", dress.id)
    seeded.assign("A", dress.id)
    seeded.assign("B", child.id)
    seeded.assign("C", child.id)

    counts = seeded.counts(["A", "B", "C"])
    assert counts[dress.id] == 3
    assert counts[child.id] == 2


def test_forget_mods_drops_stale_records(seeded: GroupTree) -> None:
    dress = seeded.find_by_name("服装")
    assert dress is not None
    seeded.assign_many(["A", "B", "C"], dress.id)

    assert seeded.forget_mods({"A"}) == 2
    assert seeded.group_of("B") is None
    assert seeded.group_of("A") == dress.id
    # 已经干净了就不该再写盘
    assert seeded.forget_mods({"A"}) == 0


# ---------------------------------------------------------------------------
# 持久化与容错
# ---------------------------------------------------------------------------


def test_round_trip_preserves_structure(tree: GroupTree) -> None:
    tree.ensure_defaults()
    dress = tree.find_by_name("服装")
    assert dress is not None
    child = tree.create("上装", dress.id)
    tree.assign("A", child.id)
    tree.set_collapsed(dress.id, True)

    reloaded = GroupTree(tree.path)
    assert reloaded.breadcrumb(child.id) == "服装 / 上装"
    assert reloaded.group_of("A") == child.id
    assert reloaded.get(dress.id).collapsed is True


def test_missing_file_starts_empty(tree: GroupTree) -> None:
    assert len(tree) == 0
    assert not tree.path.is_file()


def test_corrupt_file_falls_back_to_empty(isolated_app_data: Path) -> None:
    paths.groups_path().write_text("{ not json", encoding="utf-8")
    tree = GroupTree()
    assert len(tree) == 0


def test_non_object_root_falls_back(isolated_app_data: Path) -> None:
    paths.groups_path().write_text("[1, 2, 3]", encoding="utf-8")
    assert len(GroupTree()) == 0


def test_entries_missing_id_or_name_are_skipped(isolated_app_data: Path) -> None:
    paths.groups_path().write_text(
        json.dumps(
            {
                "version": 1,
                "groups": [
                    {"id": "ok", "name": "好的"},
                    {"id": "", "name": "没有 id"},
                    {"id": "x", "name": ""},
                    "不是对象",
                ],
                "assignments": {"A": "ok"},
            }
        ),
        encoding="utf-8",
    )
    tree = GroupTree()
    assert [g.name for g in tree.all()] == ["好的"]
    assert tree.group_of("A") == "ok"


def test_dangling_parent_becomes_root(isolated_app_data: Path) -> None:
    paths.groups_path().write_text(
        json.dumps(
            {
                "groups": [{"id": "a", "name": "孤儿", "parentId": "ghost"}],
                "assignments": {"A": "ghost"},
            }
        ),
        encoding="utf-8",
    )
    tree = GroupTree()
    assert tree.get("a").parent_id is None
    # 归属指向不存在的分组 → 视为未分组
    assert tree.group_of("A") is None


def test_cycle_in_file_is_broken(isolated_app_data: Path) -> None:
    paths.groups_path().write_text(
        json.dumps(
            {
                "groups": [
                    {"id": "a", "name": "甲", "parentId": "b"},
                    {"id": "b", "name": "乙", "parentId": "a"},
                ]
            }
        ),
        encoding="utf-8",
    )
    tree = GroupTree()
    # 不应死循环；遍历能正常结束
    assert len(tree.sorted_groups()) == 2
    assert tree.depth("a") < 5


def test_save_is_atomic_and_leaves_no_temp(seeded: GroupTree) -> None:
    seeded.create("触发一次写入")
    temp = seeded.path.with_suffix(seeded.path.suffix + ".tmp")
    assert not temp.exists()
    assert seeded.path.is_file()
    # 落盘内容是可读 JSON 且带版本号
    payload = json.loads(seeded.path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert isinstance(payload["groups"], list)


def test_new_sibling_orders_after_existing(tree: GroupTree) -> None:
    first = tree.create("甲")
    second = tree.create("乙")
    third = tree.create("丙")
    assert first.order < second.order < third.order
    assert [g.id for g in tree.roots()] == [first.id, second.id, third.id]


def test_collapsed_state_is_a_noop_when_unchanged(seeded: GroupTree) -> None:
    group = seeded.roots()[0]
    seeded.set_collapsed(group.id, False)  # 本来就是 False
    assert group.collapsed is False
    seeded.set_collapsed("ghost", True)  # 不存在也不该抛
    assert seeded.get("ghost") is None


def test_group_is_root_property() -> None:
    assert ModGroup(id="a", name="A").is_root is True
    assert ModGroup(id="a", name="A", parent_id="b").is_root is False
