"""Mod 列表项卡片。

视觉上分三层，和列表的节奏配合：

* **左侧状态条** —— 一眼扫出哪些启用、哪些只在库里、哪些未纳管，
  不必逐个去看开关；
* **中间信息区** —— 主行（名称 + 状态）、次行（体量 + 时间）、来源；
* **右侧操作区** —— 开关与菜单。

行内间距刻意小于行间距（由 :mod:`pages.library_page` 控制），
让每一行读起来是一个整体，而不是一堵连续的文字墙。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from ... import TRANSLATION_ENABLED
from ...core.formatting import human_size, truncate_middle
from ...core.models import Mod, PayloadKind
from ..icons import icon
from ..theme import SPACE_MD, SPACE_SM, Palette
from .common import Badge, icon_button
from .switch import Switch

#: 载荷类型 → 徽标配色。只保留「值得区分」的几种，其余走中性色。
_KIND_ROLE = {
    PayloadKind.PAK: "accent",
    PayloadKind.UTOC: "accent2",
    PayloadKind.UCAS: "warning",
    PayloadKind.SIG: "text_faint",
    PayloadKind.OTHER: "text_faint",
}


class ModCard(QFrame):
    """一行 Mod：状态条、图标、名称、徽标、元信息、启用开关。"""

    selected = Signal(object)
    toggle_requested = Signal(object, bool)
    uninstall_requested = Signal(object)
    reveal_requested = Signal(object)
    adopt_requested = Signal(object)
    translate_requested = Signal(object)
    move_requested = Signal(object, object)
    """``(mod, group_id)``——``group_id`` 为 ``None`` 表示移出分组。"""

    def __init__(
        self,
        mod: Mod,
        palette: Palette,
        parent: QWidget | None = None,
        *,
        indent: int = 0,
        move_targets: list[tuple[str | None, str, int]] | None = None,
        current_group: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.mod = mod
        self._palette = palette
        self._selected = False
        #: ``(分组 id, 显示名, 层级)``；由页面按当前分组树传入，
        #: 卡片只负责把菜单画出来，不认识分组模型。
        self._move_targets = move_targets or []
        #: 当前所属分组 id，用于在菜单里打勾。
        self._current_group = current_group

        self.setProperty("role", "modcard")
        self.setProperty("selected", "false")
        self.setCursor(Qt.PointingHandCursor)

        root = QHBoxLayout(self)
        root.setContentsMargins(indent, 0, SPACE_MD, 0)
        root.setSpacing(0)

        # 状态条贴在卡片最左边，是一整行最先被看到的东西
        self._state_bar = QFrame(self)
        self._state_bar.setFixedWidth(3)
        root.addWidget(self._state_bar)

        body = QHBoxLayout()
        body.setContentsMargins(SPACE_MD, SPACE_SM + 2, 0, SPACE_SM + 2)
        body.setSpacing(SPACE_MD)
        body.addWidget(self._build_tile())

        column = QVBoxLayout()
        column.setSpacing(1)
        column.addLayout(self._build_title_row())
        column.addWidget(self._build_meta_row())
        column.addWidget(self._build_source_row())
        body.addLayout(column, 1)

        self.switch = Switch(palette, self)
        self.switch.set_checked_silently(mod.enabled)
        if mod.in_library:
            self.switch.setToolTip("启用 / 停用（停用后库里仍保留一份）")
        else:
            self.switch.setEnabled(False)
            self.switch.setToolTip("这个 Mod 还没纳入库，无法停用——停用会让文件无处可去")
        self.switch.toggled.connect(
            lambda checked: self.toggle_requested.emit(self.mod, checked)
        )
        body.addWidget(self.switch, 0, Qt.AlignVCenter)

        self.menu_button = icon_button("chevron-down", palette, tooltip="更多操作", size=16)
        self.menu_button.clicked.connect(self._show_menu)
        body.addWidget(self.menu_button, 0, Qt.AlignVCenter)

        root.addLayout(body, 1)

        self._paint_state()

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    def _state_role(self) -> str:
        """这一行在视觉上属于哪一类。"""
        if not self.mod.in_library:
            return "warning"  # 未纳管：需要用户处理
        return "accent" if self.mod.deployed else "text_faint"

    def _paint_state(self) -> None:
        role = self._state_role()
        color = self._palette.qcolor(role)
        # 未启用的行用低透明度，让列表里「生效中」的那些自己浮出来
        alpha = 255 if self.mod.deployed else 90
        self._state_bar.setStyleSheet(
            f"QFrame {{ background-color: rgba({color.red()}, {color.green()},"
            f" {color.blue()}, {alpha}); border: none; }}"
        )

    def _build_tile(self) -> QWidget:
        tile = QLabel(self)
        tile.setFixedSize(38, 38)
        tile.setAlignment(Qt.AlignCenter)

        role = self._state_role()
        color = self._palette.qcolor(role)
        tile.setPixmap(icon("archive", color.name(), 19).pixmap(19, 19))
        # 中性底 + 彩色图标：颜色信息靠图标和左侧状态条传达，
        # 不再铺一层同色淡底（95 行下来会非常花）。
        tile.setStyleSheet(
            f"QLabel {{ background-color: {self._palette.surface_alt};"
            f" border-radius: 10px; }}"
        )
        return tile

    # ------------------------------------------------------------------
    # 信息区
    # ------------------------------------------------------------------

    def _build_title_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(SPACE_SM)

        name = QLabel(truncate_middle(self.mod.name, 52), self)
        name.setStyleSheet("font-size: 15px; font-weight: 600;")
        name.setToolTip(self.mod.name)
        row.addWidget(name)

        # 徽标最多两个：一个说格式，一个说状态。
        # 之前每个 Mod 挂三个徽标，彼此等权，等于都没说。
        if not self.mod.in_library:
            row.addWidget(Badge("未纳管", self._palette, color_role="warning"))
        elif not self.mod.deployed:
            row.addWidget(Badge("仅存库中", self._palette, color_role="text_faint"))

        row.addWidget(
            Badge(self.mod.format_label, self._palette, color_role=_KIND_ROLE_KIND(self.mod))
        )
        row.addStretch(1)
        return row

    def _build_meta_row(self) -> QLabel:
        parts = [f"{len(self.mod.files)} 个文件", human_size(self.mod.total_size)]
        if self.mod.deployed and self.mod.deploy_mode == "hardlink":
            parts.append("硬链接")
        elif self.mod.deployed and self.mod.deploy_mode == "copy":
            parts.append("复制部署")
        if self.mod.installed_at is not None:
            parts.append(self.mod.installed_at.strftime("%Y-%m-%d %H:%M"))
        meta = QLabel("  ·  ".join(parts), self)
        meta.setProperty("role", "muted")
        meta.setStyleSheet("font-size: 12px;")
        return meta

    def _build_source_row(self) -> QLabel:
        source = self.mod.source_archive
        text = f"来源：{source.name}" if source else "来源：手动放入"
        label = QLabel(text, self)
        label.setProperty("role", "faint")
        label.setStyleSheet("font-size: 11px;")
        if source:
            label.setToolTip(str(source))
        return label

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------

    def set_selected(self, value: bool) -> None:
        if self._selected == value:
            return
        self._selected = value
        # 属性值是字符串比较的，必须写 "true"/"false"
        self.setProperty("selected", "true" if value else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.LeftButton:
            self.selected.emit(self.mod)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.LeftButton:
            self.reveal_requested.emit(self.mod)
        super().mouseDoubleClickEvent(event)

    def _show_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction(
            icon("folder", self._palette.qcolor("text_muted").name(), 16), "在资源管理器中显示",
            lambda: self.reveal_requested.emit(self.mod),
        )

        if not self.mod.in_library:
            menu.addAction(
                icon("import", self._palette.qcolor("accent").name(), 16),
                "纳入库（不额外占空间）",
                lambda: self.adopt_requested.emit(self.mod),
            )
        else:
            menu.addAction(
                icon("refresh", self._palette.qcolor("text_muted").name(), 16),
                "停用（库中保留）" if self.mod.deployed else "启用（部署进游戏）",
                lambda: self.toggle_requested.emit(self.mod, not self.mod.deployed),
            )
            if TRANSLATION_ENABLED:
                menu.addAction(
                    icon("info", self._palette.qcolor("accent").name(), 16),
                    "翻译…",
                    lambda: self.translate_requested.emit(self.mod),
                )

        menu.addSeparator()
        move_menu = menu.addMenu(
            icon("tag", self._palette.qcolor("text_muted").name(), 16), "移动到分组"
        )
        moved = False
        for group_id, label, depth in self._move_targets:
            action = move_menu.addAction("　" * depth + label)
            action.setCheckable(True)
            # 用 setChecked 而不是勾选图标：这样一眼能看出当前在哪个分组
            action.setChecked(group_id == self._current_group)
            action.triggered.connect(
                lambda _checked=False, gid=group_id: self.move_requested.emit(self.mod, gid)
            )
            moved = True
        if not moved:
            placeholder = move_menu.addAction("（还没有分组）")
            placeholder.setEnabled(False)
            placeholder.setToolTip("先在工具栏点「新建分组」")

        menu.addSeparator()
        menu.addAction(
            icon("trash", self._palette.qcolor("danger").name(), 16),
            "从库中删除",
            lambda: self.uninstall_requested.emit(self.mod),
        )
        menu.exec(self.menu_button.mapToGlobal(self.menu_button.rect().bottomLeft()))

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        return QSize(420, 76)


def _KIND_ROLE_KIND(mod: Mod) -> str:
    """格式徽标的语义色：IoStore 与 Pak 是两类不同的打包方式。"""
    primary = mod.primary_file.kind if mod.primary_file else PayloadKind.OTHER
    return _KIND_ROLE.get(primary, "accent")
