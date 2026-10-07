"""轻量级浮层提示（Toast）：在窗口底部弹出，几秒后自动淡出。"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    Qt,
    QTimer,
)
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QWidget,
)

from ..icons import icon
from ..theme import SPACE_LG, SPACE_MD, Palette

_LEVELS = {
    "info": ("info", "accent"),
    "success": ("check", "success"),
    "warning": ("alert", "warning"),
    "error": ("alert", "danger"),
}


class Toast(QWidget):
    """非阻塞提示条。多次调用会复用同一个实例。"""

    def __init__(self, parent: QWidget, palette: Palette) -> None:
        super().__init__(parent)
        self._palette = palette
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        # QWidget 子类不会自动绘制 QSS 的 background/border，
        # 不加这个属性提示条就是透明的，只剩文字浮在内容上。
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setVisible(False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE_LG, 10, SPACE_LG, 10)
        layout.setSpacing(SPACE_MD)

        self._icon = QLabel(self)
        self._icon.setFixedSize(18, 18)
        layout.addWidget(self._icon)

        self._text = QLabel("", self)
        self._text.setStyleSheet("font-size: 13px;")
        layout.addWidget(self._text, 1)

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)

        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(180)
        self._fade.setEasingCurve(QEasingCurve.InOutQuad)
        # 只连一次：靠透明度判断这次是淡入还是淡出结束，避免反复 connect/disconnect
        self._fade.finished.connect(self._on_fade_finished)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._fade_out)

    # ------------------------------------------------------------------

    def show_message(self, text: str, level: str = "info", duration_ms: int = 3200) -> None:
        icon_name, color_role = _LEVELS.get(level, _LEVELS["info"])
        color = self._palette.qcolor(color_role)
        self._icon.setPixmap(icon(icon_name, color.name(), 18).pixmap(18, 18))
        self._text.setText(text)

        self.setStyleSheet(
            f"Toast {{ background-color: {self._palette.elevated};"
            f" border: 1px solid {color.name()}; border-radius: 10px; }}"
        )
        self.adjustSize()
        self.reposition()

        self.setVisible(True)
        self.raise_()
        self._fade.stop()
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._hide_timer.start(duration_ms)

    def reposition(self) -> None:
        """把提示条摆到父控件底部居中（父控件尺寸变化后调用）。"""
        parent = self.parentWidget()
        if parent is None:
            return
        x = (parent.width() - self.width()) // 2
        y = parent.height() - self.height() - 28
        self.move(QPoint(max(12, x), max(12, y)))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self.reposition()

    def _fade_out(self) -> None:
        self._fade.stop()
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_fade_finished(self) -> None:
        if self._effect.opacity() <= 0.01:
            self.setVisible(False)
