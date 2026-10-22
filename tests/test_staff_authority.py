import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from room_state import Room, RoomStore, build_overwrites
import tests.test_room_service as fixtures


class StaffAuthorityTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ServiceTests.asyncSetUp
    asyncTearDown = fixtures.ServiceTests.asyncTearDown

    async def test_server_owner_can_override_settings_without_becoming_host(self):
        owner = fixtures.member(self.guild.owner_id, self.guild)
        await self.service.rename(100, owner, 'Staff name')
        await self.service.set_limit(100, owner, 2)
        self.assertEqual(self.store.rooms[100].host_id, 10)
        self.assertEqual(self.channel.edit.call_args.kwargs['user_limit'], 2)

    async def test_native_moderation_permissions_allow_staff_overrides(self):
        for permission in ['administrator', 'manage_guild', 'manage_channels', 'move_members',
                           'kick_members', 'ban_members', 'moderate_members', 'mute_members', 'deafen_members']:
            self.other.guild_permissions = discord.Permissions(**{permission: True})
            await self.service.set_limit(100, self.other, 1)
        self.assertEqual(self.store.rooms[100].host_id, 10)

    async def test_staff_role_name_alone_does_not_grant_permissions(self):
        self.other.display_name = 'Admin / Server Mod'
        with self.assertRaises(fixtures.service_module.RoomError):
            await self.service.rename(100, self.other, 'Not authorized')
        self.channel.edit.assert_not_awaited()

    async def test_staff_permissions_do_not_apply_across_servers(self):
        outsider = fixtures.member(40, SimpleNamespace(id=2), admin=True)
        with self.assertRaises(fixtures.service_module.RoomError):
            await self.service.rename(100, outsider, 'Not this server')
        self.channel.edit.assert_not_awaited()

    async def test_staff_in_a_room_can_open_its_settings_without_owning_it(self):
        self.other.guild_permissions = discord.Permissions(move_members=True)
        self.other.voice = SimpleNamespace(channel=self.channel)
        self.assertEqual(self.service.managed(self.other).channel_id, 100)
        self.assertEqual(self.store.rooms[100].host_id, 10)

    async def test_host_handover_preserves_manual_moderator_overwrites(self):
        self.room.public_policy_applied = True
        self.channel.overwrites = {self.other: discord.PermissionOverwrite(connect=False)}
        self.channel.members = [self.friend]
        await self.service.reconcile(100)
        self.assertEqual(self.store.rooms[100].host_id, 20)
        self.channel.edit.assert_not_awaited()
        self.assertFalse(self.channel.overwrites[self.other].connect)
        self.other.move_to.assert_not_awaited()

    async def test_restart_preserves_manual_staff_limit_and_overwrites_after_migration(self):
        self.settings['1'].pop('creator_channel_id')
        self.service.ensure_launcher = AsyncMock()
        self.room.public_policy_applied = False
        await self.service.restore(self.guild)
        self.assertTrue(self.room.public_policy_applied)
        self.service.store = RoomStore(self.store.path)
        self.channel.edit.reset_mock()
        self.channel.user_limit = 2
        self.channel.overwrites = {self.other: discord.PermissionOverwrite(connect=False)}
        await self.service.restore(self.guild)
        self.channel.edit.assert_not_awaited()
        self.assertFalse(self.channel.overwrites[self.other].connect)
        self.assertEqual(self.channel.user_limit, 2)

    async def test_manual_staff_limit_change_is_not_automatically_undone(self):
        self.channel.user_limit = 2
        await self.service.channel_update(self.channel)
        self.channel.edit.assert_not_awaited()
        self.assertEqual(self.channel.user_limit, 2)

    async def test_create_does_not_move_a_user_past_a_manual_connect_denial(self):
        self.creator.voice = SimpleNamespace(channel=SimpleNamespace(id=999))
        self.channel.permissions_for.return_value = discord.Permissions.none()
        with self.assertRaises(fixtures.service_module.RoomError):
            await self.service.create(self.creator)
        self.creator.move_to.assert_not_awaited()

    async def test_native_moderation_permissions_are_not_denied_or_granted_by_room_policy(self):
        moderator = MagicMock(spec=discord.Role)
        moderator.id = 3
        moderator.permissions = discord.Permissions(move_members=True)
        self.guild.roles = [self.role, moderator]
        policy = build_overwrites(self.guild, self.room, [2])
        self.assertIn(moderator, policy)
        self.assertTrue(policy[moderator].view_channel)
        self.assertTrue(policy[moderator].connect)
        for role in [self.role, moderator]:
            for permission in ['move_members', 'mute_members', 'deafen_members', 'manage_channels']:
                self.assertIsNone(getattr(policy[role], permission))

    async def test_new_room_does_not_auto_move_creator_past_a_connect_denial(self):
        self.store.rooms.clear()
        self.creator.voice = SimpleNamespace(channel=SimpleNamespace(id=999))
        self.channel.permissions_for.return_value = discord.Permissions.none()
        with self.assertRaises(fixtures.service_module.RoomError):
            await self.service.create(self.creator)
        self.creator.move_to.assert_not_awaited()
