"""内置日志查看器。

让用户不必去翻 ``%LOCALAPPDATA%`` ——出问题时直接在这里看、筛选、复制。
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ... import logging_setup
from ..theme import Palette
from ..widgets.common import button

#: ``(显示名, 最低级别)``——级别名与 logging 的输出保持一致
_LEVELS = (
    ("全部", 0),
    ("信息及以上", 20),  # INFO
    ("警告及以上", 30),  # WARNING
    ("仅错误", 40),  # ERROR
)

_LEVEL_RE = re.compile(r"\s(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s")
_LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


class LogDialog(QDialog):
    """查看当前的运行日志。"""

    def __init__(self, colors: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.colors = colors

        self.setWindowTitle("运行日志")
        self.setMinimumSize(860, 560)
        self.resize(940, 640)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(10)

        root.addLayout(self._build_toolbar())
        root.addWidget(self._build_view(), 1)
        root.addLayout(self._build_buttons())

        self.reload()

    # ------------------------------------------------------------------

    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(9)

        row.addWidget(QLabel("级别", self))

        self.level_combo = QComboBox(self)
        for text, _ in _LEVELS:
            self.level_combo.addItem(text)
        self.level_combo.setFixedWidth(130)
        self.level_combo.currentIndexChanged.connect(self.reload)
        row.addWidget(self.level_combo)

        self.stats_label = QLabel("", self)
        self.stats_label.setProperty("role", "muted")
        self.stats_label.setStyleSheet("font-size: 12px;")
        row.addWidget(self.stats_label, 1)

        refresh_btn = button("刷新", icon_name="refresh", palette=self.colors, parent=self)
        refresh_btn.clicked.connect(self.reload)
        row.addWidget(refresh_btn)

        return row

    def _build_view(self) -> QPlainTextEdit:
        self.view = QPlainTextEdit(self)
        self.view.setReadOnly(True)
        self.view.setProperty("mono", "true")
        # 日志里常有很长的路径，这里让它按窗口宽度换行，比横向滚动好读
        self.view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.view.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
        )
        return self.view

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)

        copy_btn = button("复制全部", icon_name="copy", palette=self.colors, parent=self)
        copy_btn.clicked.connect(self._copy)
        row.addWidget(copy_btn)

        folder_btn = button("打开日志目录", icon_name="external", palette=self.colors, parent=self)
        folder_btn.clicked.connect(logging_setup.open_log_folder)
        row.addWidget(folder_btn)

        row.addStretch(1)

        close_btn = button("关闭", variant="primary", parent=self)
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        return row

    # ------------------------------------------------------------------

    def reload(self) -> None:
        """重新读取日志并按当前级别筛选。"""
        raw = logging_setup.read_log_tail()
        if not raw:
            self.view.setPlainText("（还没有日志内容）")
            self.stats_label.setText(logging_setup.log_file_path().name)
            return

        threshold = _LEVELS[max(0, self.level_combo.currentIndex())][1]
        kept: list[str] = []
        counts: dict[str, int] = {}
        for line in raw.splitlines():
            match = _LEVEL_RE.search(line)
            level = match.group(1) if match else ""
            if level:
                counts[level] = counts.get(level, 0) + 1
            # 会话头、堆栈续行没有级别标记，跟着上一条一起显示
            if not level or _LEVEL_ORDER.get(level, 0) >= threshold:
                kept.append(line)

        self.view.setPlainText("\n".join(kept))
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

        summary = "  ·  ".join(
            f"{name} {counts.get(name, 0)}"
            for name in ("ERROR", "WARNING", "INFO")
            if counts.get(name)
        )
        self.stats_label.setText(
            f"{logging_setup.log_file_path().name}  ·  {summary or '无记录'}"
        )

    def _copy(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.view.toPlainText())


__all__ = ["LogDialog"]
