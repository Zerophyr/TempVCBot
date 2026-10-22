import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock

import discord

from room_state import Room, RoomStore, build_overwrites, member_access, migration_overwrites
from TempVCBot import RoomBot
import tests.test_room_service as fixtures


class MigrationAndAuditTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ServiceTests.asyncSetUp
    asyncTearDown = fixtures.ServiceTests.asyncTearDown

    def legacy_room(self, **changes):
        value = dict(channel_id=100, guild_id=1, creator_id=10, host_id=10,
                     mode='hidden', invited=[20], blocked=[30])
        value.update(changes)
        return Room.from_dict(value, legacy=True)

    async def test_migration_removes_only_old_bot_rules_and_preserves_staff_exceptions(self):
        room = self.legacy_room()
        custom = fixtures.member(40, self.guild)
        old_role = member_access(False)
        old_role.manage_messages = False
        invited = member_access()
        invited.connect = False  # Staff denied this previously invited person.
        policy = migration_overwrites(self.guild, room, [2], {
            self.guild.default_role: member_access(False),
            self.role: old_role, self.friend: invited,
            self.other: member_access(False),
            custom: discord.PermissionOverwrite(connect=False, speak=False),
        })
        self.assertTrue(policy[self.role].connect)
        self.assertTrue(policy[self.role].view_channel)
        self.assertFalse(policy[self.role].manage_messages)
        self.assertFalse(policy[self.friend].connect)
        self.assertIsNone(policy[self.friend].view_channel)
        self.assertNotIn(self.other, policy)  # Former host-created block removed.
        self.assertFalse(policy[custom].connect)
        self.assertFalse(policy[custom].speak)

    async def test_pending_provenance_survives_a_save_and_process_restart(self):
        self.store.rooms[100] = self.legacy_room()
        self.store.save()
        recovered = RoomStore(self.store.path).rooms[100]
        self.assertEqual(recovered.migration_policy, self.store.rooms[100].migration_policy)
        self.assertFalse(recovered.public_policy_applied)
        self.assertFalse({'mode', 'invited', 'blocked'} & recovered.to_dict().keys())
        policy = migration_overwrites(self.guild, recovered, [2], {self.other: member_access(False)})
        self.assertNotIn(self.other, policy)

    async def test_successful_migration_discards_provenance_only_after_edit(self):
        self.store.rooms[100] = self.legacy_room()
        self.settings['1'].pop('creator_channel_id')
        self.service.ensure_launcher = AsyncMock()
        await self.service.restore(self.guild)
        saved = RoomStore(self.store.path).rooms[100]
        self.assertTrue(saved.public_policy_applied)
        self.assertIsNone(saved.migration_policy)
        self.assertNotIn('migration_policy', saved.to_dict())

    async def test_failed_migration_retains_provenance_for_retry(self):
        self.store.rooms[100] = self.legacy_room()
        self.store.save()
        self.channel.edit.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'Denied')
        with self.assertRaises(discord.Forbidden):
            await self.service.restore(self.guild)
        recovered = RoomStore(self.store.path).rooms[100]
        self.assertFalse(recovered.public_policy_applied)
        self.assertIsNotNone(recovered.migration_policy)

    async def test_no_provenance_preserves_unknown_overwrites_conservatively(self):
        restriction = discord.PermissionOverwrite(connect=False, create_instant_invite=False)
        policy = migration_overwrites(self.guild, self.room, [2], {self.other: restriction})
        self.assertFalse(policy[self.other].connect)
        self.assertFalse(policy[self.other].create_instant_invite)

    async def test_rename_records_actor_old_and_new_name_in_chat_and_audit_reason(self):
        old = self.channel.name
        await self.service.rename(100, self.creator, 'New **name** @everyone')
        content = self.channel.send.call_args.args[0]
        self.assertIn('<@10>', content)
        self.assertIn('User ID: `10`', content)
        self.assertIn(old, content)
        self.assertIn(r'New \*\*name\*\* @everyone', content)
        self.assertIn('Current room owner', content)
        self.assertFalse(self.channel.send.call_args.kwargs['allowed_mentions'].everyone)
        self.assertFalse(self.channel.send.call_args.kwargs['allowed_mentions'].users)
        self.assertIn('user 10', self.channel.edit.call_args.kwargs['reason'])

    async def test_staff_rename_attributes_staff_without_changing_the_owner(self):
        self.other.guild_permissions = discord.Permissions(move_members=True)
        await self.service.rename(100, self.other, 'Staff correction')
        content = self.channel.send.call_args.args[0]
        self.assertIn('<@30>', content)
        self.assertIn('User ID: `30`', content)
        self.assertIn('Current room owner:** <@10>', content)
        self.assertEqual(self.store.rooms[100].host_id, 10)
        self.assertIn('user 30; current host 10', self.channel.edit.call_args.kwargs['reason'])

    async def test_limit_announcement_records_actor_and_unlimited_transition(self):
        await self.service.set_limit(100, self.creator, 5)
        content = self.channel.send.call_args.args[0]
        self.assertIn('User ID: `10`', content)
        self.assertIn('**Unlimited** → **5**', content)
        self.channel.user_limit = 5
        await self.service.set_limit(100, self.creator, 0)
        self.assertIn('**5** → **Unlimited**', self.channel.send.call_args.args[0])

    async def test_consecutive_limit_edits_use_rest_values_while_gateway_cache_lags(self):
        fresh = MagicMock(spec=discord.VoiceChannel)
        fresh.id, fresh.guild, fresh.name, fresh.user_limit = 100, self.guild, 'Current name', 0
        fresh.permissions_for.return_value = discord.Permissions(view_channel=True, connect=True)
        fresh.send = AsyncMock(return_value=self.panel)
        fresh.fetch_message = AsyncMock(return_value=self.panel)
        async def edit(**kwargs):
            fresh.user_limit = kwargs['user_limit']
            return fresh
        fresh.edit = AsyncMock(side_effect=edit)
        self.guild.fetch_channel.side_effect = None
        self.guild.fetch_channel.return_value = fresh
        await self.service.set_limit(100, self.creator, 5)
        await self.service.set_limit(100, self.creator, 0)
        changes = [call.args[0] for call in fresh.send.call_args_list if call.args]
        self.assertEqual(len(changes), 2)
        self.assertIn('**5** → **Unlimited**', changes[-1])
        self.assertEqual(self.channel.user_limit, 0)  # Gateway never caught up.

    async def test_unavailable_current_settings_cannot_publish_a_change(self):
        self.guild.fetch_channel.side_effect = discord.NotFound(SimpleNamespace(status=404, reason='Not Found'), 'Gone')
        with self.assertRaises(discord.NotFound):
            await self.service.rename(100, self.creator, 'Not applied')
        self.channel.edit.assert_not_awaited()
        self.channel.send.assert_not_awaited()

    async def test_no_change_does_not_announce_a_settings_change(self):
        self.room.panel_id = 500
        await self.service.rename(100, self.creator, self.channel.name)
        await self.service.set_limit(100, self.creator, 0)
        self.channel.send.assert_not_awaited()

    async def test_rejected_action_cannot_publish_a_change_announcement(self):
        with self.assertRaises(fixtures.service_module.RoomError):
            await self.service.rename(100, self.friend, 'Not allowed')
        self.channel.send.assert_not_awaited()
        self.channel.edit.assert_not_awaited()

    async def test_failed_edit_does_not_publish_a_success_announcement(self):
        self.channel.edit.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'Denied')
        with self.assertRaises(discord.Forbidden):
            await self.service.rename(100, self.creator, 'Not applied')
        self.channel.send.assert_not_awaited()

    async def test_announcement_failure_does_not_undo_a_successful_rename(self):
        self.room.panel_id = 500
        self.channel.send.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'Denied')
        with self.assertLogs('room_service', level='WARNING'):
            await self.service.rename(100, self.creator, 'Applied')
        self.channel.edit.assert_awaited_once()

    async def test_panel_prominently_identifies_current_owner_with_stable_id(self):
        self.room.host_id = 20
        await self.service.ensure_panel(self.room)
        sent = self.channel.send.call_args.kwargs
        self.assertIn('Current room owner:** <@20>', sent['content'])
        owner = sent['embed'].fields[0]
        self.assertIn('owner', owner.name)
        self.assertIn('`20`', owner.value)
        self.assertFalse(owner.inline)

    async def test_handover_and_creator_return_update_card_and_announce_both_ids(self):
        self.room.panel_id = 500
        self.channel.members = [self.friend]
        await self.service.reconcile(100)
        self.assertIn('<@20>', self.panel.edit.call_args.kwargs['content'])
        content = self.channel.send.call_args.args[0]
        self.assertIn('<@10> (`10`) → <@20> (`20`)', content)
        self.channel.members = [self.friend, self.creator]
        await self.service.reconcile(100)
        self.assertIn('<@10>', self.panel.edit.call_args.kwargs['content'])
        self.assertIn('The creator returned', self.channel.send.call_args.args[0])

    async def test_owner_status_updates_on_handover_without_changing_native_settings(self):
        self.channel.permissions_for.return_value = discord.Permissions(
            view_channel=True, connect=True, manage_channels=True, set_voice_channel_status=True)
        await self.service.refresh_panel(self.room)
        status = self.channel.edit.call_args.kwargs['status']
        self.assertIn('Member 10', status)
        self.assertIn('10', status)
        self.channel.edit.reset_mock()
        await self.service.refresh_panel(self.room)
        self.channel.edit.assert_not_awaited()  # No repeated status writes.
        self.channel.members = [self.friend]
        await self.service.reconcile(100)
        self.assertIn('Member 20', self.channel.edit.call_args.kwargs['status'])
        self.assertNotIn('overwrites', self.channel.edit.call_args.kwargs)
        self.assertNotIn('user_limit', self.channel.edit.call_args.kwargs)

    async def test_denied_status_permission_does_not_block_chat_owner_indicator(self):
        await self.service.refresh_panel(self.room)
        self.channel.edit.assert_not_awaited()
        self.assertIn('<@10>', self.channel.send.call_args.kwargs['content'])

    async def test_status_owner_label_is_bot_controlled_with_staff_override(self):
        staff = MagicMock(spec=discord.Role)
        staff.id = 3
        staff.permissions = discord.Permissions(move_members=True)
        self.guild.roles.append(staff)
        policy = build_overwrites(self.guild, self.room, [2])
        self.assertFalse(policy[self.guild.default_role].set_voice_channel_status)
        self.assertFalse(policy[self.role].set_voice_channel_status)
        self.assertTrue(policy[self.guild.me].set_voice_channel_status)
        self.assertTrue(policy[staff].set_voice_channel_status)
        self.assertIsNone(policy[self.creator].manage_channels)

    async def test_owner_status_failure_keeps_chat_controls_and_retries_later(self):
        self.channel.permissions_for.return_value = discord.Permissions(
            view_channel=True, connect=True, manage_channels=True, set_voice_channel_status=True)
        self.channel.edit.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'Denied')
        with self.assertLogs('room_service', level='WARNING'):
            await self.service.refresh_panel(self.room)
        self.assertNotIn(100, self.service.owner_statuses)
        self.channel.edit.side_effect = None
        await self.service.refresh_panel(self.room)
        self.assertIn(100, self.service.owner_statuses)

    async def test_panel_uses_rest_edit_result_before_gateway_cache_catches_up(self):
        fresh = MagicMock(spec=discord.VoiceChannel)
        fresh.id, fresh.guild, fresh.name, fresh.user_limit = 100, self.guild, 'Fresh name', 0
        fresh.permissions_for.return_value = discord.Permissions(view_channel=True, connect=True)
        fresh.send = AsyncMock(return_value=self.panel)
        self.channel.edit.return_value = fresh
        await self.service.rename(100, self.creator, 'Fresh name')
        panel = fresh.send.call_args_list[0].kwargs
        self.assertEqual(panel['embed'].title, 'Fresh name')

    async def test_startup_replay_preserves_rejoin_order_and_clears_waiter(self):
        bot = RoomBot(Path(self.directory.name) / 'startup')
        bot.settings, bot.store, bot.service = self.settings, self.store, self.service
        bot._connection._guilds = {1: self.guild}
        bot.heartbeat = AsyncMock()
        self.service.restore = AsyncMock()
        self.service.grace = 60
        self.room.waiting = {20}
        self.room.join(30)

        async def changes_during_sync():
            await bot.on_voice_state_update(self.friend, SimpleNamespace(channel=self.channel), SimpleNamespace(channel=None))
            await bot.on_voice_state_update(self.friend, SimpleNamespace(channel=None), SimpleNamespace(channel=self.channel))
            return []

        bot.tree.sync = AsyncMock(side_effect=changes_during_sync)
        try:
            await bot.on_ready()
            self.assertTrue(bot.initialized)
            self.assertFalse(bot.pending_voice_updates)
            self.assertGreater(self.room.join_order[20], self.room.join_order[30])
            self.assertNotIn(20, self.room.waiting)
            self.assertIn(100, self.service.tasks)
        finally:
            await bot.close()

    async def test_creator_return_during_startup_restores_ownership_after_initialization(self):
        self.room.host_id = 20
        self.service.grace = 60
        bot = RoomBot(Path(self.directory.name) / 'startup')
        bot.settings, bot.store, bot.service = self.settings, self.store, self.service
        bot._connection._guilds = {1: self.guild}
        bot.get_channel = self.bot.get_channel
        bot.heartbeat = AsyncMock()
        self.service.restore = AsyncMock()
        bot.tree.sync = AsyncMock(return_value=[])
        try:
            await bot.on_voice_state_update(self.creator, SimpleNamespace(channel=None), SimpleNamespace(channel=self.channel))
            await bot.on_ready()
            task = self.service.tasks[100]
            await asyncio.wait_for(asyncio.shield(task), timeout=1)
            self.assertEqual(self.store.rooms[100].host_id, 10)
            self.assertIn('The creator returned', self.channel.send.call_args.args[0])
        finally:
            await bot.close()


if __name__ == '__main__':
    unittest.main()
