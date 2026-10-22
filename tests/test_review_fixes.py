"""Regression coverage for the three offline predeployment review findings."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from TempVCBot import RoomBot
import tests.test_room_service as fixtures


class ReviewRegressions(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ServiceTests.asyncSetUp
    asyncTearDown = fixtures.ServiceTests.asyncTearDown

    async def test_migration_preserves_a_manual_moderator_connect_denial(self):
        self.settings['1'].pop('creator_channel_id')
        self.service.ensure_launcher = AsyncMock()
        # Member 30 was manually restricted by a moderator, not an old bot block.
        self.channel.overwrites = {
            self.other: discord.PermissionOverwrite(connect=False, speak=False)
        }
        self.room.public_policy_applied = False
        await self.service.restore(self.guild)
        applied = self.channel.edit.call_args.kwargs['overwrites']
        preserved = applied.get(self.other)
        self.assertIsNotNone(preserved, 'Migration dropped a native moderator restriction')
        self.assertFalse(preserved.connect)

    async def test_notification_checks_live_limit_after_rest_snapshot_becomes_stale(self):
        # VoiceChannel.edit returns a detached new channel instance. Both real
        # channel instances share current voice membership through their guild.
        self.channel.user_limit = 6
        present = [self.creator]
        for ident in [40, 50, 60, 70]:
            person = fixtures.member(ident, self.guild)
            self.members[ident] = person
            present.append(person)
        self.channel.members = present
        self.guild._voice_states = {
            person.id: SimpleNamespace(channel=self.channel) for person in present
        }
        rest_channel = discord.VoiceChannel(
            state=SimpleNamespace(), guild=self.guild,
            data={'id': '100', 'type': 2, 'name': 'Public room', 'position': 0,
                  'permission_overwrites': [], 'bitrate': 64000, 'user_limit': 6},
        )
        self.friend.voice = self.other.voice = None
        self.friend.send, self.other.send = AsyncMock(), AsyncMock()
        self.room.waiting = {20, 30}

        async def staff_lowers_limit_while_first_dm_is_in_flight(*args, **kwargs):
            self.channel.user_limit = 5  # Gateway updates the cached instance.

        self.friend.send.side_effect = staff_lowers_limit_while_first_dm_is_in_flight
        await self.service.notify_available(100, channel=rest_channel)
        self.other.send.assert_not_awaited()
        self.assertEqual(self.room.waiting, {30})

    async def test_last_departure_during_initial_restore_still_schedules_cleanup(self):
        self.settings['1'].pop('creator_channel_id')
        self.service.ensure_launcher = AsyncMock()
        bot = RoomBot(Path(self.directory.name) / 'startup')
        bot.settings = self.settings
        bot.store = self.store
        bot.service = self.service
        bot._connection._guilds = {1: self.guild}
        bot.heartbeat = AsyncMock()
        bot.tree.sync = AsyncMock(return_value=[])

        async def depart_during_migration(**kwargs):
            self.channel.members = []  # SDK cache is updated before dispatch.
            for person in [self.creator, self.friend]:
                await bot.on_voice_state_update(
                    person, SimpleNamespace(channel=self.channel),
                    SimpleNamespace(channel=None),
                )
            return self.channel

        self.channel.edit.side_effect = depart_during_migration
        try:
            await bot.on_ready()
            self.assertTrue(bot.initialized)
            self.assertIn(100, self.service.tasks,
                          'Departure was ignored; the empty room has no cleanup task')
        finally:
            await bot.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
