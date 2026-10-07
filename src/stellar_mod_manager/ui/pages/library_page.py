"""Mod 库页：查看、筛选、启用/停用、卸载已安装的 Mod。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core import classify
from ...core.formatting import human_size
from ...core.groups import UNGROUPED_LABEL, GroupError
from ...core.library import LibraryError
from ...core.models import Mod
from ..dialogs.classify_dialog import ClassifyDialog
from ..dialogs.group_dialog import GroupNameDialog
from ..dialogs.translate_dialog import TranslateDialog
from ..icons import icon
from ..theme import SPACE_MD, SPACE_SM, banner_style
from ...services.worker import Task
from ..widgets.common import EmptyState, StatCard, button
from ..widgets.group_row import INDENT_STEP, GroupRow
from ..widgets.mod_card import ModCard
from .base import Page

from ...logging_setup import get_logger
logger = get_logger(__name__)

_FILTERS = (
    ("全部", "all"),
    ("已启用", "enabled"),
    ("仅库中", "stored"),
    ("未纳管", "unmanaged"),
    ("Pak 格式", "pak"),
    ("IoStore 格式", "iostore"),
)


class LibraryPage(Page):
    title = "Mod 库"
    subtitle = "管理已安装的 Mod"

    def __init__(self, context, colors, parent: QWidget | None = None) -> None:
        super().__init__(context, colors, parent)
        self._mods: list[Mod] = []
        self._cards: list[ModCard] = []
        self._ungrouped_collapsed = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        root.addLayout(self._build_stats())
        root.addLayout(self._build_toolbar())
        root.addWidget(self._build_unmanaged_banner())

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 10, 20)
        # 行间距必须明显大于行内文字间距（卡片内部只有 1px），
        # 否则每一行读不出边界、整列糊成一堵墙——这是列表显得呆板的主因。
        self.list_layout.setSpacing(SPACE_SM)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.list_host)
        root.addWidget(self.scroll, 1)

        self.empty = EmptyState(
            colors,
            icon_name="library",
            title="还没有安装任何 Mod",
            description="到「导入安装」页把 Mod 压缩包拖进来，或者直接把 .pak 文件放进 ~mods 目录。",
            action_text="去导入 Mod",
        )
        self.empty.setVisible(False)
        root.addWidget(self.empty, 1)

        context.game_root_changed.connect(self.refresh)
        context.mods_changed.connect(self.refresh)
        context.groups_changed.connect(self._apply_filter)

    # ------------------------------------------------------------------
    # 顶部
    # ------------------------------------------------------------------

    def _build_stats(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(SPACE_MD)

        self.stat_total = StatCard("库中 Mod", self.colors, icon_name="library", color_role="accent")
        self.stat_enabled = StatCard("已启用", self.colors, icon_name="check", color_role="success")
        self.stat_stored = StatCard("仅存库中", self.colors, icon_name="pause", color_role="text_faint")
        self.stat_size = StatCard("实际占用", self.colors, icon_name="archive", color_role="text_muted")

        for card in (self.stat_total, self.stat_enabled, self.stat_stored, self.stat_size):
            row.addWidget(card, 1)
        return row

    def _build_unmanaged_banner(self) -> QWidget:
        """提示「只在游戏目录里、还没进库」的 Mod。

        这类 Mod 是你手动放进 ``~mods`` 的，管理器不认识它们，也就无法停用
        （停用会让文件无处可去）。纳入库之后才归管理器管。
        """
        self.banner = QFrame(self)
        self.banner.setVisible(False)
        warn = self.colors.qcolor("warning")
        self.banner.setStyleSheet(banner_style(self.colors, "warning"))

        row = QHBoxLayout(self.banner)
        row.setContentsMargins(14, 11, 14, 11)
        row.setSpacing(11)

        mark = QLabel(self.banner)
        mark.setPixmap(icon("alert", warn.name(), 20).pixmap(20, 20))
        row.addWidget(mark, 0, Qt.AlignTop)

        text = QLabel(self.banner)
        text.setWordWrap(True)
        text.setStyleSheet("font-size: 12px;")
        row.addWidget(text, 1)
        self.banner_text = text

        adopt_btn = button("全部纳入库", variant="primary", palette=self.colors, parent=self.banner)
        adopt_btn.clicked.connect(self._adopt_all)
        row.addWidget(adopt_btn, 0, Qt.AlignVCenter)

        return self.banner

    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(SPACE_SM)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索 Mod 名称…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_filter)
        # 用 min/max 而不是 setFixedWidth：窗口变窄时可以让出空间，
        # 固定宽度会撑破布局并把后面的控件挤到一起。
        self.search_edit.setMinimumWidth(180)
        self.search_edit.setMaximumWidth(240)
        row.addWidget(self.search_edit, 1)

        self.filter_combo = QComboBox()
        for text, key in _FILTERS:
            self.filter_combo.addItem(text, key)
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)
        self.filter_combo.setFixedWidth(120)
        row.addWidget(self.filter_combo)

        row.addSpacing(SPACE_MD)

        self.new_group_btn = button(
            "新建分组", icon_name="plus", palette=self.colors
        )
        self.new_group_btn.setToolTip("建一个分类（如「服装」「武器」），再把 Mod 拖进去")
        self.new_group_btn.clicked.connect(lambda: self._create_group(None))
        row.addWidget(self.new_group_btn)

        self.classify_btn = button(
            "自动分类", icon_name="tag", palette=self.colors
        )
        self.classify_btn.setToolTip(
            "读取每个 Mod 内部的资源清单，猜出它属于哪一类。\n"
            "只处理尚未分组的 Mod，结果先给你确认。"
        )
        self.classify_btn.clicked.connect(self._auto_classify)
        row.addWidget(self.classify_btn)

        row.addStretch(1)

        self.check_btn = button(
            "冲突检测", variant="primary", icon_name="shield", palette=self.colors
        )
        self.check_btn.setToolTip(
            "调用 DekPakModAudit 检查 Mod 之间是否存在资源重叠或 ChunkID 冲突。\n"
            "ChunkID 冲突会导致游戏无法启动。"
        )
        self.check_btn.clicked.connect(self.context.request_conflict_check)
        row.addWidget(self.check_btn)

        self.refresh_btn = button("刷新", icon_name="refresh", palette=self.colors)
        self.refresh_btn.clicked.connect(self.refresh)
        row.addWidget(self.refresh_btn)

        self.open_dir_btn = button("打开 ~mods", icon_name="external", palette=self.colors)
        self.open_dir_btn.clicked.connect(self._open_mods_dir)
        row.addWidget(self.open_dir_btn)

        self.open_library_btn = button("打开库目录", icon_name="folder", palette=self.colors)
        self.open_library_btn.setToolTip("Mod 的权威副本都在这里，游戏不会加载它")
        self.open_library_btn.clicked.connect(self._open_library_dir)
        row.addWidget(self.open_library_btn)

        return row

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------

    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        self._mods = self.context.scan_mods()
        self._sync_groups()
        self._update_stats()
        self._update_banner()
        self._apply_filter()
        self.check_btn.setEnabled(self.context.is_ready and bool(self._mods))
        self.open_dir_btn.setEnabled(self.context.is_ready)

    def _sync_groups(self) -> None:
        """扫描之后和分组对账。

        首次使用时建好默认分类；把已经不在库里的 Mod 的归属记录清掉，
        否则 ``groups.json`` 会随着增删 Mod 膨胀。

        **扫描结果为空时绝不做清理。** 那通常意味着游戏目录暂时不可用、盘符掉线或
        配置被改坏，而不是 Mod 真的都没了——此时清空会让用户手工整理的分级全丢。
        宁可留几条失效记录，也不能冒这个险。
        """
        groups = self.context.groups
        if groups.ensure_defaults():
            logger.info("首次使用，已创建默认分组")

        known = {mod.name for mod in self._mods}
        if not known:
            logger.warning("本次扫描没有找到任何 Mod，跳过分组归属清理以免误删")
            return
        removed = groups.forget_mods(known)
        if removed:
            logger.info("清理了 %d 条失效的分组归属记录", removed)

    def _update_banner(self) -> None:
        unmanaged = [m for m in self._mods if not m.in_library]
        if not unmanaged:
            self.banner.setVisible(False)
            return

        names = "、".join(m.name for m in unmanaged[:3])
        more = f" 等 {len(unmanaged)} 个" if len(unmanaged) > 3 else ""
        self.banner_text.setText(
            f"<b>{len(unmanaged)} 个 Mod 只在游戏目录里，尚未纳入库</b><br>"
            f"{names}{more}。纳入库后管理器才能停用或卸载它们"
            f"（同盘使用硬链接，<b>不会多占空间</b>）。"
        )
        self.banner.setVisible(True)

    def _update_stats(self) -> None:
        in_library = sum(1 for m in self._mods if m.in_library)
        deployed = sum(1 for m in self._mods if m.deployed)
        stored = sum(1 for m in self._mods if m.in_library and not m.deployed)
        # 硬链接不重复计数，所以这里是真实磁盘占用而不是简单相加
        usage = sum(m.disk_usage for m in self._mods)

        self.stat_total.set_value(str(in_library))
        self.stat_enabled.set_value(str(deployed))
        self.stat_stored.set_value(str(stored))
        self.stat_size.set_value(human_size(usage))

        total = len(self._mods)
        unmanaged = total - in_library
        self.stat_total.title_label.setText(
            f"库中 Mod（另有 {unmanaged} 个未纳管）" if unmanaged else "库中 Mod"
        )

    def _visible_mods(self) -> list[Mod]:
        needle = self.search_edit.text().strip().lower()
        key = self.filter_combo.currentData() or "all"

        # 用户可以选择隐藏未启用的 Mod
        show_idle = self.context.config.show_disabled_mods
        result: list[Mod] = []
        for mod in self._mods:
            if not show_idle and not mod.deployed:
                continue
            if key == "enabled" and not mod.deployed:
                continue
            if key == "stored" and not (mod.in_library and not mod.deployed):
                continue
            if key == "unmanaged" and mod.in_library:
                continue
            if key == "pak" and mod.is_iostore:
                continue
            if key == "iostore" and not mod.is_iostore:
                continue
            if needle and needle not in mod.name.lower():
                continue
            result.append(mod)
        return result

    def _apply_filter(self) -> None:
        self._clear_cards()
        mods = self._visible_mods()

        ready = self.context.is_ready
        if not ready:
            self._show_empty(
                "game",
                "还没有配置游戏目录",
                "先在「设置」里指定《剑星》的安装位置，才能读取 ~mods 目录。",
                "去设置",
            )
            return
        if not self._mods:
            self._show_empty(
                "library",
                "还没有安装任何 Mod",
                "到「导入安装」页把 Mod 压缩包拖进来，或直接把 .pak 文件放进 ~mods。",
                "去导入 Mod",
            )
            return
        if not mods:
            self._show_empty(
                "search", "没有匹配的 Mod", "换个关键词或筛选条件试试。", ""
            )
            return

        self._hide_empty()
        self._render_tree(mods)

    # ------------------------------------------------------------------
    # 层级渲染
    # ------------------------------------------------------------------

    def _group_move_targets(self) -> list[tuple[str | None, str, int]]:
        """给 Mod 卡片的「移动到分组」菜单准备条目。"""
        groups = self.context.groups
        targets: list[tuple[str | None, str, int]] = [
            (None, UNGROUPED_LABEL, 0)
        ]
        for group in groups.sorted_groups():
            targets.append((group.id, group.name, groups.depth(group.id) + 1))
        return targets

    def _is_filtering(self) -> bool:
        """搜索或筛选生效时，强制展开所有分组——否则匹配项被折叠藏起来，
        用户会以为「搜不到」。"""
        return bool(self.search_edit.text().strip()) or (
            (self.filter_combo.currentData() or "all") != "all"
        )

    def _render_tree(self, mods: list[Mod]) -> None:
        groups = self.context.groups
        by_group: dict[str | None, list[Mod]] = {}
        for mod in mods:
            by_group.setdefault(groups.group_of(mod.name), []).append(mod)

        filtering = self._is_filtering()
        targets = self._group_move_targets()

        def visible_count(group_id: str) -> int:
            total = len(by_group.get(group_id, []))
            for child in groups.children(group_id):
                total += visible_count(child.id)
            return total

        def insert(widget: QWidget) -> None:
            self.list_layout.insertWidget(self.list_layout.count() - 1, widget)

        def render_group(group, depth: int) -> None:
            count = visible_count(group.id)
            # 搜索/筛选时只留下真有匹配项的分组，避免一屏空壳子
            if filtering and count == 0:
                return
            children = groups.children(group.id)
            collapsed = group.collapsed and not filtering

            row = GroupRow(
                group_id=group.id,
                name=group.name,
                count=count,
                depth=depth,
                collapsed=collapsed,
                palette=self.colors,
                has_children=bool(children),
                parent=self.list_host,
            )
            self._wire_group_row(row)
            insert(row)

            if collapsed:
                return
            for child in children:
                render_group(child, depth + 1)
            # Mod 缩进到父分组名称的右边再退一格，层级方向才是自上而下
            indent = GroupRow.content_indent(depth) + INDENT_STEP
            for mod in by_group.get(group.id, []):
                insert(self._build_card(mod, indent, targets, group.id))

        for root in groups.roots():
            render_group(root, 0)

        # 未分组永远排在最后：它是「还没归类」的收容所，不是分类之一
        leftovers = by_group.get(None, [])
        if leftovers or not filtering:
            row = GroupRow(
                group_id=None,
                name=UNGROUPED_LABEL,
                count=len(leftovers),
                depth=0,
                collapsed=self._ungrouped_collapsed and not filtering,
                palette=self.colors,
                has_children=bool(leftovers),
                parent=self.list_host,
            )
            row.toggled.connect(self._on_ungrouped_toggled)
            insert(row)
            if not self._ungrouped_collapsed or filtering:
                indent = GroupRow.content_indent(0) + INDENT_STEP
                for mod in leftovers:
                    insert(self._build_card(mod, indent, targets, None))

    def _build_card(
        self,
        mod: Mod,
        indent: int,
        targets: list[tuple[str | None, str, int]],
        group_id: str | None,
    ) -> ModCard:
        card = ModCard(
            mod,
            self.colors,
            self.list_host,
            indent=indent,
            move_targets=targets,
            current_group=group_id,
        )
        card.selected.connect(self._on_selected)
        card.toggle_requested.connect(self._on_toggle)
        card.uninstall_requested.connect(self._on_uninstall)
        card.reveal_requested.connect(self._on_reveal)
        card.adopt_requested.connect(self._on_adopt)
        card.translate_requested.connect(self._on_translate)
        card.move_requested.connect(self._on_move_mod)
        self._cards.append(card)
        return card

    def _wire_group_row(self, row: GroupRow) -> None:
        row.toggled.connect(self._on_group_toggled)
        row.rename_requested.connect(self._rename_group)
        row.add_child_requested.connect(lambda gid: self._create_group(gid))
        row.delete_requested.connect(self._delete_group)
        row.enable_all_requested.connect(lambda gid: self._set_group_enabled(gid, True))
        row.disable_all_requested.connect(lambda gid: self._set_group_enabled(gid, False))
        row.move_requested.connect(self._move_group_dialog)

    # ------------------------------------------------------------------
    # 分组操作
    # ------------------------------------------------------------------

    def _create_group(self, parent_id: str | None) -> None:
        groups = self.context.groups
        parent = groups.get(parent_id)
        label = f"在「{parent.name}」下新建子分组：" if parent else "新建分组："
        taken = {g.name for g in groups.children(parent_id)}
        name = GroupNameDialog.ask(
            self.colors,
            title="新建分组",
            label_text=label,
            taken=taken,
            parent=self,
        )
        if not name:
            return
        try:
            group = groups.create(name, parent_id)
        except GroupError as exc:
            self.context.notify(str(exc), "error")
            return
        logger.info("新建分组「%s」", group.name)
        self._after_groups_changed(f"已新建分组「{group.name}」")

    def _rename_group(self, group_id: str) -> None:
        groups = self.context.groups
        group = groups.get(group_id)
        if group is None:
            return
        taken = {g.name for g in groups.siblings(group_id)}
        name = GroupNameDialog.ask(
            self.colors,
            title="重命名分组",
            label_text="新的分组名：",
            initial=group.name,
            taken=taken,
            parent=self,
        )
        if not name or name == group.name:
            return
        try:
            groups.rename(group_id, name)
        except GroupError as exc:
            self.context.notify(str(exc), "error")
            return
        self._after_groups_changed(f"已重命名为「{name}」")

    def _delete_group(self, group_id: str) -> None:
        groups = self.context.groups
        group = groups.get(group_id)
        if group is None:
            return
        count = len(groups.mods_in(group_id, recursive=True))
        detail = (
            f"组内 {count} 个 Mod 会变成「{UNGROUPED_LABEL}」，子分组上提一级。"
            if count
            else "子分组会上提一级。"
        )
        answer = QMessageBox.question(
            self,
            "删除分组",
            f"确定删除分组「{group.name}」？\n\n{detail}\n"
            "Mod 文件本身不会被删除，也不会有任何移动。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        groups.delete(group_id)
        logger.info("删除分组「%s」", group.name)
        self._after_groups_changed(f"已删除分组「{group.name}」")

    def _move_group_dialog(self, group_id: str) -> None:
        """把分组挪到别的分组下（选择「顶层」即移出）。"""
        groups = self.context.groups
        group = groups.get(group_id)
        if group is None:
            return
        blocked = groups.descendants(group_id) | {group_id}
        options: list[tuple[str | None, str]] = [(None, "顶层")]
        for candidate in groups.sorted_groups():
            if candidate.id in blocked:
                continue
            options.append(
                (candidate.id, "　" * groups.depth(candidate.id) + candidate.name)
            )

        menu = QMenu(self)
        for target_id, label in options:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(target_id == group.parent_id)
            action.triggered.connect(
                lambda _c=False, tid=target_id: self._move_group(group_id, tid)
            )
        menu.exec(self.cursor().pos())

    def _move_group(self, group_id: str, parent_id: str | None) -> None:
        try:
            self.context.groups.move(group_id, parent_id)
        except GroupError as exc:
            self.context.notify(str(exc), "error")
            return
        self._after_groups_changed("已移动分组")

    def _on_group_toggled(self, group_id: str, collapsed: bool) -> None:
        if not group_id:
            return
        self.context.groups.set_collapsed(group_id, collapsed)
        self._apply_filter()

    def _on_ungrouped_toggled(self, _group_id: str, collapsed: bool) -> None:
        self._ungrouped_collapsed = collapsed
        self._apply_filter()

    def _set_group_enabled(self, group_id: str, enabled: bool) -> None:
        """整组启用 / 停用。

        只处理库里有副本的 Mod——未纳管的停用后文件无处可去。
        """
        groups = self.context.groups
        names = groups.mods_in(group_id, recursive=True)
        targets = [m for m in self._mods if m.name in names and m.in_library]
        if not targets:
            self.context.notify("这个分组里没有可管理的 Mod", "warning")
            return
        pending = [m for m in targets if m.deployed != enabled]
        if not pending:
            self.context.notify(
                f"组内 Mod 已经全部{'启用' if enabled else '停用'}", "info"
            )
            return

        verb = "启用" if enabled else "停用"
        answer = QMessageBox.question(
            self,
            f"批量{verb}",
            f"要{verb}这 {len(pending)} 个 Mod 吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return
        self._run_group_toggle(pending, enabled, verb)

    def _run_group_toggle(self, mods: list[Mod], enabled: bool, verb: str) -> None:
        library = self.context.library

        def work(task: Task) -> object:
            done = 0
            for index, mod in enumerate(mods, start=1):
                task.report(index / len(mods), f"{verb} {mod.name}")
                if task.cancelled:
                    break
                try:
                    library.set_enabled(mod, enabled)
                except LibraryError as exc:
                    logger.warning("%s「%s」失败：%s", verb, mod.name, exc)
                    continue
                done += 1
            return done

        task = Task(work, self, label=f"批量{verb}")
        task.succeeded.connect(
            lambda done: self._after_group_toggle(done, verb, len(mods))
        )
        task.failed.connect(lambda msg: self.context.notify(msg, "error"))
        self.context.tasks.start(task)

    def _after_group_toggle(self, done: object, verb: str, total: int) -> None:
        self.context.notify(f"已{verb} {done}/{total} 个 Mod", "success")
        logger.info("批量%s：%d/%d", verb, done, total)
        self.context.mods_changed.emit()

    def _on_move_mod(self, mod: Mod, group_id: str | None) -> None:
        try:
            changed = self.context.groups.assign_many([mod.name], group_id)
        except GroupError as exc:
            self.context.notify(str(exc), "error")
            return
        if not changed:
            return
        target = self.context.groups.get(group_id)
        where = target.name if target else UNGROUPED_LABEL
        logger.info("把「%s」移到分组「%s」", mod.name, where)
        self._after_groups_changed(f"「{mod.name}」→ {where}")

    def _after_groups_changed(self, message: str = "") -> None:
        """分组改动后的统一收尾：通知、重绘、广播。"""
        if message:
            self.context.notify(message, "success")
        self._apply_filter()
        self.context.groups_changed.emit()

    # ------------------------------------------------------------------
    # 自动分类
    # ------------------------------------------------------------------

    def _auto_classify(self) -> None:
        """按 Mod 内容猜分类。只动尚未分组的 Mod，且先给用户确认。"""
        groups = self.context.groups
        pending = [m for m in self._mods if groups.group_of(m.name) is None]
        if not pending:
            self.context.notify("所有 Mod 都已经分好类了", "info")
            return

        def work(task: Task) -> object:
            result: dict[str, list[str]] = {}
            for index, mod in enumerate(pending, start=1):
                task.report(index / len(pending), f"识别 {mod.name}")
                if task.cancelled:
                    break
                # 只读 Mod 自己的文件（.utoc / .dekcns.json），不碰库，也不改任何东西
                result.setdefault(classify.classify(mod), []).append(mod.name)
            return result

        task = Task(work, self, label="识别 Mod 内容")
        task.succeeded.connect(lambda result: self._show_classify_preview(result))
        task.failed.connect(lambda msg: self.context.notify(msg, "error"))
        self.context.tasks.start(task)

    def _show_classify_preview(self, result: object) -> None:
        suggestions: dict[str, list[str]] = result if isinstance(result, dict) else {}
        if not suggestions:
            self.context.notify("没能识别出任何分类", "warning")
            return

        groups = self.context.groups
        existing = {g.name for g in groups.roots()}
        missing = {name for name in suggestions if name not in existing}

        dialog = ClassifyDialog(
            self.colors,
            suggestions=suggestions,
            missing_groups=missing,
            total=sum(len(v) for v in suggestions.values()),
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        self._apply_classification(suggestions, create_missing=dialog.should_create_missing)

    def _apply_classification(
        self, suggestions: dict[str, list[str]], *, create_missing: bool
    ) -> None:
        groups = self.context.groups
        by_name = {g.name: g for g in groups.roots()}
        moved = 0
        skipped: list[str] = []

        for category, mod_names in suggestions.items():
            group = by_name.get(category)
            if group is None:
                if not create_missing:
                    skipped.append(category)
                    continue
                try:
                    group = groups.create(category)
                except GroupError as exc:
                    logger.warning("创建分类「%s」失败：%s", category, exc)
                    skipped.append(category)
                    continue
                by_name[category] = group
            try:
                moved += groups.assign_many(mod_names, group.id)
            except GroupError as exc:
                logger.warning("归类到「%s」失败：%s", category, exc)
                skipped.append(category)

        summary = "、".join(
            f"{name} {len(mods)}" for name, mods in suggestions.items()
        )
        logger.info("自动分类：%s（共 %d 个）", summary, moved)
        note = f"已按内容归类 {moved} 个 Mod"
        if skipped:
            note += f"；{('、'.join(skipped))} 未处理（分类不存在）"
            self.context.notify(note, "warning")
        else:
            self.context.notify(note, "success")
        self._after_groups_changed()

    def _clear_cards(self) -> None:
        self._cards.clear()
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _show_empty(self, icon: str, title: str, description: str, action: str) -> None:
        self.empty.configure(
            icon_name=icon, title=title, description=description, action_text=action
        )
        self.scroll.setVisible(False)
        self.empty.setVisible(True)

    def _hide_empty(self) -> None:
        self.empty.setVisible(False)
        self.scroll.setVisible(True)

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------

    def _on_selected(self, mod: Mod) -> None:
        for card in self._cards:
            card.set_selected(card.mod is mod)

    def _on_toggle(self, mod: Mod, enabled: bool) -> None:
        if enabled and not mod.in_library:
            self.context.notify(
                f"「{mod.name}」还没纳入库，无法启用/停用。请先点「纳入库」。", "warning"
            )
            self.refresh()
            return

        try:
            self.context.library.set_enabled(mod, enabled)
        except LibraryError as exc:
            self.context.notify(str(exc), "error")
        else:
            self.context.notify(
                f"已{'启用' if enabled else '停用'}「{mod.name}」"
                + ("" if enabled else "（库中副本保留）"),
                "success",
            )
        self.refresh()

    def _on_adopt(self, mod: Mod) -> None:
        """把游戏目录里的 Mod 收进库。"""
        try:
            self.context.library.adopt(mod)
        except LibraryError as exc:
            self.context.notify(str(exc), "error")
        else:
            self.context.notify(f"「{mod.name}」已纳入库", "success")
        self.refresh()

    def _on_translate(self, mod: Mod) -> None:
        """打开翻译对话框：先预览、可编辑，确认后才写回。

        入口由 :data:`stellar_mod_manager.TRANSLATION_ENABLED` 控制；功能暂时撤下，
        所以这个方法目前不会被触发（实现与测试都完整保留）。
        """
        documents = self.context.mod_documents(mod)
        if not documents:
            self.context.notify(
                f"「{mod.name}」里没有 .dekcns.json，没有可翻译的内容", "warning"
            )
            return

        dialog = TranslateDialog(self.context, self.colors, mod, documents, self)
        dialog.exec()
        self.refresh()

    def _adopt_all(self) -> None:
        pending = [m for m in self._mods if not m.in_library]
        if not pending:
            return

        answer = QMessageBox.question(
            self,
            "纳入库",
            f"把 {len(pending)} 个 Mod 纳入库？\n\n"
            "它们会保留在游戏目录里继续生效，同时在库中留下一份权威副本。\n"
            "同一个盘上使用硬链接，因此不会多占磁盘空间。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return

        done = 0
        failed: list[str] = []
        for mod in pending:
            try:
                self.context.library.adopt(mod)
                done += 1
            except LibraryError as exc:
                failed.append(f"{mod.name}：{exc}")

        if failed:
            self.context.notify(
                f"已纳入 {done} 个，{len(failed)} 个失败：{failed[0]}", "warning"
            )
            logger.warning("批量纳管：成功 %d，失败 %d", done, len(failed))
        else:
            self.context.notify(f"已纳入 {done} 个 Mod", "success")
            logger.info("批量纳管成功：%d 个", done)
        self.refresh()

    def _on_uninstall(self, mod: Mod) -> None:
        if mod.deployed:
            detail = "会先把它从游戏目录移除，然后从库中彻底删除。"
        else:
            detail = "会从库中彻底删除。"

        answer = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除「{mod.name}」吗？\n\n"
            f"将删除 {len(mod.files)} 个文件（{human_size(mod.total_size)}）。\n"
            f"{detail}\n\n此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        try:
            self.context.library.remove_from_library(mod)
        except LibraryError as exc:
            self.context.notify(str(exc), "error")
            logger.warning("删除「%s」失败：%s", mod.name, exc)
        else:
            self.context.notify(f"已删除「{mod.name}」", "success")
            logger.info("已从库中删除「%s」", mod.name)
        self.refresh()

    def _on_reveal(self, mod: Mod) -> None:
        primary = mod.primary_file
        if primary is None:
            return
        _reveal_in_explorer(primary.path)

    def _open_mods_dir(self) -> None:
        target = self.context.mods_dir
        if target is None:
            self.context.notify("尚未配置游戏目录", "warning")
            return
        target.mkdir(parents=True, exist_ok=True)
        _reveal_in_explorer(target)

    def _open_library_dir(self) -> None:
        target = self.context.library_dir
        target.mkdir(parents=True, exist_ok=True)
        _reveal_in_explorer(target)


def _reveal_in_explorer(path: Path) -> None:
    """在资源管理器中定位到某个文件/目录。"""
    try:
        if not path.exists():
            return
        if sys.platform == "win32":
            if path.is_file():
                subprocess.Popen(["explorer", "/select,", str(path)])
            else:
                os.startfile(str(path))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path if path.is_dir() else path.parent)])
    except OSError:
        pass
