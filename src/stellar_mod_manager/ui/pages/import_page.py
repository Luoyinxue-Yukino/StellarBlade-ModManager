"""导入安装页：拖入压缩包 → 解压 → 安装到 ``~mods``。

这是「解压功能」在界面上的主入口。整个流程放在后台线程里跑，界面通过信号更新
进度，因此大压缩包也不会卡死。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core import paths
from ...core.archive import (
    SUPPORTED_SUFFIXES,
    ArchiveError,
    ExtractionCancelled,
    extract_archive,
    find_mod_files,
    guess_mod_name,
    inspect_archive,
)
from ...core.formatting import human_size, truncate_middle
from ...core.library import FolderImportResult, LibraryError
from ...core.models import ArchiveInspection, Mod
from ...services.worker import Task, TaskCancelled
from ..theme import SPACE_LG, SPACE_SM
from ..widgets.common import EmptyState, button, icon_button
from ..widgets.drop_zone import DropZone
from .base import Page

from ...logging_setup import get_logger
logger = get_logger(__name__)


@dataclass
class QueueItem:
    """待导入队列中的一项。"""

    path: Path
    inspection: ArchiveInspection
    mod_name: str = ""
    staging: Path | None = None
    installed: Mod | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.inspection.ok and self.inspection.has_payload and self.error is None


class ImportPage(Page):
    title = "导入安装"
    subtitle = "把 Mod 压缩包解压并安装到游戏中"

    def __init__(self, context, colors, parent: QWidget | None = None) -> None:
        super().__init__(context, colors, parent)
        self._queue: list[QueueItem] = []
        self._rows: list[QWidget] = []
        self._task: Task | None = None
        self._progress_base = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(SPACE_LG)

        self.drop_zone = DropZone(colors, parent=self)
        self.drop_zone.files_dropped.connect(self._on_dropped)
        self.drop_zone.clicked.connect(self._choose_files)
        root.addWidget(self.drop_zone)

        root.addLayout(self._build_options())
        root.addWidget(self._build_queue_area(), 1)
        root.addWidget(self._build_progress())

        context.game_root_changed.connect(self._sync_enabled)

    # ------------------------------------------------------------------
    # 布局
    # ------------------------------------------------------------------

    def _build_options(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(SPACE_LG)

        self.check_install = QCheckBox("导入后立即启用（部署进游戏）")
        self.check_install.setChecked(True)
        self.check_install.setToolTip(
            "Mod 一律先存进管理器自己的库；勾选则同时部署到游戏的 ~mods 目录。\n"
            "取消勾选就只入库，之后在「Mod 库」页按需启用。"
        )
        row.addWidget(self.check_install)

        self.check_cleanup = QCheckBox("安装后清理暂存文件")
        self.check_cleanup.setChecked(True)
        row.addWidget(self.check_cleanup)

        row.addStretch(1)

        self.folder_btn = button(
            "从文件夹导入…", icon_name="folder", palette=self.colors
        )
        self.folder_btn.setToolTip(
            "把一个目录下已解压好的多个 Mod 文件夹批量收进库。\n"
            "同盘使用硬链接，不会额外占用空间，原目录也不会被动。"
        )
        self.folder_btn.clicked.connect(self._import_folder)
        row.addWidget(self.folder_btn)

        self.clear_btn = button("清空列表", variant="ghost", palette=self.colors)
        self.clear_btn.clicked.connect(self._clear_queue)
        row.addWidget(self.clear_btn)

        self.start_btn = button("开始导入", variant="primary", icon_name="play", palette=self.colors)
        self.start_btn.clicked.connect(self._start_import)
        row.addWidget(self.start_btn)

        self.cancel_btn = button("取消", variant="danger", icon_name="stop", palette=self.colors)
        self.cancel_btn.clicked.connect(self._cancel_import)
        self.cancel_btn.setVisible(False)
        row.addWidget(self.cancel_btn)

        return row

    def _build_queue_area(self) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.queue_title = QLabel("待导入（0）")
        self.queue_title.setProperty("role", "section")
        layout.addWidget(self.queue_title)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 10, SPACE_SM)
        self.list_layout.setSpacing(6)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.list_host)

        self.placeholder = EmptyState(
            self.colors,
            icon_name="import",
            title="还没有选择压缩包",
            description=(
                "把 Mod 压缩包拖到上方区域，或点「选择文件…」；"
                "已经解压好的 Mod 也可以用「从文件夹导入…」整批纳入。"
            ),
        )

        # 两者都给拉伸因子：谁可见谁就占满剩余空间，文字居中。
        # 只给其中一个的话，另一个隐藏时空间仍被预留，占位文字会被挤到底部。
        layout.addWidget(self.scroll, 1)
        layout.addWidget(self.placeholder, 1)
        return holder

    def _build_progress(self) -> QWidget:
        holder = QWidget()
        holder.setVisible(False)
        self.progress_box = holder

        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(6)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)

        self.progress_label = QLabel("")
        self.progress_label.setProperty("role", "muted")
        self.progress_label.setStyleSheet("font-size: 12px;")
        layout.addWidget(self.progress_label)

        return holder

    # ------------------------------------------------------------------
    # 队列维护
    # ------------------------------------------------------------------

    def on_show(self) -> None:
        self._sync_enabled()

    def _sync_enabled(self) -> None:
        ready = self.context.is_ready
        self.check_install.setEnabled(ready)
        if not ready and self.check_install.isChecked():
            self.check_install.setChecked(False)
        self._update_buttons()

    def _choose_files(self) -> None:
        patterns = " ".join(f"*{suffix}" for suffix in SUPPORTED_SUFFIXES)
        start = self.context.config.last_browse_dir or str(paths.default_downloads_dir())
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择 Mod 压缩包", start, f"压缩包 ({patterns});;所有文件 (*)"
        )
        if files:
            self.context.config.last_browse_dir = str(Path(files[0]).parent)
            self.context.config.save()
            self._add_paths(files)

    def _on_dropped(self, paths: list[str]) -> None:
        self._add_paths(paths)

    # ------------------------------------------------------------------
    # 从文件夹批量导入（已解压好的一堆 Mod）
    # ------------------------------------------------------------------

    def _import_folder(self) -> None:
        if self._task is not None and self._task.isRunning():
            self.context.notify("已有任务在运行，请稍候", "warning")
            return

        start = self.context.config.last_browse_dir or str(paths.default_downloads_dir())
        chosen = QFileDialog.getExistingDirectory(
            self, "选择包含多个 Mod 文件夹的目录", start
        )
        if not chosen:
            return

        root = Path(chosen)
        folders = [
            d
            for d in sorted(root.iterdir())
            if d.is_dir() and not d.name.startswith(".") and find_mod_files(d)
        ]
        if not folders:
            self.context.notify("这个目录下没有找到含 .pak/.utoc/.ucas 的子文件夹", "warning")
            return

        answer = QMessageBox.question(
            self,
            "从文件夹导入",
            f"在\n{root}\n下找到 {len(folders)} 个 Mod 文件夹。\n\n"
            f"全部收进库：\n{self.context.library_dir}\n\n"
            "同盘会使用硬链接，不会额外占用空间，原目录也不会被动过。\n"
            "导入的 Mod 保持停用，之后在「Mod 库」页按需启用。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return

        self.context.config.last_browse_dir = str(root)
        self.context.config.save()

        self.progress_box.setVisible(True)
        self.progress_bar.setProperty("state", "running")
        self.progress_bar.setValue(0)
        self.progress_label.setText("准备中…")

        task = Task(
            lambda t: self.context.library.import_folder_tree(
                root,
                link=True,
                progress=t.report,
                cancel=lambda: t.cancelled,
            ),
            self,
            label="从文件夹导入",
        )
        task.progressed.connect(self._on_progress)
        task.succeeded.connect(self._on_folder_imported)
        task.failed.connect(self._on_failed)
        task.cancelled_signal.connect(self._on_cancelled)

        self._task = task
        self._update_buttons()
        self.context.tasks.start(task)

    def _on_folder_imported(self, result: FolderImportResult) -> None:
        self._task = None
        self.progress_bar.setValue(100)
        self.progress_bar.setProperty("state", "error" if result.failed else "done")
        self.progress_bar.style().unpolish(self.progress_bar)
        self.progress_bar.style().polish(self.progress_bar)

        size = human_size(result.total_bytes)
        text = f"导入完成：{result.imported_count} 个 Mod（{size}）"
        if result.failed:
            text += f"，{len(result.failed)} 个失败"
        self.progress_label.setText(text)
        self.context.notify(text, "error" if result.failed else "success")
        self.context.mods_updated()
        self._update_buttons()

    def _add_paths(self, paths: list[str]) -> None:
        added = 0
        for raw in paths:
            path = Path(raw)
            if path.is_dir():
                # 拖进来的是文件夹：把里面的压缩包都收进来
                nested = [
                    p
                    for p in sorted(path.rglob("*"))
                    if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
                ]
                added += self._add_one_by_one(nested)
                continue
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
                added += self._add_one_by_one([path])

        if added:
            self._render_queue()
            self.context.notify(f"已加入 {added} 个压缩包", "success")
        else:
            self.context.notify("没有找到可识别的压缩包", "warning")

    def _add_one_by_one(self, paths: list[Path]) -> int:
        count = 0
        known = {item.path for item in self._queue}
        for path in paths:
            if path in known:
                continue
            self._queue.append(self._inspect(path))
            count += 1
        return count

    def _inspect(self, path: Path) -> QueueItem:
        """只读探测压缩包，并把可能的坑写进 warnings。"""
        inspection = inspect_archive(path)
        item = QueueItem(path=path, inspection=inspection, mod_name=guess_mod_name(path))

        if not inspection.ok:
            item.error = inspection.error
            return item
        if inspection.encrypted:
            item.warnings.append("压缩包已加密，导入时可能需要密码")
        if inspection.unsafe_entries:
            item.warnings.append(
                f"{len(inspection.unsafe_entries)} 个条目含越界路径，导入时会被跳过"
            )
        if not inspection.has_payload:
            item.error = "压缩包里没有 .pak / .utoc / .ucas 文件"
        return item

    def _clear_queue(self) -> None:
        self._queue.clear()
        self._render_queue()

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------

    def _render_queue(self) -> None:
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()

        for item in self._queue:
            row = self._build_row(item)
            self._rows.append(row)
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)

        count = len(self._queue)
        self.queue_title.setText(f"待导入（{count}）")
        self.scroll.setVisible(count > 0)
        self.placeholder.setVisible(count == 0)
        self._update_buttons()

    def _build_row(self, item: QueueItem) -> QWidget:
        row = QWidget(self.list_host)
        row.setProperty("role", "rowcard")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(10)

        info = QVBoxLayout()
        info.setSpacing(2)

        name = QLabel(truncate_middle(item.path.name, 56), row)
        name.setStyleSheet("font-size: 13px; font-weight: 600;")
        name.setToolTip(str(item.path))
        info.addWidget(name)

        if item.error:
            detail = item.error
            color_role = "danger"
        else:
            inspection = item.inspection
            detail = (
                f"{inspection.archive_format.display_name}  ·  "
                f"{human_size(inspection.total_size)}  ·  "
                f"{inspection.file_count} 个文件  ·  "
                f"{len(inspection.payload_entries)} 个 Mod 文件"
            )
            color_role = "success"
            if item.warnings:
                detail += "  ·  " + "；".join(item.warnings)

        detail_label = QLabel(detail, row)
        detail_label.setWordWrap(True)
        detail_label.setStyleSheet(
            f"font-size: 12px; color: {self.colors.qcolor(color_role).name()};"
        )
        info.addWidget(detail_label)
        layout.addLayout(info, 1)

        if item.inspection.ok:
            view_btn = button("内容", variant="ghost", palette=self.colors)
            view_btn.clicked.connect(lambda _=False, it=item: self._show_entries(it))
            layout.addWidget(view_btn)

        remove_btn = icon_button("close", self.colors, tooltip="从列表移除")
        remove_btn.clicked.connect(lambda _=False, it=item: self._remove_item(it))
        layout.addWidget(remove_btn)

        return row

    def _remove_item(self, item: QueueItem) -> None:
        if item in self._queue:
            self._queue.remove(item)
            self._render_queue()

    def _show_entries(self, item: QueueItem) -> None:
        dialog = _EntryDialog(item, self.colors, self)
        dialog.exec()

    def _update_buttons(self) -> None:
        busy = self._task is not None and self._task.isRunning()
        ready_count = sum(1 for item in self._queue if item.ready)
        can_install = self.context.is_ready or not self.check_install.isChecked()

        self.start_btn.setEnabled(ready_count > 0 and not busy and can_install)
        self.clear_btn.setEnabled(bool(self._queue) and not busy)
        self.cancel_btn.setVisible(busy)
        self.start_btn.setText(
            f"导入 {ready_count} 个" if ready_count else "开始导入"
        )

    # ------------------------------------------------------------------
    # 导入流程
    # ------------------------------------------------------------------

    def _start_import(self) -> None:
        items = [item for item in self._queue if item.ready]
        if not items:
            self.context.notify("没有可导入的压缩包", "warning")
            return

        if not self.context.is_ready and self.check_install.isChecked():
            self.context.notify("请先在「设置」里配置游戏目录", "error")
            return

        if self.context.config.confirm_before_install:
            target = (
                self.context.library_dir
                if self.check_install.isChecked()
                else "(只入库，不动游戏目录)"
            )
            answer = QMessageBox.question(
                self,
                "确认导入",
                f"即将导入 {len(items)} 个 Mod。\n\n"
                f"Mod 会先存进库：\n{self.context.library_dir}\n\n"
                f"启用目标：\n{target}",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer != QMessageBox.Yes:
                return

        install = self.check_install.isChecked() and self.context.is_ready
        cleanup = self.check_cleanup.isChecked()
        logger.info(
            "开始导入 %d 个压缩包（部署=%s，清理暂存=%s）：%s",
            len(items),
            install,
            cleanup,
            "、".join(i.path.name for i in items[:5]),
        )

        self.progress_box.setVisible(True)
        self.progress_bar.setProperty("state", "running")
        self.progress_bar.setValue(0)
        self.progress_label.setText("准备中…")

        task = Task(
            lambda t: self._work_import(t, items, install=install, cleanup=cleanup),
            self,
            label="导入 Mod",
        )
        task.progressed.connect(self._on_progress)
        task.messaged.connect(self.progress_label.setText)
        task.succeeded.connect(self._on_finished)
        task.failed.connect(self._on_failed)
        task.cancelled_signal.connect(self._on_cancelled)

        self._task = task
        self._progress_base = 0
        self._update_buttons()
        self.context.tasks.start(task)

    # -- 工作线程 -------------------------------------------------------

    def _work_import(
        self, task: Task, items: list[QueueItem], *, install: bool, cleanup: bool
    ) -> list[QueueItem]:
        staging_root = self.context.config.staging_path
        staging_root.mkdir(parents=True, exist_ok=True)

        total_steps = len(items)
        for index, item in enumerate(items, start=1):
            if task.cancelled:
                raise TaskCancelled()

            task.message(f"正在解压 {item.path.name} …")
            staging = staging_root / _safe_folder(item.mod_name)
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True, exist_ok=True)
            item.staging = staging

            def report(done: int, count: int, message: str, *, _i=index, _n=item.path.name) -> None:
                # 把「当前压缩包内的进度」折算进整体进度
                fraction = (done / count) if count else 0.0
                task.report(int((_i - 1 + fraction) * 100 / total_steps), 100, f"{_n} — {message}")

            try:
                extract_archive(
                    item.path,
                    staging,
                    progress=report,
                    cancel=lambda: task.cancelled,
                )
            except ExtractionCancelled as exc:
                raise TaskCancelled() from exc
            except ArchiveError as exc:
                item.error = str(exc)
                task.report(int(index * 100 / total_steps), 100, f"{item.path.name} 失败")
                continue

            if not install:
                task.report(int(index * 100 / total_steps), 100, f"{item.path.name} 已解压")
                continue

            task.message(f"正在存入 Mod 库 {item.mod_name} …")
            try:
                mod = self.context.library.import_to_library(
                    staging, item.mod_name, source_archive=item.path
                )
                if install:
                    task.message(f"正在部署进游戏 {mod.name} …")
                    mod = self.context.library.deploy(mod)
                item.installed = mod
            except LibraryError as exc:
                item.error = str(exc)

            if cleanup and item.installed is not None:
                shutil.rmtree(staging, ignore_errors=True)
                item.staging = None

            task.report(int(index * 100 / total_steps), 100, f"{item.path.name} 完成")

        return items

    # -- 回调（主线程） -------------------------------------------------

    def _on_progress(self, done: int, total: int, message: str) -> None:
        self.progress_bar.setValue(max(0, min(100, done)))
        if message:
            self.progress_label.setText(truncate_middle(message, 90))

    def _on_finished(self, items: list[QueueItem]) -> None:
        self._task = None
        succeeded = [i for i in items if i.installed is not None or i.staging is not None]
        failed = [i for i in items if i.error]

        self.progress_bar.setValue(100)
        self.progress_bar.setProperty("state", "error" if failed and not succeeded else "done")
        self.progress_bar.style().unpolish(self.progress_bar)
        self.progress_bar.style().polish(self.progress_bar)
        self.progress_label.setText(
            f"完成：成功 {len(succeeded)} 个" + (f"，失败 {len(failed)} 个" if failed else "")
        )

        if failed:
            self.context.notify(
                f"{len(failed)} 个 Mod 导入失败，详见列表", "error"
            )
            logger.warning(
                "导入结束：成功 %d，失败 %d —— %s",
                len(succeeded),
                len(failed),
                "；".join(f"{i.path.name}: {i.error}" for i in failed[:5]),
            )
        else:
            self.context.notify(f"成功导入 {len(succeeded)} 个 Mod", "success")
            logger.info("导入结束：成功 %d", len(succeeded))

        installed_count = sum(1 for item in items if item.installed is not None)

        # 成功的条目从队列里移除，失败的留下让用户处理
        self._queue = [item for item in self._queue if item.error]
        for item in self._queue:
            item.staging = None
            item.installed = None
        self._render_queue()

        if self.context.config.open_mods_dir_after_install and self.context.mods_dir:
            self.context.mods_dir.mkdir(parents=True, exist_ok=True)

        self.context.mods_updated()
        self._update_buttons()

        if installed_count:
            self._offer_conflict_check(installed_count)

    def _offer_conflict_check(self, installed_count: int) -> None:
        """刚装完 Mod 是冲突检测最有价值的时机，主动问一次。"""
        if not installed_count or not self.context.config.audit_prompt_after_install:
            return

        answer = QMessageBox.question(
            self,
            "检测 Mod 冲突",
            f"已安装 {installed_count} 个 Mod。\n\n"
            "现在就检测一下它们和已有 Mod 之间有没有冲突吗？\n"
            "（ChunkID 冲突会导致游戏无法启动，建议检测）",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes:
            self.context.request_conflict_check()

    def _on_failed(self, message: str) -> None:
        self._task = None
        self.progress_bar.setProperty("state", "error")
        self.progress_bar.style().unpolish(self.progress_bar)
        self.progress_bar.style().polish(self.progress_bar)
        self.progress_label.setText(f"导入失败：{message}")
        self.context.notify(f"导入失败：{message}", "error")
        self._update_buttons()

    def _on_cancelled(self) -> None:
        self._task = None
        self.progress_label.setText("已取消")
        self.progress_bar.setValue(0)
        self.context.notify("已取消导入", "warning")
        self._update_buttons()

    def _cancel_import(self) -> None:
        if self._task is not None and self._task.isRunning():
            self._task.request_cancel()
            self.progress_label.setText("正在取消…")


def _safe_folder(name: str) -> str:
    cleaned = "".join(ch for ch in name if ch not in '<>:"/\\|?*').strip(" .")
    return cleaned or "mod"


class _EntryDialog(QDialog):
    """查看压缩包内容清单。"""

    def __init__(self, item: QueueItem, colors, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"压缩包内容 — {item.path.name}")
        self.resize(680, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        header = QLabel(
            f"{item.inspection.archive_format.display_name}  ·  "
            f"{item.inspection.file_count} 个文件  ·  "
            f"解压后约 {human_size(item.inspection.total_size)}"
        )
        header.setStyleSheet("font-size: 13px; font-weight: 600;")
        layout.addWidget(header)

        if item.inspection.unsafe_entries:
            warn = QLabel(
                f"⚠ 有 {len(item.inspection.unsafe_entries)} 个条目因路径越界被跳过，"
                r"不会出现在下面的清单里。"
            )
            warn.setWordWrap(True)
            warn.setStyleSheet(f"font-size: 12px; color: {colors.warning};")
            layout.addWidget(warn)

        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setProperty("mono", "true")
        entries = item.inspection.entries
        lines = [
            f"{'[目录] ' if e.is_dir else '       '}{e.path}"
            + ("" if e.is_dir else f"    ({human_size(e.size)})")
            for e in entries[:2000]
        ]
        if len(entries) > 2000:
            lines.append(f"… 其余 {len(entries) - 2000} 项已省略")
        view.setPlainText("\n".join(lines) or "（空）")
        layout.addWidget(view, 1)

        close_btn = button("关闭", variant="primary", parent=self)
        close_btn.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close_btn)
        layout.addLayout(row)
