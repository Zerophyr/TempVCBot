import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

import discord

import room_state
import tests.test_room_service as fixtures


class PublicPolicyTests(unittest.TestCase):
    def test_room_has_no_privacy_invitation_or_block_state(self):
        room = room_state.Room(100, 1, 10, 10)
        for field in ['mode', 'invited', 'blocked']:
            self.assertFalse(hasattr(room, field), field)

    def test_public_policy_grants_voice_audience_chat_and_video(self):
        from tests.test_room_state import Guild
        guild = Guild()
        overwrites = room_state.build_overwrites(guild, room_state.Room(100, 1, 10, 10), [2, 3])
        for role in guild.roles:
            for name in ['view_channel', 'connect', 'send_messages', 'stream']:
                self.assertTrue(getattr(overwrites[role], name), name)
        self.assertFalse(overwrites[guild.default_role].connect)

    def test_v2_load_migrates_in_memory_without_writing_production_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rooms.json'
            old = {'version': 2, 'rooms': {'100': {
                'channel_id': 100, 'guild_id': 1, 'creator_id': 10, 'host_id': 20,
                'mode': 'hidden', 'invited': [20], 'blocked': [30],
                'join_order': {'20': 4}, 'sequence': 4, 'panel_id': 500, 'created_at': 12.5}}}
            path.write_text(json.dumps(old), encoding='utf-8')
            original = path.read_bytes()
            store = room_state.RoomStore(path)
            room = store.rooms[100]
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual((room.creator_id, room.host_id, room.panel_id, room.created_at), (10, 20, 500, 12.5))
            self.assertEqual(room.join_order, {20: 4})
            self.assertEqual(room.waiting, set())
            store.save()
            saved = json.loads(path.read_text())
            self.assertEqual(saved['version'], 3)
            self.assertFalse({'mode', 'invited', 'blocked'} & saved['rooms']['100'].keys())

    def test_unsupported_state_is_rejected_without_overwriting_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rooms.json'
            path.write_text('{"version": 99, "rooms": {}}')
            original = path.read_bytes()
            with self.assertRaises(ValueError):
                room_state.RoomStore(path)
            self.assertEqual(path.read_bytes(), original)


class PublicServiceTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ServiceTests.asyncSetUp
    asyncTearDown = fixtures.ServiceTests.asyncTearDown

    async def test_creation_is_explicitly_unlimited(self):
        self.store.rooms.clear()
        await self.service.create(self.creator)
        options = self.category.create_voice_channel.call_args.kwargs
        self.assertEqual(options['user_limit'], 0)
        self.assertTrue(options['overwrites'][self.role].connect)

    async def test_no_room_level_removal_or_privacy_endpoints(self):
        for action in ['manage_member', 'set_mode', 'invite']:
            self.assertFalse(hasattr(self.service, action), action)

    async def test_limit_allows_unlimited_or_at_least_five(self):
        for value in [0, 5, 99]:
            await self.service.set_limit(100, self.creator, value)
        self.channel.edit.reset_mock()
        for value in [-1, 1, 2, 3, 4, 100, True, 5.0, '5']:
            with self.assertRaises(fixtures.service_module.RoomError):
                await self.service.set_limit(100, self.creator, value)
        self.channel.edit.assert_not_awaited()

    async def test_only_host_can_change_size(self):
        with self.assertRaises(fixtures.service_module.RoomError):
            await self.service.set_limit(100, self.friend, 5)
        self.channel.edit.assert_not_awaited()

    async def test_reconnect_clears_old_restrictions_and_raises_small_limits(self):
        self.channel.user_limit = 3
        self.service.ensure_launcher = AsyncMock()
        self.service.refresh_panel = AsyncMock()
        self.settings['1'].pop('creator_channel_id')
        await self.service.restore(self.guild)
        options = self.channel.edit.call_args.kwargs
        self.assertEqual(options['user_limit'], 5)
        self.assertTrue(options['overwrites'][self.role].view_channel)
        self.assertTrue(options['overwrites'][self.role].connect)

    async def test_return_to_existing_full_room_does_not_bypass_limit_by_moving_creator(self):
        self.channel.user_limit = 5
        self.channel.members = [self.friend] * 5
        self.creator.voice.channel = type('OtherChannel', (), {'id': 999})()
        await self.service.create(self.creator)
        self.creator.move_to.assert_not_awaited()

    async def test_reconnect_preserves_unlimited_and_valid_optional_limits(self):
        self.settings['1'].pop('creator_channel_id')
        self.service.ensure_launcher = AsyncMock()
        for limit in [0, 5, 99]:
            self.room.public_policy_applied = False
            self.channel.user_limit = limit
            await self.service.restore(self.guild)
            self.assertEqual(self.channel.edit.call_args.kwargs['user_limit'], limit)

    async def test_setup_does_not_expose_old_archives_by_changing_category_permissions(self):
        import discord
        from unittest.mock import MagicMock
        self.settings['1'].pop('creator_channel_id')
        controls = MagicMock(spec=discord.TextChannel)
        controls.id = 600
        self.category.edit = AsyncMock()
        self.category.create_text_channel = AsyncMock(return_value=controls)
        self.service.ensure_launcher = AsyncMock()
        await self.service.setup(self.guild)
        self.category.edit.assert_not_awaited()
        self.assertTrue(self.category.create_text_channel.call_args.kwargs['overwrites'][self.role].view_channel)
