"""Mod 翻译对话框：先预览、可编辑，确认后才写回 Mod 文件。

流程刻意做成「翻译 → 人工过一眼 → 写回」：机器翻译难免出错，而写回会改到
Mod 自己的 json，所以中间必须留一道人工确认。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.library import LibraryError, TranslationWriteResult
from ...core.models import Mod
from ...core.translate import (
    DekcnsDocument,
    TranslationPlan,
    build_plan,
)
from ...services.context import AppContext
from ...services.worker import Task, is_running
from ..icons import icon
from ..theme import Palette, banner_style
from ..widgets.common import button

_CATEGORY_ROLE = {
    "Mod 名称": "accent",
    "Mod 描述": "text_muted",
    "组件名称": "accent2",
    "组件说明": "text_muted",
}


class TranslateDialog(QDialog):
    """预览并编辑某个 Mod 的译文，确认后写回。"""

    def __init__(
        self,
        context: AppContext,
        colors: Palette,
        mod: Mod,
        documents: list[DekcnsDocument],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.context = context
        self.colors = colors
        self.mod = mod
        self.documents = documents
        self.plan: TranslationPlan | None = None
        self._task: Task | None = None
        self._rows: list[tuple[DekcnsDocument, int]] = []

        self.setWindowTitle(f"翻译 Mod — {mod.name}")
        self.setMinimumSize(880, 620)
        self.resize(1000, 700)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 16)
        root.setSpacing(12)

        root.addWidget(self._build_header())
        root.addLayout(self._build_actions())
        root.addWidget(self._build_table(), 1)
        root.addWidget(self._build_notice())
        root.addLayout(self._build_buttons())

        self._load_from_cache()
        self._refresh_summary()

    # ------------------------------------------------------------------
    # 顶部
    # ------------------------------------------------------------------

    def _build_header(self) -> QWidget:
        holder = QWidget(self)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        accent = self.colors.qcolor("accent")
        mark = QLabel(holder)
        mark.setPixmap(icon("info", accent.name(), 24).pixmap(24, 24))
        mark.setFixedSize(44, 44)
        mark.setAlignment(Qt.AlignCenter)
        mark.setStyleSheet(
            f"QLabel {{ background-color: {self.colors.surface_alt};"
            f" border-radius: 12px; }}"
        )
        row.addWidget(mark, 0, Qt.AlignTop)

        column = QVBoxLayout()
        column.setSpacing(3)

        title = QLabel(self.mod.name, holder)
        title.setStyleSheet("font-size: 15px; font-weight: 700;")
        title.setWordWrap(True)
        column.addWidget(title)

        self.summary_label = QLabel("", holder)
        self.summary_label.setProperty("role", "muted")
        self.summary_label.setStyleSheet("font-size: 12px;")
        self.summary_label.setWordWrap(True)
        column.addWidget(self.summary_label)

        row.addLayout(column, 1)
        return holder

    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)

        self.translate_btn = button(
            "翻译", variant="primary", icon_name="play", palette=self.colors, parent=self
        )
        self.translate_btn.clicked.connect(self._start_translation)
        row.addWidget(self.translate_btn)

        self.cancel_task_btn = button(
            "停止", variant="danger", icon_name="stop", palette=self.colors, parent=self
        )
        self.cancel_task_btn.clicked.connect(self._cancel_translation)
        self.cancel_task_btn.setVisible(False)
        row.addWidget(self.cancel_task_btn)

        self.reset_btn = button("清空译文", variant="ghost", palette=self.colors, parent=self)
        self.reset_btn.setToolTip("把表格里的译文全部清掉，重新翻一次")
        self.reset_btn.clicked.connect(self._clear_translations)
        row.addWidget(self.reset_btn)

        row.addStretch(1)

        engine = self.context.translator
        engine_label = QLabel(engine.name if engine else "未配置翻译引擎", self)
        engine_label.setProperty("role", "faint")
        engine_label.setStyleSheet("font-size: 11px;")
        row.addWidget(engine_label)

        return row

    def _build_table(self) -> QTableWidget:
        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["类别", "原文", "译文（可直接编辑）"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.SelectedClicked
            | QAbstractItemView.EditKeyPressed
        )
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        # 原文里常有换行，开启换行会把行撑得极高、整张表没法扫读；
        # 统一单行显示，完整内容放进 tooltip。
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 92)

        # 手动改了译文也要立刻反映到按钮状态，否则用户填完发现「写入」还是灰的
        self.table.itemChanged.connect(self._on_item_changed)

        return self.table

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 2:
            self._refresh_summary()

    def _build_notice(self) -> QWidget:
        frame = QFrame(self)
        warn = self.colors.qcolor("warning")
        frame.setStyleSheet(banner_style(self.colors, "warning", radius=9))
        row = QHBoxLayout(frame)
        row.setContentsMargins(12, 9, 12, 9)
        row.setSpacing(9)

        mark = QLabel(frame)
        mark.setPixmap(icon("alert", warn.name(), 18).pixmap(18, 18))
        row.addWidget(mark, 0, Qt.AlignTop)

        text = QLabel(
            "写回会修改 Mod 自己的 <code>.dekcns.json</code>，游戏内随即显示中文。"
            "原件已自动备份，随时可以还原；写入采用原子替换，<b>不会影响你其它位置的"
            "Mod 备份</b>（硬链接会被断开）。",
            frame,
        )
        text.setWordWrap(True)
        text.setStyleSheet("font-size: 12px;")
        row.addWidget(text, 1)
        return frame

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)

        self.revert_btn = button(
            "还原原始文件", variant="ghost", palette=self.colors, parent=self
        )
        self.revert_btn.setToolTip("把之前写入的译文撤掉，恢复 Mod 作者的原文")
        self.revert_btn.clicked.connect(self._revert)
        self.revert_btn.setEnabled(self.context.library.has_translation_backup(self.mod))
        row.addWidget(self.revert_btn)

        row.addStretch(1)

        close_btn = button("取消", palette=self.colors, parent=self)
        close_btn.clicked.connect(self.reject)
        row.addWidget(close_btn)

        self.apply_btn = button(
            "写入 Mod", variant="primary", icon_name="check", palette=self.colors, parent=self
        )
        self.apply_btn.clicked.connect(self._apply)
        row.addWidget(self.apply_btn)

        return row

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------

    def _load_from_cache(self) -> None:
        """打开时先用缓存把能填的填上，用户不必等接口。"""
        cache = self.context.translation_cache
        self._rebuild_rows()
        filled = 0
        for row, (doc, index) in enumerate(self._rows):
            cached = cache.get(doc.spans[index].text)
            if cached:
                self.table.item(row, 2).setText(cached)
                filled += 1
        self._cached_count = filled

    def _rebuild_rows(self) -> None:
        self._rows = []
        for doc in self.documents:
            for index in range(len(doc.spans)):
                self._rows.append((doc, index))

        # 建表期间会不断触发 itemChanged，先屏蔽掉，最后统一刷新一次
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(len(self._rows))
            for row, (doc, index) in enumerate(self._rows):
                span = doc.spans[index]

                category = QTableWidgetItem(span.category)
                category.setForeground(
                    self.colors.qcolor(_CATEGORY_ROLE.get(span.category, "text"))
                )
                category.setFlags(Qt.ItemIsEnabled)
                category.setToolTip(span.json_path)
                self.table.setItem(row, 0, category)

                original = QTableWidgetItem(span.text)
                original.setFlags(Qt.ItemIsEnabled)
                original.setToolTip(f"{span.json_path}\n\n{span.text}")
                self.table.setItem(row, 1, original)

                if span.protected:
                    # 被当作引用键的文本绝不能翻，明说原因而不是留个空单元格
                    locked = QTableWidgetItem("不翻译（被引用）")
                    locked.setFlags(Qt.ItemIsEnabled)
                    locked.setForeground(self.colors.qcolor("warning"))
                    locked.setToolTip(span.skip_reason)
                    self.table.setItem(row, 2, locked)
                    for column in range(3):
                        self.table.item(row, column).setToolTip(span.skip_reason)
                else:
                    translated = QTableWidgetItem("")
                    translated.setToolTip("双击编辑")
                    self.table.setItem(row, 2, translated)
        finally:
            self.table.blockSignals(False)

    def _current_plan(self) -> TranslationPlan:
        """把表格里的译文收集成计划。"""
        plan = TranslationPlan(documents=list(self.documents))
        for row, (doc, index) in enumerate(self._rows):
            span = doc.spans[index]
            if span.protected:
                continue  # 被引用的名称不参与翻译
            item = self.table.item(row, 2)
            text = item.text().strip() if item is not None else ""
            if text and text != span.text:
                plan.translations[row] = text
        return plan

    def _refresh_summary(self) -> None:
        total = len(self._rows)
        locked = sum(1 for doc, i in self._rows if doc.spans[i].protected)
        pending = total - locked - len(self._current_plan().translations)

        parts = [f"共 {total} 条文本"]
        if locked:
            parts.append(f"{locked} 条因被引用而跳过")
        parts.append(f"待翻译 {pending} 条" if pending else "其余已填译文")
        if self.documents:
            parts.append(f"{len(self.documents)} 个配置文件")
        self.summary_label.setText("  ·  ".join(parts))
        self.apply_btn.setEnabled(bool(self._current_plan().translations))

    # ------------------------------------------------------------------
    # 翻译
    # ------------------------------------------------------------------

    def _start_translation(self) -> None:
        if is_running(self._task):
            return

        engine = self.context.translator
        if engine is None:
            QMessageBox.information(
                self,
                "尚未配置翻译引擎",
                "请先到「设置 → Mod 翻译」填写接口地址、API Key 与模型名。\n\n"
                "任何 OpenAI 兼容接口都可以，例如 DeepSeek。",
            )
            return

        cache = self.context.translation_cache
        documents = self.documents

        self.translate_btn.setEnabled(False)
        self.cancel_task_btn.setVisible(True)
        self.summary_label.setText("正在翻译…")

        task = Task(
            lambda t: build_plan(
                documents, engine, cache, progress=lambda d, n, m: t.report(d, n, m)
            ),
            self,
            label="翻译 Mod",
        )
        task.progressed.connect(self._on_progress)
        task.succeeded.connect(self._on_translated)
        task.failed.connect(self._on_failed)
        task.cancelled_signal.connect(self._on_cancelled)
        # finished 一定会发，用它兜底清引用；只接 succeeded/failed 会漏掉取消路径
        task.finished.connect(lambda: self._forget_task(task))
        self._task = task
        self.context.tasks.start(task)

    def _forget_task(self, task: Task) -> None:
        """任务结束后解除引用（C++ 对象会被 deleteLater() 销毁）。"""
        if self._task is task:
            self._task = None

    def _on_progress(self, done: int, total: int, message: str) -> None:
        self.summary_label.setText(f"{message}（{done}/{total}）")

    def _on_translated(self, plan: TranslationPlan) -> None:
        self._task = None
        self.translate_btn.setEnabled(True)
        self.cancel_task_btn.setVisible(False)

        # 计划里的下标与表格行号一一对应
        for row, text in plan.translations.items():
            if 0 <= row < self.table.rowCount():
                self.table.item(row, 2).setText(text)

        self.context.save_translation_cache()
        detail = f"翻译完成：{plan.requested} 条送翻译"
        if plan.skipped_cached:
            detail += f"，{plan.skipped_cached} 条命中缓存"
        self.context.notify(detail, "success")
        self._refresh_summary()

    def _on_failed(self, message: str) -> None:
        self._task = None
        self.translate_btn.setEnabled(True)
        self.cancel_task_btn.setVisible(False)
        self._refresh_summary()
        self.context.notify(f"翻译失败：{message}", "error")
        QMessageBox.warning(self, "翻译失败", message)

    def _on_cancelled(self) -> None:
        self._task = None
        self.translate_btn.setEnabled(True)
        self.cancel_task_btn.setVisible(False)
        self.context.notify("已取消翻译", "warning")
        self._refresh_summary()

    def _cancel_translation(self) -> None:
        if is_running(self._task):
            self._task.request_cancel()
            self.summary_label.setText("正在取消…")

    def _clear_translations(self) -> None:
        for row in range(self.table.rowCount()):
            self.table.item(row, 2).setText("")
        self._refresh_summary()

    # ------------------------------------------------------------------
    # 写回
    # ------------------------------------------------------------------

    def _apply(self) -> None:
        plan = self._current_plan()
        if not plan.translations:
            QMessageBox.information(self, "没有译文", "表格里还没有可写入的译文。")
            return

        answer = QMessageBox.question(
            self,
            "写入 Mod",
            f"把 {len(plan.translations)} 条译文写入「{self.mod.name}」的配置文件？\n\n"
            "原件会自动备份，之后可以还原。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return

        # 行号 → 各文档内的 span 下标
        per_document: dict[int, dict[int, str]] = {}
        for row, text in plan.translations.items():
            doc, index = self._rows[row]
            slot = next(i for i, d in enumerate(self.documents) if d is doc)
            per_document.setdefault(slot, {})[index] = text

        written = 0
        try:
            for slot, translations in per_document.items():
                result: TranslationWriteResult = self.context.library.apply_translation(
                    self.mod, self.documents[slot], translations
                )
                written += result.entries
        except LibraryError as exc:
            QMessageBox.critical(self, "写入失败", str(exc))
            self.context.notify(f"写入失败：{exc}", "error")
            return

        self.context.notify(f"已为「{self.mod.name}」写入 {written} 条译文", "success")
        self.context.mods_updated()
        self.accept()

    def _revert(self) -> None:
        answer = QMessageBox.question(
            self,
            "还原原始文件",
            f"把「{self.mod.name}」的配置文件恢复成 Mod 作者的原文？\n\n"
            "你写入的译文会被撤销。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        try:
            restored = self.context.library.revert_translation(self.mod)
        except LibraryError as exc:
            QMessageBox.critical(self, "还原失败", str(exc))
            return

        self.context.notify(f"已还原 {restored} 个文件", "success")
        self.context.mods_updated()
        self.accept()

    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if is_running(self._task):
            self._task.request_cancel()
        self.context.save_translation_cache()
        super().closeEvent(event)


def available_documents(context: AppContext, mod: Mod) -> list[DekcnsDocument]:
    """轻量包装，便于调用方判断「这个 Mod 有没有可翻译的内容」。"""
    return context.mod_documents(mod)


def describe(mod: Mod, documents: list[DekcnsDocument]) -> str:
    """给菜单项用的一句话说明。"""
    if not documents:
        return "这个 Mod 没有 .dekcns.json，无法翻译"
    total = sum(len(d.spans) for d in documents)
    return f"{total} 条文本待翻译"


__all__ = ["TranslateDialog", "available_documents", "describe"]
