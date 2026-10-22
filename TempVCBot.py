"""TempVCBot: public rooms with host settings and opted-in availability alerts."""
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
from room_state import RoomStore, atomic_json
from control_messages import ResponseTracker

log = logging.getLogger(__name__)
HELP = (
    '**Create:** use Create a room in Voice Dashboard. Rooms are public with no limit by default.\n'
    '**Join:** use Find a room or Join / Notify me to check availability and open its voice link.\n'
    '**Full room:** choose Notify me for one DM when space opens. You can cancel anytime; '
    'enable server-member DMs. Notifications do not reserve a place or move you automatically.\n'
    '**Settings:** the current host can rename or set a limit: 5–99 people, or 0 for unlimited. '
    'Admins and server moderators can override settings; staff can set 1–99 or unlimited. '
    'There are no private modes, invitations, kicks or blocks. Everyone with normal voice access can chat and use video.\n'
    '**Hosting:** after a 10-second grace, the earliest-joined person still present becomes host. '
    'The creator regains hosting on return.\n'
    '**Controls:** use Manage my room, /room, or the room chat panel. Reopen personal menus anytime.\n'
    '**Who owns this room?** The room card shows the current owner and user ID; the voice status also identifies them. '
    'Ownership changes and bot name/limit changes are announced in room chat with the responsible user ID.\n'
    '**Server staff:** retain normal Discord removal, move, mute, kick and ban powers according to their permissions. '
    'Staff can use Find a room → Manage room without becoming host.\n'
    '**Cleanup:** rooms disappear 10 seconds after everyone leaves. Rooms created outside voice '
    'allow 60 seconds for someone to join. Save anything you want to keep from the chat.'
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
        if room and client.service.can_manage(room, interaction.user):
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


class LauncherView(SafeView):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label='Create a room', emoji='➕', style=discord.ButtonStyle.primary, custom_id='tempvc:create')
    async def create(self, interaction, button):
        await self.bot.create_for(interaction)

    @discord.ui.button(label='Find a room', emoji='🔎', style=discord.ButtonStyle.primary, custom_id='tempvc:browse')
    async def browse(self, interaction, button):
        await self.bot.browse_for(interaction)

    @discord.ui.button(label='Manage my room', emoji='⚙️', custom_id='tempvc:manage')
    async def manage(self, interaction, button):
        await self.bot.manage_for(interaction)

    @discord.ui.button(label='Help', emoji='❔', custom_id='tempvc:help')
    async def help(self, interaction, button):
        await respond(interaction, HELP)

class DashboardView(SafeView):
    def __init__(self, bot, channel_id):
        super().__init__(timeout=None)
        self.bot, self.channel_id = bot, channel_id
        for action, label, emoji in [
            ('availability', 'Join / Notify me', '🔔'), ('rename', 'Rename', '✏️'),
            ('limit', 'User limit', '🔢'), ('help', 'Help', '❔'),
        ]:
            button = discord.ui.Button(label=label, emoji=emoji,
                style=discord.ButtonStyle.primary if action == 'availability' else discord.ButtonStyle.secondary,
                custom_id=f'tempvc:room:{channel_id}:{action}')
            button.callback = self.callback_for(action)
            self.add_item(button)

    async def interaction_check(self, interaction):
        try:
            self.bot.service.accessible(self.channel_id, interaction.user)
            return True
        except RoomError as error:
            await show_error(interaction, error, room_id=self.channel_id)
            return False

    def callback_for(self, action):
        async def callback(interaction):
            if action == 'availability':
                return await self.bot.open_room(interaction, self.channel_id)
            if action == 'help':
                return await respond(interaction, HELP, room_id=self.channel_id)
            room = self.bot.service.authorize(self.channel_id, interaction.user)
            channel = self.bot.service.channel(room)
            value = channel.name if action == 'rename' else str(channel.user_limit)
            await interaction.response.send_modal(SettingsModal(self.bot, self.channel_id, action, value,
                                                                staff=self.bot.service.is_staff(interaction.user)))
        return callback


