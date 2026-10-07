"""后台任务：把耗时的解压/复制放到工作线程，避免界面卡死。

用法::

    task = Task(lambda t: extract_archive(path, dest, progress=t.report), parent=self)
    task.progressed.connect(self._on_progress)
    task.succeeded.connect(self._on_done)
    task.failed.connect(self._on_error)
    task.start()
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from ..logging_setup import get_logger
logger = get_logger(__name__)

#: 工作函数签名：接受 Task 本身，可调用 ``task.report()`` 上报进度。
WorkFn = Callable[["Task"], Any]


class Task(QThread):
    """在独立线程里执行一个函数，并用信号把结果送回主线程。"""

    progressed = Signal(int, int, str)
    """(已完成, 总数, 当前项描述)"""

    messaged = Signal(str)
    """仅文字进度（总数未知时使用）。"""

    succeeded = Signal(object)
    failed = Signal(str)
    cancelled_signal = Signal()

    def __init__(self, work: WorkFn, parent: QObject | None = None, *, label: str = "") -> None:
        super().__init__(parent)
        self._work = work
        self.label = label
        self._cancel_requested = False

    # ------------------------------------------------------------------
    # 供工作函数调用
    # ------------------------------------------------------------------

    @property
    def cancelled(self) -> bool:
        """工作函数应定期检查；为 ``True`` 时尽快收尾并抛出 :class:`TaskCancelled`。"""
        return self._cancel_requested

    def report(self, done: int, total: int, message: str = "") -> None:
        """上报进度。频繁调用也是安全的——信号会排队到主线程。"""
        self.progressed.emit(done, total, message)

    def message(self, text: str) -> None:
        self.messaged.emit(text)

    def request_cancel(self) -> None:
        self._cancel_requested = True

    # ------------------------------------------------------------------
    # 线程体
    # ------------------------------------------------------------------

    def run(self) -> None:  # noqa: D102 - QThread 钩子
        try:
            result = self._work(self)
        except TaskCancelled:
            logger.info("任务已取消：%s", self.label or self._work)
            self.cancelled_signal.emit()
        except Exception as exc:  # noqa: BLE001 - 必须兜住，否则线程静默死掉
            logger.error("任务失败 %s: %s\n%s", self.label, exc, traceback.format_exc())
            self.failed.emit(str(exc) or exc.__class__.__name__)
        else:
            if self._cancel_requested:
                self.cancelled_signal.emit()
            else:
                self.succeeded.emit(result)


class TaskCancelled(Exception):
    """工作函数用抛出它来响应取消请求。"""


class TaskManager(QObject):
    """持有正在运行的任务引用，防止被 GC 提前回收。"""

    busy_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tasks: list[Task] = []

    @property
    def busy(self) -> bool:
        return any(t.isRunning() for t in self._tasks)

    @property
    def current(self) -> Task | None:
        for task in reversed(self._tasks):
            if task.isRunning():
                return task
        return None

    def start(self, task: Task) -> Task:
        """启动任务并纳入管理。"""
        self._tasks.append(task)
        task.finished.connect(lambda: self._reap(task))
        self.busy_changed.emit(True)
        task.start()
        return task

    def cancel_all(self) -> None:
        for task in list(self._tasks):
            if task.isRunning():
                task.request_cancel()

    def wait_all(self, timeout_ms: int = 8000) -> bool:
        """退出程序前调用，尽量让后台任务收尾。"""
        self.cancel_all()
        ok = True
        for task in list(self._tasks):
            if task.isRunning():
                ok = task.wait(timeout_ms) and ok
        return ok

    def _reap(self, task: Task) -> None:
        if task in self._tasks:
            self._tasks.remove(task)
        task.deleteLater()
        self.busy_changed.emit(self.busy)
