"""TempVCBot: hidden rooms with persistent, host-only Discord controls."""
import asyncio
import json
import logging
import os
import signal
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from room_service import RoomError, RoomService
from room_state import MODES, RoomStore, atomic_json
from control_messages import ResponseTracker

log = logging.getLogger(__name__)
HELP = (
    '**Create:** use Create a room or Create with friends in Voice Dashboard. New rooms are hidden.\n'
    '**Invite:** the host selects friends with Invite friends; they receive voice and chat access.\n'
    '**Privacy:** Hidden, Visible invite-only, or Public. Existing guests keep access when you lock the room.\n'
    '**People:** disconnect, block from the room, revoke an invitation or unblock. '
    'Revoking an invite alone does not stop public-room access; use Block for that.\n'
    '**Hosting:** after a 10-second grace, the earliest-joined person still present becomes host. '
    'The creator regains ownership on return and keeps access to their room.\n'
    '**Controls:** use Manage my room, /room, or the panel in the room chat. '
    'Only the current host can invite or change settings. Personal menus can always be reopened.\n'
    '**Cleanup:** rooms disappear 10 seconds after everyone leaves. Rooms created outside voice '
    'allow 60 seconds for someone to join. Server administrators retain access.'
)

async def respond(interaction, content=None, *, room_id=None, **kwargs):
    """Reuse personal workspaces; never replace a shared launcher/room panel."""
    kwargs.setdefault('ephemeral', True)
    kwargs.setdefault('allowed_mentions', discord.AllowedMentions.none())
    room_id = room_id or getattr(kwargs.get('view'), 'channel_id', None)
    client = interaction.client
    tracker = getattr(client, 'responses', None)
    tracking = isinstance(tracker, ResponseTracker)
    if tracking and room_id and 'view' not in kwargs:
        room = client.store.rooms.get(room_id)
        if room and room.host_id == interaction.user.id:
            kwargs['view'] = DashboardView(client, room_id)
            kwargs.setdefault('embed', client.service.embed(room, client.service.channel(room)))
    source = getattr(interaction, 'message', None)
    if source and source.flags.ephemeral is True:
        edit = {key: value for key, value in kwargs.items() if key != 'ephemeral'}
        edit.setdefault('embed', None)
        edit.setdefault('view', None)
        if interaction.response.is_done():
            result = await interaction.edit_original_response(content=content, **edit)
        else:
            result = await interaction.response.edit_message(content=content, **edit)
        message_id = source.id
        delete = interaction.delete_original_response
    elif interaction.response.is_done():
        result = await interaction.followup.send(content, wait=True, **kwargs)
        message_id = result.id
        delete = result.delete
    else:
        result = await interaction.response.send_message(content, **kwargs)
        resource = getattr(result, 'resource', None)
        if resource is None and tracking:
            resource = await interaction.original_response()
        message_id = getattr(resource, 'id', None)
        delete = interaction.delete_original_response
    if tracking and isinstance(message_id, int):
        await tracker.add(room_id, interaction.user.id, message_id, delete)
        if room_id and room_id not in client.store.rooms:
            await tracker.cleanup_room(room_id)
    return result

async def show_error(interaction, error, *, room_id=None):
    if isinstance(error, RoomError):
        message = str(error)
    elif isinstance(error, discord.Forbidden):
        message = 'Discord denied this action. Ask an admin to check the bot’s channel and role permissions, then reopen /room.'
    elif isinstance(error, discord.NotFound):
        message = 'This room or message no longer exists. Open Manage my room again.'
    elif isinstance(error, discord.HTTPException):
        message = 'Discord could not apply that change. Check the current room settings before trying again.'
    else:
        log.error('Unexpected room action error', exc_info=(type(error), error, error.__traceback__))
        message = 'Something went wrong. Reopen Manage my room; ask an admin if it happens again.'
    await respond(interaction, message, room_id=room_id)

class SafeView(discord.ui.View):
    async def on_error(self, interaction, error, item):
        await show_error(interaction, error, room_id=getattr(self, 'channel_id', None))

class HostView(SafeView):
    def __init__(self, bot, channel_id, *, timeout=300):
        super().__init__(timeout=timeout)
        self.bot, self.channel_id = bot, channel_id

    async def interaction_check(self, interaction):
        try:
            if interaction.guild is None:
                raise RoomError('Open these controls inside the server.')
            self.bot.service.authorize(self.channel_id, interaction.user)
            return True
        except RoomError as error:
            await show_error(interaction, error, room_id=self.channel_id)
            return False

