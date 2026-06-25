"""RunCoordinator: FIFO serialization for session execution."""

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")


class RunCoordinator:
    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}

    async def acquire(self, session_id: str) -> None:
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        await self._locks[session_id].acquire()

    def release(self, session_id: str):
        lock = self._locks.get(session_id)
        if lock and lock.locked():
            lock.release()

    async def run_exclusive(self, session_id: str, coro: Awaitable[T]) -> T:
        await self.acquire(session_id)
        try:
            return await coro
        finally:
            self.release(session_id)
