"""日志与全局异常处理。

日志去哪
--------
* **控制台**：开发时直接看；
* ``%LOCALAPPDATA%\\StellarModManager\\modmanager.log``：轮转，5 份 × 1 MB。

为什么要写会话头
----------------
日志是追加写的，跑几次之后就分不清哪一段属于哪次运行。每次启动写一条醒目的
会话头（版本、Python/Qt 版本、关键路径、是否翻译入口开启），排查时一眼定位。

为什么要接管 Qt 与线程的异常
----------------------------
Qt 信号槽里抛出的异常**不走** ``sys.excepthook``，PySide6 默认只往 stderr 打印堆栈；
工作线程里的异常同理。两条路都接到同一套「记日志 + 弹窗」上，
用户才不会遇到「程序突然没了，什么都没留下」。
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import platform
import sys
import threading
import traceback
import warnings
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from types import TracebackType

from . import APP_NAME, TRANSLATION_ENABLED, __version__
from .core import paths

#: 这个模块自己的日志器，不经过 get_logger 以免递归
logger = logging.getLogger(__name__[len("stellar_mod_manager.") :])

LOG_FILENAME = "modmanager.log"
_MAX_BYTES = 1024 * 1024
_BACKUP_COUNT = 5

#: 异常回调签名：``(摘要, 完整堆栈)``。
ErrorReporter = Callable[[str, str], None]

_reporter: ErrorReporter | None = None
_installed = False


# ---------------------------------------------------------------------------
# 格式化
# ---------------------------------------------------------------------------


class _SessionFormatter(logging.Formatter):
    """普通行带时间与模块，会话头不加前缀。"""

    def format(self, record: logging.LogRecord) -> str:
        if getattr(record, "raw", False):
            return record.getMessage()
        return super().format(record)


_LINE_FORMAT = "%(asctime)s  %(levelname)-7s  %(name)-24s  %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_PACKAGE_PREFIX = "stellar_mod_manager."


def get_logger(name: str) -> logging.Logger:
    """取一个日志器，顺便去掉包名前缀。

    ``stellar_mod_manager.core.library`` 有 33 个字符，对齐之后会把日志正文挤到
    屏幕外；缩成 ``core.library`` 既好读也留得住正文。
    """
    if name.startswith(_PACKAGE_PREFIX):
        name = name[len(_PACKAGE_PREFIX) :]
    return logging.getLogger(name)


# ---------------------------------------------------------------------------
# 安装
# ---------------------------------------------------------------------------


def log_file_path() -> Path:
    return paths.app_data_dir() / LOG_FILENAME


def _print_safely(text: str) -> None:
    """往 stderr 写一句话，但**绝不因为写不出去而抛异常**。

    冻结成 GUI 程序后 ``sys.stderr`` 可能是 ``None``；英文 Windows 上它的编码
    还可能是 cp1252，写中文会抛 :class:`UnicodeEncodeError`。两种情况都不该
    让「日志初始化」本身成为崩溃点。
    """
    if sys.stderr is None:
        return
    try:
        print(text, file=sys.stderr)
    except (OSError, UnicodeEncodeError, ValueError):
        pass


def setup_logging(level: int = logging.INFO, *, console: bool = True) -> Path:
    """配置根日志器，返回日志文件路径。重复调用是安全的。"""
    paths.ensure_app_dirs()
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    if not _installed:
        formatter = _SessionFormatter(_LINE_FORMAT, datefmt=_DATE_FORMAT)

        try:
            file_handler = logging.handlers.RotatingFileHandler(
                log_file_path(),
                maxBytes=_MAX_BYTES,
                backupCount=_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            file_handler.setLevel(logging.DEBUG)
            root.addHandler(file_handler)
        except OSError as exc:  # 日志写不了也不能拦住程序
            _print_safely(f"无法创建日志文件：{exc}")

        # 打包成 GUI 程序后 stdout/stderr 可能压根不存在（PyInstaller 的
        # windowed 模式），这时挂控制台处理器会让每条日志都触发一次
        # AttributeError。没有流就只写文件。
        if console and sys.stderr is not None:
            stream = logging.StreamHandler(sys.stderr)
            stream.setFormatter(formatter)
            stream.setLevel(level)
            root.addHandler(stream)

        # 第三方库的 DEBUG 噪音太多，压到 WARNING
        for noisy in ("urllib3", "PIL", "py7zr", "rarfile", "asyncio"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

        # warnings 模块也收进日志，别让它们只出现在 stderr
        logging.captureWarnings(True)
        warnings.simplefilter("default")

    install_excepthooks()
    write_session_header(level)
    return log_file_path()


def write_session_header(level: int = logging.INFO) -> None:
    """写一条会话分隔头，让同一份日志里的多次运行能分清。"""
    line = "─" * 78
    logger.info(
        "\n%s\n  %s v%s  ·  %s\n  日志级别 %s  ·  翻译入口 %s\n%s",
        line,
        APP_NAME,
        __version__,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        logging.getLevelName(level),
        "开启" if TRANSLATION_ENABLED else "关闭",
        line,
        extra={"raw": True},
    )
    log_environment()


def log_environment() -> None:
    """把排查问题会用到的环境信息一次性记下来。"""
    try:
        import PySide6

        qt_version = PySide6.__version__
    except Exception:  # noqa: BLE001 - 只为记日志，失败无所谓
        qt_version = "未知"

    logger.info(
        "运行环境：Python %s · Qt %s · %s %s",
        platform.python_version(),
        qt_version,
        platform.system(),
        platform.release(),
    )
    logger.info("程序目录：%s", _safe(paths.app_data_dir()))
    logger.info("配置文件：%s", _safe(paths.config_path()))

    # 尽量把用户实际在用的路径也记上，出问题时不用再问
    try:
        from .core.config import AppConfig

        config = AppConfig.load()
        logger.info("游戏目录：%s", config.game_root or "（未配置）")
        logger.info("Mod 库  ：%s", _safe(config.library_path))
        logger.info("暂存目录：%s", _safe(config.staging_path))
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取配置用于记录环境信息时失败：%s", exc)


def _safe(path: Path) -> str:
    return str(path)


# ---------------------------------------------------------------------------
# 全局异常
# ---------------------------------------------------------------------------


def set_error_reporter(reporter: ErrorReporter | None) -> None:
    """注册异常弹窗回调（由界面层提供，日志层不依赖 Qt）。"""
    global _reporter
    _reporter = reporter


def install_excepthooks() -> None:
    """接管主线程与工作线程的未捕获异常。"""
    sys.excepthook = _handle_exception
    if hasattr(threading, "excepthook"):
        threading.excepthook = _handle_thread_exception


def _handle_exception(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_tb: TracebackType | None,
) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    _report(exc_type, exc_value, exc_tb, source="主线程")


def _handle_thread_exception(args: threading.ExceptHookArgs) -> None:
    if args.exc_type is SystemExit:
        return
    _report(args.exc_type, args.exc_value, args.exc_traceback, source="工作线程")


def _report(
    exc_type: type[BaseException] | None,
    exc_value: BaseException | None,
    exc_tb: TracebackType | None,
    *,
    source: str,
) -> None:
    name = getattr(exc_type, "__name__", "Exception")
    summary = f"{name}: {exc_value}"
    detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))

    # 只把最后几帧放进日志正文，完整堆栈走 DEBUG，避免日志被刷屏
    frames = traceback.extract_tb(exc_tb)[-5:]
    where = "  ←  ".join(f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in frames)
    logger.error("【%s】未捕获异常 %s\n    调用链：%s", source, summary, where or "（无）")
    logger.debug("完整堆栈：\n%s", detail)

    if _reporter is not None:
        try:
            _reporter(summary, detail)
        except Exception:  # noqa: BLE001 - 报错回调自己出错就只记日志
            logger.exception("异常回调本身失败")


def install_qt_message_handler() -> None:
    """把 Qt 自己的警告接进 logging。

    Qt 的 ``qWarning`` 默认直接写 stderr，混在日志里没法筛。接管之后它们会带上
    时间、级别与来源，和程序日志排在一起。
    """
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    except ImportError:  # pragma: no cover - 没装 Qt 时静默跳过
        return

    level_map = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }
    qt_logger = logging.getLogger("Qt")

    def handler(mode, context, message: str) -> None:  # noqa: ANN001
        qt_logger.log(level_map.get(mode, logging.INFO), "%s", message)

    qInstallMessageHandler(handler)


def log_uncaught_in_slot(func: Callable) -> Callable:
    """装饰器：把 Qt 槽函数里的异常交给统一处理，而不是让它冒泡。

    PySide6 对槽里抛出的异常处理得不太一致，严重时会终止进程。给关键槽加上它，
    至少能保证「有日志、有弹窗、程序还活着」。
    """

    def wrapper(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            _report(type(exc), exc, exc.__traceback__, source=f"界面 {func.__name__}")
            return None

    wrapper.__name__ = getattr(func, "__name__", "wrapper")
    wrapper.__doc__ = func.__doc__
    return wrapper


# ---------------------------------------------------------------------------
# 供界面使用
# ---------------------------------------------------------------------------


def read_log_tail(max_lines: int = 2000) -> str:
    """读取日志尾部若干行，供内置查看器显示。"""
    path = log_file_path()
    if not path.is_file():
        return ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError as exc:
        return f"（无法读取日志：{exc}）"
    if len(lines) > max_lines:
        lines = [f"… 仅显示最后 {max_lines} 行 …\n", *lines[-max_lines:]]
    return "".join(lines)


def open_log_folder() -> None:
    """在系统文件管理器里定位日志文件。"""
    path = log_file_path()
    try:
        if sys.platform == "win32":
            if path.is_file():
                os.startfile(str(path.parent))  # noqa: S606
            else:
                os.startfile(str(paths.app_data_dir()))  # noqa: S606
    except OSError:
        logger.warning("无法打开日志目录")


__all__ = [
    "get_logger",
    "install_excepthooks",
    "install_qt_message_handler",
    "log_file_path",
    "log_uncaught_in_slot",
    "open_log_folder",
    "read_log_tail",
    "set_error_reporter",
    "setup_logging",
    "write_session_header",
]
