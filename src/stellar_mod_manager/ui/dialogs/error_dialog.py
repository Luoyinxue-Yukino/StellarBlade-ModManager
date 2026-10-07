"""未捕获异常的友好提示。

程序出错时最糟的体验是「窗口突然没了，什么也没留下」。这个对话框保证三件事：

1. 告诉用户**出了什么事**，以及**程序还在不在**；
2. 给出**可复制的完整堆栈**，方便贴到 issue 或发给作者；
3. 一键**打开日志文件**，那里有上下文。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ... import logging_setup
from ..icons import icon
from ..theme import Palette, banner_style
from ..widgets.common import button

#: 同时最多弹一个错误框，避免连环异常刷屏
_active_dialog: "ErrorDialog | None" = None


class ErrorRelay(QObject):
    """把任意线程的异常安全地转到主线程弹窗。

    ``sys.excepthook`` 与 ``threading.excepthook`` 都可能在非主线程触发，
    而 Qt 只允许在主线程创建窗口。信号槽的跨线程投递正好解决这个问题。
    """

    raised = Signal(str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.raised.connect(self._show)

    def report(self, summary: str, detail: str) -> None:
        self.raised.emit(summary, detail)

    def _show(self, summary: str, detail: str) -> None:
        global _active_dialog

        if _active_dialog is not None:
            # 已经有一个错误框在屏幕上，只更新内容不再叠一个
            _active_dialog.append(summary, detail)
            return

        dialog = ErrorDialog(summary, detail)
        _active_dialog = dialog
        dialog.finished.connect(_clear_active)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()


def _clear_active(*_: object) -> None:
    global _active_dialog
    _active_dialog = None


class ErrorDialog(QDialog):
    """展示一条未捕获异常。"""

    def __init__(
        self,
        summary: str,
        detail: str,
        colors: Palette | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("出现了一个意外错误")
        self.setMinimumSize(620, 380)
        self.resize(680, 440)
        self._detail = detail
        self._count = 1

        # 没有父窗口时也得能看清，用深色兜底
        if colors is None:
            from ..theme import get_palette

            colors = get_palette("dark")
        self.colors = colors

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 16)
        root.setSpacing(12)

        root.addWidget(self._build_header(summary))
        root.addWidget(self._build_detail(), 1)
        root.addLayout(self._build_buttons())

    # ------------------------------------------------------------------

    def _build_header(self, summary: str) -> QWidget:
        frame = QFrame(self)
        danger = self.colors.qcolor("danger")
        frame.setStyleSheet(banner_style(self.colors, "danger", radius=11))
        row = QHBoxLayout(frame)
        row.setContentsMargins(14, 12, 14, 12)
        row.setSpacing(12)

        mark = QLabel(frame)
        mark.setPixmap(icon("alert", danger.name(), 26).pixmap(26, 26))
        row.addWidget(mark, 0, Qt.AlignTop)

        column = QVBoxLayout()
        column.setSpacing(4)

        headline = QLabel("程序遇到了一个意外错误", frame)
        headline.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {danger.name()};")
        column.addWidget(headline)

        self.summary_label = QLabel(summary, frame)
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.summary_label.setStyleSheet("font-size: 12px;")
        column.addWidget(self.summary_label)

        hint = QLabel(
            "程序仍在运行，但刚才的操作可能没有完成。详细信息已写入日志。",
            frame,
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        hint.setStyleSheet("font-size: 12px;")
        column.addWidget(hint)

        row.addLayout(column, 1)
        return frame

    def _build_detail(self) -> QWidget:
        holder = QWidget(self)
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        caption = QLabel("技术细节（可复制后反馈给开发者）", holder)
        caption.setProperty("role", "section")
        layout.addWidget(caption)

        self.detail_view = QPlainTextEdit(holder)
        self.detail_view.setReadOnly(True)
        self.detail_view.setProperty("mono", "true")
        self.detail_view.setPlainText(self._detail)
        layout.addWidget(self.detail_view, 1)
        return holder

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)

        copy_btn = button("复制详情", icon_name="copy", palette=self.colors, parent=self)
        copy_btn.clicked.connect(self._copy)
        row.addWidget(copy_btn)

        log_btn = button(
            "打开日志文件", icon_name="external", palette=self.colors, parent=self
        )
        log_btn.clicked.connect(logging_setup.open_log_folder)
        row.addWidget(log_btn)

        row.addStretch(1)

        close_btn = button("关闭", variant="primary", parent=self)
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        return row

    # ------------------------------------------------------------------

    def append(self, summary: str, detail: str) -> None:
        """已经有错误框时，把新异常追加进去而不是再弹一个。"""
        self._count += 1
        self._detail = f"{self._detail}\n\n{'=' * 70}\n\n{detail}"
        self.summary_label.setText(f"{summary}（本次共 {self._count} 个错误）")
        self.detail_view.setPlainText(self._detail)

    def _copy(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._detail)


__all__ = ["ErrorDialog", "ErrorRelay"]
