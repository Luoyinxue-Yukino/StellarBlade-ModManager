"""分组功能在界面层的冒烟测试。

直接替换 ``context.scan_mods`` 返回构造好的 Mod，避免为了测渲染去铺一整套
库目录结构——渲染逻辑和磁盘扫描是两件事，混在一起测会互相拖累。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from stellar_mod_manager.core.config import AppConfig
from stellar_mod_manager.core.groups import UNGROUPED_LABEL
from stellar_mod_manager.core.models import Mod
from stellar_mod_manager.ui.main_window import MainWindow
from stellar_mod_manager.ui.theme import get_palette, stylesheet
from stellar_mod_manager.ui.widgets.group_row import GroupRow
from stellar_mod_manager.ui.widgets.mod_card import ModCard


def _mod(name: str, *, deployed: bool = False) -> Mod:
    return Mod(name=name, in_library=True, deployed=deployed)


@pytest.fixture
def library_window(qapp: QApplication, fake_game: Path, isolated_app_data: Path):
    """一个装有 4 个假 Mod 的主窗口。"""
    config = AppConfig(game_root=str(fake_game), theme="dark")
    qapp.setStyleSheet(stylesheet(get_palette(config.theme)))
    win = MainWindow(config)

    mods = [_mod("Dress A", deployed=True), _mod("Dress B"), _mod("Sword X"), _mod("Tool Z")]
    win.context.scan_mods = lambda: list(mods)
    page = win._pages["library"]
    page.refresh()
    yield win
    win.close()


def _rows(page) -> list:
    """按顺序取出列表里的分组行与 Mod 卡片。"""
    out = []
    for index in range(page.list_layout.count()):
        widget = page.list_layout.itemAt(index).widget()
        if isinstance(widget, (GroupRow, ModCard)):
            out.append(widget)
    return out


def _group_rows(page) -> list[GroupRow]:
    return [w for w in _rows(page) if isinstance(w, GroupRow)]


# ---------------------------------------------------------------------------


def test_default_groups_are_created_on_first_refresh(library_window) -> None:
    groups = library_window.context.groups
    assert [g.name for g in groups.roots()] == ["服装", "武器", "玩法", "其他"]


def test_tree_renders_groups_and_ungrouped(library_window) -> None:
    page = library_window._pages["library"]
    names = [row.name_label.text() for row in _group_rows(page)]
    assert "服装" in names
    # 未分组永远在最后
    assert names[-1] == UNGROUPED_LABEL


def test_ungrouped_count_matches_unassigned_mods(library_window) -> None:
    page = library_window._pages["library"]
    ungrouped = [r for r in _group_rows(page) if r.name_label.text() == UNGROUPED_LABEL]
    assert ungrouped and ungrouped[0].count_badge.text() == "4"


def test_assigned_mod_moves_under_its_group(library_window) -> None:
    page = library_window._pages["library"]
    groups = library_window.context.groups
    dress = groups.find_by_name("服装")
    assert dress is not None
    groups.assign("Dress A", dress.id)
    page._apply_filter()

    rows = _rows(page)
    dress_index = next(
        i for i, w in enumerate(rows) if isinstance(w, GroupRow) and w.group_id == dress.id
    )
    card_index = next(
        i for i, w in enumerate(rows) if isinstance(w, ModCard) and w.mod.name == "Dress A"
    )
    # 卡片必须排在自己的分组行之后、下一个分组行之前
    following = [i for i, w in enumerate(rows) if isinstance(w, GroupRow) and i > dress_index]
    assert dress_index < card_index < (following[0] if following else len(rows))
    # 计数跟着归属走：只放了 Dress A 进去
    assert rows[dress_index].count_badge.text() == "1"


def test_collapsing_a_group_hides_its_mods(library_window) -> None:
    page = library_window._pages["library"]
    groups = library_window.context.groups
    dress = groups.find_by_name("服装")
    assert dress is not None
    groups.assign_many(["Dress A", "Dress B"], dress.id)
    page._apply_filter()
    assert any(isinstance(w, ModCard) and w.mod.name == "Dress A" for w in _rows(page))

    groups.set_collapsed(dress.id, True)
    page._apply_filter()
    assert not any(isinstance(w, ModCard) and w.mod.name == "Dress A" for w in _rows(page))
    # 分组行本身还在，只是收起来了
    assert any(isinstance(w, GroupRow) and w.group_id == dress.id for w in _rows(page))


def test_search_expands_collapsed_groups(library_window) -> None:
    """搜索时必须强制展开：否则命中项被折叠藏起来，用户会以为搜不到。"""
    page = library_window._pages["library"]
    groups = library_window.context.groups
    dress = groups.find_by_name("服装")
    assert dress is not None
    groups.assign("Dress A", dress.id)
    groups.set_collapsed(dress.id, True)

    page.search_edit.setText("Dress A")
    page._apply_filter()
    assert any(isinstance(w, ModCard) and w.mod.name == "Dress A" for w in _rows(page))


def test_search_hides_groups_without_matches(library_window) -> None:
    page = library_window._pages["library"]
    page.search_edit.setText("Sword")
    page._apply_filter()
    labels = [row.name_label.text() for row in _group_rows(page)]
    # 有命中的「未分组」保留，空分组不显示
    assert labels == [UNGROUPED_LABEL]


def test_nested_group_indents_deeper_than_parent(library_window) -> None:
    page = library_window._pages["library"]
    groups = library_window.context.groups
    dress = groups.find_by_name("服装")
    assert dress is not None
    sub = groups.create("连衣裙", dress.id)
    groups.assign("Dress A", sub.id)
    page._apply_filter()

    rows = _rows(page)
    parent = next(w for w in rows if isinstance(w, GroupRow) and w.group_id == dress.id)
    child = next(w for w in rows if isinstance(w, GroupRow) and w.group_id == sub.id)
    card = next(w for w in rows if isinstance(w, ModCard) and w.mod.name == "Dress A")

    def left(widget) -> int:
        return widget.layout().contentsMargins().left()

    assert left(parent) < left(child) < left(card)


def test_move_mod_through_the_page(library_window) -> None:
    page = library_window._pages["library"]
    groups = library_window.context.groups
    weapon = groups.find_by_name("武器")
    assert weapon is not None

    page._on_move_mod(_mod("Dress A"), weapon.id)
    assert groups.group_of("Dress A") == weapon.id

    page._on_move_mod(_mod("Dress A"), None)
    assert groups.group_of("Dress A") is None


def test_move_targets_include_ungrouped_and_nesting(library_window) -> None:
    page = library_window._pages["library"]
    groups = library_window.context.groups
    dress = groups.find_by_name("服装")
    assert dress is not None
    groups.create("连衣裙", dress.id)

    targets = page._group_move_targets()
    assert targets[0] == (None, UNGROUPED_LABEL, 0)
    by_name = {label: depth for _gid, label, depth in targets}
    assert by_name["服装"] == 1
    assert by_name["连衣裙"] == 2


def test_collapse_state_survives_a_rerender(library_window) -> None:
    """折叠是持久化偏好：重绘之后仍然是收起的。"""
    page = library_window._pages["library"]
    groups = library_window.context.groups
    dress = groups.find_by_name("服装")
    assert dress is not None
    groups.assign("Dress A", dress.id)

    page._on_group_toggled(dress.id, True)
    assert groups.get(dress.id).collapsed is True
    assert not any(isinstance(w, ModCard) and w.mod.name == "Dress A" for w in _rows(page))

    page._on_group_toggled(dress.id, False)
    assert groups.get(dress.id).collapsed is False
    assert any(isinstance(w, ModCard) and w.mod.name == "Dress A" for w in _rows(page))


def test_ungrouped_collapse_is_independent(library_window) -> None:
    """「未分组」是虚拟分组，折叠状态只存在页面里，不写进 groups.json。"""
    page = library_window._pages["library"]
    assert any(isinstance(w, ModCard) for w in _rows(page))

    page._on_ungrouped_toggled("", True)
    assert page._ungrouped_collapsed is True
    assert not any(isinstance(w, ModCard) for w in _rows(page))
    # 存档里不该出现未分组这个伪分组
    assert all(g.name != UNGROUPED_LABEL for g in library_window.context.groups.all())

    page._on_ungrouped_toggled("", False)
    assert any(isinstance(w, ModCard) for w in _rows(page))


def test_stale_assignments_are_pruned_on_refresh(library_window) -> None:
    page = library_window._pages["library"]
    groups = library_window.context.groups
    dress = groups.find_by_name("服装")
    assert dress is not None
    groups.assign("早就删掉的 Mod", dress.id)

    page.refresh()
    assert groups.group_of("早就删掉的 Mod") is None


def test_delete_group_prompts_and_keeps_mods(
    library_window, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = library_window._pages["library"]
    groups = library_window.context.groups
    dress = groups.find_by_name("服装")
    assert dress is not None
    groups.assign_many(["Dress A", "Dress B"], dress.id)

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    page._delete_group(dress.id)

    assert groups.get(dress.id) is None
    # Mod 没被删，只是变回未分组
    assert groups.group_of("Dress A") is None
    assert {m.name for m in page._mods} >= {"Dress A", "Dress B"}


def test_delete_group_can_be_cancelled(
    library_window, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = library_window._pages["library"]
    groups = library_window.context.groups
    dress = groups.find_by_name("服装")
    assert dress is not None

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    page._delete_group(dress.id)
    assert groups.get(dress.id) is not None


def test_populated_library_shows_tree_not_empty_state(library_window) -> None:
    page = library_window._pages["library"]
    assert not _group_rows(page) == []
    assert not page.scroll.isHidden()


# ---------------------------------------------------------------------------
# 批量操作的接线（回归）
# ---------------------------------------------------------------------------


def _capture_tasks(window, monkeypatch) -> list:
    """拦住 TaskManager.start，只记录任务，不真的起线程。"""
    started: list = []

    def fake_start(task):
        started.append(task)
        return task

    monkeypatch.setattr(window.context.tasks, "start", fake_start)
    return started


def test_auto_classify_starts_a_task(library_window, monkeypatch) -> None:
    """回归：曾把标签当成第二个位置参数传给 tasks.start()，一按按钮就 TypeError。

    标签属于 ``Task(label=...)``，不是 ``start()`` 的参数。
    """
    page = library_window._pages["library"]
    started = _capture_tasks(library_window, monkeypatch)

    page._auto_classify()
    assert len(started) == 1


def test_auto_classify_with_nothing_pending_does_not_start_a_task(
    library_window, monkeypatch
) -> None:
    page = library_window._pages["library"]
    groups = library_window.context.groups
    for mod in page._mods:
        groups.assign(mod.name, groups.roots()[0].id)

    started = _capture_tasks(library_window, monkeypatch)
    page._auto_classify()
    assert started == []


def test_group_toggle_starts_a_task(library_window, monkeypatch) -> None:
    """整组启停同样要经过 Task，别再写错签名。"""
    page = library_window._pages["library"]
    groups = library_window.context.groups
    dress = groups.find_by_name("服装")
    assert dress is not None
    groups.assign_many(["Dress A", "Dress B"], dress.id)

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    started = _capture_tasks(library_window, monkeypatch)

    page._set_group_enabled(dress.id, True)
    assert len(started) == 1


def test_group_toggle_reports_nothing_to_do(library_window, monkeypatch) -> None:
    page = library_window._pages["library"]
    groups = library_window.context.groups
    empty = groups.find_by_name("玩法")
    assert empty is not None

    started = _capture_tasks(library_window, monkeypatch)
    page._set_group_enabled(empty.id, True)
    assert started == []


def test_classify_preview_receives_grouped_result(library_window, monkeypatch) -> None:
    """分类任务成功后要把结果交给预览对话框，而不是直接落盘。"""
    page = library_window._pages["library"]
    captured: dict = {}

    class _FakeDialog:
        Accepted = 1

        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        def exec(self):
            return 0  # 用户取消

    monkeypatch.setattr(
        "stellar_mod_manager.ui.pages.library_page.ClassifyDialog", _FakeDialog
    )
    page._show_classify_preview({"服装": ["Dress A"], "武器": ["Sword X"]})
    assert captured["suggestions"] == {"服装": ["Dress A"], "武器": ["Sword X"]}
    assert captured["total"] == 2
    # 取消之后一个 Mod 都不该被归类
    assert library_window.context.groups.group_of("Dress A") is None
