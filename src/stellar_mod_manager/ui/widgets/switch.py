"""开关（Switch）控件：比复选框更直观的启用/停用交互。"""

from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QCheckBox, QWidget

from ..theme import Palette


class Switch(QCheckBox):
    """带动画的胶囊开关。行为与 ``QCheckBox`` 完全一致（``toggled`` 信号照用）。"""

    WIDTH = 44
    HEIGHT = 24

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._position = 0.0
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setCursor(Qt.PointingHandCursor)

        self._animation = QPropertyAnimation(self, b"position", self)
        self._animation.setDuration(140)
        self._animation.setEasingCurve(QEasingCurve.InOutCubic)
        self.toggled.connect(self._animate)

    # -- 动画属性 ---------------------------------------------------------

    def _get_position(self) -> float:
        return self._position

    def _set_position(self, value: float) -> None:
        self._position = value
        self.update()

    position = Property(float, _get_position, _set_position)

    def _animate(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def set_checked_silently(self, checked: bool) -> None:
        """设置**初始**状态：不播动画也不发信号，直接落位。

        构造列表项时用得到——那时控件还没显示，动画不会推进，位置会停在错的
        地方，直到用户第一次点击才「跳」过去。
        """
        self._animation.stop()
        self.blockSignals(True)
        self.setChecked(checked)
        self.blockSignals(False)
        self._position = 1.0 if checked else 0.0
        self.update()

    # -- 外观 -------------------------------------------------------------

    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.update()

    def _track_color(self) -> QColor:
        if not self.isEnabled():
            return self._palette.qcolor("border")
        if self._position <= 0.01:
            return self._palette.qcolor("border_strong")
        on = self._palette.qcolor("accent")
        off = self._palette.qcolor("border_strong")
        ratio = self._position
        return QColor(
            int(off.red() + (on.red() - off.red()) * ratio),
            int(off.green() + (on.green() - off.green()) * ratio),
            int(off.blue() + (on.blue() - off.blue()) * ratio),
        )

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        radius = self.HEIGHT / 2
        rect = QRectF(0.5, 0.5, self.WIDTH - 1, self.HEIGHT - 1)

        painter.setPen(Qt.NoPen)
        painter.setBrush(self._track_color())
        painter.drawRoundedRect(rect, radius, radius)

        knob_diameter = self.HEIGHT - 6
        travel = self.WIDTH - knob_diameter - 6
        x = 3 + travel * self._position
        painter.setBrush(QColor("#ffffff") if self.isEnabled() else QColor("#8a8f9c"))
        painter.drawEllipse(QRectF(x, 3, knob_diameter, knob_diameter))

    def hitButton(self, pos) -> bool:  # noqa: N802 - Qt 命名
        return self.rect().contains(pos)
