import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from room_state import RoomStore
import tests.test_room_service as fixtures


class AvailabilityTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ServiceTests.asyncSetUp
    asyncTearDown = fixtures.ServiceTests.asyncTearDown

    def prepare(self):
        self.channel.user_limit = 5
        self.channel.members = [self.creator] * 5
        self.friend.voice = None
        self.channel.permissions_for.return_value = discord.Permissions(view_channel=True, connect=True)
        self.friend.send = AsyncMock()
        self.other.send = AsyncMock()

    async def test_only_an_explicit_opt_in_gets_notified_once(self):
        self.prepare()
        await self.service.subscribe(100, self.friend)
        self.friend.send.assert_not_awaited()
        self.channel.members.pop()
        await self.service.notify_available(100)
        self.friend.send.assert_awaited_once()
        self.other.send.assert_not_awaited()
        await self.service.notify_available(100)
        self.friend.send.assert_awaited_once()
        self.assertFalse(self.room.waiting)

    async def test_full_room_keeps_subscription_without_sending(self):
        self.prepare()
        await self.service.subscribe(100, self.friend)
        await self.service.notify_available(100)
        self.friend.send.assert_not_awaited()
        self.assertEqual(self.room.waiting, {20})

    async def test_available_room_does_not_register_an_alert(self):
        self.prepare()
        self.channel.user_limit = 0
        self.assertFalse(await self.service.subscribe(100, self.friend))
        self.assertFalse(self.room.waiting)

    async def test_duplicate_and_concurrent_subscriptions_do_not_duplicate_alerts(self):
        self.prepare()
        await asyncio.gather(*(self.service.subscribe(100, self.friend) for _ in range(3)))
        self.assertEqual(self.room.waiting, {20})
        self.channel.members.pop()
        await asyncio.gather(*(self.service.notify_available(100) for _ in range(3)))
        self.friend.send.assert_awaited_once()

    async def test_cancel_is_personal_and_survives_restart(self):
        self.prepare()
        await self.service.subscribe(100, self.friend)
        await self.service.subscribe(100, self.other)
        await self.service.unsubscribe(100, self.friend)
        self.assertEqual(RoomStore(self.store.path).rooms[100].waiting, {30})
        self.channel.members.pop()
        await self.service.notify_available(100)
        self.friend.send.assert_not_awaited()
        self.other.send.assert_awaited_once()

    async def test_joining_target_room_cancels_own_notification(self):
        self.prepare()
        await self.service.subscribe(100, self.friend)
        await self.service.voice_update(self.friend, SimpleNamespace(channel=None), SimpleNamespace(channel=self.channel))
        self.assertNotIn(20, self.room.waiting)
        self.friend.send.assert_not_awaited()

    async def test_voice_departure_notifies_without_host_authority(self):
        self.prepare()
        await self.service.subscribe(100, self.friend)
        self.channel.members.pop()
        await self.service.voice_update(self.creator, SimpleNamespace(channel=self.channel), SimpleNamespace(channel=None))
        self.friend.send.assert_awaited_once()

    async def test_closed_dms_do_not_break_other_subscribers_or_repeat(self):
        self.prepare()
        await self.service.subscribe(100, self.friend)
        await self.service.subscribe(100, self.other)
        self.friend.send.side_effect = discord.Forbidden(MagicMock(status=403), 'DMs closed')
        self.channel.members.pop()
        await self.service.notify_available(100)
        self.other.send.assert_awaited_once()
        await self.service.notify_available(100)
        self.friend.send.assert_awaited_once()

    async def test_lost_channel_access_prevents_notification(self):
        self.prepare()
        await self.service.subscribe(100, self.friend)
        self.channel.members.pop()
        self.channel.permissions_for.return_value = discord.Permissions.none()
        await self.service.notify_available(100)
        self.friend.send.assert_not_awaited()
        self.assertFalse(self.room.waiting)

    async def test_other_server_and_bots_cannot_subscribe(self):
        self.prepare()
        for actor in [fixtures.member(40, SimpleNamespace(id=2)), self.guild.me]:
            with self.assertRaises(fixtures.service_module.RoomError):
                await self.service.subscribe(100, actor)
        self.assertFalse(self.room.waiting)

    async def test_person_already_inside_cannot_subscribe(self):
        self.prepare()
        with self.assertRaises(fixtures.service_module.RoomError):
            await self.service.subscribe(100, self.creator)

    async def test_room_deletion_discards_opt_ins_without_sending_dms(self):
        self.prepare()
        await self.service.subscribe(100, self.friend)
        await self.service.forget(100)
        self.assertFalse(RoomStore(self.store.path).rooms)
        await self.service.notify_available(100)
        self.friend.send.assert_not_awaited()

    async def test_opt_in_survives_restart_and_alerts_when_room_is_available(self):
        self.prepare()
        await self.service.subscribe(100, self.friend)
        self.service.store = RoomStore(self.store.path)
        self.channel.members.pop()
        await self.service.notify_available(100)
        self.friend.send.assert_awaited_once()
        self.assertFalse(RoomStore(self.store.path).rooms[100].waiting)

    async def test_cancellation_can_run_while_another_subscribers_dm_is_in_flight(self):
        self.prepare()
        await self.service.subscribe(100, self.friend)
        await self.service.subscribe(100, self.other)
        async def cancel_other(*args, **kwargs):
            await self.service.unsubscribe(100, self.other)
        self.friend.send.side_effect = cancel_other
        self.channel.members.pop()
        await asyncio.wait_for(self.service.notify_available(100), timeout=1)
        self.other.send.assert_not_awaited()

    async def test_deleting_room_during_dm_stops_remaining_alerts(self):
        self.prepare()
        await self.service.subscribe(100, self.friend)
        await self.service.subscribe(100, self.other)
        async def close_room(*args, **kwargs):
            await self.service.forget(100)
        self.friend.send.side_effect = close_room
        self.channel.members.pop()
        await self.service.notify_available(100)
        self.other.send.assert_not_awaited()
        self.assertFalse(self.store.rooms)

    async def test_room_refilling_keeps_unsent_notifications_queued(self):
        self.prepare()
        await self.service.subscribe(100, self.friend)
        await self.service.subscribe(100, self.other)
        async def fill_room(*args, **kwargs):
            self.channel.members = [self.creator] * 5
        self.friend.send.side_effect = fill_room
        self.channel.members.pop()
        await self.service.notify_available(100)
        self.other.send.assert_not_awaited()
        self.assertEqual(self.room.waiting, {30})

    async def test_removing_limit_triggers_notification(self):
        self.prepare()
        await self.service.subscribe(100, self.friend)
        async def edit_limit(**kwargs):
            self.channel.user_limit = kwargs['user_limit']
            return self.channel
        self.channel.edit.side_effect = edit_limit
        await self.service.set_limit(100, self.creator, 0)
        self.friend.send.assert_awaited_once()

    async def test_external_limit_increase_triggers_notification(self):
        self.prepare()
        await self.service.subscribe(100, self.friend)
        self.channel.user_limit = 6
        await self.service.channel_update(self.channel)
        self.friend.send.assert_awaited_once()

    async def test_external_small_limit_is_normalized_to_five(self):
        self.prepare()
        self.channel.user_limit = 2
        async def edit_limit(**kwargs):
            self.channel.user_limit = kwargs['user_limit']
            return self.channel
        self.channel.edit.side_effect = edit_limit
        await self.service.channel_update(self.channel)
        self.assertEqual(self.channel.user_limit, 5)

    async def test_notification_never_moves_a_user(self):
        self.prepare()
        await self.service.subscribe(100, self.friend)
        self.channel.members.pop()
        await self.service.notify_available(100)
        self.friend.move_to.assert_not_awaited()
        self.assertIn('not reserved', self.friend.send.call_args.args[0])

    async def test_access_is_required_to_subscribe_but_not_to_cancel(self):
        self.prepare()
        await self.service.subscribe(100, self.friend)
        self.channel.permissions_for.return_value = discord.Permissions.none()
        with self.assertRaises(fixtures.service_module.RoomError):
            await self.service.subscribe(100, self.other)
        await self.service.unsubscribe(100, self.friend)
        self.assertFalse(self.room.waiting)

    async def test_host_handover_during_delivery_preserves_remaining_alerts(self):
        self.prepare()
        await self.service.subscribe(100, self.friend)
        await self.service.subscribe(100, self.other)
        successor = fixtures.member(40, self.guild)
        self.members[40] = successor
        self.channel.members = [successor] * 4
        async def handover(*args, **kwargs):
            await self.service.reconcile(100)
        self.friend.send.side_effect = handover
        await self.service.notify_available(100)
        self.assertEqual(self.store.rooms[100].host_id, 40)
        self.other.send.assert_awaited_once()
