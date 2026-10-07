"""主窗口：侧边栏 + 顶部标题栏 + 页面堆栈 + 状态栏。"""

from __future__ import annotations


from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, logging_setup
from ..core.audit import (
    DEFAULT_TIMEOUT_S,
    AuditReport,
    run_audit,
)
from ..core.config import AppConfig
from ..services.context import AppContext
from ..services.worker import Task
from .dialogs.conflict_dialog import ConflictDialog
from .dialogs.error_dialog import ErrorRelay
from .dialogs.tool_setup_dialog import ToolSetupDialog
from .pages.archives_page import ArchivesPage
from .pages.base import Page
from .pages.import_page import ImportPage
from .pages.library_page import LibraryPage
from .pages.settings_page import SettingsPage
from .icons import app_icon
from .theme import SPACE_LG, SPACE_MD, SPACE_XL, Palette, get_palette, stylesheet
from .widgets.common import icon_button
from .widgets.sidebar import Sidebar
from .widgets.toast import Toast

from ..logging_setup import get_logger
logger = get_logger(__name__)

_NAV = (
    ("library", "Mod 库", "library"),
    ("import", "导入安装", "import"),
    ("archives", "压缩包", "archive"),
    ("settings", "设置", "settings"),
)


class MainWindow(QMainWindow):
    """应用主窗口。"""

    theme_changed = Signal(str)

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config
        self.colors: Palette = get_palette(config.theme)
        self.context = AppContext(config, self)

        self.setWindowTitle(f"{APP_NAME}")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(960, 640)
        self.resize(config.window_width, config.window_height)

        self._current_key = "library"
        self._pages: dict[str, Page] = {}

        # 未捕获异常统一弹这一个窗；ErrorRelay 负责把跨线程的异常投递回主线程
        self.error_relay = ErrorRelay(self)
        logging_setup.set_error_reporter(self.error_relay.report)

        self._build()
        self._wire_pages()
        self._wire_context()

        self.context.status.connect(self._on_status)
        self._sync_sidebar()
        self._switch_page("library", force=True)

        # 把旧版放在应用数据里的「已停用」Mod 迁进新库（只做一次，失败不拦启动）
        self.context.migrate_legacy_store()

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------

    def _build(self) -> None:
        central = QWidget(self)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = Sidebar(self.colors, central)
        for key, text, icon_name in _NAV:
            self.sidebar.add_page(key, text, icon_name)
        layout.addWidget(self.sidebar)

        right = QWidget(central)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        right_layout.addWidget(self._build_header(right))

        self.stack = QStackedWidget(right)
        self._pages = {
            "library": LibraryPage(self.context, self.colors, self.stack),
            "import": ImportPage(self.context, self.colors, self.stack),
            "archives": ArchivesPage(self.context, self.colors, self.stack),
            "settings": SettingsPage(self.context, self.colors, self.stack),
        }
        for key, _, _ in _NAV:
            self.stack.addWidget(self._pages[key])

        # 注意：这里不能写成 QWidget(self.stack)——那会让 content 成为 stack 的子控件，
        # 紧接着又把 stack 放进 content 的布局，形成父子循环并卡死 Qt。
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(
            SPACE_XL, SPACE_LG, SPACE_LG, SPACE_MD
        )
        content_layout.setSpacing(0)
        content_layout.addWidget(self.stack)
        right_layout.addWidget(content, 1)

        layout.addWidget(right, 1)
        self.setCentralWidget(central)

        self._build_status_bar()

        self.toast = Toast(central, self.colors)
        self.toast.reposition()

    def _build_header(self, parent: QWidget) -> QWidget:
        header = QFrame(parent)
        header.setProperty("role", "toolbar")
        header.setFixedHeight(78)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(SPACE_XL, SPACE_LG, SPACE_LG, SPACE_LG)
        layout.setSpacing(SPACE_LG)

        column = QVBoxLayout()
        column.setSpacing(2)
        self.title_label = QLabel("Mod 库", header)
        self.title_label.setProperty("role", "title")
        self.subtitle_label = QLabel("", header)
        self.subtitle_label.setProperty("role", "subtitle")
        column.addWidget(self.title_label)
        column.addWidget(self.subtitle_label)
        layout.addLayout(column)

        layout.addStretch(1)

        self.game_chip = QLabel("未连接游戏目录", header)
        self.game_chip.setProperty("role", "badge")
        self.game_chip.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.game_chip)

        return header

    def _build_status_bar(self) -> None:
        bar = QStatusBar(self)
        bar.setSizeGripEnabled(True)

        self.status_label = QLabel("就绪")
        self.status_label.setProperty("role", "muted")
        bar.addWidget(self.status_label, 1)

        self.log_button = icon_button(
            "info", self.colors, tooltip="查看运行日志（F12）", size=15
        )
        self.log_button.clicked.connect(self.show_log)
        bar.addPermanentWidget(self.log_button)

        self.status_progress = QProgressBar()
        self.status_progress.setFixedWidth(180)
        self.status_progress.setFixedHeight(8)
        self.status_progress.setTextVisible(False)
        self.status_progress.setVisible(False)
        bar.addPermanentWidget(self.status_progress)

        self.setStatusBar(bar)

    def show_log(self) -> None:
        """打开内置日志查看器。"""
        from .dialogs.log_dialog import LogDialog

        LogDialog(self.colors, self).exec()

    # ------------------------------------------------------------------
    # 信号
    # ------------------------------------------------------------------

    def _wire_pages(self) -> None:
        """连接**本次构建出来的控件**。切换主题重建界面时必须重新执行。"""
        self.sidebar.navigated.connect(self._switch_page)
        self.sidebar.settings_requested.connect(lambda: self._switch_page("settings"))

        settings_page = self._pages["settings"]
        if isinstance(settings_page, SettingsPage):
            settings_page.theme_changed.connect(self._on_theme_changed)

    def _wire_context(self) -> None:
        """连接全局对象与快捷键。**只能在构造时执行一次**。

        这些都是挂在 ``self`` 或 ``AppContext`` 上的长生命周期对象：重复连接会让
        一个信号触发多次（例如检测会弹出两个对话框），快捷键也会累积成按一次触发
        多次。界面重建时它们并不随之销毁，所以不能放进 ``_wire_pages``。
        """
        self.context.game_root_changed.connect(self._sync_sidebar)
        self.context.mods_changed.connect(self._sync_sidebar)
        self.context.tasks.busy_changed.connect(self._on_busy_changed)
        self.context.conflict_check_requested.connect(self.run_conflict_check)

        for index, (key, _, _) in enumerate(_NAV, start=1):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index}"), self)
            shortcut.activated.connect(lambda k=key: self._switch_page(k))

        QShortcut(QKeySequence("F5"), self).activated.connect(self._refresh_current)
        QShortcut(QKeySequence("Ctrl+O"), self).activated.connect(
            lambda: self._switch_page("import")
        )
        QShortcut(QKeySequence("F12"), self).activated.connect(self.show_log)

    def _on_busy_changed(self, busy: bool) -> None:
        # 取当前的状态栏控件而不是绑定时捕获的那个：主题重建会换掉它
        self.status_progress.setVisible(busy)
        if not busy:
            self.status_progress.setValue(0)

    # ------------------------------------------------------------------
    # 页面切换
    # ------------------------------------------------------------------

    def _switch_page(self, key: str, *, force: bool = False) -> None:
        page = self._pages.get(key)
        if page is None or (key == self._current_key and not force):
            return

        self._current_key = key
        self.stack.setCurrentWidget(page)
        self.sidebar.set_current(key)
        self.title_label.setText(page.title)
        self.subtitle_label.setText(page.subtitle)
        page.on_show()

    def _refresh_current(self) -> None:
        page = self._pages.get(self._current_key)
        if page is not None:
            page.on_show()
            self._on_status("已刷新", "info")

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    def _sync_sidebar(self) -> None:
        configured = self.context.is_ready
        root = self.context.game_root

        detail = ""
        if root is not None:
            mods = self.context.mods_dir
            detail = str(mods) if mods else str(root)

        self.sidebar.set_game_status(
            configured=configured, path=str(root) if root else "", detail=detail
        )

        if configured:
            self.game_chip.setText("● 游戏目录已就绪")
            color = self.colors.qcolor("success")
        elif self.config.game_root:
            self.game_chip.setText("● 目录无效")
            color = self.colors.qcolor("danger")
        else:
            self.game_chip.setText("● 未配置游戏目录")
            color = self.colors.qcolor("warning")

        # 只留彩点 + 彩字，不铺淡色底：淡底会在大片纯色上切出一块补丁
        self.game_chip.setStyleSheet(
            f"QLabel {{ color: {color.name()}; background: transparent;"
            f" border: none; padding: 4px 2px; font-size: 12px; font-weight: 600; }}"
        )
        self.game_chip.setToolTip(str(root) if root else "请在「设置」中配置游戏目录")

    def _on_status(self, text: str, level: str = "info") -> None:
        self.status_label.setText(text)
        color_role = {
            "success": "success",
            "warning": "warning",
            "error": "danger",
        }.get(level, "text_muted")
        self.status_label.setStyleSheet(
            f"color: {self.colors.qcolor(color_role).name()};"
        )
        if hasattr(self, "toast"):
            self.toast.show_message(text, level)

    # ------------------------------------------------------------------
    # 冲突检测
    # ------------------------------------------------------------------

    def run_conflict_check(self) -> None:
        """运行一次 Mod 冲突检测并展示结果。"""
        if self.context.tasks.busy:
            self._on_status("已有任务在运行，请等它结束再检测", "warning")
            return

        tool = self.context.audit_tool
        if tool is None or not tool.has_config:
            if not self.ensure_audit_tool():
                return
            tool = self.context.audit_tool
        if tool is None or not tool.has_config:
            return

        folders = self.context.audit_folders()
        self.status_progress.setRange(0, 100)
        self.status_progress.setValue(0)
        self._on_status(f"正在检测 Mod 冲突（{'、'.join(folders)}）…", "info")

        task = Task(
            lambda worker: run_audit(
                tool,
                folders=folders,
                timeout_s=DEFAULT_TIMEOUT_S,
                cancel=lambda: worker.cancelled,
                progress=worker.report,
            ),
            self,
            label="Mod 冲突检测",
        )
        task.progressed.connect(self._on_audit_progress)
        task.succeeded.connect(self._on_audit_finished)
        task.failed.connect(self._on_audit_failed)
        task.cancelled_signal.connect(
            lambda: self._on_status("已取消冲突检测", "warning")
        )
        self.context.tasks.start(task)

    def _on_audit_progress(self, done: int, total: int, message: str) -> None:
        if total > 0:
            self.status_progress.setValue(int(done * 100 / total))
        if message:
            self.status_label.setText(message)

    def _on_audit_finished(self, report: AuditReport) -> None:
        self.status_progress.setValue(100)
        text, level = report.verdict
        self._on_status(text, level)

        dialog = ConflictDialog(report, self.colors, self)
        dialog.exec()
        # 结果里可能建议停用某个 Mod，回到库页时重新扫描一次更稳妥
        self._pages["library"].on_show()

    def _on_audit_failed(self, message: str) -> None:
        self._on_status(f"冲突检测失败：{message}", "error")
        QMessageBox.critical(self, "冲突检测失败", message)

    def ensure_audit_tool(self) -> bool:
        """确保检测工具可用；缺失时弹出引导安装对话框。

        返回工具是否已就绪。分发出去的版本不会自带这个第三方工具，所以这条引导
        是最终用户唯一的自助路径。
        """
        tool = self.context.audit_tool
        if tool is not None and tool.has_config:
            return True

        dialog = ToolSetupDialog(self.context, self.colors, self)
        dialog.exec()

        tool = self.context.audit_tool
        ready = tool is not None and tool.has_config
        if ready:
            self._on_status("检测工具已就绪", "success")
        return ready

    # ------------------------------------------------------------------
    # 主题
    # ------------------------------------------------------------------

    def _on_theme_changed(self, theme: str) -> None:
        self.colors = get_palette(theme)
        application = QApplication.instance()
        if application is not None:
            application.setStyleSheet(stylesheet(self.colors))

        self._rebuild()
        self.theme_changed.emit(theme)
        self._on_status("主题已切换", "success")

    def _rebuild(self) -> None:
        """主题切换会改变内联样式，因此重建整棵界面树。"""
        current = self._current_key
        old = self.centralWidget()
        self._build()
        if old is not None:
            old.deleteLater()
        self._wire_pages()
        self._sync_sidebar()
        self._switch_page(current, force=True)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        if hasattr(self, "toast"):
            self.toast.reposition()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt 命名
        if self.context.tasks.busy:
            self.context.tasks.cancel_all()

        self.config.window_width = self.width()
        self.config.window_height = self.height()
        self.context.shutdown()
        logger.info("窗口关闭，配置已保存")
        super().closeEvent(event)