class LauncherView(SafeView):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label='Create a room', emoji='➕', style=discord.ButtonStyle.primary, custom_id='tempvc:create')
    async def create(self, interaction, button):
        await self.bot.create_for(interaction)

    @discord.ui.button(label='Create with friends', emoji='👥', style=discord.ButtonStyle.primary, custom_id='tempvc:create_friends')
    async def create_friends(self, interaction, button):
        if interaction.guild is None:
            raise RoomError('Create your room inside the server.')
        if self.bot.service.owned(interaction.user):
            return await self.bot.create_for(interaction)
        await respond(interaction, 'Choose who can see, join and chat in your hidden room, then press **Create room**.',
                      view=CreateRoomView(self.bot, interaction.user))

    @discord.ui.button(label='Manage my room', emoji='⚙️', custom_id='tempvc:manage')
    async def manage(self, interaction, button):
        await self.bot.manage_for(interaction)

    @discord.ui.button(label='Help', emoji='❔', custom_id='tempvc:help')
    async def help(self, interaction, button):
        await respond(interaction, HELP)

class DashboardView(HostView):
    def __init__(self, bot, channel_id):
        super().__init__(bot, channel_id, timeout=None)
        for action, label, emoji, row in [
            ('invite', 'Invite friends', '👥', 0), ('privacy', 'Privacy', '🔒', 0),
            ('people', 'Manage people', '🛡️', 0), ('rename', 'Rename', '✏️', 1),
            ('limit', 'User limit', '🔢', 1), ('help', 'Help', '❔', 1),
        ]:
            button = discord.ui.Button(label=label, emoji=emoji, row=row,
                style=discord.ButtonStyle.primary if action == 'invite' else discord.ButtonStyle.secondary,
                custom_id=f'tempvc:room:{channel_id}:{action}')
            button.callback = self.callback_for(action)
            self.add_item(button)

    def callback_for(self, action):
        async def callback(interaction):
            room = self.bot.service.authorize(self.channel_id, interaction.user)
            if action == 'invite':
                await respond(interaction, 'Select server members, then press **Invite selected**.', view=InviteView(self.bot, room.channel_id))
            elif action == 'privacy':
                await respond(interaction, 'Choose who can see and join. Server administrators retain access.', view=PrivacyView(self.bot, room.channel_id))
            elif action == 'people':
                invited = ', '.join(f'<@{ident}>' for ident in sorted(room.invited - {room.creator_id, room.host_id})) or 'None'
                blocked = ', '.join(f'<@{ident}>' for ident in sorted(room.blocked)) or 'None'
                await respond(interaction, f'**Invited:** {invited}\n**Blocked:** {blocked}\n\nSelect a person and an action. '
                              'Revoking an invite does not stop public-room access; blocking prevents rejoining.', view=PeopleView(self.bot, room.channel_id))
            elif action in {'rename', 'limit'}:
                channel = self.bot.service.channel(room)
                value = channel.name if action == 'rename' else str(channel.user_limit)
                await interaction.response.send_modal(SettingsModal(self.bot, room.channel_id, action, value))
            else:
                await respond(interaction, HELP, room_id=room.channel_id)
        return callback

class CreateRoomView(SafeView):
    def __init__(self, bot, creator):
        super().__init__(timeout=300)
        self.bot, self.creator_id, self.guild_id = bot, creator.id, creator.guild.id
        self.selected_ids = []
        picker = discord.ui.UserSelect(placeholder='Choose friends who can see and join…', min_values=0, max_values=10, row=0)

        async def selected(interaction):
            self.selected_ids = [member.id for member in picker.values]
            await interaction.response.defer()

        picker.callback = selected
        self.add_item(picker)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.creator_id or interaction.guild is None or interaction.guild.id != self.guild_id:
            await respond(interaction, 'Only the person creating this room can choose its initial guests.')
            return False
        return True

    @discord.ui.button(label='Create room', emoji='➕', style=discord.ButtonStyle.primary, row=1)
    async def create(self, interaction, button):
        guests = []
        await interaction.response.defer(ephemeral=True)
        for ident in self.selected_ids:
            guests.append(interaction.guild.get_member(ident) or await interaction.guild.fetch_member(ident))
        await self.bot.create_for(interaction, guests=guests)

    @discord.ui.button(label='Cancel', row=1)
    async def cancel(self, interaction, button):
        await interaction.response.defer()
        await interaction.delete_original_response()
        await self.bot.responses.cleanup_user(None, interaction.user.id)

