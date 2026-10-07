"""设置页：游戏目录、暂存目录、行为偏好、外观与关于信息。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from ... import APP_NAME, APP_NAME_LONG, TRANSLATION_ENABLED, __version__
from ...core import audit, paths
from ...core.translate import DEFAULT_BASE_URL, DEFAULT_MODEL
from ...services.worker import Task
from ..theme import SPACE_MD, SPACE_SM
from ..widgets.common import Card, Divider, button
from .base import Page, card_header, field_label, page_scroll


class SettingsPage(Page):
    title = "设置"
    subtitle = "配置游戏目录与管理器行为"

    theme_changed = Signal(str)

    def __init__(self, context, colors, parent: QWidget | None = None) -> None:
        super().__init__(context, colors, parent)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 12, 24)
        layout.setSpacing(SPACE_MD)

        # 各卡片把自己的开关登记进来，refresh() 统一回填
        self._flags: list[tuple[QCheckBox, str]] = []
        self._test_task: Task | None = None

        # 分组标题：六张卡片等权时看不出该先看哪张。
        # 用「小标题 + 更大留白」分组，比再套一层卡片便宜得多。
        def section(text: str, *, first: bool = False) -> None:
            if not first:
                layout.addSpacing(SPACE_SM)
            caption = QLabel(text, content)
            caption.setProperty("role", "section")
            layout.addWidget(caption)

        section("目录位置", first=True)
        layout.addWidget(self._build_game_card())
        layout.addWidget(self._build_library_card())
        layout.addWidget(self._build_staging_card())

        section("检测与行为")
        if TRANSLATION_ENABLED:
            layout.addWidget(self._build_translate_card())
        layout.addWidget(self._build_audit_card())
        layout.addWidget(self._build_behaviour_card())

        section("外观与关于")
        layout.addWidget(self._build_appearance_card())
        layout.addWidget(self._build_about_card())
        layout.addStretch(1)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(page_scroll(content))

        self.refresh()

    # ------------------------------------------------------------------
    # 游戏目录
    # ------------------------------------------------------------------

    def _build_game_card(self) -> Card:
        card = Card()
        card.body.addWidget(
            card_header(
                "game",
                "《剑星》游戏目录",
                "Mod 会被安装到 <游戏目录>\\SB\\Content\\Paks\\~mods",
                self.colors,
            )
        )
        card.body.addWidget(Divider())

        self.game_path_edit = QLineEdit()
        self.game_path_edit.setReadOnly(True)
        self.game_path_edit.setPlaceholderText("尚未配置")
        self.game_path_edit.setProperty("mono", "true")

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.game_path_edit, 1)

        detect_btn = button("自动探测", icon_name="search", palette=self.colors)
        detect_btn.clicked.connect(self._autodetect)
        row.addWidget(detect_btn)

        browse_btn = button("浏览…", icon_name="folder", palette=self.colors)
        browse_btn.clicked.connect(self._browse_game_root)
        row.addWidget(browse_btn)

        card.body.addLayout(row)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.open_mods_btn = button("打开 ~mods 目录", icon_name="external", palette=self.colors)
        self.open_mods_btn.clicked.connect(self._open_mods_dir)
        actions.addWidget(self.open_mods_btn)

        self.clear_game_btn = button("清除配置", variant="ghost", palette=self.colors)
        self.clear_game_btn.clicked.connect(lambda: self._apply_game_root(None))
        actions.addWidget(self.clear_game_btn)
        actions.addStretch(1)
        card.body.addLayout(actions)

        self.game_hint = QLabel()
        self.game_hint.setWordWrap(True)
        self.game_hint.setStyleSheet("font-size: 12px;")
        card.body.addWidget(self.game_hint)

        return card

    # ------------------------------------------------------------------
    # 暂存目录
    # ------------------------------------------------------------------

    def _build_staging_card(self) -> Card:
        card = Card()
        card.body.addWidget(
            card_header(
                "archive",
                "解压暂存目录",
                "压缩包先解压到这里，确认无误后再安装进游戏目录。留空则使用应用数据目录。",
                self.colors,
            )
        )
        card.body.addWidget(Divider())

        self.staging_edit = QLineEdit()
        self.staging_edit.setProperty("mono", "true")
        self.staging_edit.setPlaceholderText(str(paths.cache_dir()))
        self.staging_edit.editingFinished.connect(self._save_staging)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.staging_edit, 1)

        browse_btn = button("浏览…", icon_name="folder", palette=self.colors)
        browse_btn.clicked.connect(self._browse_staging)
        row.addWidget(browse_btn)

        reset_btn = button("恢复默认", variant="ghost", palette=self.colors)
        reset_btn.clicked.connect(self._reset_staging)
        row.addWidget(reset_btn)

        card.body.addLayout(row)
        return card

    # ------------------------------------------------------------------
    # Mod 库位置
    # ------------------------------------------------------------------

    def _build_library_card(self) -> Card:
        card = Card()
        card.body.addWidget(
            card_header(
                "library",
                "Mod 库位置",
                "全部 Mod 的权威副本都放在这里，游戏不会加载它。启用某个 Mod 时从库"
                "部署到游戏目录，停用则只删游戏目录那份——库里那份永远保留。",
                self.colors,
            )
        )
        card.body.addWidget(Divider())

        self.library_edit = QLineEdit()
        self.library_edit.setProperty("mono", "true")
        self.library_edit.setPlaceholderText(str(paths.library_dir()))
        self.library_edit.editingFinished.connect(self._save_library_dir)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.library_edit, 1)

        browse_btn = button("浏览…", icon_name="folder", palette=self.colors)
        browse_btn.clicked.connect(self._browse_library)
        row.addWidget(browse_btn)

        reset_btn = button("恢复默认", variant="ghost", palette=self.colors)
        reset_btn.clicked.connect(self._reset_library)
        row.addWidget(reset_btn)

        card.body.addLayout(row)

        self.library_hint = QLabel()
        self.library_hint.setWordWrap(True)
        self.library_hint.setStyleSheet("font-size: 12px;")
        card.body.addWidget(self.library_hint)

        open_btn = button("打开库目录", icon_name="external", palette=self.colors)
        open_btn.clicked.connect(self._open_library)
        card.body.addWidget(open_btn, 0, Qt.AlignLeft)

        return card

    def _refresh_library_card(self) -> None:
        config = self.context.config
        self.library_edit.setText(config.library_dir)
        self.library_edit.setCursorPosition(0)
        actual = self.context.library_dir

        if not config.is_game_configured:
            self.library_hint.setText(f"当前：{actual}（配置游戏目录后会给出同盘推荐位置）")
            self.library_hint.setStyleSheet(
                f"font-size: 12px; color: {self.colors.text_muted};"
            )
        elif self.context.library.same_volume:
            self.library_hint.setText(
                f"✔ 与游戏同盘，启用时使用硬链接 —— 同一份数据出现在两处，"
                f"但磁盘只占一份空间。当前：{actual}"
            )
            self.library_hint.setStyleSheet(
                f"font-size: 12px; color: {self.colors.success};"
            )
        else:
            self.library_hint.setText(
                f"⚠ 与游戏不在同一个盘，无法使用硬链接，启用时会复制一份"
                f"（占双份空间）。建议把库放到游戏所在盘。当前：{actual}"
            )
            self.library_hint.setStyleSheet(
                f"font-size: 12px; color: {self.colors.warning};"
            )

    def _browse_library(self) -> None:
        start = self.context.config.library_dir or str(self.context.library_dir.parent)
        chosen = QFileDialog.getExistingDirectory(self, "选择 Mod 库位置", start)
        if chosen:
            self.library_edit.setText(chosen)
            self._save_library_dir()

    def _save_library_dir(self) -> None:
        self.context.set_library_dir(self.library_edit.text().strip())
        self.refresh()
        self.context.notify(f"Mod 库已切换到 {self.context.library_dir}", "success")

    def _reset_library(self) -> None:
        self.context.set_library_dir(None)
        self.refresh()
        self.context.notify(f"Mod 库已恢复默认：{self.context.library_dir}", "success")

    def _open_library(self) -> None:
        target = self.context.library_dir
        target.mkdir(parents=True, exist_ok=True)
        _reveal(target)

    # ------------------------------------------------------------------
    # Mod 翻译
    # ------------------------------------------------------------------

    def _build_translate_card(self) -> Card:
        card = Card()
        card.body.addWidget(
            card_header(
                "info",
                "Mod 翻译",
                "CNS 系列 Mod 的名称与说明写在 .dekcns.json 里，翻译后游戏内直接显示中文。"
                "任何 OpenAI 兼容接口都可以用（DeepSeek、Moonshot、本地 vLLM 等）。",
                self.colors,
            )
        )
        card.body.addWidget(Divider())

        self.translate_url_edit = QLineEdit()
        self.translate_url_edit.setProperty("mono", "true")
        self.translate_url_edit.setPlaceholderText(DEFAULT_BASE_URL)
        self.translate_url_edit.editingFinished.connect(self._save_translate)

        self.translate_key_edit = QLineEdit()
        self.translate_key_edit.setProperty("mono", "true")
        self.translate_key_edit.setEchoMode(QLineEdit.Password)
        self.translate_key_edit.setPlaceholderText("sk-…（只存在本机配置文件里）")
        self.translate_key_edit.editingFinished.connect(self._save_translate)

        self.translate_model_edit = QLineEdit()
        self.translate_model_edit.setProperty("mono", "true")
        self.translate_model_edit.setPlaceholderText(DEFAULT_MODEL)
        self.translate_model_edit.editingFinished.connect(self._save_translate)

        for caption, widget, hint in (
            ("接口地址", self.translate_url_edit, "形如 https://api.deepseek.com/v1"),
            ("API Key", self.translate_key_edit, ""),
            ("模型名", self.translate_model_edit, "形如 deepseek-chat"),
        ):
            row = QHBoxLayout()
            row.setSpacing(10)
            label = field_label(caption)
            label.setFixedWidth(96)
            row.addWidget(label)
            row.addWidget(widget, 1)

            if caption == "API Key":
                self.reveal_btn = button("显示", variant="ghost", palette=self.colors)
                self.reveal_btn.setCheckable(True)
                self.reveal_btn.toggled.connect(self._toggle_key_visibility)
                row.addWidget(self.reveal_btn)

            card.body.addLayout(row)
            if hint:
                tip = QLabel(hint, card)
                tip.setProperty("role", "faint")
                tip.setStyleSheet("font-size: 11px;")
                tip.setContentsMargins(106, 0, 0, 0)
                card.body.addWidget(tip)

        actions = QHBoxLayout()
        actions.setSpacing(8)

        self.translate_test_btn = button(
            "测试连接", icon_name="play", palette=self.colors
        )
        self.translate_test_btn.clicked.connect(self._test_translation)
        actions.addWidget(self.translate_test_btn)

        self.translate_clear_cache_btn = button(
            "清空翻译缓存", variant="ghost", palette=self.colors
        )
        self.translate_clear_cache_btn.setToolTip(
            "缓存让同一个词在各 Mod 里译法一致、也省 token；清空后下次会重新翻译"
        )
        self.translate_clear_cache_btn.clicked.connect(self._clear_translation_cache)
        actions.addWidget(self.translate_clear_cache_btn)

        actions.addStretch(1)
        card.body.addLayout(actions)

        self.translate_hint = QLabel()
        self.translate_hint.setWordWrap(True)
        self.translate_hint.setStyleSheet("font-size: 12px;")
        card.body.addWidget(self.translate_hint)

        card.body.addWidget(Divider())

        self.check_translate_auto = QCheckBox("导入完成后提示翻译（仍需你确认才写回）")
        self.check_translate_auto.toggled.connect(
            lambda checked: self._save_flag("translate_auto_after_import", checked)
        )
        card.body.addWidget(self.check_translate_auto)
        self._flags.append((self.check_translate_auto, "translate_auto_after_import"))

        return card

    def _refresh_translate_card(self) -> None:
        config = self.context.config
        for widget, value in (
            (self.translate_url_edit, config.translate_base_url),
            (self.translate_key_edit, config.translate_api_key),
            (self.translate_model_edit, config.translate_model),
        ):
            widget.blockSignals(True)
            widget.setText(value)
            widget.setCursorPosition(0)
            widget.blockSignals(False)

        if config.translation_ready:
            self.translate_hint.setText("✔ 翻译功能已启用")
            self.translate_hint.setStyleSheet(
                f"font-size: 12px; color: {self.colors.success};"
            )
        else:
            self.translate_hint.setText(
                "翻译是可选功能：三项都填好之后，「Mod 库」里才会出现「翻译…」入口。"
                "留空则完全不会联网。"
            )
            self.translate_hint.setStyleSheet(
                f"font-size: 12px; color: {self.colors.text_muted};"
            )

        self.translate_test_btn.setEnabled(config.translation_ready)
        cached = len(self.context.translation_cache)
        self.translate_clear_cache_btn.setEnabled(cached > 0)
        self.translate_clear_cache_btn.setText(f"清空翻译缓存（{cached} 条）")

    def _toggle_key_visibility(self, shown: bool) -> None:
        self.translate_key_edit.setEchoMode(
            QLineEdit.Normal if shown else QLineEdit.Password
        )
        self.reveal_btn.setText("隐藏" if shown else "显示")

    def _save_translate(self) -> None:
        config = self.context.config
        config.translate_base_url = self.translate_url_edit.text().strip()
        config.translate_api_key = self.translate_key_edit.text().strip()
        config.translate_model = self.translate_model_edit.text().strip()
        config.save()
        self._refresh_translate_card()

    def _clear_translation_cache(self) -> None:
        cache = self.context.translation_cache
        cache._entries.clear()  # noqa: SLF001 - 缓存对象本身没有公开的清空接口
        cache.save(force=True)
        self._refresh_translate_card()
        self.context.notify("翻译缓存已清空", "success")

    def _test_translation(self) -> None:
        engine = self.context.translator
        if engine is None:
            return

        self.translate_test_btn.setEnabled(False)
        self.translate_hint.setText("正在测试连接…")
        self.translate_hint.setStyleSheet(
            f"font-size: 12px; color: {self.colors.text_muted};"
        )

        task = Task(
            lambda t: engine.translate(["Skin Color"]),
            self,
            label="测试翻译接口",
        )
        task.succeeded.connect(self._on_test_ok)
        task.failed.connect(self._on_test_failed)
        task.finished.connect(lambda: self._forget_test_task(task))
        self._test_task = task
        self.context.tasks.start(task)

    def _forget_test_task(self, task: Task) -> None:
        """任务结束后解除引用（C++ 对象会被 deleteLater() 销毁）。"""
        if self._test_task is task:
            self._test_task = None

    def _on_test_ok(self, result: list[str]) -> None:
        self.translate_test_btn.setEnabled(True)
        sample = result[0] if result else ""
        self.translate_hint.setText(f'✔ 连接正常："Skin Color" → "{sample}"')
        self.translate_hint.setStyleSheet(
            f"font-size: 12px; color: {self.colors.success};"
        )

    def _on_test_failed(self, message: str) -> None:
        self.translate_test_btn.setEnabled(True)
        self.translate_hint.setText(f"✖ {message}")
        self.translate_hint.setStyleSheet(f"font-size: 12px; color: {self.colors.danger};")

    # ------------------------------------------------------------------
    # 冲突检测
    # ------------------------------------------------------------------

    def _build_audit_card(self) -> Card:
        card = Card()
        card.body.addWidget(
            card_header(
                "shield",
                "Mod 冲突检测",
                "调用第三方工具 DekPakModAudit 比对资源路径，找出会让游戏无法启动的冲突。"
                "把它解压到游戏目录下的 DekPakModAudit 文件夹即可被自动识别。",
                self.colors,
            )
        )
        card.body.addWidget(Divider())

        self.audit_path_edit = QLineEdit()
        self.audit_path_edit.setReadOnly(True)
        self.audit_path_edit.setProperty("mono", "true")
        self.audit_path_edit.setPlaceholderText("未找到 DekPakModAudit")
        card.body.addWidget(self.audit_path_edit)

        row = QHBoxLayout()
        row.setSpacing(8)

        detect_btn = button("重新探测", icon_name="search", palette=self.colors)
        detect_btn.clicked.connect(self._detect_audit_tool)
        row.addWidget(detect_btn)

        locate_btn = button("手动指定…", icon_name="folder", palette=self.colors)
        locate_btn.clicked.connect(self._locate_audit_tool)
        row.addWidget(locate_btn)

        self.audit_clear_btn = button("清除", variant="ghost", palette=self.colors)
        self.audit_clear_btn.clicked.connect(self._clear_audit_tool)
        row.addWidget(self.audit_clear_btn)

        self.audit_run_btn = button(
            "立即检测", variant="primary", icon_name="play", palette=self.colors
        )
        self.audit_run_btn.clicked.connect(self.context.request_conflict_check)
        row.addWidget(self.audit_run_btn)

        row.addStretch(1)
        card.body.addLayout(row)

        self.audit_hint = QLabel()
        self.audit_hint.setWordWrap(True)
        self.audit_hint.setStyleSheet("font-size: 12px;")
        card.body.addWidget(self.audit_hint)

        card.body.addWidget(Divider())

        # 分发给别人时用得上：把官方发布页填进来，缺工具的引导对话框就会出现按钮
        url_row = QHBoxLayout()
        url_row.setSpacing(10)
        url_label = field_label("下载页地址")
        url_label.setFixedWidth(96)
        url_row.addWidget(url_label)

        self.audit_url_edit = QLineEdit()
        self.audit_url_edit.setProperty("mono", "true")
        self.audit_url_edit.setPlaceholderText("留空则不显示「打开下载页」按钮")
        self.audit_url_edit.editingFinished.connect(self._save_audit_url)
        url_row.addWidget(self.audit_url_edit, 1)
        card.body.addLayout(url_row)

        card.body.addWidget(Divider())

        self.check_audit_logicmods = QCheckBox("同时检测 LogicMods 目录（UE4SS 蓝图 Mod）")
        self.check_audit_logicmods.toggled.connect(
            lambda checked: self._save_flag("audit_include_logicmods", checked)
        )
        card.body.addWidget(self.check_audit_logicmods)

        self.check_audit_prompt = QCheckBox("安装完 Mod 后询问是否立即检测冲突")
        self.check_audit_prompt.toggled.connect(
            lambda checked: self._save_flag("audit_prompt_after_install", checked)
        )
        card.body.addWidget(self.check_audit_prompt)

        self._flags.extend(
            (
                (self.check_audit_logicmods, "audit_include_logicmods"),
                (self.check_audit_prompt, "audit_prompt_after_install"),
            )
        )

        return card

    def _detect_audit_tool(self) -> None:
        self.context.config.audit_tool_dir = ""
        self.context.config.save()
        self.refresh()
        if self.context.audit_tool is not None:
            self.context.notify("已找到 DekPakModAudit", "success")
        else:
            self.context.notify(
                "没有在游戏目录下找到 DekPakModAudit，请手动指定它的位置", "warning"
            )

    def _locate_audit_tool(self) -> None:
        start = self.context.config.audit_tool_dir or self.context.config.game_root
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "选择 DekPakModAudit.exe",
            start or str(paths.default_downloads_dir()),
            "可执行文件 (*.exe);;所有文件 (*)",
        )
        if not chosen:
            return
        self.context.config.audit_tool_dir = chosen
        self.context.config.save()
        self.refresh()
        if self.context.audit_tool is not None:
            self.context.notify("检测工具已配置", "success")
        else:
            self.context.notify("这个位置似乎不是有效的检测工具", "error")

    def _clear_audit_tool(self) -> None:
        self._detect_audit_tool()

    def _save_audit_url(self) -> None:
        self.context.config.audit_download_url = self.audit_url_edit.text().strip()
        self.context.config.save()

    # ------------------------------------------------------------------
    # 行为
    # ------------------------------------------------------------------

    def _build_behaviour_card(self) -> Card:
        card = Card()
        card.body.addWidget(
            card_header("settings", "行为偏好", "", self.colors)
        )
        card.body.addWidget(Divider())

        self.check_confirm = QCheckBox("安装前显示确认对话框")
        self.check_delete_staging = QCheckBox("安装成功后删除暂存文件")
        self.check_open_after = QCheckBox("安装完成后打开 ~mods 目录")
        self.check_scan_downloads = QCheckBox("启动时扫描下载目录里的压缩包")
        self.check_show_disabled = QCheckBox("在 Mod 库中显示已停用的 Mod")

        mapping = (
            (self.check_confirm, "confirm_before_install"),
            (self.check_delete_staging, "delete_staging_after_install"),
            (self.check_open_after, "open_mods_dir_after_install"),
            (self.check_scan_downloads, "scan_downloads_on_start"),
            (self.check_show_disabled, "show_disabled_mods"),
        )
        for widget, attr in mapping:
            widget.toggled.connect(
                lambda checked, name=attr: self._save_flag(name, checked)
            )
            card.body.addWidget(widget)

        # 注意用 extend 而不是赋值：检测卡片已经先注册过自己的开关了
        self._flags.extend(mapping)
        return card

    # ------------------------------------------------------------------
    # 外观
    # ------------------------------------------------------------------

    def _build_appearance_card(self) -> Card:
        card = Card()
        card.body.addWidget(card_header("info", "外观", "切换配色方案（立即生效）", self.colors))
        card.body.addWidget(Divider())

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(field_label("主题"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("深色（默认）", "dark")
        self.theme_combo.addItem("浅色", "light")
        self.theme_combo.setFixedWidth(180)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        row.addWidget(self.theme_combo)
        row.addStretch(1)
        card.body.addLayout(row)
        return card

    # ------------------------------------------------------------------
    # 关于
    # ------------------------------------------------------------------

    def _build_about_card(self) -> Card:
        card = Card()
        card.body.addWidget(card_header("shield", "关于", "", self.colors))
        card.body.addWidget(Divider())

        self.about_labels: list[QLabel] = []
        for caption in ("版本", "配置文件", "暂存目录", "禁用 Mod 存放处"):
            row = QHBoxLayout()
            row.setSpacing(10)
            caption_label = field_label(caption)
            caption_label.setFixedWidth(96)
            row.addWidget(caption_label)

            value = QLabel("—")
            value.setProperty("mono", "true")
            value.setStyleSheet("font-size: 12px;")
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            value.setWordWrap(True)
            row.addWidget(value, 1)

            self.about_labels.append(value)
            card.body.addLayout(row)

        actions = QHBoxLayout()
        actions.setSpacing(8)

        log_btn = button("查看运行日志", icon_name="info", palette=self.colors)
        log_btn.setToolTip("出问题时先看这里；也可以按 F12 随时打开")
        log_btn.clicked.connect(self._show_log)
        actions.addWidget(log_btn)

        open_config = button("打开应用数据目录", icon_name="external", palette=self.colors)
        open_config.clicked.connect(lambda: _reveal(paths.app_data_dir()))
        actions.addWidget(open_config)

        actions.addStretch(1)
        card.body.addLayout(actions)
        return card

    def _show_log(self) -> None:
        from ..dialogs.log_dialog import LogDialog

        LogDialog(self.colors, self).exec()

    # ------------------------------------------------------------------
    # 状态同步
    # ------------------------------------------------------------------

    def on_show(self) -> None:
        # 检测工具位置可能在别处被改（例如主窗口引导用户手动指定），进来时重读一次
        self.refresh()

    def refresh(self) -> None:
        config = self.context.config

        self.game_path_edit.setText(config.game_root or "")
        self.game_path_edit.setCursorPosition(0)
        if config.is_game_configured:
            self.game_hint.setText("✔ 目录有效，可以安装 Mod")
            self.game_hint.setStyleSheet(
                f"font-size: 12px; color: {self.colors.success};"
            )
        elif config.game_root:
            self.game_hint.setText(
                "✖ 这个目录下找不到 SB\\Content\\Paks，可能不是游戏根目录"
            )
            self.game_hint.setStyleSheet(f"font-size: 12px; color: {self.colors.danger};")
        else:
            self.game_hint.setText("尚未配置游戏目录，Mod 库与安装功能不可用")
            self.game_hint.setStyleSheet(
                f"font-size: 12px; color: {self.colors.warning};"
            )

        self.open_mods_btn.setEnabled(config.is_game_configured)
        self.clear_game_btn.setEnabled(bool(config.game_root))
        self.staging_edit.setText(config.staging_dir)

        self._refresh_library_card()
        if TRANSLATION_ENABLED:
            self._refresh_translate_card()
        self._refresh_audit_card()

        for widget, attr in self._flags:
            widget.blockSignals(True)
            widget.setChecked(bool(getattr(config, attr)))
            widget.blockSignals(False)

        index = self.theme_combo.findData(config.theme)
        if index >= 0:
            self.theme_combo.blockSignals(True)
            self.theme_combo.setCurrentIndex(index)
            self.theme_combo.blockSignals(False)

        values = (
            f"{APP_NAME} v{__version__}  ·  {APP_NAME_LONG}",
            str(paths.config_path()),
            str(config.staging_path),
            str(paths.disabled_store_dir()),
        )
        for label, text in zip(self.about_labels, values, strict=True):
            label.setText(text)
            label.setToolTip(text)

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------

    def _refresh_audit_card(self) -> None:
        tool = self.context.audit_tool
        if tool is None:
            self.audit_path_edit.setText("")
            self.audit_hint.setText(
                "✖ 未找到检测工具。请从 DekPakModAudit 的发布页下载，"
                "解压到游戏根目录（与 SB.exe 同级）后点「重新探测」。"
            )
            self.audit_hint.setStyleSheet(f"font-size: 12px; color: {self.colors.warning};")
        elif not tool.has_config:
            self.audit_path_edit.setText(str(tool.executable))
            self.audit_hint.setText(
                "✖ 找到了程序，但缺少 input\\DekPakModAuditConfig.json ——"
                "工具需要它来定位换算表，请确认解压完整。"
            )
            self.audit_hint.setStyleSheet(f"font-size: 12px; color: {self.colors.danger};")
        else:
            self.audit_path_edit.setText(str(tool.executable))
            self.audit_hint.setText("✔ 检测工具就绪")
            self.audit_hint.setStyleSheet(f"font-size: 12px; color: {self.colors.success};")

        self.audit_clear_btn.setEnabled(bool(self.context.config.audit_tool_dir))
        self.audit_run_btn.setEnabled(
            tool is not None and tool.has_config and self.context.is_ready
        )
        self.audit_path_edit.setCursorPosition(0)  # 长路径要能看到开头的盘符

        self.audit_url_edit.blockSignals(True)
        self.audit_url_edit.setText(self.context.config.audit_download_url)
        self.audit_url_edit.blockSignals(False)
        self.audit_url_edit.setPlaceholderText(
            audit.DOWNLOAD_URL or "留空则不显示「打开下载页」按钮"
        )

    def _apply_game_root(self, root: Path | None) -> None:
        if self.context.set_game_root(root):
            self.refresh()
            if root:
                self.context.notify("游戏目录已更新", "success")

    def _autodetect(self) -> None:
        found = paths.detect_game_roots()
        if not found:
            self.context.notify("没有自动找到《剑星》安装目录，请手动选择", "warning")
            return
        self._apply_game_root(found[0])

    def _browse_game_root(self) -> None:
        start = self.context.config.game_root or str(paths.default_downloads_dir())
        chosen = QFileDialog.getExistingDirectory(
            self, "选择《剑星》游戏根目录（含 SB 文件夹的那一层）", start
        )
        if chosen:
            self._apply_game_root(Path(chosen))

    def _open_mods_dir(self) -> None:
        target = self.context.mods_dir
        if target is None:
            return
        target.mkdir(parents=True, exist_ok=True)
        _reveal(target)

    def _browse_staging(self) -> None:
        start = self.context.config.staging_dir or str(paths.app_data_dir())
        chosen = QFileDialog.getExistingDirectory(self, "选择解压暂存目录", start)
        if chosen:
            self.staging_edit.setText(chosen)
            self._save_staging()

    def _save_staging(self) -> None:
        self.context.config.staging_dir = self.staging_edit.text().strip()
        self.context.config.save()

    def _reset_staging(self) -> None:
        self.staging_edit.clear()
        self._save_staging()
        self.context.notify("暂存目录已恢复默认", "success")

    def _save_flag(self, attr: str, value: bool) -> None:
        setattr(self.context.config, attr, value)
        self.context.config.save()

    def _on_theme_changed(self) -> None:
        theme = self.theme_combo.currentData()
        if not theme or theme == self.context.config.theme:
            return
        self.context.config.theme = theme
        self.context.config.save()
        self.theme_changed.emit(theme)


def _reveal(path: Path) -> None:
    """在系统文件管理器中打开目录。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606 - 打开资源管理器是预期行为
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        pass
