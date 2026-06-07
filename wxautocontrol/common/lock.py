from __future__ import annotations

import asyncio
import functools
import inspect
import multiprocessing
import threading
from contextlib import asynccontextmanager, contextmanager
from typing import Any, Awaitable, Callable, TypeVar, overload


F = TypeVar("F", bound=Callable[..., Any])
AsyncReturn = TypeVar("AsyncReturn")


class LockManager:
    process_lock = multiprocessing.RLock()
    thread_lock = threading.RLock()
    _async_lock: asyncio.Lock | None = None

    @classmethod
    def _get_async_lock(cls) -> asyncio.Lock:
        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        lock = cls._async_lock
        if lock is None or (loop and getattr(lock, "_loop", loop) is not loop):
            lock = asyncio.Lock()
            cls._async_lock = lock
        return lock

    @classmethod
    @contextmanager
    def acquire(cls):
        with cls.process_lock:
            with cls.thread_lock:
                yield

    @classmethod
    @asynccontextmanager
    async def acquire_async(cls):
        async with cls._get_async_lock():
            with cls.process_lock:
                with cls.thread_lock:
                    yield


@overload
def uilock(func: Callable[..., Awaitable[AsyncReturn]]) -> Callable[..., Awaitable[AsyncReturn]]:
    ...


@overload
def uilock(func: F) -> F:
    ...


def uilock(func: F):
    if inspect.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any):
            async with LockManager.acquire_async():
                return await func(*args, **kwargs)

        return async_wrapper

    @functools.wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any):
        with LockManager.acquire():
            return func(*args, **kwargs)

    return sync_wrapper