class InviteView(HostView):
    def __init__(self, bot, channel_id):
        super().__init__(bot, channel_id)
        self.selected_ids = []
        self.picker = discord.ui.UserSelect(placeholder='Search server members…', min_values=1, max_values=10, row=0)
        self.picker.callback = self.select_members
        self.add_item(self.picker)

    async def select_members(self, interaction):
        self.selected_ids = [value.id for value in self.picker.values]
        await interaction.response.defer()

    @discord.ui.button(label='Invite selected', emoji='👥', style=discord.ButtonStyle.primary, row=1)
    async def invite_selected(self, interaction, button):
        await self.submit(interaction, notify=False)

    @discord.ui.button(label='Invite + notify by DM', emoji='✉️', row=1)
    async def invite_notify(self, interaction, button):
        await self.submit(interaction, notify=True)

    async def submit(self, interaction, *, notify):
        if not self.selected_ids:
            raise RoomError('Select at least one server member first.')
        self.bot.service.authorize(self.channel_id, interaction.user)
        await interaction.response.defer(ephemeral=True)
        members = []
        for ident in self.selected_ids:
            members.append(interaction.guild.get_member(ident) or await interaction.guild.fetch_member(ident))
        room = await self.bot.service.invite(self.channel_id, interaction.user, members)
        names = ', '.join(discord.utils.escape_markdown(member.display_name) for member in members)
        failed = 0
        if notify:
            for member in members:
                try:
                    await member.send(f'{discord.utils.escape_markdown(interaction.user.display_name)} invited you to a voice room '
                        f'in **{discord.utils.escape_markdown(interaction.guild.name)}**. '
                        f'You can see, join and chat here: {self.bot.service.link(room)}', allowed_mentions=discord.AllowedMentions.none())
                except discord.HTTPException:
                    failed += 1
        suffix = f'\n{failed} DM notification(s) could not be delivered; their room access is already granted.' if failed else ''
        await respond(interaction, f'✅ Access granted to **{names}**.\nShare this room link: {self.bot.service.link(room)}{suffix}', room_id=room.channel_id)

class PrivacyView(HostView):
    def __init__(self, bot, channel_id):
        super().__init__(bot, channel_id)
        picker = discord.ui.Select(placeholder='Choose room privacy', options=[
            discord.SelectOption(label='Hidden', value='hidden', emoji='👻', description='Only you and invited members can see and join'),
            discord.SelectOption(label='Visible · invite-only', value='visible', emoji='🔒', description='Visible to voice members; invitations required to join'),
            discord.SelectOption(label='Public', value='public', emoji='🌐', description='Members with normal voice access can join'),
        ])
        async def selected(interaction):
            await interaction.response.defer(ephemeral=True)
            room = await self.bot.service.set_mode(self.channel_id, interaction.user, picker.values[0])
            await respond(interaction, f'✅ Room privacy: **{MODES[room.mode][0]}**. Existing participants and invitations keep access.', room_id=room.channel_id)
        picker.callback = selected
        self.add_item(picker)

class PeopleView(HostView):
    def __init__(self, bot, channel_id):
        super().__init__(bot, channel_id)
        self.member_id, self.action = None, None
        users = discord.ui.UserSelect(placeholder='Choose a server member', row=0)
        actions = discord.ui.Select(placeholder='Choose what to do', row=1, options=[
            discord.SelectOption(label='Disconnect', value='disconnect', description='Remove from voice; they may rejoin'),
            discord.SelectOption(label='Block from this room', value='block', description='Remove access and disconnect; prevents rejoining'),
            discord.SelectOption(label='Revoke invitation', value='revoke', description='Remove invitation; public-room access still applies'),
            discord.SelectOption(label='Unblock', value='unblock', description='Remove block; invite again for hidden-room access'),
        ])
        async def selected_member(interaction):
            self.member_id = users.values[0].id
            await interaction.response.defer()
        async def selected_action(interaction):
            self.action = actions.values[0]
            await interaction.response.defer()
        users.callback, actions.callback = selected_member, selected_action
        self.add_item(users)
        self.add_item(actions)

    @discord.ui.button(label='Apply action', style=discord.ButtonStyle.primary, row=2)
    async def apply(self, interaction, button):
        if self.member_id is None or self.action is None:
            raise RoomError('Choose a member and an action first.')
        self.bot.service.authorize(self.channel_id, interaction.user)
        await interaction.response.defer(ephemeral=True)
        member = interaction.guild.get_member(self.member_id) or await interaction.guild.fetch_member(self.member_id)
        await self.bot.service.manage_member(self.channel_id, interaction.user, member, self.action)
        labels = {'disconnect': 'Disconnected from voice', 'block': 'Blocked from this room',
                  'revoke': 'Invitation revoked', 'unblock': 'Unblocked; invite them again if the room is hidden'}
        await respond(interaction, f'✅ {labels[self.action]}: {discord.utils.escape_markdown(member.display_name)}.', room_id=self.channel_id)

