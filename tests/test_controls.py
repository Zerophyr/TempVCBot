import asyncio
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
from room_state import Room

scratch = tempfile.TemporaryDirectory()
os.environ['DATA_DIR'] = scratch.name
os.environ['ACCESS_ROLE_IDS'] = '2'
try:
    controls = importlib.import_module('TempVCBot')
except ImportError:
    controls = None


class ControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertTrue(controls is not None and hasattr(controls, 'RoomBot'), 'Persistent bot controls are not implemented')
        self.bot = controls.RoomBot(Path(scratch.name))
        self.room = Room(channel_id=100, guild_id=1, creator_id=10, host_id=10)
        self.bot.store.rooms[100] = self.room
        self.guild = MagicMock(spec=discord.Guild)
        self.guild.id = 1
        self.guild.owner_id = 999
        self.actor = MagicMock(spec=discord.Member)
        self.actor.id = 10
        self.actor.guild = self.guild
        self.actor.voice = None
        self.actor.bot = False
        self.actor.guild_permissions = discord.Permissions.none()
        self.channel = MagicMock(spec=discord.VoiceChannel)
        self.channel.id, self.channel.guild = 100, self.guild
        self.channel.name, self.channel.user_limit, self.channel.members = 'Public room', 0, []
        self.channel.permissions_for.return_value = discord.Permissions(view_channel=True, connect=True)
        self.bot.get_channel = MagicMock(side_effect=lambda ident: self.channel if ident == 100 else None)
        self.interaction = MagicMock(spec=discord.Interaction)
        self.interaction.guild = self.guild
        self.interaction.user = self.actor
        self.interaction.response = MagicMock()
        self.interaction.response.is_done.return_value = False
        self.interaction.response.send_message = AsyncMock()
        self.interaction.response.defer = AsyncMock()
        self.interaction.response.send_modal = AsyncMock()
        self.interaction.followup.send = AsyncMock()

    async def asyncTearDown(self):
        if hasattr(self, 'bot'):
            await self.bot.close()

    async def test_launcher_is_persistent_with_stable_unique_ids(self):
        view = controls.LauncherView(self.bot)
        self.assertTrue(view.is_persistent())
        self.assertEqual([item.custom_id for item in view.children], ['tempvc:create', 'tempvc:manage', 'tempvc:help'])

    async def test_room_panel_is_persistent_and_scoped_to_room(self):
        view = controls.DashboardView(self.bot, 100)
        self.assertTrue(view.is_persistent())
        self.assertEqual(len(view.children), 4)
        self.assertTrue(all(':100:' in item.custom_id for item in view.children))

    async def test_nonhost_can_open_public_room_panel(self):
        self.actor.id = 20
        view = controls.DashboardView(self.bot, 100)
        self.assertTrue(await view.interaction_check(self.interaction))
        self.interaction.response.send_message.assert_not_awaited()

    async def test_host_passes_dashboard_check(self):
        self.assertTrue(await controls.DashboardView(self.bot, 100).interaction_check(self.interaction))

    async def test_rename_modal_rechecks_host_at_submission(self):
        modal = controls.SettingsModal(self.bot, 100, 'rename', 'Practice')
        self.room.host_id = 20
        self.bot.service.rename = AsyncMock()
        await modal.on_submit(self.interaction)
        self.bot.service.rename.assert_not_awaited()
        self.interaction.response.send_message.assert_awaited_once()

    async def test_setup_hook_registers_launcher_and_saved_room_panels(self):
        self.bot.add_view = MagicMock()
        await self.bot.setup_hook()
        views = [call.args[0] for call in self.bot.add_view.call_args_list]
        self.assertTrue(any(isinstance(view, controls.LauncherView) for view in views))
        self.assertTrue(any(isinstance(view, controls.DashboardView) for view in views))
        self.assertTrue(all(view.is_persistent() for view in views))

    async def test_success_feedback_is_private(self):
        await controls.respond(self.interaction, 'Done')
        self.assertTrue(self.interaction.response.send_message.call_args.kwargs['ephemeral'])

    async def test_room_command_is_registered_with_existing_commands(self):
        self.assertEqual({'setup', 'createvc', 'room', 'rooms', 'notify', 'unnotify'}, {command.name for command in self.bot.tree.get_commands()})

    async def test_creation_outside_voice_explains_how_long_user_has_to_join(self):
        channel = MagicMock(spec=discord.VoiceChannel)
        channel.name = 'My room'
        channel.user_limit = 0
        self.bot.service.create = AsyncMock(return_value=self.room)
        self.bot.service.channel = MagicMock(return_value=channel)
        await self.bot.create_for(self.interaction)
        text = self.interaction.response.send_message.call_args.args[0]
        self.assertIn('60 seconds', text)

    async def test_private_component_response_is_edited_without_creating_more_messages(self):
        self.interaction.message = SimpleNamespace(id=500, flags=SimpleNamespace(ephemeral=True))
        self.interaction.response.edit_message = AsyncMock()
        await controls.respond(self.interaction, 'Updated', room_id=100)
        self.interaction.response.edit_message.assert_awaited_once()
        self.interaction.response.send_message.assert_not_awaited()

    async def test_public_launcher_is_never_replaced_by_private_controls(self):
        self.interaction.message = SimpleNamespace(id=500, flags=SimpleNamespace(ephemeral=False))
        self.interaction.response.edit_message = AsyncMock()
        await controls.respond(self.interaction, 'Updated', room_id=100)
        self.interaction.response.send_message.assert_awaited_once()
        self.interaction.response.edit_message.assert_not_awaited()

    async def test_real_tracker_removes_private_controls_when_service_forgets_room(self):
        self.interaction.client = self.bot
        self.interaction.message = None
        self.interaction.response.send_message.return_value = SimpleNamespace(resource=SimpleNamespace(id=500))
        self.interaction.delete_original_response = AsyncMock()
        await controls.respond(self.interaction, 'Your room', room_id=100, view=controls.DashboardView(self.bot, 100))
        self.assertIn((100, 10), self.bot.responses.entries)
        await self.bot.service.forget(100)
        self.interaction.delete_original_response.assert_awaited_once()
        self.assertFalse(self.bot.responses.entries)

    async def test_deferred_private_component_edits_and_refreshes_cleanup_handle(self):
        self.interaction.client = self.bot
        self.interaction.message = SimpleNamespace(id=500, flags=SimpleNamespace(ephemeral=True))
        self.interaction.response.is_done.return_value = True
        self.interaction.edit_original_response = AsyncMock()
        self.interaction.delete_original_response = AsyncMock()
        await controls.respond(self.interaction, 'Updated', room_id=100, view=controls.DashboardView(self.bot, 100))
        self.interaction.edit_original_response.assert_awaited_once()
        self.interaction.followup.send.assert_not_awaited()
        await self.bot.responses.cleanup_room(100)
        self.interaction.delete_original_response.assert_awaited_once()

    async def test_removed_privacy_and_moderation_menus_are_unavailable(self):
        for name in ['PrivacyView', 'PeopleView', 'InviteView', 'CreateRoomView']:
            self.assertFalse(hasattr(controls, name), name)
        labels = {item.label for item in controls.DashboardView(self.bot, 100).children}
        self.assertEqual(labels, {'Join / Notify me', 'Rename', 'User limit', 'Help'})

    async def test_nonhost_cannot_open_rename_or_limit_modals(self):
        self.actor.id = 20
        for action in ['rename', 'limit']:
            with self.assertRaises(controls.RoomError):
                await controls.DashboardView(self.bot, 100).callback_for(action)(self.interaction)
        self.interaction.response.send_modal.assert_not_awaited()

    async def test_nonhost_can_use_join_and_notification_flow(self):
        self.actor.id = 20
        self.bot.open_room = AsyncMock()
        await controls.DashboardView(self.bot, 100).callback_for('availability')(self.interaction)
        self.bot.open_room.assert_awaited_once_with(self.interaction, 100)

    async def test_full_room_offers_an_alert_and_cancellation_is_user_bound(self):
        self.channel.user_limit = 5
        self.channel.members = [self.actor] * 5
        view = controls.AvailabilityView(self.bot, self.actor, self.room, self.channel)
        self.assertIn('Notify me when a place opens', {item.label for item in view.children})
        self.assertFalse(any(item.style == discord.ButtonStyle.link for item in view.children))
        self.room.waiting.add(10)
        view = controls.AvailabilityView(self.bot, self.actor, self.room, self.channel)
        self.assertIn('Cancel my notification', {item.label for item in view.children})
        self.actor.id = 20
        self.assertFalse(await view.interaction_check(self.interaction))

    async def test_room_with_space_offers_join_link(self):
        view = controls.AvailabilityView(self.bot, self.actor, self.room, self.channel)
        join = next(item for item in view.children if item.style == discord.ButtonStyle.link)
        self.assertEqual(join.url, 'https://discord.com/channels/1/100')
        self.assertNotIn('Notify me when a place opens', {item.label for item in view.children})

    async def test_browser_pages_more_than_25_rooms_and_filters_inaccessible_rooms(self):
        channels = {}
        for ident in range(100, 131):
            channel = MagicMock(spec=discord.VoiceChannel)
            channel.id, channel.guild = ident, self.guild
            channel.name, channel.user_limit, channel.members = f'Room {ident}', 0, []
            channel.permissions_for.return_value = discord.Permissions(view_channel=ident != 130, connect=True)
            channels[ident] = channel
            self.bot.store.rooms[ident] = Room(ident, 1, 10, 10)
        self.bot.get_channel.side_effect = channels.get
        first = controls.RoomBrowserView(self.bot, self.actor)
        second = controls.RoomBrowserView(self.bot, self.actor, page=1)
        self.assertEqual(first.total, 30)
        self.assertEqual(len(next(item for item in first.children if isinstance(item, discord.ui.Select)).options), 25)
        self.assertEqual(len(next(item for item in second.children if isinstance(item, discord.ui.Select)).options), 5)
        self.assertTrue(next(item for item in second.children if getattr(item, 'label', None) == 'Next').disabled)

    async def test_availability_changed_while_menu_open_does_not_queue_an_unnecessary_alert(self):
        self.bot.service.subscribe = AsyncMock(return_value=False)
        self.bot.open_room = AsyncMock()
        view = controls.AvailabilityView(self.bot, self.actor, self.room, self.channel)
        await view.subscribe(self.interaction)
        self.interaction.response.defer.assert_awaited_once()
        self.assertIn('already available', self.bot.open_room.call_args.kwargs['notice'])

    async def test_channel_update_is_dispatched_only_for_managed_ready_voice_rooms(self):
        self.bot.service.channel_update = AsyncMock()
        await self.bot.on_guild_channel_update(self.channel, self.channel)
        self.bot.service.channel_update.assert_not_awaited()
        self.bot.initialized = True
        await self.bot.on_guild_channel_update(self.channel, self.channel)
        self.bot.service.channel_update.assert_awaited_once_with(self.channel)

    async def test_limit_modal_rejects_four_without_editing_channel(self):
        self.bot.service.set_limit = AsyncMock(side_effect=controls.RoomError('Choose 5–99 participants, or 0 for unlimited.'))
        modal = controls.SettingsModal(self.bot, 100, 'limit', '5')
        modal.value_input._value = '4'
        await modal.on_submit(self.interaction)
        self.bot.service.set_limit.assert_awaited_once_with(100, self.actor, 4)
        self.assertIn('5–99', self.interaction.response.send_message.call_args.args[0])

    async def test_person_already_inside_a_full_room_is_not_offered_a_notification(self):
        self.channel.user_limit = 5
        self.channel.members = [self.actor] * 5
        self.actor.voice = SimpleNamespace(channel=self.channel)
        view = controls.AvailabilityView(self.bot, self.actor, self.room, self.channel)
        self.assertNotIn('Notify me when a place opens', {item.label for item in view.children})
        self.assertIn('You are in this room', {item.label for item in view.children})

    async def test_staff_can_open_room_management_from_room_browser_card(self):
        self.actor.id = 20
        self.actor.guild_permissions = discord.Permissions(move_members=True)
        view = controls.AvailabilityView(self.bot, self.actor, self.room, self.channel)
        button = next(item for item in view.children if item.label == 'Manage room')
        self.bot.manage_room_for = AsyncMock()
        await button.callback(self.interaction)
        self.bot.manage_room_for.assert_awaited_once_with(self.interaction, 100)
        self.assertEqual(self.room.host_id, 10)

    async def test_demoted_staff_cannot_submit_an_old_staff_settings_modal(self):
        self.actor.id = 20
        self.actor.guild_permissions = discord.Permissions(administrator=True)
        modal = controls.SettingsModal(self.bot, 100, 'limit', '5', staff=True)
        modal.value_input._value = '1'
        self.actor.guild_permissions = discord.Permissions.none()
        self.bot.service.set_limit = AsyncMock()
        await modal.on_submit(self.interaction)
        self.bot.service.set_limit.assert_not_awaited()

    async def test_regular_guest_has_no_management_button(self):
        self.actor.id = 20
        view = controls.AvailabilityView(self.bot, self.actor, self.room, self.channel)
        self.assertNotIn('Manage room', {item.label for item in view.children})


if __name__ == '__main__':
    unittest.main()
