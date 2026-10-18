"""Short-lived, in-memory deletion handles for private Discord controls.

Interaction tokens are never persisted. Ordinary launcher/room messages are
not registered here: only ephemeral responses use this cleanup path.
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

import discord

log = logging.getLogger(__name__)


@dataclass
class ResponseHandle:
    message_id: int
    delete: Callable[[], Awaitable]
    task: asyncio.Task | None = None


class ResponseTracker:
    def __init__(self, ttl=14 * 60):
        self.ttl = ttl
        self.entries = {}
        self.locks = {}

    async def add(self, room_id, user_id, message_id, delete):
        key = room_id, user_id
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            # A creation menu becomes the room panel by editing the same reply.
            # Transfer its deletion handle rather than leaving two expiry tasks.
            for other in list(self.entries):
                handle = self.entries.get(other)
                if other != key and other[1] == user_id and handle.message_id == message_id:
                    self.entries.pop(other)
                    self._cancel(handle)
            previous = self.entries.pop(key, None)
            if previous:
                self._cancel(previous)
                if previous.message_id != message_id:
                    await self._delete(previous)
            handle = ResponseHandle(message_id, delete)
            self.entries[key] = handle

            async def expire():
                await asyncio.sleep(self.ttl)
                async with lock:
                    if self.entries.get(key) is handle:
                        self.entries.pop(key)
                        await self._delete(handle)

            handle.task = asyncio.create_task(expire())

    @staticmethod
    def _cancel(handle):
        if handle.task and handle.task is not asyncio.current_task():
            handle.task.cancel()

    @staticmethod
    async def _delete(handle):
        try:
            await handle.delete()
        except discord.HTTPException as error:
            # Never log webhook URLs/tokens or entire interaction objects.
            if error.status not in {401, 403, 404}:
                log.warning('Private control cleanup failed: HTTP %s, code %s', error.status, error.code)

    async def cleanup_room(self, room_id):
        for key in [key for key in self.entries if key[0] == room_id]:
            await self.cleanup_user(*key)

    async def cleanup_user(self, room_id, user_id):
        key = room_id, user_id
        async with self.locks.setdefault(key, asyncio.Lock()):
            handle = self.entries.pop(key, None)
            if handle:
                self._cancel(handle)
                await self._delete(handle)

    async def close(self):
        for room_id in {key[0] for key in self.entries}:
            await self.cleanup_room(room_id)