class SettingsModal(discord.ui.Modal):
    def __init__(self, bot, channel_id, action, current):
        super().__init__(title='Rename room' if action == 'rename' else 'Participant limit')
        self.bot, self.channel_id, self.action = bot, channel_id, action
        self.value_input = discord.ui.TextInput(label='Room name' if action == 'rename' else 'Limit (0 means unlimited)',
            default=current, min_length=1, max_length=100 if action == 'rename' else 2)
        self.add_item(self.value_input)

    async def on_submit(self, interaction):
        try:
            self.bot.service.authorize(self.channel_id, interaction.user)
            value = self.value_input.value
            if self.action == 'limit' and (not value.isascii() or not value.isdigit()):
                raise RoomError('Enter a whole number from 0 to 99. Use 0 for unlimited.')
            await interaction.response.defer(ephemeral=True)
            if self.action == 'rename':
                await self.bot.service.rename(self.channel_id, interaction.user, value)
                await respond(interaction, f'✅ Room renamed to **{discord.utils.escape_markdown(value.strip())}**.', room_id=self.channel_id)
            else:
                await self.bot.service.set_limit(self.channel_id, interaction.user, int(value))
                await respond(interaction, f'✅ Participant limit: **{value if int(value) else "Unlimited"}**.', room_id=self.channel_id)
        except Exception as error:
            await show_error(interaction, error, room_id=self.channel_id)

    async def on_error(self, interaction, error):
        await show_error(interaction, error, room_id=self.channel_id)

