"""分组的新建 / 重命名对话框。

用自绘的小对话框而不是 ``QInputDialog``：后者样式与全局 QSS 脱节，
在深色主题下会突兀地弹出一个系统观感的窗口。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from ..theme import SPACE_LG, SPACE_MD, Palette
from ..widgets.common import button

#: 分组名的长度上限。太长会把列表行撑变形，也几乎不可能用到。
MAX_NAME_LENGTH = 40


class GroupNameDialog(QDialog):
    """输入分组名。

    ``taken`` 由调用方传入「同级已存在的名字」集合，用于就地提示重名，
    不必等提交后再报错。
    """

    def __init__(
        self,
        colors: Palette,
        *,
        title: str,
        label_text: str,
        initial: str = "",
        taken: set[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.colors = colors
        self._taken = {name.casefold() for name in (taken or set())}

        self.setWindowTitle(title)
        self.setMinimumWidth(400)

        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_MD)
        root.setSpacing(SPACE_MD)

        caption = QLabel(label_text, self)
        root.addWidget(caption)

        self.edit = QLineEdit(initial, self)
        self.edit.setMaxLength(MAX_NAME_LENGTH)
        self.edit.selectAll()
        self.edit.textChanged.connect(self._validate)
        self.edit.returnPressed.connect(self._accept_if_valid)
        root.addWidget(self.edit)

        self.hint = QLabel("", self)
        self.hint.setStyleSheet(
            f"font-size: 12px; color: {colors.qcolor('danger').name()};"
        )
        self.hint.setVisible(False)
        root.addWidget(self.hint)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = button("取消", palette=colors, parent=self)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.ok_button = button("确定", variant="primary", parent=self)
        self.ok_button.clicked.connect(self._accept_if_valid)
        row.addWidget(self.ok_button)
        root.addLayout(row)

        self._validate(initial)

    # ------------------------------------------------------------------

    def _validate(self, text: str) -> None:
        clean = text.strip()
        problem = ""
        if not clean:
            problem = "分组名不能为空"
        elif clean.casefold() in self._taken:
            problem = "同级已经有同名的分组了"

        self.hint.setText(problem)
        self.hint.setVisible(bool(problem))
        self.ok_button.setEnabled(not problem)

    def _accept_if_valid(self) -> None:
        if self.ok_button.isEnabled():
            self.accept()

    @property
    def value(self) -> str:
        return self.edit.text().strip()

    # ------------------------------------------------------------------

    @classmethod
    def ask(
        cls,
        colors: Palette,
        *,
        title: str,
        label_text: str,
        initial: str = "",
        taken: set[str] | None = None,
        parent: QWidget | None = None,
    ) -> str | None:
        """弹出对话框，返回去掉首尾空白的名字；取消则返回 ``None``。"""
        dialog = cls(
            colors,
            title=title,
            label_text=label_text,
            initial=initial,
            taken=taken,
            parent=parent,
        )
        dialog.edit.setFocus(Qt.PopupFocusReason)
        if dialog.exec() == QDialog.Accepted:
            return dialog.value
        return None


__all__ = ["MAX_NAME_LENGTH", "GroupNameDialog"]
