"""压缩包页：批量查看并解压本地压缩包（不写入游戏）。

这一页是「解压」能力的独立入口：选择一个目录，扫描出里面的压缩包，查看内容，
再解压到指定位置。适合先解压检查、再手动安装的工作流。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core import paths
from ...core.archive import (
    SUPPORTED_SUFFIXES,
    ArchiveError,
    ExtractionCancelled,
    detect_format,
    extract_archive,
    inspect_archive,
)
from ...core.formatting import human_size, truncate_middle
from ...core.models import ArchiveInspection
from ...services.worker import Task, TaskCancelled
from ..theme import SPACE_LG, SPACE_MD, SPACE_SM
from ..widgets.common import Card, EmptyState, button
from .base import Page, card_header

from ...logging_setup import get_logger
logger = get_logger(__name__)


@dataclass
class ScanRow:
    """扫描结果的一行。"""

    path: Path
    size: int
    inspection: ArchiveInspection | None = None
    result: str = ""


class ArchivesPage(Page):
    title = "压缩包"
    subtitle = "查看并解压本地压缩包"

    def __init__(self, context, colors, parent: QWidget | None = None) -> None:
        super().__init__(context, colors, parent)
        self._rows: list[ScanRow] = []
        self._task: Task | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(SPACE_LG)

        root.addWidget(self._build_source_card())
        root.addWidget(self._build_table(), 1)
        root.addWidget(self._build_action_card())

        start = Path(self.context.config.last_browse_dir) if self.context.config.last_browse_dir else None
        if start is None or not start.is_dir():
            start = paths.default_downloads_dir()
        self.folder_edit.setText(str(start))

    # ------------------------------------------------------------------
    # 布局
    # ------------------------------------------------------------------

    def _build_source_card(self) -> Card:
        card = Card(padding=14, spacing=8)
        card.body.addWidget(
            card_header(
                "folder",
                "来源目录",
                "扫描该目录（含子目录）下的 zip / 7z / rar / tar 压缩包。",
                self.colors,
            )
        )

        row = QHBoxLayout()
        row.setSpacing(8)

        self.folder_edit = QLineEdit()
        self.folder_edit.setProperty("mono", "true")
        self.folder_edit.setPlaceholderText("选择包含压缩包的目录…")
        row.addWidget(self.folder_edit, 1)

        browse_btn = button("浏览…", icon_name="folder", palette=self.colors)
        browse_btn.clicked.connect(self._browse_folder)
        row.addWidget(browse_btn)

        self.scan_btn = button("扫描", variant="primary", icon_name="search", palette=self.colors)
        self.scan_btn.clicked.connect(self._scan)
        row.addWidget(self.scan_btn)

        card.body.addLayout(row)

        self.summary_label = QLabel("尚未扫描")
        self.summary_label.setProperty("role", "muted")
        self.summary_label.setStyleSheet("font-size: 12px;")
        card.body.addWidget(self.summary_label)
        return card

    def _build_table(self) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_SM)

        self.table = QTableWidget(0, 5, holder)
        self.table.setHorizontalHeaderLabels(["文件", "格式", "大小", "内容", "状态"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        # 行高固定，让 Qt 跳过逐行测量；长路径靠 tooltip 看全，不换行撑高行
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(34)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)

        # 空表时不能只留一片黑：换个说法告诉用户下一步做什么。
        # 文案随「有没有扫描过」变化——没扫描是引导，扫描了但没结果是结论。
        self.table_empty = EmptyState(
            self.colors,
            icon_name="archive",
            title="还没有扫描",
            description=(
                "选好来源目录后点「扫描」，这里会列出目录（含子目录）里"
                "所有 zip / 7z / rar / tar 压缩包。"
            ),
        )

        layout.addWidget(self.table, 1)
        layout.addWidget(self.table_empty, 1)
        self._show_table_empty(True, scanned=False)
        return holder

    def _show_table_empty(self, empty: bool, *, scanned: bool) -> None:
        """在表格与空状态之间切换，并让文案贴合当前情况。

        「还没扫描」是引导，「扫描了但没结果」是结论——两者要说不同的话。
        """
        if empty:
            if scanned:
                self.table_empty.configure(
                    title="这个目录里没有压缩包",
                    description="换一个目录，或者把要处理的压缩包放进来源目录后重新扫描。",
                )
            else:
                self.table_empty.configure(
                    title="还没有扫描",
                    description=(
                        "选好来源目录后点「扫描」，这里会列出目录（含子目录）里"
                        "所有 zip / 7z / rar / tar 压缩包。"
                    ),
                )
        self.table.setVisible(not empty)
        self.table_empty.setVisible(empty)

    def _build_action_card(self) -> Card:
        card = Card(padding=14, spacing=8)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("解压到"))

        self.dest_edit = QLineEdit()
        self.dest_edit.setProperty("mono", "true")
        self.dest_edit.setPlaceholderText(str(self.context.config.staging_path))
        row.addWidget(self.dest_edit, 1)

        dest_btn = button("浏览…", icon_name="folder", palette=self.colors)
        dest_btn.clicked.connect(self._browse_destination)
        row.addWidget(dest_btn)

        card.body.addLayout(row)

        actions = QHBoxLayout()
        actions.setSpacing(SPACE_MD)

        self.cleanup_check = QCheckBox("解压前清空同名目录")
        self.cleanup_check.setChecked(True)
        actions.addWidget(self.cleanup_check)

        self.open_after_check = QCheckBox("完成后打开目标目录")
        actions.addWidget(self.open_after_check)

        actions.addStretch(1)

        self.extract_btn = button(
            "解压选中项", variant="primary", icon_name="extract", palette=self.colors
        )
        self.extract_btn.clicked.connect(self._start_extract)
        self.extract_btn.setEnabled(False)
        actions.addWidget(self.extract_btn)

        self.cancel_btn = button("取消", variant="danger", icon_name="stop", palette=self.colors)
        self.cancel_btn.clicked.connect(self._cancel)
        self.cancel_btn.setVisible(False)
        actions.addWidget(self.cancel_btn)

        card.body.addLayout(actions)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        card.body.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setProperty("role", "muted")
        self.status_label.setStyleSheet("font-size: 12px;")
        card.body.addWidget(self.status_label)

        self.table.itemSelectionChanged.connect(self._update_buttons)
        return card

    # ------------------------------------------------------------------
    # 扫描
    # ------------------------------------------------------------------

    def _browse_folder(self) -> None:
        start = self.folder_edit.text().strip() or str(paths.default_downloads_dir())
        chosen = QFileDialog.getExistingDirectory(self, "选择包含压缩包的目录", start)
        if chosen:
            self.folder_edit.setText(chosen)
            self._scan()

    def _scan(self) -> None:
        folder = Path(self.folder_edit.text().strip())
        if not folder.is_dir():
            self.context.notify("请选择一个存在的目录", "warning")
            return

        files = sorted(
            (
                p
                for p in folder.rglob("*")
                if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
            ),
            key=lambda p: p.name.lower(),
        )

        self._rows = [ScanRow(path=p, size=_safe_size(p)) for p in files]
        self._populate_table()

        self.summary_label.setText(
            f"在 {folder} 下找到 {len(files)} 个压缩包"
            if files
            else f"{folder} 下没有找到压缩包"
        )
        self._update_buttons()

        if files:
            self._inspect_all()

    def _populate_table(self) -> None:
        self.table.setRowCount(len(self._rows))
        for index, row in enumerate(self._rows):
            self._fill_row(index, row)
        # 扫描过了但一个压缩包都没有，也要给出结论而不是留一片空表
        self._show_table_empty(not self._rows, scanned=True)

    def _fill_row(self, index: int, row: ScanRow) -> None:
        name_item = QTableWidgetItem(truncate_middle(row.path.name, 48))
        name_item.setToolTip(str(row.path))
        self.table.setItem(index, 0, name_item)

        fmt = row.inspection.archive_format if row.inspection else detect_format(row.path)
        self.table.setItem(index, 1, QTableWidgetItem(fmt.display_name))

        size_item = QTableWidgetItem(human_size(row.size))
        size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.table.setItem(index, 2, size_item)

        if row.inspection is None:
            content = "分析中…"
        elif not row.inspection.ok:
            content = "—"
        else:
            payloads = len(row.inspection.payload_entries)
            content = (
                f"{row.inspection.file_count} 个文件"
                + (f"，{payloads} 个 Mod 文件" if payloads else "（无 Mod 文件）")
            )
        self.table.setItem(index, 3, QTableWidgetItem(content))

        if row.result:
            status = row.result
        elif row.inspection is None:
            status = "…"
        elif not row.inspection.ok:
            status = row.inspection.error or "无法读取"
        elif row.inspection.encrypted:
            status = "已加密"
        elif not row.inspection.has_payload:
            status = "不含 Mod 文件"
        else:
            status = "就绪"

        status_item = QTableWidgetItem(status)
        if status == "就绪":
            status_item.setForeground(self.colors.qcolor("success"))
        elif status in ("不含 Mod 文件", "已加密", "…"):
            status_item.setForeground(self.colors.qcolor("warning"))
        elif status not in ("完成",):
            status_item.setForeground(self.colors.qcolor("danger"))
        self.table.setItem(index, 4, status_item)

    def _inspect_all(self) -> None:
        """后台分析每个压缩包，避免大文件卡住界面。"""
        rows = list(self._rows)
        if not rows:
            return

        task = Task(lambda t: self._work_inspect(t, rows), self, label="分析压缩包")
        task.progressed.connect(self._on_inspect_progress)
        task.succeeded.connect(lambda _: self.status_label.setText("分析完成"))
        task.failed.connect(lambda msg: self.status_label.setText(f"分析失败：{msg}"))
        self._task = task
        self.status_label.setText("正在分析压缩包…")
        self.context.tasks.start(task)

    @staticmethod
    def _work_inspect(task: Task, rows: list[ScanRow]) -> int:
        total = len(rows)
        for index, row in enumerate(rows, start=1):
            if task.cancelled:
                raise TaskCancelled()
            row.inspection = inspect_archive(row.path)
            task.report(index, total, row.path.name)
        return total

    def _on_inspect_progress(self, done: int, total: int, message: str) -> None:
        self.summary_label.setText(f"正在分析 {done}/{total}：{message}")
        if 0 < done <= len(self._rows):
            self._fill_row(done - 1, self._rows[done - 1])

    # ------------------------------------------------------------------
    # 解压
    # ------------------------------------------------------------------

    def _browse_destination(self) -> None:
        start = self.dest_edit.text().strip() or str(self.context.config.staging_path)
        chosen = QFileDialog.getExistingDirectory(self, "选择解压目标目录", start)
        if chosen:
            self.dest_edit.setText(chosen)

    def _selected_rows(self) -> list[tuple[int, ScanRow]]:
        indexes = {i.row() for i in self.table.selectedIndexes()}
        return [(i, self._rows[i]) for i in sorted(indexes) if 0 <= i < len(self._rows)]

    def _start_extract(self) -> None:
        selected = self._selected_rows()
        if not selected:
            self.context.notify("请先选中要解压的压缩包", "warning")
            return

        destination = Path(self.dest_edit.text().strip() or str(self.context.config.staging_path))
        try:
            destination.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.context.notify(f"无法创建目标目录：{exc}", "error")
            return

        self.dest_edit.setText(str(destination))
        logger.info("开始解压 %d 个压缩包到 %s", len(selected), destination)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.extract_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)

        cleanup = self.cleanup_check.isChecked()
        task = Task(
            lambda t: self._work_extract(t, selected, destination, cleanup),
            self,
            label="解压压缩包",
        )
        task.progressed.connect(self._on_extract_progress)
        task.succeeded.connect(self._on_extract_finished)
        task.failed.connect(self._on_extract_failed)
        task.cancelled_signal.connect(self._on_extract_cancelled)
        self._task = task
        self.context.tasks.start(task)

    @staticmethod
    def _work_extract(
        task: Task,
        selected: list[tuple[int, ScanRow]],
        destination: Path,
        cleanup: bool,
    ) -> list[tuple[int, str]]:
        outcomes: list[tuple[int, str]] = []
        total = len(selected)

        for position, (row_index, row) in enumerate(selected, start=1):
            if task.cancelled:
                raise TaskCancelled()

            target = destination / _safe_folder(row.path.stem)
            if cleanup and target.exists():
                shutil.rmtree(target, ignore_errors=True)

            def report(done: int, count: int, message: str, *, _p=position, _n=row.path.name) -> None:
                fraction = (done / count) if count else 0.0
                task.report(int((_p - 1 + fraction) * 100 / total), 100, f"{_n} — {message}")

            try:
                result = extract_archive(
                    row.path, target, progress=report, cancel=lambda: task.cancelled
                )
            except ExtractionCancelled as exc:
                raise TaskCancelled() from exc
            except ArchiveError as exc:
                row.result = f"失败：{exc}"
                outcomes.append((row_index, row.result))
                continue

            suffix = (
                f"（跳过 {len(result.skipped)} 项）" if result.skipped else ""
            )
            row.result = f"完成：{result.files_written} 个文件{suffix}"
            outcomes.append((row_index, row.result))
            task.report(int(position * 100 / total), 100, row.path.name)

        return outcomes

    def _on_extract_progress(self, done: int, total: int, message: str) -> None:
        self.progress_bar.setValue(max(0, min(100, done)))
        self.status_label.setText(truncate_middle(message, 100))

    def _on_extract_finished(self, outcomes: list[tuple[int, str]]) -> None:
        self._task = None
        self.cancel_btn.setVisible(False)
        self.progress_bar.setValue(100)

        failed = sum(1 for _, text in outcomes if text.startswith("失败"))
        for row_index, text in outcomes:
            if 0 <= row_index < len(self._rows):
                self._fill_row(row_index, self._rows[row_index])

        self.status_label.setText(
            f"解压完成：成功 {len(outcomes) - failed} 个"
            + (f"，失败 {failed} 个" if failed else "")
        )
        logger.info(
            "解压结束：成功 %d，失败 %d", len(outcomes) - failed, failed
        )
        self.context.notify(
            self.status_label.text(), "error" if failed else "success"
        )
        self._update_buttons()

        if self.open_after_check.isChecked():
            destination = Path(self.dest_edit.text().strip())
            if destination.is_dir():
                _open_directory(destination)

    def _on_extract_failed(self, message: str) -> None:
        self._task = None
        self.cancel_btn.setVisible(False)
        self.status_label.setText(f"解压失败：{message}")
        self.context.notify(f"解压失败：{message}", "error")
        self._update_buttons()

    def _on_extract_cancelled(self) -> None:
        self._task = None
        self.cancel_btn.setVisible(False)
        self.progress_bar.setVisible(False)
        self.status_label.setText("已取消")
        self.context.notify("已取消解压", "warning")
        self._update_buttons()

    def _cancel(self) -> None:
        if self._task is not None and self._task.isRunning():
            self._task.request_cancel()
            self.status_label.setText("正在取消…")

    def _update_buttons(self) -> None:
        busy = self._task is not None and self._task.isRunning()
        self.extract_btn.setEnabled(bool(self.table.selectedIndexes()) and not busy)
        self.scan_btn.setEnabled(not busy)
        self.table.setEnabled(not busy)


def _safe_folder(name: str) -> str:
    cleaned = "".join(ch for ch in name if ch not in '<>:"/\\|?*').strip(" .")
    return cleaned or "archive"


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _open_directory(path: Path) -> None:
    import os
    import sys

    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606
        elif sys.platform == "darwin":
            import subprocess

            subprocess.Popen(["open", str(path)])
        else:
            import subprocess

            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        pass
