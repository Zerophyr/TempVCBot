import asyncio
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
from room_state import Room, RoomStore

try:
    service_module = importlib.import_module('room_service')
except ModuleNotFoundError:
    service_module = None


def member(ident, guild, *, bot=False, admin=False):
    value = MagicMock(spec=discord.Member)
    value.id = ident
    value.guild = guild
    value.display_name = f'Member {ident}'
    value.bot = bot
    value.guild_permissions = discord.Permissions(administrator=admin)
    value.voice = None
    value.move_to = AsyncMock()
    return value


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNotNone(service_module, 'Discord room service is not implemented')
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.guild = MagicMock(spec=discord.Guild)
        self.guild.id = 1
        self.guild.owner_id = 999
        self.guild.me = member(9, self.guild, bot=True)
        self.guild.default_role = MagicMock(spec=discord.Role)
        self.guild.default_role.id = 1
        self.role = MagicMock(spec=discord.Role)
        self.role.id = 2
        self.guild.get_role.side_effect = lambda ident: self.role if ident == 2 else None
        self.creator = member(10, self.guild)
        self.friend = member(20, self.guild)
        self.other = member(30, self.guild)
        self.members = {m.id: m for m in [self.guild.me, self.creator, self.friend, self.other]}
        self.guild.get_member.side_effect = self.members.get
        self.channel = MagicMock(spec=discord.VoiceChannel)
        self.channel.id = 100
        self.channel.guild = self.guild
        self.channel.name = "Member 10's Room"
        self.channel.user_limit = 0
        self.channel.members = [self.creator, self.friend]
        self.channel.edit = AsyncMock()
        self.channel.delete = AsyncMock()
        self.channel.send = AsyncMock()
        self.panel = MagicMock(spec=discord.Message)
        self.panel.id = 500
        self.panel.edit = AsyncMock()
        self.panel.pin = AsyncMock()
        self.channel.send.return_value = self.panel
        self.channel.fetch_message = AsyncMock(return_value=self.panel)
        self.creator.voice = SimpleNamespace(channel=self.channel)
        self.friend.voice = SimpleNamespace(channel=self.channel)
        self.category = MagicMock(spec=discord.CategoryChannel)
        self.category.id = 200
        self.category.create_voice_channel = AsyncMock(return_value=self.channel)
        self.channels = {100: self.channel, 200: self.category}
        self.guild.get_channel.side_effect = self.channels.get
        self.bot = MagicMock()
        self.bot.get_channel.side_effect = self.channels.get
        self.bot.get_guild.return_value = self.guild
        self.bot.add_view = MagicMock()
        self.store = RoomStore(Path(self.directory.name) / 'rooms.json')
        self.settings = {'1': {'category_id': 200, 'creator_channel_id': 300}}
        self.service = service_module.RoomService(
            self.bot, self.store, self.settings, Path(self.directory.name) / 'config.json',
            audience_ids=[2], entry_category_id=0, grace=0,
        )
        self.room = Room(channel_id=100, guild_id=1, creator_id=10, host_id=10)
        self.room.join(10)
        self.room.join(20)
        self.store.rooms[100] = self.room

    async def asyncTearDown(self):
        if hasattr(self, 'service'):
            await self.service.close()

    async def test_only_host_can_invite(self):
        with self.assertRaisesRegex(service_module.RoomError, 'host'):
            await self.service.invite(100, self.friend, [self.other])
        self.channel.edit.assert_not_awaited()

    async def test_stale_host_cannot_submit_after_ownership_changes(self):
        self.room.host_id = 20
        with self.assertRaisesRegex(service_module.RoomError, 'host'):
            await self.service.set_mode(100, self.creator, 'public')

    async def test_invite_grants_all_access_and_persists(self):
        await self.service.invite(100, self.creator, [self.other])
        updated = self.store.rooms[100]
        self.assertIn(30, updated.invited)
        overwrite = self.channel.edit.call_args.kwargs['overwrites'][self.other]
        self.assertTrue(overwrite.view_channel)
        self.assertTrue(overwrite.connect)
        self.assertTrue(overwrite.send_messages)
        self.assertIn(30, RoomStore(self.store.path).rooms[100].invited)

    async def test_failed_permission_update_does_not_save_invitation(self):
        self.channel.edit.side_effect = discord.Forbidden(MagicMock(status=403), 'denied')
        with self.assertRaises(discord.Forbidden):
            await self.service.invite(100, self.creator, [self.other])
        self.assertNotIn(30, self.store.rooms[100].invited)

    async def test_cross_server_invitation_is_rejected(self):
        stranger = member(40, SimpleNamespace(id=2))
        with self.assertRaisesRegex(service_module.RoomError, 'server'):
            await self.service.invite(100, self.creator, [stranger])

    async def test_bots_cannot_be_invited_or_become_host(self):
        with self.assertRaises(service_module.RoomError):
            await self.service.invite(100, self.creator, [self.guild.me])
        self.channel.members = [self.guild.me, self.friend]
        await self.service.reconcile(100)
        self.assertEqual(self.store.rooms[100].host_id, 20)

    async def test_creator_cannot_be_blocked_by_temporary_host(self):
        self.room.host_id = 20
        with self.assertRaisesRegex(service_module.RoomError, 'creator'):
            await self.service.manage_member(100, self.friend, self.creator, 'block')

    async def test_block_disconnects_and_prevents_reentry(self):
        await self.service.manage_member(100, self.creator, self.friend, 'block')
        self.friend.move_to.assert_awaited_once_with(None, reason='Blocked from temporary room by its host')
        self.assertIn(20, self.store.rooms[100].blocked)
        self.assertFalse(self.channel.edit.call_args.kwargs['overwrites'][self.friend].connect)

    async def test_revoke_removes_invite_but_public_access_still_applies(self):
        self.room.invited.add(30)
        self.room.mode = 'public'
        await self.service.manage_member(100, self.creator, self.other, 'revoke')
        self.assertNotIn(30, self.store.rooms[100].invited)
        self.assertNotIn(30, self.store.rooms[100].blocked)

    async def test_creator_return_restores_ownership_immediately(self):
        self.room.host_id = 20
        await self.service.reconcile(100)
        self.assertEqual(self.store.rooms[100].host_id, 10)

    async def test_handover_selects_earliest_joined_present_human(self):
        self.room.join_order = {30: 2, 20: 3}
        self.channel.members = [self.friend, self.other]
        await self.service.reconcile(100)
        self.assertEqual(self.store.rooms[100].host_id, 30)

    async def test_new_room_is_hidden_at_creation_not_after_creation(self):
        self.store.rooms.clear()
        await self.service.create(self.creator)
        overwrites = self.category.create_voice_channel.call_args.kwargs['overwrites']
        self.assertFalse(overwrites[self.role].view_channel)
        self.assertTrue(overwrites[self.creator].send_messages)
        self.creator.move_to.assert_awaited_once_with(self.channel)
        self.assertEqual(self.store.rooms[100].mode, 'hidden')

    async def test_duplicate_create_returns_existing_room_without_creating_another(self):
        room = await self.service.create(self.creator)
        self.assertEqual(room.channel_id, 100)
        self.category.create_voice_channel.assert_not_awaited()
        self.creator.move_to.assert_not_awaited()

    async def test_voice_chat_panel_is_kept_without_attempting_unsupported_pin(self):
        await self.service.ensure_panel(self.room)
        self.assertEqual(self.room.panel_id, 500)
        self.panel.pin.assert_not_awaited()

    async def test_empty_room_is_deleted_and_persistence_removed(self):
        self.channel.members = []
        self.room.created_at = 0
        await self.service.reconcile(100)
        self.channel.delete.assert_awaited_once()
        self.assertNotIn(100, self.store.rooms)

    async def test_room_that_was_joined_does_not_wait_60_seconds_after_everyone_leaves(self):
        self.channel.members = []
        await self.service.reconcile(100)
        self.channel.delete.assert_awaited_once()

    async def test_never_joined_room_retains_initial_join_window(self):
        self.room.join_order.clear()
        self.room.sequence = 0
        self.channel.members = []
        await self.service.reconcile(100)
        self.channel.delete.assert_not_awaited()
        self.assertIn(100, self.store.rooms)

    async def test_voice_events_record_rejoin_order_but_ignore_mute_changes(self):
        self.room.host_id = 20
        before = SimpleNamespace(channel=self.channel)
        after = SimpleNamespace(channel=None)
        await self.service.voice_update(self.friend, before, before)
        self.assertEqual(self.room.join_order[20], 2)
        await self.service.voice_update(self.friend, before, after)
        self.assertNotIn(20, self.room.join_order)
        await self.service.voice_update(self.friend, after, before)
        self.assertEqual(self.room.join_order[20], 3)

    async def test_hide_preserves_current_participants_chat_access(self):
        self.room.mode = 'public'
        await self.service.set_mode(100, self.creator, 'hidden')
        self.assertIn(20, self.store.rooms[100].invited)
        self.assertTrue(self.channel.edit.call_args.kwargs['overwrites'][self.friend].send_messages)

    async def test_limit_accepts_zero_and_99_but_rejects_out_of_range(self):
        for value in [0, 99]:
            await self.service.set_limit(100, self.creator, value)
        for value in [-1, 100]:
            with self.assertRaises(service_module.RoomError):
                await self.service.set_limit(100, self.creator, value)

    async def test_name_rejects_blank_control_characters_and_too_long(self):
        for name in [' ', 'hello\nworld', 'x' * 101]:
            with self.assertRaises(service_module.RoomError):
                await self.service.rename(100, self.creator, name)
        await self.service.rename(100, self.creator, 'German practice')
        self.channel.edit.assert_any_await(name='German practice', reason='Renamed by temporary room host')

    async def test_legacy_migration_preserves_creator_and_existing_visibility(self):
        self.store.rooms.clear()
        legacy = Path(self.directory.name) / 'channels.json'
        legacy.write_text(json.dumps({'100': 10}), encoding='utf-8')
        self.channel.overwrites = {self.guild.default_role: discord.PermissionOverwrite(connect=True)}
        await self.service.migrate_legacy(legacy)
        self.assertEqual(self.store.rooms[100].creator_id, 10)
        self.assertEqual(self.store.rooms[100].mode, 'public')
        self.assertEqual(json.loads(legacy.read_text()), {'100': 10})

    async def test_controls_created_in_managed_category_not_restricted_entry_category(self):
        parent = MagicMock(spec=discord.CategoryChannel)
        parent.id = 400
        parent.create_text_channel = AsyncMock()
        self.channels[400] = parent
        self.channels[300] = self.channel
        controls_channel = MagicMock(spec=discord.TextChannel)
        controls_channel.id = 600
        parent.create_text_channel.return_value = controls_channel
        self.category.edit = AsyncMock()
        self.category.create_text_channel = AsyncMock(return_value=controls_channel)
        self.service.entry_category_id = 400
        self.service.ensure_launcher = AsyncMock()
        settings = await self.service.setup(self.guild)
        self.category.create_text_channel.assert_awaited_once()
        parent.create_text_channel.assert_not_awaited()
        self.assertEqual(settings['controls_channel_id'], 600)


if __name__ == '__main__':
    unittest.main()
