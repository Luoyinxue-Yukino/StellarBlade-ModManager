"""通用小组件：卡片、统计块、徽标、空状态、图标按钮等。

这些组件只负责外观，不含业务逻辑；颜色全部取自传入的 :class:`~..theme.Palette`。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..icons import icon
from ..theme import SPACE_2XL, SPACE_LG, SPACE_MD, Palette


# ---------------------------------------------------------------------------
# 卡片 / 容器
# ---------------------------------------------------------------------------


class Card(QFrame):
    """带边框与圆角的面板容器。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        padding: int = 16,
        spacing: int = 10,
        role: str = "card",
    ) -> None:
        super().__init__(parent)
        self.setProperty("role", role)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(padding, padding, padding, padding)
        self.body.setSpacing(spacing)


class Divider(QFrame):
    """一像素水平分隔线。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "divider")
        self.setFixedHeight(1)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)


def soft_shadow(widget: QWidget, *, blur: int = 26, alpha: int = 90, dy: int = 6) -> None:
    """给控件加一层柔和投影，让卡片从背景里浮起来。"""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, dy)
    from PySide6.QtGui import QColor

    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)


# ---------------------------------------------------------------------------
# 文本
# ---------------------------------------------------------------------------


def label(
    text: str = "",
    *,
    role: str | None = None,
    size: int | None = None,
    bold: bool = False,
    wrap: bool = False,
    parent: QWidget | None = None,
) -> QLabel:
    """快速创建一个语义化 QLabel。"""
    widget = QLabel(text, parent)
    if role:
        widget.setProperty("role", role)
    if size or bold:
        font = widget.font()
        if size:
            font.setPointSizeF(size)
        if bold:
            font.setWeight(QFont.DemiBold)
        widget.setFont(font)
    widget.setWordWrap(wrap)
    return widget


class Badge(QLabel):
    """小徽标（Pak / IoStore / 仅存库中…）。

    只用**文字颜色 + 描边**表示语义，不铺淡色底：淡色块在纯色界面里会显得
    一块块拼接得很碎，而且同色淡底和描边叠起来并不比纯描边更清楚。
    """

    def __init__(
        self,
        text: str,
        palette: Palette,
        *,
        color_role: str = "accent",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setProperty("role", "badge")
        self.setAlignment(Qt.AlignCenter)
        self.set_palette_role(palette, color_role)

    def set_palette_role(self, palette: Palette, color_role: str) -> None:
        color = palette.qcolor(color_role)
        self.setStyleSheet(
            f"QLabel {{ color: {color.name()};"
            f" background: transparent;"
            f" border: 1px solid rgba({color.red()}, {color.green()}, {color.blue()}, 110);"
            f" border-radius: 9px; font-size: 11px; font-weight: 600;"
            f" padding: 2px 8px; }}"
        )


# ---------------------------------------------------------------------------
# 按钮
# ---------------------------------------------------------------------------


def button(
    text: str = "",
    *,
    variant: str = "default",
    icon_name: str | None = None,
    palette: Palette | None = None,
    tooltip: str = "",
    parent: QWidget | None = None,
) -> QPushButton:
    """创建按钮。``variant`` 取 ``default`` / ``primary`` / ``danger`` / ``ghost`` / ``icon``。"""
    widget = QPushButton(text, parent)
    widget.setProperty("variant", variant)
    widget.setCursor(Qt.PointingHandCursor)
    if icon_name and palette is not None:
        tint = {
            "primary": "#08111c",
            "danger": palette.danger,
        }.get(variant, palette.text_muted)
        widget.setIcon(icon(icon_name, tint, 16))
        widget.setIconSize(QSize(16, 16))
    if tooltip:
        widget.setToolTip(tooltip)
    return widget


def icon_button(
    icon_name: str,
    palette: Palette,
    *,
    tooltip: str = "",
    color_role: str = "text_muted",
    size: int = 18,
    parent: QWidget | None = None,
) -> QPushButton:
    """纯图标按钮。"""
    widget = QPushButton(parent)
    widget.setProperty("variant", "icon")
    widget.setIcon(icon(icon_name, palette.qcolor(color_role).name(), size))
    widget.setIconSize(QSize(size, size))
    widget.setFixedSize(QSize(size + 14, size + 14))
    widget.setCursor(Qt.PointingHandCursor)
    if tooltip:
        widget.setToolTip(tooltip)
    return widget


# ---------------------------------------------------------------------------
# 统计块
# ---------------------------------------------------------------------------


class StatCard(QFrame):
    """仪表盘上的一个统计块：图标 + 数值 + 标题。

    左侧一条强调色竖条，让四个统计块在纯色背景上一眼能区分开。
    """

    def __init__(
        self,
        title: str,
        palette: Palette,
        *,
        icon_name: str = "library",
        color_role: str = "accent",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("role", "stat")
        self._palette = palette
        self._color_role = color_role

        root = QHBoxLayout(self)
        root.setContentsMargins(SPACE_LG, SPACE_MD, SPACE_LG, SPACE_MD)
        root.setSpacing(SPACE_MD)

        self._accent_bar = QFrame(self)
        self._accent_bar.setFixedWidth(3)
        self._accent_bar.setMinimumHeight(38)
        root.addWidget(self._accent_bar)

        self._icon_label = QLabel(self)
        self._icon_label.setPixmap(
            icon(icon_name, palette.qcolor(color_role).name(), 21).pixmap(21, 21)
        )
        self._icon_label.setFixedSize(38, 38)
        self._icon_label.setAlignment(Qt.AlignCenter)
        self._paint_colors()
        root.addWidget(self._icon_label)

        column = QVBoxLayout()
        column.setSpacing(0)
        self.value_label = label("0", role="metric", parent=self)
        self.title_label = label(title, role="muted", parent=self)
        column.addWidget(self.value_label)
        column.addWidget(self.title_label)
        root.addLayout(column, 1)

        # 只给仪表盘这四块加投影：列表里几百个控件加效果会明显拖慢滚动
        soft_shadow(self, blur=24, alpha=64, dy=5)

    def _paint_colors(self) -> None:
        color = self._palette.qcolor(self._color_role)
        # 左侧实心竖条是这张卡唯一的「颜色」，它已经足够区分四块；
        # 图标再铺一层同色淡底就重复了，而且拼色感很重。
        self._accent_bar.setStyleSheet(
            f"QFrame {{ background-color: {color.name()}; border: none;"
            f" border-radius: 2px; }}"
        )
        self._icon_label.setStyleSheet(
            f"QLabel {{ background-color: {self._palette.surface_alt};"
            f" border-radius: 11px; }}"
        )

    def set_value(self, text: str) -> None:
        self.value_label.setText(text)


# ---------------------------------------------------------------------------
# 空状态
# ---------------------------------------------------------------------------


class EmptyState(QWidget):
    """列表为空时的占位提示。"""

    action_clicked = Signal()

    def __init__(
        self,
        palette: Palette,
        *,
        icon_name: str = "archive",
        title: str = "这里还是空的",
        description: str = "",
        action_text: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._palette = palette

        root = QVBoxLayout(self)
        # 左右留出较宽的边距：这既是给描述文字一个舒服的行宽，
        # 也让描述标签能拿到**确定宽度**——自动换行的标签只有在宽度确定时
        # 才会被正确算出换行后的高度，否则会被压成一行并截断。
        root.setContentsMargins(SPACE_2XL * 2, 40, SPACE_2XL * 2, 40)
        root.setSpacing(SPACE_MD)
        # 不能给布局设 AlignCenter：那会让自动换行的描述标签只拿到最小宽度。
        # 改为上下留白 + 逐个控件水平居中。
        root.addStretch(1)

        self.icon_label = QLabel(self)
        self.icon_label.setPixmap(
            icon(icon_name, palette.qcolor("text_faint").name(), 46).pixmap(46, 46)
        )
        self.icon_label.setAlignment(Qt.AlignCenter)
        root.addWidget(self.icon_label, 0, Qt.AlignHCenter)

        self.title_label = label(title, size=15, bold=True, parent=self)
        self.title_label.setAlignment(Qt.AlignCenter)
        root.addWidget(self.title_label, 0, Qt.AlignHCenter)

        self.description_label = label(
            description, role="muted", wrap=True, parent=self
        )
        self.description_label.setAlignment(Qt.AlignCenter)
        self.description_label.setVisible(bool(description))
        root.addWidget(self.description_label)

        self.action_button = button(
            action_text, variant="primary", parent=self
        )
        self.action_button.setVisible(bool(action_text))
        self.action_button.clicked.connect(self.action_clicked)
        root.addWidget(self.action_button, 0, Qt.AlignHCenter)

        root.addStretch(1)

    def configure(
        self,
        *,
        icon_name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        action_text: str | None = None,
    ) -> None:
        if icon_name:
            self.icon_label.setPixmap(
                icon(icon_name, self._palette.qcolor("text_faint").name(), 46).pixmap(46, 46)
            )
        if title is not None:
            self.title_label.setText(title)
        if description is not None:
            self.description_label.setText(description)
            self.description_label.setVisible(bool(description))
        if action_text is not None:
            self.action_button.setText(action_text)
            self.action_button.setVisible(bool(action_text))
