"""应用装配与启动。"""

from __future__ import annotations

import logging
import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from . import APP_NAME, APP_NAME_EN, __version__
from .core import paths
from .core.config import AppConfig
from .logging_setup import (
    get_logger,
    install_qt_message_handler,
    log_file_path,
    setup_logging,
)
from .ui.main_window import MainWindow
from .ui.icons import app_icon
from .ui.theme import get_palette, stylesheet

logger = get_logger(__name__)


def create_application(argv: list[str] | None = None) -> QApplication:
    """创建并配置 ``QApplication``。"""
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME_EN)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(APP_NAME_EN)
    app.setWindowIcon(app_icon())
    # Fusion 对各平台一致，且对 QSS 的支持最完整
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 9))
    return app


def run(argv: list[str] | None = None) -> int:
    """程序入口：加载配置、装配主窗口并进入事件循环。"""
    # 日志要最先起来——之后任何一步出问题都得留下痕迹
    setup_logging()
    logger.info("启动 %s v%s", APP_NAME, __version__)

    app = create_application(argv)
    install_qt_message_handler()

    config = AppConfig.load()
    paths.ensure_app_dirs()

    # 首次启动时顺手探测一次游戏目录，省掉用户一次手动配置
    if not config.game_root:
        found = paths.detect_game_roots()
        if found:
            config.game_root = str(found[0])
            config.save()
            logger.info("自动探测到游戏目录：%s", found[0])

    app.setStyleSheet(stylesheet(get_palette(config.theme)))

    logger.info("日志文件：%s", log_file_path())

    window = MainWindow(config)
    window.show()

    exit_code = app.exec()
    logger.info("退出，代码 %s", exit_code)
    logging.shutdown()
    return exit_code
