import asyncio
import importlib
import unittest
from unittest.mock import AsyncMock

try:
    messages = importlib.import_module('control_messages')
except ModuleNotFoundError:
    messages = None


class ResponseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNotNone(messages, 'Private response cleanup is not implemented')
        self.tracker = messages.ResponseTracker(ttl=60)

    async def asyncTearDown(self):
        if hasattr(self, 'tracker'):
            await self.tracker.close()

    async def test_room_removal_deletes_only_that_rooms_private_responses(self):
        first, second = AsyncMock(), AsyncMock()
        await self.tracker.add(100, 10, 500, first)
        await self.tracker.add(200, 20, 600, second)
        await self.tracker.cleanup_room(100)
        first.assert_awaited_once()
        second.assert_not_awaited()

    async def test_reopening_replaces_old_response_for_same_member_and_room(self):
        first, second = AsyncMock(), AsyncMock()
        await self.tracker.add(100, 10, 500, first)
        await self.tracker.add(100, 10, 501, second)
        first.assert_awaited_once()
        second.assert_not_awaited()

    async def test_editing_same_response_refreshes_handle_without_deleting_it(self):
        first, second = AsyncMock(), AsyncMock()
        await self.tracker.add(100, 10, 500, first)
        await self.tracker.add(100, 10, 500, second)
        first.assert_not_awaited()
        await self.tracker.cleanup_room(100)
        second.assert_awaited_once()

    async def test_private_messages_expire_before_interaction_tokens(self):
        delete = AsyncMock()
        self.tracker.ttl = 0.01
        await self.tracker.add(100, 10, 500, delete)
        await asyncio.sleep(0.03)
        delete.assert_awaited_once()
        self.assertFalse(self.tracker.entries)

    async def test_shutdown_cleans_messages_before_discarding_tokens(self):
        delete = AsyncMock()
        await self.tracker.add(100, 10, 500, delete)
        await self.tracker.close()
        delete.assert_awaited_once()
        self.assertFalse(self.tracker.entries)

    async def test_room_with_no_responses_is_safe_to_remove(self):
        await self.tracker.cleanup_room(100)

    async def test_cancelling_one_creation_menu_does_not_close_another_users_menu(self):
        first, second = AsyncMock(), AsyncMock()
        await self.tracker.add(None, 10, 500, first)
        await self.tracker.add(None, 20, 501, second)
        self.assertTrue(hasattr(self.tracker, 'cleanup_user'), 'User-scoped menu cleanup is missing')
        await self.tracker.cleanup_user(None, 10)
        first.assert_awaited_once()
        second.assert_not_awaited()

    async def test_creation_menu_moves_to_room_cleanup_without_duplicate_handle(self):
        first, second = AsyncMock(), AsyncMock()
        await self.tracker.add(None, 10, 500, first)
        await self.tracker.add(100, 10, 500, second)
        self.assertEqual(len(self.tracker.entries), 1)
        first.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
