"""检测工具缺失时的引导安装对话框。

本管理器不打包 DekPakModAudit（见 :data:`core.audit.DOWNLOAD_URL` 的说明），
所以当用户机器上没有它时，必须给出一条**能自己走完**的路：下载 → 放到哪 → 重新探测。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from ...core import audit
from ...core.audit import AUDIT_DIR_NAME, AUDIT_EXE_NAME, ToolLocation
from ...services.context import AppContext
from ..icons import icon
from ..theme import Palette
from ..widgets.common import Divider, button


class ToolSetupDialog(QDialog):
    """引导用户获取并放置 DekPakModAudit。

    关闭时若 :attr:`located` 有值，说明工具已经就绪。
    """

    def __init__(
        self, context: AppContext, colors: Palette, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.context = context
        self.colors = colors
        self.located: ToolLocation | None = None

        self.setWindowTitle("安装冲突检测工具")
        self.setMinimumWidth(620)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 16)
        root.setSpacing(14)

        root.addWidget(self._build_intro())
        root.addWidget(self._build_steps())
        root.addWidget(self._build_status())
        root.addLayout(self._build_buttons())

        self._refresh_status()

    # ------------------------------------------------------------------
    # 顶部说明
    # ------------------------------------------------------------------

    def _build_intro(self) -> QWidget:
        holder = QWidget(self)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        accent = self.colors.qcolor("accent")
        mark = QLabel(holder)
        mark.setPixmap(icon("shield", accent.name(), 26).pixmap(26, 26))
        mark.setFixedSize(46, 46)
        mark.setAlignment(Qt.AlignCenter)
        mark.setStyleSheet(
            f"QLabel {{ background-color: {self.colors.surface_alt};"
            f" border-radius: 13px; }}"
        )
        row.addWidget(mark, 0, Qt.AlignTop)

        column = QVBoxLayout()
        column.setSpacing(4)
        title = QLabel("需要先安装冲突检测工具", holder)
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        column.addWidget(title)

        body = QLabel(
            "冲突检测由第三方工具 <b>DekPakModAudit</b> 完成，它不随本管理器分发，"
            "每台机器只需单独下载一次。装好之后本管理器会自动识别，以后都不用再管。",
            holder,
        )
        body.setWordWrap(True)
        body.setProperty("role", "muted")
        body.setStyleSheet("font-size: 12px;")
        column.addWidget(body)
        row.addLayout(column, 1)
        return holder

    # ------------------------------------------------------------------
    # 步骤
    # ------------------------------------------------------------------

    def _build_steps(self) -> QFrame:
        card = QFrame(self)
        card.setProperty("role", "card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        # 第 1 步：下载
        download_row = QHBoxLayout()
        download_row.setSpacing(10)
        download_row.addWidget(self._step_label("1"))

        download_text = QVBoxLayout()
        download_text.setSpacing(2)
        download_title = QLabel("下载 DekPakModAudit", card)
        download_title.setStyleSheet("font-size: 13px; font-weight: 600;")
        download_text.addWidget(download_title)

        url = self._download_url()
        hint = QLabel(
            "点右侧按钮打开发布页。" if url else "请向本管理器的分发者索取下载地址。",
            card,
        )
        hint.setProperty("role", "muted")
        hint.setStyleSheet("font-size: 12px;")
        download_text.addWidget(hint)
        download_row.addLayout(download_text, 1)

        if url:
            open_btn = button("打开下载页", icon_name="external", palette=self.colors, parent=card)
            open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
            download_row.addWidget(open_btn, 0, Qt.AlignVCenter)

        layout.addLayout(download_row)
        layout.addWidget(Divider(card))

        # 第 2 步：放到哪
        place_row = QHBoxLayout()
        place_row.setSpacing(10)
        place_row.addWidget(self._step_label("2"))

        place_text = QVBoxLayout()
        place_text.setSpacing(6)
        place_title = QLabel("解压到游戏根目录，保持文件夹名为 " + AUDIT_DIR_NAME, card)
        place_title.setStyleSheet("font-size: 13px; font-weight: 600;")
        place_text.addWidget(place_title)

        self.game_path_edit = QLineEdit(card)
        self.game_path_edit.setReadOnly(True)
        self.game_path_edit.setProperty("mono", "true")
        game_root = self.context.config.game_root
        self.game_path_edit.setText(game_root or "（还没有配置游戏目录）")
        # setText 会把光标停在末尾，长路径就会把开头的盘符滚出视野
        self.game_path_edit.setCursorPosition(0)
        place_text.addWidget(self.game_path_edit)

        self.layout_label = QLabel(card)
        self.layout_label.setProperty("mono", "true")
        self.layout_label.setStyleSheet(
            f"font-size: 11px; color: {self.colors.text_faint};"
        )
        self.layout_label.setText(
            f"放好后应当是这样：  {game_root or '<游戏目录>'}\\{AUDIT_DIR_NAME}\\{AUDIT_EXE_NAME}"
        )
        self.layout_label.setWordWrap(True)
        place_text.addWidget(self.layout_label)

        place_row.addLayout(place_text, 1)

        place_buttons = QVBoxLayout()
        place_buttons.setSpacing(8)
        copy_btn = button("复制路径", icon_name="copy", palette=self.colors, parent=card)
        copy_btn.clicked.connect(self._copy_game_root)
        copy_btn.setEnabled(bool(game_root))
        place_buttons.addWidget(copy_btn)

        open_dir_btn = button("打开游戏目录", icon_name="folder", palette=self.colors, parent=card)
        open_dir_btn.clicked.connect(self._open_game_root)
        open_dir_btn.setEnabled(bool(game_root))
        place_buttons.addWidget(open_dir_btn)
        place_buttons.addStretch(1)
        place_row.addLayout(place_buttons)

        layout.addLayout(place_row)
        layout.addWidget(Divider(card))

        # 第 3 步：重新探测
        detect_row = QHBoxLayout()
        detect_row.setSpacing(10)
        detect_row.addWidget(self._step_label("3"))

        detect_text = QVBoxLayout()
        detect_text.setSpacing(2)
        detect_title = QLabel("点「重新探测」，管理器会自动找到它", card)
        detect_title.setStyleSheet("font-size: 13px; font-weight: 600;")
        detect_text.addWidget(detect_title)
        detect_hint = QLabel(
            "如果放到了别的位置，用「手动指定…」直接选中 "
            f"{AUDIT_EXE_NAME} 即可。",
            card,
        )
        detect_hint.setProperty("role", "muted")
        detect_hint.setStyleSheet("font-size: 12px;")
        detect_text.addWidget(detect_hint)
        detect_row.addLayout(detect_text, 1)
        layout.addLayout(detect_row)

        return card

    def _step_label(self, text: str) -> QLabel:
        """序号圆点：实心强调色底 + 深色数字，比淡底彩字更清楚。"""
        label = QLabel(text, self)
        label.setFixedSize(22, 22)
        label.setAlignment(Qt.AlignCenter)
        accent = self.colors.qcolor("accent")
        label.setStyleSheet(
            f"QLabel {{ background-color: {accent.name()}; color: {self.colors.bg};"
            f" border-radius: 11px; font-size: 12px; font-weight: 700; }}"
        )
        return label

    # ------------------------------------------------------------------
    # 状态与按钮
    # ------------------------------------------------------------------

    def _build_status(self) -> QLabel:
        self.status_label = QLabel(self)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("font-size: 12px;")
        return self.status_label

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)

        locate_btn = button("手动指定…", icon_name="search", palette=self.colors, parent=self)
        locate_btn.clicked.connect(self._locate_manually)
        row.addWidget(locate_btn)

        row.addStretch(1)

        skip_btn = button("以后再说", variant="ghost", palette=self.colors, parent=self)
        skip_btn.clicked.connect(self.reject)
        row.addWidget(skip_btn)

        self.retry_btn = button(
            "重新探测", variant="primary", icon_name="refresh", palette=self.colors, parent=self
        )
        self.retry_btn.clicked.connect(self._detect)
        row.addWidget(self.retry_btn)

        return row

    def _refresh_status(self) -> None:
        tool = self.context.audit_tool
        if tool is not None and tool.has_config:
            self.status_label.setText(f"✔ 已找到：{tool.executable}")
            self.status_label.setStyleSheet(
                f"font-size: 12px; color: {self.colors.success};"
            )
            self.located = tool
            self.retry_btn.setText("完成")
        elif tool is not None:
            self.status_label.setText(
                f"⚠ 找到了 {tool.executable.name}，但缺少 input\\"
                f"{tool.config_path.name}，解压可能不完整。"
            )
            self.status_label.setStyleSheet(
                f"font-size: 12px; color: {self.colors.warning};"
            )
            self.located = None
            self.retry_btn.setText("重新探测")
        else:
            self.status_label.setText("尚未找到检测工具。")
            self.status_label.setStyleSheet(
                f"font-size: 12px; color: {self.colors.text_muted};"
            )
            self.located = None
            self.retry_btn.setText("重新探测")

    # ------------------------------------------------------------------
    # 动作
    # ------------------------------------------------------------------

    def _download_url(self) -> str:
        return self.context.config.audit_download_url or audit.DOWNLOAD_URL

    def _copy_game_root(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.context.config.game_root)
        self.context.notify("游戏目录已复制到剪贴板", "success")

    def _open_game_root(self) -> None:
        root = self.context.config.game_root
        if not root:
            return
        _reveal(Path(root))

    def _detect(self) -> None:
        if self.located is not None:
            self.accept()
            return

        # 清掉手动指定的旧路径，让自动探测重新发挥
        self.context.config.audit_tool_dir = ""
        self.context.config.save()
        self._refresh_status()

        if self.located is not None:
            self.context.notify("已找到 DekPakModAudit", "success")
        else:
            self.context.notify("还是没找到，请确认文件夹名与层级", "warning")

    def _locate_manually(self) -> None:
        start = (
            self.context.config.audit_tool_dir
            or self.context.config.game_root
        )
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            f"选择 {AUDIT_EXE_NAME}",
            start or "",
            "可执行文件 (*.exe);;所有文件 (*)",
        )
        if not chosen:
            return

        self.context.config.audit_tool_dir = chosen
        self.context.config.save()
        self._refresh_status()

        if self.located is not None:
            self.context.notify("检测工具已配置", "success")


def _reveal(path: Path) -> None:
    try:
        if not path.is_dir():
            return
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606 - 打开资源管理器是预期行为
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        pass