class RoomBot(commands.Bot):
    def __init__(self, data_dir=None):
        intents = discord.Intents.default()
        intents.members, intents.voice_states = True, True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents, help_command=None, allowed_mentions=discord.AllowedMentions.none())
        self.data_dir = Path(data_dir or os.environ.get('DATA_DIR', '/data'))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.data_dir / 'config.json'
        self.settings = json.loads(self.config_path.read_text(encoding='utf-8')) if self.config_path.exists() else {}
        self.store = RoomStore(self.data_dir / 'rooms.json')
        self.responses = ResponseTracker()
        audience = [int(value) for value in os.environ.get('ACCESS_ROLE_IDS', '').split(',') if value]
        self.service = RoomService(self, self.store, self.settings, self.config_path, audience_ids=audience,
                                   entry_category_id=int(os.environ.get('ENTRY_CATEGORY_ID', '0')))
        self.service.dashboard_factory = lambda channel_id: DashboardView(self, channel_id)
        self.service.launcher_factory = lambda: LauncherView(self)
        self.service.cleanup_controls = self.responses.cleanup_room
        self.heartbeat_task = None
        self.restore_lock = asyncio.Lock()
        self.initialized, self.initialization_error = False, None
        self.register_commands()

    async def setup_hook(self):
        self.add_view(LauncherView(self))
        for channel_id in self.store.rooms:
            self.add_view(DashboardView(self, channel_id))

    async def heartbeat(self):
        while not self.is_closed():
            atomic_json(self.data_dir / 'health.json', {'ready': self.is_ready() and self.initialized,
                'updated': time.time(), 'guilds': [guild.id for guild in self.guilds], 'rooms': len(self.store.rooms),
                'private_responses': len(self.responses.entries),
                'persistent_views': len(self.persistent_views), 'initialization_error': self.initialization_error})
            await asyncio.sleep(20)

    async def on_ready(self):
        if self.heartbeat_task is None or self.heartbeat_task.done():
            self.heartbeat_task = asyncio.create_task(self.heartbeat())
        async with self.restore_lock:
            try:
                await self.service.migrate_legacy(self.data_dir / 'channels.json')
                for guild in self.guilds:
                    if str(guild.id) in self.settings:
                        await self.service.restore(guild)
                synced = await self.tree.sync()
                self.initialized, self.initialization_error = True, None
                log.info('Ready as %s; guilds=%s; commands=%s; persistent_views=%s',
                         self.user, [guild.id for guild in self.guilds], len(synced), len(self.persistent_views))
            except Exception as error:
                self.initialized, self.initialization_error = False, type(error).__name__
                log.exception('Bot initialization failed; existing configuration has been preserved')

    async def on_voice_state_update(self, member, before, after):
        if self.initialized:
            await self.service.voice_update(member, before, after)

    async def on_guild_channel_delete(self, channel):
        if channel.id in self.store.rooms:
            await self.service.forget(channel.id)

    async def close(self):
        await self.service.close()
        await self.responses.close()
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            await asyncio.gather(self.heartbeat_task, return_exceptions=True)
        await super().close()

    async def create_for(self, interaction, *, guests=()):
        if interaction.guild is None:
            raise RoomError('Create your room inside the server.')
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        room = await self.service.create(interaction.user, guests=guests)
        join_note = ('Open the room link and join within 60 seconds of creation. '
                     if not interaction.user.voice and room.sequence == 0 else '')
        await respond(interaction, f'Your room is ready: {self.service.link(room)}\n'
            f'Privacy: **{MODES[room.mode][0]}**. {join_note}Open the room’s chat or use /room for controls.',
            embed=self.service.embed(room, self.service.channel(room)), view=DashboardView(self, room.channel_id), room_id=room.channel_id)

    async def manage_for(self, interaction):
        if interaction.guild is None:
            raise RoomError('Manage your room inside the server.')
        room = self.service.managed(interaction.user)
        await interaction.response.defer(ephemeral=True)
        await self.service.refresh_panel(room)
        await respond(interaction, embed=self.service.embed(room, self.service.channel(room)), view=DashboardView(self, room.channel_id))

    def register_commands(self):
        @self.tree.command(name='setup', description='Admin: configure voice room creation and permanent controls.')
        @app_commands.guild_only()
        @app_commands.default_permissions(manage_channels=True)
        @app_commands.checks.has_permissions(manage_channels=True)
        async def setup(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            settings = await self.service.setup(interaction.guild)
            await respond(interaction, f'✅ Voice Dashboard is ready: <#{settings["controls_channel_id"]}>. New rooms start hidden.')

        @self.tree.command(name='createvc', description='Create your hidden voice room, or return to your existing room.')
        @app_commands.guild_only()
        async def createvc(interaction: discord.Interaction):
            await self.create_for(interaction)

        @self.tree.command(name='room', description='Open controls for the voice room you currently host.')
        @app_commands.guild_only()
        async def room(interaction: discord.Interaction):
            await self.manage_for(interaction)

        @self.tree.command(name='invite', description='Host: give a server member access to your room and its chat.')
        @app_commands.guild_only()
        @app_commands.describe(user_to_invite='The server member to invite')
        async def invite(interaction: discord.Interaction, user_to_invite: discord.Member):
            room = self.service.managed(interaction.user)
            await interaction.response.defer(ephemeral=True)
            await self.service.invite(room.channel_id, interaction.user, [user_to_invite])
            await respond(interaction, f'✅ Access granted to {user_to_invite.mention}. Share this room link: {self.service.link(room)}', room_id=room.channel_id)

        @self.tree.command(name='kick', description='Host: disconnect someone from your room; they may rejoin.')
        @app_commands.guild_only()
        @app_commands.describe(user_to_kick='The person to disconnect (use Manage people to block rejoining)')
        async def kick(interaction: discord.Interaction, user_to_kick: discord.Member):
            room = self.service.managed(interaction.user)
            await interaction.response.defer(ephemeral=True)
            await self.service.manage_member(room.channel_id, interaction.user, user_to_kick, 'disconnect')
            await respond(interaction, '✅ Disconnected from voice. To prevent rejoining, use **Manage people → Block**.', room_id=room.channel_id)

        @self.tree.error
        async def command_error(interaction, error):
            if isinstance(error, app_commands.MissingPermissions):
                await respond(interaction, 'Only a server admin with Manage Channels can run setup.')
            else:
                await show_error(interaction, getattr(error, 'original', error))

async def main():
    bot = RoomBot()
    # Docker sends SIGTERM on replacement. Delete private replies while their
    # in-memory interaction tokens and Discord HTTP session still exist.
    try:
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(bot.close()))
    except NotImplementedError:  # Windows event loops use normal Ctrl+C shutdown.
        pass
    token = Path(os.environ.get('DISCORD_TOKEN_FILE', '/run/secrets/discord_token')).read_text(encoding='utf-8').strip()
    async with bot:
        await bot.start(token)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    asyncio.run(main())
