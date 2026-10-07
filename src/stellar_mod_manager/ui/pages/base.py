"""页面基类与共用布局工具。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ...services.context import AppContext
from ..icons import icon
from ..theme import Palette


class Page(QWidget):
    """所有页面的基类：统一持有上下文与配色。"""

    #: 侧边栏与标题栏显示的名字。
    title: str = "页面"
    subtitle: str = ""

    def __init__(
        self, context: AppContext, colors: Palette, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.context = context
        self.colors = colors

    def on_show(self) -> None:
        """页面被切换到前台时调用，用于刷新数据。"""


def page_scroll(content: QWidget) -> QScrollArea:
    """把内容包进一个无边框滚动区域。"""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setWidget(content)
    return area


def icon_label(name: str, colors: Palette, *, role: str = "accent", size: int = 20) -> QLabel:
    """图标块，用于卡片标题。

    底色用中性表面而不是强调色的淡色版——淡色块在纯色界面里像一块块补丁。
    语义仍然靠图标本身的颜色表达。
    """
    holder = QLabel()
    holder.setFixedSize(size + 18, size + 18)
    holder.setAlignment(Qt.AlignCenter)
    color = colors.qcolor(role)
    holder.setPixmap(icon(name, color.name(), size).pixmap(size, size))
    holder.setStyleSheet(
        f"QLabel {{ background-color: {colors.surface_alt};"
        f" border-radius: {(size + 18) // 2}px; }}"
    )
    return holder


def card_header(icon_name: str, title: str, description: str, colors: Palette) -> QWidget:
    """卡片顶部：图标 + 标题 + 说明。"""
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(11)

    row.addWidget(icon_label(icon_name, colors), 0, Qt.AlignTop)

    column = QVBoxLayout()
    column.setSpacing(2)

    title_label = QLabel(title)
    title_label.setStyleSheet("font-size: 14px; font-weight: 600;")
    column.addWidget(title_label)

    if description:
        desc = QLabel(description)
        desc.setProperty("role", "muted")
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size: 12px;")
        column.addWidget(desc)

    row.addLayout(column, 1)
    return holder


def field_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "muted")
    label.setStyleSheet("font-size: 12px;")
    return label
