"""拖放区域：把压缩包拖进来即可导入。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from ...core.archive import SUPPORTED_SUFFIXES
from ..icons import icon
from ..theme import SPACE_LG, SPACE_SM, SPACE_XL, Palette


class DropZone(QFrame):
    """接收文件拖放的虚线框。"""

    files_dropped = Signal(list)
    clicked = Signal()

    def __init__(
        self,
        palette: Palette,
        *,
        title: str = "把 Mod 压缩包拖到这里",
        hint: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._palette = palette
        self.setProperty("role", "dropzone")
        self.setProperty("dragActive", "false")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(150)

        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE_XL, SPACE_LG, SPACE_XL, SPACE_LG)
        root.setSpacing(SPACE_SM)
        # 注意：这里**不能**给布局设 AlignCenter。那会让开启自动换行的标签
        # 只能拿到自己的最小宽度，长提示会被压成很窄的一条并互相叠压。
        # 正确做法是让标签占满宽度，靠标签自身的 setAlignment 把文字居中。
        root.addStretch(1)

        self._icon = QLabel(self)
        self._icon.setPixmap(
            icon("import", palette.qcolor("accent").name(), 40).pixmap(40, 40)
        )
        self._icon.setAlignment(Qt.AlignCenter)
        root.addWidget(self._icon, 0, Qt.AlignHCenter)

        self._title = QLabel(title, self)
        self._title.setStyleSheet("font-size: 15px; font-weight: 600;")
        self._title.setAlignment(Qt.AlignCenter)
        self._title.setWordWrap(True)
        root.addWidget(self._title)

        default_hint = "支持 " + "、".join(SUPPORTED_SUFFIXES[:4]) + "，也可以点击选择文件"
        self._hint = QLabel(hint or default_hint, self)
        self._hint.setProperty("role", "muted")
        self._hint.setAlignment(Qt.AlignCenter)
        self._hint.setWordWrap(True)
        root.addWidget(self._hint)

        root.addStretch(1)

    # ------------------------------------------------------------------

    def _set_active(self, active: bool) -> None:
        self.setProperty("dragActive", "true" if active else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    @staticmethod
    def _accepted_paths(urls) -> list[str]:
        accepted: list[str] = []
        for url in urls:
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
                accepted.append(str(path))
            elif path.is_dir():
                accepted.append(str(path))
        return accepted

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.mimeData().hasUrls() and self._accepted_paths(event.mimeData().urls()):
            event.acceptProposedAction()
            self._set_active(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._set_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._set_active(False)
        paths = self._accepted_paths(event.mimeData().urls())
        if paths:
            event.acceptProposedAction()
            self.files_dropped.emit(paths)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def set_enabled_state(self, enabled: bool) -> None:
        self.setEnabled(enabled)
        self._title.setVisible(enabled)