class UserView(SafeView):
    def __init__(self, bot, actor, *, channel_id=None):
        super().__init__(timeout=300)
        self.bot, self.user_id, self.guild_id = bot, actor.id, actor.guild.id
        self.channel_id = channel_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id or interaction.guild is None or interaction.guild.id != self.guild_id:
            await respond(interaction, 'Open your own room menu in Voice Dashboard.')
            return False
        return True


class AvailabilityView(UserView):
    def __init__(self, bot, actor, room, channel):
        super().__init__(bot, actor, channel_id=room.channel_id)
        inside = actor.voice and actor.voice.channel and actor.voice.channel.id == room.channel_id
        if inside:
            self.add_item(discord.ui.Button(label='You are in this room', disabled=True, row=0))
        elif not bot.service.full(channel):
            self.add_item(discord.ui.Button(label='Join voice room', style=discord.ButtonStyle.link, url=bot.service.link(room), row=0))
        else:
            subscribe = discord.ui.Button(label='Notify me when a place opens', emoji='🔔',
                style=discord.ButtonStyle.primary, disabled=actor.id in room.waiting, row=0)
            subscribe.callback = self.subscribe
            self.add_item(subscribe)
        if actor.id in room.waiting:
            cancel = discord.ui.Button(label='Cancel my notification', row=1)
            cancel.callback = self.cancel
            self.add_item(cancel)
        if bot.service.can_manage(room, actor):
            manage = discord.ui.Button(label='Manage room', emoji='⚙️', row=1)
            manage.callback = self.manage
            self.add_item(manage)
        refresh = discord.ui.Button(label='Check again', emoji='🔄', row=1)
        refresh.callback = self.refresh
        self.add_item(refresh)

    async def subscribe(self, interaction):
        await interaction.response.defer(ephemeral=True)
        subscribed = await self.bot.service.subscribe(self.channel_id, interaction.user)
        notice = ('✅ I will DM you once when space opens. Enable DMs from server members. '
                  'This does not reserve a place or move you automatically.' if subscribed
                  else 'A place is already available. You can join now.')
        await self.bot.open_room(interaction, self.channel_id, notice=notice)

    async def cancel(self, interaction):
        await interaction.response.defer(ephemeral=True)
        await self.bot.service.unsubscribe(self.channel_id, interaction.user)
        await self.bot.open_room(interaction, self.channel_id, notice='Your notification has been cancelled.')

    async def refresh(self, interaction):
        await self.bot.open_room(interaction, self.channel_id)

    async def manage(self, interaction):
        await self.bot.manage_room_for(interaction, self.channel_id)


