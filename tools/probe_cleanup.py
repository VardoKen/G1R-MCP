"""Ограниченное завершение операций probe перед передачей отмены вызывающему коду."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import math
import signal
import threading
import time
from typing import Any

import anyio


class CleanupTimeout(RuntimeError):
    code = "cleanup_timeout"
    outcome_unknown = True


async def settle_task(task: asyncio.Task, timeout: float, label: str) -> Any:
    """Дождаться одного уже созданного task; отмена не повторяет его действия.

    Внешняя отмена передаётся после завершения task, поэтому закрываемые в task
    процессы и сохранённые его closure идентичности остаются доступны владельцу.
    По истечении явного срока результат операции объявляется неопределённым.
    """
    if (not isinstance(task, asyncio.Task) or isinstance(timeout, bool)
            or not isinstance(timeout, (int, float)) or not math.isfinite(timeout)
            or not 0 < timeout <= 120):
        raise ValueError("Нужен task и конечный срок до 120 секунд")
    cancelled = False
    deadline = time.monotonic() + timeout
    current = asyncio.current_task()
    with anyio.CancelScope(shield=True):
        while not task.done():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                task.cancel()
                # Освободить локальный task; неопределённая внешняя операция не повторяется.
                try:
                    await asyncio.wait({task}, timeout=1)
                    if task.done():
                        task.exception() if not task.cancelled() else None
                except BaseException:
                    pass
                raise CleanupTimeout(f"{label}: операция не завершилась за {timeout:g} с")
            try:
                await asyncio.wait({task}, timeout=remaining)
            except asyncio.CancelledError:
                cancelled = True
                if current is not None:
                    current.uncancel()
                if task.done():
                    break
            except BaseException:
                break
        try:
            result = task.result()
        except BaseException as error:
            if cancelled:
                error.add_note(f"{label}: отмена вызывающего кода отложена до завершения операции")
            raise
    if cancelled:
        raise asyncio.CancelledError(f"{label}: отмена после подтверждённого завершения")
    return result


@contextmanager
def deferred_sigint():
    """Повторный Ctrl+C отменяет task; не обрывает его защищённую очистку."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    task = asyncio.current_task()
    if task is None:
        yield
        return
    previous = signal.getsignal(signal.SIGINT)

    def cancel(signum, frame):
        task.cancel("SIGINT")

    signal.signal(signal.SIGINT, cancel)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)
