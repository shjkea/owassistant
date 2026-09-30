# -*- coding: utf-8 -*-
"""Qt 线程池小封装：所有阻塞网络/签名工作都通过这里离开 GUI 主线程。"""
from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class FunctionWorker(QRunnable):
    def __init__(self, function: Callable[[], Any]) -> None:
        super().__init__()
        self.function = function
        self.signals = WorkerSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            result = self.function()
        except Exception as exc:  # UI 只显示清洗后的异常文本，不打印 traceback/token。
            message = str(exc).strip() or type(exc).__name__
            self.signals.error.emit(message)
        else:
            self.signals.result.emit(result)
        finally:
            self.signals.finished.emit()


def submit(
    owner: QObject,
    function: Callable[[], Any],
    *,
    on_result: Optional[Callable[[Any], None]] = None,
    on_error: Optional[Callable[[str], None]] = None,
    on_finished: Optional[Callable[[], None]] = None,
) -> FunctionWorker:
    """提交一个函数并让回调在 owner 所在线程（通常 GUI 线程）执行。"""
    worker = FunctionWorker(function)
    workers = getattr(owner, "_async_workers", None)
    if workers is None:
        workers = set()
        setattr(owner, "_async_workers", workers)
    workers.add(worker)

    if on_result is not None:
        worker.signals.result.connect(on_result)
    if on_error is not None:
        worker.signals.error.connect(on_error)
    if on_finished is not None:
        worker.signals.finished.connect(on_finished)

    def cleanup() -> None:
        workers.discard(worker)

    worker.signals.finished.connect(cleanup)
    QThreadPool.globalInstance().start(worker)
    return worker
