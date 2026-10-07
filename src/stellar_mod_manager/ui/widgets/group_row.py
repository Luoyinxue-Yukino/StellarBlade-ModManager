"""列表里的分组行。

层级列表的「父行」：折叠箭头 + 名称 + 数量 + 操作菜单。点击整行即可折叠/展开，
不要求用户精确点到那个小箭头上。

「未分组」也是一个 GroupRow，只是 ``group_id`` 为 ``None``——它是「归属为空」
这个状态的显示名，不对应真实的 ``ModGroup`` 记录，所以不提供改名/删除。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QWidget

from ..icons import icon
from ..theme import SPACE_MD, SPACE_SM, Palette
from .common import Badge, icon_button

#: 每一层缩进的像素数。够看出层级，又不至于把深层内容挤没。
INDENT_STEP = 20

#: 从行左边缘到名称文字的宽度：折叠箭头(16) + 间距(8) + 分组图标(18) + 间距(8)。
_CONTENT_OFFSET = 50


class GroupRow(QFrame):
    """分组行（列表里的父节点）。"""

    toggled = Signal(str, bool)
    """``(group_id, collapsed)``——用户点了这一行。"""
    rename_requested = Signal(str)
    add_child_requested = Signal(str)
    delete_requested = Signal(str)
    enable_all_requested = Signal(str)
    disable_all_requested = Signal(str)
    move_requested = Signal(str)
    selected = Signal(str)

    def __init__(
        self,
        *,
        group_id: str | None,
        name: str,
        count: int,
        depth: int = 0,
        collapsed: bool = False,
        palette: Palette,
        has_children: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.group_id = group_id
        self._palette = palette
        self._collapsed = collapsed
        self._is_pseudo = group_id is None

        self.setProperty("role", "grouprow")
        self.setCursor(Qt.PointingHandCursor)

        root = QHBoxLayout(self)
        root.setContentsMargins(
            SPACE_MD + depth * INDENT_STEP, SPACE_SM, SPACE_MD, SPACE_SM
        )
        root.setSpacing(SPACE_SM)

        # 折叠箭头：没有子节点时留一个等宽占位，否则同级的名字对不齐
        self.chevron = QLabel(self)
        self.chevron.setFixedSize(16, 16)
        self.chevron.setAlignment(Qt.AlignCenter)
        if has_children:
            self.chevron.setPixmap(
                icon(
                    "chevron-down" if not collapsed else "chevron-right",
                    palette.qcolor("text_muted").name(),
                    16,
                ).pixmap(16, 16)
            )
        root.addWidget(self.chevron)

        self.folder_icon = QLabel(self)
        self.folder_icon.setFixedSize(18, 18)
        self.folder_icon.setPixmap(
            icon(
                "folder-open" if not collapsed else "folder",
                palette.qcolor("accent" if not self._is_pseudo else "text_faint").name(),
                18,
            ).pixmap(18, 18)
        )
        root.addWidget(self.folder_icon)

        self.name_label = QLabel(name, self)
        self.name_label.setStyleSheet("font-size: 13px; font-weight: 600;")
        root.addWidget(self.name_label)

        self.count_badge = Badge(
            str(count),
            palette,
            color_role="text_faint" if self._is_pseudo else "accent",
        )
        root.addWidget(self.count_badge)
        root.addStretch(1)

        if not self._is_pseudo:
            self.menu_button = icon_button(
                "more", palette, tooltip="分组操作", size=15
            )
            self.menu_button.clicked.connect(self._show_menu)
            root.addWidget(self.menu_button)

    # ------------------------------------------------------------------

    @staticmethod
    def content_indent(depth: int) -> int:
        """该层级分组「内容」的左起点（名称文字那里）。

        子节点要按这个值再往右缩，才能落在父分组名称的右边——否则子节点
        看起来比父节点还靠左，层级就反了。
        """
        return SPACE_MD + depth * INDENT_STEP + _CONTENT_OFFSET

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def set_count(self, count: int) -> None:
        self.count_badge.setText(str(count))

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.LeftButton:
            self.selected.emit(self.group_id or "")
            self.toggled.emit(self.group_id or "", not self._collapsed)
        super().mousePressEvent(event)

    def _show_menu(self) -> None:
        menu = QMenu(self)
        muted = self._palette.qcolor("text_muted").name()
        accent = self._palette.qcolor("accent").name()
        danger = self._palette.qcolor("danger").name()

        menu.addAction(
            icon("plus", accent, 16), "新建子分组…",
            lambda: self.add_child_requested.emit(self.group_id or ""),
        )
        menu.addAction(
            icon("tag", muted, 16), "重命名…",
            lambda: self.rename_requested.emit(self.group_id or ""),
        )
        menu.addAction(
            icon("folder", muted, 16), "移动到…",
            lambda: self.move_requested.emit(self.group_id or ""),
        )
        menu.addSeparator()
        menu.addAction(
            icon("play", muted, 16), "启用组内全部",
            lambda: self.enable_all_requested.emit(self.group_id or ""),
        )
        menu.addAction(
            icon("pause", muted, 16), "停用组内全部",
            lambda: self.disable_all_requested.emit(self.group_id or ""),
        )
        menu.addSeparator()
        menu.addAction(
            icon("trash", danger, 16), "删除分组",
            lambda: self.delete_requested.emit(self.group_id or ""),
        )
        menu.exec(self.menu_button.mapToGlobal(self.menu_button.rect().bottomLeft()))


__all__ = ["INDENT_STEP", "GroupRow"]
