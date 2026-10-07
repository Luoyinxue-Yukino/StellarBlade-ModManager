"""冲突检测结果对话框。

结论优先：顶部一条彩色横幅直接给「能不能进游戏」的答案，下面是可展开的明细，
最后保留工具原始报告以备排查。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.audit import AuditReport, ConflictKind
from ..icons import icon
from ..theme import Palette, banner_style
from ..widgets.common import button


class ConflictDialog(QDialog):
    """展示一次冲突检测的完整结果。"""

    def __init__(
        self, report: AuditReport, colors: Palette, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.report = report
        self.colors = colors

        self.setWindowTitle("Mod 冲突检测结果")
        self.setMinimumSize(780, 600)
        self.resize(880, 660)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 16)
        root.setSpacing(12)

        root.addWidget(self._build_banner())
        root.addWidget(self._build_meta())
        root.addWidget(self._build_tabs(), 1)
        root.addLayout(self._build_buttons())

    # ------------------------------------------------------------------
    # 顶部
    # ------------------------------------------------------------------

    def _build_banner(self) -> QWidget:
        text, role = self.report.verdict
        color = self.colors.qcolor(role)

        banner = QFrame(self)
        banner.setStyleSheet(banner_style(self.colors, role, radius=12))
        layout = QHBoxLayout(banner)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        icon_name = "check" if role == "success" else "alert"
        mark = QLabel(banner)
        mark.setPixmap(icon(icon_name, color.name(), 26).pixmap(26, 26))
        layout.addWidget(mark, 0, Qt.AlignTop)

        column = QVBoxLayout()
        column.setSpacing(3)

        headline = QLabel(text, banner)
        headline.setWordWrap(True)
        headline.setStyleSheet(
            f"font-size: 16px; font-weight: 700; color: {color.name()};"
        )
        column.addWidget(headline)

        if self.report.blocking_conflicts:
            hint = "请先停用冲突的 Mod 再启动游戏，否则可能卡在加载界面。"
        elif self.report.has_conflicts:
            hint = "游戏可以正常启动；若发现贴图或模型异常，再处理下面的冲突。"
        else:
            hint = "所有 Mod 的资源互不重叠，可以放心启动游戏。"
        sub = QLabel(hint, banner)
        sub.setWordWrap(True)
        sub.setStyleSheet("font-size: 12px;")
        column.addWidget(sub)

        layout.addLayout(column, 1)
        return banner

    def _build_meta(self) -> QWidget:
        report = self.report
        parts = [
            f"检测 {report.mod_count} 个 Mod",
            f"{report.utoc_count} 个资源包",
            f"{report.total_assets} 条资源",
            f"耗时 {report.duration_s:.1f} 秒",
            f"目录 {'、'.join(report.folders)}",
        ]
        label = QLabel("  ·  ".join(parts), self)
        label.setProperty("role", "muted")
        label.setStyleSheet("font-size: 12px;")
        label.setWordWrap(True)
        return label

    # ------------------------------------------------------------------
    # 标签页
    # ------------------------------------------------------------------

    def _build_tabs(self) -> QTabWidget:
        tabs = QTabWidget(self)
        tabs.addTab(
            self._build_conflict_tab(), f"冲突（{len(self.report.conflicts)}）"
        )
        tabs.addTab(
            self._build_override_tab(),
            f"覆盖原版资源（{len(self.report.override_mods)}）",
        )
        tabs.addTab(self._build_raw_tab(), "原始报告")
        return tabs

    def _build_conflict_tab(self) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        if not self.report.conflicts:
            layout.addWidget(
                _centered_hint(
                    "check",
                    self.colors,
                    "没有发现任何冲突",
                    "被多个 Mod 同时提供的资源、重复的 ChunkID 都没有出现。",
                )
            )
            return holder

        counts = QLabel(
            "  ·  ".join(
                f"{kind.label} {len(self.report.conflicts_of(kind))} 处"
                for kind in ConflictKind
                if self.report.conflicts_of(kind)
            ),
            holder,
        )
        counts.setStyleSheet("font-size: 12px;")
        counts.setWordWrap(True)
        layout.addWidget(counts)

        tree = QTreeWidget(holder)
        tree.setColumnCount(2)
        tree.setHeaderLabels(["资源 / 涉及的 Mod", "涉及 Mod 数"])
        tree.setSelectionMode(QAbstractItemView.SingleSelection)
        tree.setUniformRowHeights(True)
        header = tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)

        for kind in ConflictKind:
            items = self.report.conflicts_of(kind)
            if not items:
                continue

            group = QTreeWidgetItem(tree, [f"{kind.label}（{len(items)} 处）", ""])
            group.setFirstColumnSpanned(True)
            group.setToolTip(0, kind.explanation)
            color = self.colors.qcolor(kind.color_role)
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            group.setForeground(0, color)

            for conflict in items:
                owners = self.report.owners_of(conflict)
                node = QTreeWidgetItem(
                    group, [conflict.display_key, f"{len(owners)} 个"]
                )
                node.setToolTip(0, conflict.key)
                node.setToolTip(1, "\n".join(owners))
                for name in owners:
                    QTreeWidgetItem(node, [name, ""])
            group.setExpanded(True)

        layout.addWidget(tree, 1)

        legend = QLabel(_legend_text(), holder)
        legend.setWordWrap(True)
        legend.setProperty("role", "faint")
        legend.setStyleSheet("font-size: 11px;")
        layout.addWidget(legend)
        return holder

    def _build_override_tab(self) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        mods = self.report.override_mods
        if not mods:
            layout.addWidget(
                _centered_hint(
                    "shield",
                    self.colors,
                    "没有 Mod 替换游戏自带资源",
                    "所有 Mod 都只新增自己的资源，对原版内容零改动。",
                )
            )
            return holder

        note = QLabel(
            "下列 Mod 替换了游戏自带资源。这类 Mod 对游戏版本更敏感——"
            "游戏更新后如果出现异常，优先从它们查起。",
            holder,
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"font-size: 12px; color: {self.colors.warning};")
        layout.addWidget(note)

        tree = QTreeWidget(holder)
        tree.setColumnCount(2)
        tree.setHeaderLabels(["Mod / 被替换的资源", "条目数"])
        header = tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)

        for mod in mods:
            node = QTreeWidgetItem(
                tree, [mod.mod_name, f"{len(mod.overridden_assets)} 条"]
            )
            font = node.font(0)
            font.setBold(True)
            node.setFont(0, font)
            node.setToolTip(0, str(mod.utoc))
            for asset in mod.overridden_assets:
                QTreeWidgetItem(node, [asset, ""])
            node.setExpanded(False)

        layout.addWidget(tree, 1)
        return holder

    def _build_raw_tab(self) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        hint = QLabel("DekPakModAudit 输出的原始报告，排查问题时可以整段复制。", holder)
        hint.setProperty("role", "muted")
        hint.setStyleSheet("font-size: 12px;")
        layout.addWidget(hint)

        view = QPlainTextEdit(holder)
        view.setReadOnly(True)
        view.setProperty("mono", "true")
        view.setPlainText(self.report.raw_log or "（没有捕获到报告文本）")
        layout.addWidget(view, 1)
        return holder

    # ------------------------------------------------------------------
    # 按钮
    # ------------------------------------------------------------------

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)

        copy_btn = button("复制摘要", icon_name="copy", palette=self.colors, parent=self)
        copy_btn.clicked.connect(self._copy_summary)
        row.addWidget(copy_btn)

        open_btn = button("打开报告文件夹", icon_name="external", palette=self.colors, parent=self)
        open_btn.clicked.connect(self._open_report_folder)
        row.addWidget(open_btn)

        row.addStretch(1)

        close_btn = button("关闭", variant="primary", parent=self)
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        return row

    def _copy_summary(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.report.to_text())

    def _open_report_folder(self) -> None:
        _reveal(self.report.tool.directory / "output")


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _legend_text() -> str:
    return (
        "说明：ChunkID 冲突会导致游戏无法启动，必须处理；"
        "覆盖同一原版资源会让其中一个 Mod 失效，出现贴图异常时再处理；"
        "重复提供同一资源通常是同一作者共用的基础资源，一般无害。"
    )


def _centered_hint(icon_name: str, colors: Palette, title: str, detail: str) -> QWidget:
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setAlignment(Qt.AlignCenter)
    layout.setSpacing(8)

    mark = QLabel(holder)
    mark.setPixmap(icon(icon_name, colors.qcolor("success").name(), 40).pixmap(40, 40))
    mark.setAlignment(Qt.AlignCenter)
    layout.addWidget(mark, 0, Qt.AlignHCenter)

    headline = QLabel(title, holder)
    headline.setStyleSheet("font-size: 14px; font-weight: 600;")
    headline.setAlignment(Qt.AlignCenter)
    layout.addWidget(headline)

    sub = QLabel(detail, holder)
    sub.setProperty("role", "muted")
    sub.setWordWrap(True)
    sub.setAlignment(Qt.AlignCenter)
    layout.addWidget(sub)
    return holder


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
