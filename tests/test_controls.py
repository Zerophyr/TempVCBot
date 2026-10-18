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
        self.actor = MagicMock(spec=discord.Member)
        self.actor.id = 10
        self.actor.guild = self.guild
        self.actor.voice = None
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
        self.assertEqual(len(view.children), 6)
        self.assertTrue(all(':100:' in item.custom_id for item in view.children))

    async def test_nonhost_is_denied_before_opening_invite_menu(self):
        self.actor.id = 20
        view = controls.DashboardView(self.bot, 100)
        self.assertFalse(await view.interaction_check(self.interaction))
        self.interaction.response.send_message.assert_awaited_once()

    async def test_host_passes_dashboard_check(self):
        self.assertTrue(await controls.DashboardView(self.bot, 100).interaction_check(self.interaction))

    async def test_rename_modal_rechecks_host_at_submission(self):
        modal = controls.SettingsModal(self.bot, 100, 'rename', 'Practice')
        self.room.host_id = 20
        self.bot.service.rename = AsyncMock()
        await modal.on_submit(self.interaction)
        self.bot.service.rename.assert_not_awaited()
        self.interaction.response.send_message.assert_awaited_once()

    async def test_invite_picker_is_multiple_members_and_rechecks_host(self):
        view = controls.InviteView(self.bot, 100)
        picker = next(item for item in view.children if isinstance(item, discord.ui.UserSelect))
        self.assertGreater(picker.max_values, 1)
        self.actor.id = 20
        self.assertFalse(await view.interaction_check(self.interaction))

    async def test_member_management_rechecks_authorization(self):
        self.actor.id = 20
        self.assertFalse(await controls.PeopleView(self.bot, 100).interaction_check(self.interaction))

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
        self.assertTrue({'setup', 'createvc', 'room', 'invite', 'kick'} <= {command.name for command in self.bot.tree.get_commands()})

    async def test_creation_outside_voice_explains_how_long_user_has_to_join(self):
        channel = MagicMock(spec=discord.VoiceChannel)
        channel.name = 'My room'
        channel.user_limit = 0
        self.bot.service.create = AsyncMock(return_value=self.room)
        self.bot.service.channel = MagicMock(return_value=channel)
        await self.bot.create_for(self.interaction)
        text = self.interaction.response.send_message.call_args.args[0]
        self.assertIn('60 seconds', text)


if __name__ == '__main__':
    unittest.main()
