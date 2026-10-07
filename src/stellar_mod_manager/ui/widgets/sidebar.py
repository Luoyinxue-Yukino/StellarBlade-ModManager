"""侧边栏：品牌区、页面导航、底部游戏状态。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ... import APP_NAME, __version__
from ..icons import app_icon_pixmap, icon
from ..theme import SPACE_LG, SPACE_MD, SPACE_SM, Palette

SIDEBAR_WIDTH = 232


class Sidebar(QFrame):
    """左侧导航栏。"""

    navigated = Signal(str)
    settings_requested = Signal()

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self.setProperty("role", "panel")
        self.setFixedWidth(SIDEBAR_WIDTH)

        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE_MD, SPACE_LG, SPACE_MD, SPACE_MD)
        root.setSpacing(SPACE_SM)

        root.addWidget(self._build_brand())
        root.addSpacing(SPACE_LG)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}

        self._nav_container = QVBoxLayout()
        self._nav_container.setSpacing(2)
        root.addLayout(self._nav_container)

        root.addStretch(1)
        root.addWidget(self._build_status_card())

        version = QLabel(f"v{__version__}", self)
        version.setAlignment(Qt.AlignCenter)
        version.setStyleSheet(f"font-size: 11px; color: {palette.text_faint};")
        root.addWidget(version)

    # ------------------------------------------------------------------

    def _build_brand(self) -> QWidget:
        holder = QWidget(self)
        row = QHBoxLayout(holder)
        row.setContentsMargins(6, 0, 0, 0)
        row.setSpacing(10)

        mark = QLabel(holder)
        mark.setPixmap(app_icon_pixmap(34, 9))
        mark.setFixedSize(34, 34)
        mark.setAlignment(Qt.AlignCenter)
        row.addWidget(mark)

        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        title = QLabel(APP_NAME, holder)
        title.setProperty("role", "brand")
        title.setStyleSheet("font-size: 15px; font-weight: 700; letter-spacing: 0.2px;")
        subtitle = QLabel("STELLAR BLADE", holder)
        subtitle.setStyleSheet(
            f"color: {self._palette.accent2}; font-size: 9px; letter-spacing: 1.6px;"
            f" font-weight: 700;"
        )
        text_col.addWidget(title)
        text_col.addWidget(subtitle)
        row.addLayout(text_col, 1)

        return holder

    def add_page(self, key: str, text: str, icon_name: str) -> QPushButton:
        """注册一个导航项。"""
        btn = QPushButton(text, self)
        btn.setProperty("nav", "true")
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setIcon(icon(icon_name, self._palette.qcolor("text_muted").name(), 17))
        btn.setIconSize(QSize(17, 17))
        btn.clicked.connect(lambda _=False, k=key: self.navigated.emit(k))
        btn.toggled.connect(
            lambda checked, b=btn, name=icon_name: self._retint(b, name, checked)
        )
        self._group.addButton(btn)
        self._buttons[key] = btn
        self._nav_container.addWidget(btn)
        return btn

    def _retint(self, button: QPushButton, icon_name: str, checked: bool) -> None:
        role = "accent" if checked else "text_muted"
        button.setIcon(icon(icon_name, self._palette.qcolor(role).name(), 17))

    def set_current(self, key: str) -> None:
        button = self._buttons.get(key)
        if button is not None and not button.isChecked():
            button.setChecked(True)

    # ------------------------------------------------------------------

    def _build_status_card(self) -> QWidget:
        card = QFrame(self)
        card.setProperty("role", "card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(SPACE_MD, SPACE_MD, SPACE_MD, SPACE_MD)
        layout.setSpacing(6)

        header = QLabel("游戏目录", card)
        header.setProperty("role", "section")
        layout.addWidget(header)

        self._status_row = QWidget(card)
        row = QHBoxLayout(self._status_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(7)

        self._dot = QLabel(self._status_row)
        self._dot.setFixedSize(8, 8)
        row.addWidget(self._dot)

        self._status_text = QLabel("未配置", self._status_row)
        self._status_text.setProperty("role", "muted")
        row.addWidget(self._status_text, 1)
        layout.addWidget(self._status_row)

        self._path_text = QLabel("", card)
        self._path_text.setProperty("role", "faint")
        self._path_text.setWordWrap(True)
        self._path_text.setStyleSheet("font-size: 11px;")
        layout.addWidget(self._path_text)

        self._settings_btn = QPushButton("配置目录", card)
        self._settings_btn.setProperty("variant", "ghost")
        self._settings_btn.setCursor(Qt.PointingHandCursor)
        self._settings_btn.clicked.connect(self.settings_requested)
        layout.addWidget(self._settings_btn)

        return card

    def set_game_status(self, *, configured: bool, path: str = "", detail: str = "") -> None:
        """更新底部状态卡片。"""
        if configured:
            color = self._palette.qcolor("success")
            self._status_text.setText("已就绪")
            self._settings_btn.setText("更换目录")
        else:
            color = self._palette.qcolor("danger")
            self._status_text.setText("未配置")
            self._settings_btn.setText("配置目录")

        self._dot.setStyleSheet(
            f"background-color: {color.name()}; border-radius: 4px;"
        )
        shown = _shorten(detail or path)
        self._path_text.setText(shown or "尚未找到《剑星》安装目录")
        self._path_text.setToolTip(path or "")


def _shorten(text: str, keep: int = 3) -> str:
    """把长路径压缩成 ``…\\Paks\\~mods`` 这样，避免侧边栏里换行被截断。"""
    if not text:
        return ""
    try:
        parts = Path(text).parts
    except (TypeError, ValueError):
        return text
    if len(parts) <= keep:
        return text
    return "…" + str(Path(*parts[-keep:]))