class RoomBrowserView(UserView):
    def __init__(self, bot, actor, *, page=0):
        super().__init__(bot, actor)
        rooms = []
        for room in list(bot.store.rooms.values()):
            try:
                _, channel = bot.service.accessible(room.channel_id, actor)
            except RoomError:
                continue
            rooms.append((room, channel))
        rooms.sort(key=lambda item: (item[1].name.casefold(), item[0].channel_id))
        self.page = max(0, min(page, max(0, (len(rooms) - 1) // 25)))
        self.total = len(rooms)
        options = []
        for room, channel in rooms[self.page * 25:(self.page + 1) * 25]:
            status = 'Full — notification available' if bot.service.full(channel) else 'Space available'
            occupancy = f'{len(channel.members)}/{channel.user_limit or "unlimited"}'
            options.append(discord.SelectOption(label=channel.name[:100], value=str(room.channel_id),
                                               description=f'{occupancy} · {status}'))
        if options:
            picker = discord.ui.Select(placeholder='Choose a public room…', options=options, row=0)
            async def selected(interaction):
                await bot.open_room(interaction, int(picker.values[0]))
            picker.callback = selected
            self.add_item(picker)
        for label, step, disabled in [
            ('Previous', -1, self.page == 0),
            ('Next', 1, (self.page + 1) * 25 >= len(rooms)),
        ]:
            button = discord.ui.Button(label=label, disabled=disabled, row=1)
            def callback_for(step):
                async def callback(interaction):
                    await self.bot.browse_for(interaction, page=self.page + step)
                return callback
            button.callback = callback_for(step)
            self.add_item(button)
        refresh = discord.ui.Button(label='Refresh rooms', row=1)
        async def refresh_rooms(interaction):
            await self.bot.browse_for(interaction, page=self.page)
        refresh.callback = refresh_rooms
        self.add_item(refresh)


class SettingsModal(discord.ui.Modal):
    def __init__(self, bot, channel_id, action, current, *, staff=False):
        super().__init__(title='Rename room' if action == 'rename' else 'Participant limit')
        self.bot, self.channel_id, self.action = bot, channel_id, action
        limit_label = 'Staff limit: 0 unlimited, or 1–99' if staff else 'Limit: 0 unlimited, or 5–99'
        self.value_input = discord.ui.TextInput(label='Room name' if action == 'rename' else limit_label,
            default=current, min_length=1, max_length=100 if action == 'rename' else 2)
        self.add_item(self.value_input)

    async def on_submit(self, interaction):
        try:
            self.bot.service.authorize(self.channel_id, interaction.user)
            value = self.value_input.value
            if self.action == 'limit' and (not value.isascii() or not value.isdigit()):
                minimum = 1 if self.bot.service.is_staff(interaction.user) else 5
                raise RoomError(f'Enter {minimum}–99 people, or 0 for unlimited.')
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
        self.pending_voice_updates = []
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
                'notification_opt_ins': sum(len(room.waiting) for room in self.store.rooms.values()),
                'persistent_views': len(self.persistent_views), 'initialization_error': self.initialization_error})
            await asyncio.sleep(20)

    async def on_ready(self):
        if self.heartbeat_task is None or self.heartbeat_task.done():
            self.heartbeat_task = asyncio.create_task(self.heartbeat())
        async with self.restore_lock:
            self.initialized = False
            try:
                await self.service.migrate_legacy(self.data_dir / 'channels.json')
                for guild in self.guilds:
                    if str(guild.id) in self.settings:
                        await self.service.restore(guild)
                synced = await self.tree.sync()
                # Replay chronology without network calls or handovers against
                # intermediate state. Events arriving during replay join the
                # next batch; the final drain/readiness transition has no await.
                while self.pending_voice_updates:
                    pending, self.pending_voice_updates = self.pending_voice_updates, []
                    for member, before, after in pending:
                        await self.service.record_voice_update(member, before, after)
                for room in list(self.store.rooms.values()):
                    channel = self.get_channel(room.channel_id)
                    creator_present = channel and any(member.id == room.creator_id for member in channel.members)
                    self.service.schedule(room.channel_id,
                        delay=0 if creator_present and room.host_id != room.creator_id else None)
                self.initialized, self.initialization_error = True, None
                # Channel updates during startup may also have opened capacity.
                for channel_id in list(self.store.rooms):
                    await self.service.notify_available(channel_id)
                log.info('Ready as %s; guilds=%s; commands=%s; persistent_views=%s',
                         self.user, [guild.id for guild in self.guilds], len(synced), len(self.persistent_views))
            except Exception as error:
                self.initialized, self.initialization_error = False, type(error).__name__
                log.exception('Bot initialization failed; existing configuration has been preserved')

    async def on_voice_state_update(self, member, before, after):
        if self.initialized:
            await self.service.voice_update(member, before, after)
        elif before.channel != after.channel:
            self.pending_voice_updates.append((member, before, after))

    async def on_guild_channel_delete(self, channel):
        if channel.id in self.store.rooms:
            await self.service.forget(channel.id)

    async def on_guild_channel_update(self, before, after):
        if self.initialized and isinstance(after, discord.VoiceChannel) and after.id in self.store.rooms:
            await self.service.channel_update(after)

    async def close(self):
        await self.service.close()
        await self.responses.close()
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            await asyncio.gather(self.heartbeat_task, return_exceptions=True)
        await super().close()

    async def create_for(self, interaction):
        if interaction.guild is None:
            raise RoomError('Create your room inside the server.')
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        room = await self.service.create(interaction.user)
        voice = interaction.user.voice
        if self.service.full(self.service.channel(room)) and not (voice and voice.channel and voice.channel.id == room.channel_id):
            return await self.open_room(interaction, room.channel_id)
        join_note = ('Open the room link and join within 60 seconds of creation. '
                     if not interaction.user.voice and room.sequence == 0 else '')
        await respond(interaction, f'Your room is ready: {self.service.link(room)}\n'
            f'**Public room.** {join_note}Open the room’s chat or use /room for controls.',
            embed=self.service.embed(room, self.service.channel(room)), view=DashboardView(self, room.channel_id), room_id=room.channel_id)

    async def browse_for(self, interaction, *, page=0):
        if interaction.guild is None:
            raise RoomError('Find rooms inside the server.')
        view = RoomBrowserView(self, interaction.user, page=page)
        content = (f'Choose a room to join or get notified when it has space. '
                   f'Page {view.page + 1} of {max(1, (view.total + 24) // 25)}.' if view.total
                   else 'No public rooms are open yet. Use Create a room to start one.')
        await respond(interaction, content, view=view)

    async def open_room(self, interaction, channel_id, *, notice=None):
        room, channel = self.service.accessible(channel_id, interaction.user)
        full = self.service.full(channel)
        inside = interaction.user.voice and interaction.user.voice.channel and interaction.user.voice.channel.id == channel_id
        content = ('You are already in this room.' if inside else
                   'This room is full. Choose **Notify me** to receive one DM when space opens. '
                   'Notifications do not reserve a place. Enable DMs from server members.' if full
                   else 'Space is available. Use **Join voice room**. Availability may change before you join; '
                        'use **Check again** if Discord says the room is full.')
        if interaction.user.id in room.waiting:
            content += '\n🔔 Your one-time notification is enabled. You can cancel it below.'
        await respond(interaction, (notice + '\n' if notice else '') + content,
                      room_id=channel_id, embed=self.service.embed(room, channel),
                      view=AvailabilityView(self, interaction.user, room, channel))

    async def manage_for(self, interaction):
        if interaction.guild is None:
            raise RoomError('Manage your room inside the server.')
        room = self.service.managed(interaction.user)
        await self.manage_room_for(interaction, room.channel_id)

    async def manage_room_for(self, interaction, channel_id):
        room = self.service.authorize(channel_id, interaction.user)
        if not interaction.response.is_done():
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
            await respond(interaction, f'✅ Voice Dashboard is ready: <#{settings["controls_channel_id"]}>. New rooms are public with no limit.')

        @self.tree.command(name='createvc', description='Create a public voice room with no limit, or return to your room.')
        @app_commands.guild_only()
        async def createvc(interaction: discord.Interaction):
            await self.create_for(interaction)

        @self.tree.command(name='room', description='Open room settings as the current host or server staff.')
        @app_commands.guild_only()
        async def room(interaction: discord.Interaction):
            await self.manage_for(interaction)

        @self.tree.command(name='rooms', description='Find a public room or opt into a notification when a full room has space.')
        @app_commands.guild_only()
        async def rooms(interaction: discord.Interaction):
            await self.browse_for(interaction)

        @self.tree.command(name='notify', description='DM me once when this full temporary voice room has a free place.')
        @app_commands.guild_only()
        async def notify(interaction: discord.Interaction, channel: discord.VoiceChannel):
            await interaction.response.defer(ephemeral=True)
            subscribed = await self.service.subscribe(channel.id, interaction.user)
            await self.open_room(interaction, channel.id, notice=(
                '✅ One-time DM enabled. Enable DMs from server members. No place is reserved.'
                if subscribed else 'Space is already available — you can join now.'))

        @self.tree.command(name='unnotify', description='Cancel my availability notification for a temporary voice room.')
        @app_commands.guild_only()
        async def unnotify(interaction: discord.Interaction, channel: discord.VoiceChannel):
            await interaction.response.defer(ephemeral=True)
            await self.service.unsubscribe(channel.id, interaction.user)
            await respond(interaction, 'Your notification has been cancelled.', room_id=channel.id)

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
