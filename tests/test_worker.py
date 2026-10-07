"""后台任务的生命周期测试。

重点是**任务结束之后的那些调用**：``TaskManager`` 会 ``deleteLater()`` 掉 C++ 对象，
而界面很难保证在每个分支上都及时清引用。真实事故就是这么来的——
扫描完成后 ``self._task`` 仍指向已销毁的对象，下一次点选表格就抛
``RuntimeError: Internal C++ object (Task) already deleted``。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from stellar_mod_manager.services.worker import Task, TaskCancelled, is_running


def _finished_task(qapp) -> Task:
    """跑完一个任务并让它被 deleteLater() **真正销毁**。

    注意不能只用 ``processEvents()``：``deleteLater()`` 投递的是
    ``DeferredDelete`` 事件，而 ``processEvents()`` 默认**不处理**它，
    对象会一直活着。必须显式 ``sendPostedEvents(..., DeferredDelete)``，
    否则测的是一个根本没发生的场景。
    """
    task = Task(lambda t: "ok", label="测试任务")
    task.start()
    assert task.wait(5000), "任务没在预期时间内结束"
    task.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()
    return task


# ---------------------------------------------------------------------------
# is_running
# ---------------------------------------------------------------------------


def test_is_running_handles_none() -> None:
    assert is_running(None) is False


def test_is_running_reports_live_task(qapp) -> None:
    task = Task(lambda t: None, label="存活检查")
    assert is_running(task) is False  # 还没 start
    task.start()
    assert task.wait(5000)
    assert is_running(task) is False  # 跑完了


def test_is_running_survives_deleted_cpp_object(qapp) -> None:
    """回归：对象被销毁后直接调 isRunning() 会抛 RuntimeError。"""
    task = _finished_task(qapp)

    # 先确认「裸调用」确实会炸——否则这个测试就是假的
    with pytest.raises(RuntimeError):
        task.isRunning()

    # 而统一入口必须扛住
    assert is_running(task) is False


def test_is_running_is_safe_to_call_repeatedly(qapp) -> None:
    task = _finished_task(qapp)
    for _ in range(3):
        assert is_running(task) is False


# ---------------------------------------------------------------------------
# 任务语义
# ---------------------------------------------------------------------------


def test_successful_task_emits_result(qapp) -> None:
    seen: list[object] = []
    task = Task(lambda t: 42, label="返回结果")
    task.succeeded.connect(seen.append)
    task.start()
    assert task.wait(5000)
    qapp.processEvents()
    assert seen == [42]


def test_failing_task_emits_message_not_crash(qapp) -> None:
    """工作函数抛异常必须被兜住，否则线程会静默死掉。"""
    errors: list[str] = []
    task = Task(lambda t: (_ for _ in ()).throw(ValueError("炸了")), label="失败任务")
    task.failed.connect(errors.append)
    task.start()
    assert task.wait(5000)
    qapp.processEvents()
    assert errors and "炸了" in errors[0]


def test_cancelled_task_emits_cancelled(qapp) -> None:
    fired: list[bool] = []
    task = Task(lambda t: (_ for _ in ()).throw(TaskCancelled()), label="取消任务")
    task.cancelled_signal.connect(lambda: fired.append(True))
    task.start()
    assert task.wait(5000)
    qapp.processEvents()
    assert fired == [True]


def test_cancel_request_makes_cancelled_true(qapp) -> None:
    task = Task(lambda t: None, label="取消标记")
    assert task.cancelled is False
    task.request_cancel()
    assert task.cancelled is True


def test_finished_always_fires_even_on_failure(qapp) -> None:
    """``finished`` 是唯一「无论成功失败取消都会发」的信号——

    页面靠它兜底清引用，所以这条性质必须在测试里钉住。
    """
    for work in (
        lambda t: "ok",
        lambda t: (_ for _ in ()).throw(ValueError("炸了")),
        lambda t: (_ for _ in ()).throw(TaskCancelled()),
    ):
        fired: list[bool] = []
        task = Task(work, label="兜底检查")
        task.finished.connect(lambda: fired.append(True))
        task.start()
        assert task.wait(5000)
        qapp.processEvents()
        assert fired == [True], "finished 没有发出"
