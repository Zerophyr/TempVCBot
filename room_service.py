"""Discord boundary for room creation, access, ownership and durable panels."""

import asyncio
import copy
import json
import logging
import time
from collections import defaultdict
from pathlib import Path

import discord

from room_state import MODES, Room, atomic_json, build_overwrites, member_access

log = logging.getLogger(__name__)


class RoomError(Exception):
    """An actionable message that is safe to show to a member."""


class RoomService:
    def __init__(self, bot, store, settings, config_path, *, audience_ids=(), entry_category_id=0, grace=10):
        self.bot = bot
        self.store = store
        self.settings = settings
        self.config_path = Path(config_path)
        self.audience_ids = list(audience_ids)
        self.entry_category_id = entry_category_id
        self.grace = grace
        self.locks = defaultdict(asyncio.Lock)
        self.creation_locks = defaultdict(asyncio.Lock)
        self.tasks = {}
        self.dashboard_factory = lambda channel_id: None
        self.launcher_factory = lambda: None

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()

    def channel(self, room):
        channel = self.bot.get_channel(room.channel_id)
        if channel is None or channel.guild.id != room.guild_id:
            raise RoomError('This room no longer exists. Use Create a room to start again.')
        return channel

    def authorize(self, channel_id, actor):
        room = self.store.rooms.get(channel_id)
        if room is None or actor.guild.id != room.guild_id:
            raise RoomError('This room no longer exists. Open Manage my room again.')
        if actor.id != room.host_id:
            raise RoomError('Only the current host can do that. Open Manage my room to check the host.')
        return room

    def owned(self, member):
        return next((room for room in self.store.rooms.values()
                     if room.guild_id == member.guild.id and room.creator_id == member.id), None)

    def managed(self, member):
        voice = getattr(member, 'voice', None)
        if voice and voice.channel:
            room = self.store.rooms.get(voice.channel.id)
            if room and room.host_id == member.id:
                return room
        room = self.owned(member)
        if room and room.host_id == member.id:
            return room
        raise RoomError('You are not hosting a room. Join your room to regain ownership, or use Create a room.')

    @staticmethod
    def link(room):
        return f'https://discord.com/channels/{room.guild_id}/{room.channel_id}'

    @staticmethod
    def humans(channel):
        return {member.id: member for member in channel.members if not member.bot}

    def embed(self, room, channel):
        label, description = MODES[room.mode]
        embed = discord.Embed(title=channel.name, description=f'**{label}**\n{description}', color=0x5865F2)
        embed.add_field(name='Current host', value=f'<@{room.host_id}>')
        embed.add_field(name='Creator', value=f'<@{room.creator_id}>')
        embed.add_field(name='Participant limit', value=str(channel.user_limit or 'Unlimited'))
        embed.add_field(name='Your room', value=f'[Open voice and chat]({self.link(room)})', inline=False)
        embed.set_footer(text='Host-only controls • Chat enabled for room members • Reopen anytime with /room')
        return embed

    async def ensure_panel(self, room):
        channel = self.channel(room)
        panel = None
        if room.panel_id:
            try:
                panel = await channel.fetch_message(room.panel_id)
            except discord.NotFound:
                pass
        if panel is None:
            panel = await channel.send(embed=self.embed(room, channel), view=self.dashboard_factory(room.channel_id),
                                       allowed_mentions=discord.AllowedMentions.none())
            room.panel_id = panel.id
            self.store.save()
        else:
            await panel.edit(embed=self.embed(room, channel), view=self.dashboard_factory(room.channel_id),
                             allowed_mentions=discord.AllowedMentions.none())
        return panel

    async def refresh_panel(self, room):
        try:
            await self.ensure_panel(room)
        except discord.HTTPException:
            log.exception('Room %s updated, but its panel could not be refreshed', room.channel_id)

    async def _apply(self, candidate, channel):
        await channel.edit(overwrites=build_overwrites(channel.guild, candidate, self.audience_ids),
                           reason='Temporary room privacy and membership update')
        self.store.rooms[candidate.channel_id] = candidate
        self.store.save()

    async def create(self, creator):
        if creator.bot:
            raise RoomError('Bots cannot create temporary rooms.')
        async with self.creation_locks[(creator.guild.id, creator.id)]:
            existing = self.owned(creator)
            if existing:
                channel = self.bot.get_channel(existing.channel_id)
                if channel:
                    if creator.voice and creator.voice.channel and creator.voice.channel.id != channel.id:
                        await creator.move_to(channel)
                    await self.refresh_panel(existing)
                    return existing
                self.store.rooms.pop(existing.channel_id)
                self.store.save()
            settings = self.settings.get(str(creator.guild.id), {})
            category = creator.guild.get_channel(settings.get('category_id', 0))
            if not isinstance(category, discord.CategoryChannel):
                raise RoomError('Room creation is not configured. Ask a server admin to run /setup.')
            room = Room(channel_id=0, guild_id=creator.guild.id, creator_id=creator.id, host_id=creator.id)
            channel = await category.create_voice_channel(
                name=f"{creator.display_name}'s Room"[:100],
                overwrites=build_overwrites(creator.guild, room, self.audience_ids),
                reason='Member created a hidden temporary voice room',
            )
            room.channel_id = channel.id
            self.store.rooms[channel.id] = room
            self.store.save()
            try:
                if creator.voice and creator.voice.channel:
                    await creator.move_to(channel)
            finally:
                self.schedule(room.channel_id, delay=60)
            await self.refresh_panel(room)
            return room

    async def invite(self, channel_id, actor, members):
        async with self.locks[channel_id]:
            room = self.authorize(channel_id, actor)
            channel = self.channel(room)
            candidate = copy.deepcopy(room)
            for member in members:
                if getattr(getattr(member, 'guild', None), 'id', None) != room.guild_id:
                    raise RoomError('Choose members of this server.')
                if member.bot:
                    raise RoomError('Choose people, rather than bot accounts.')
                candidate.invited.add(member.id)
                candidate.blocked.discard(member.id)
            await self._apply(candidate, channel)
            await self.refresh_panel(candidate)
            return candidate

    async def set_mode(self, channel_id, actor, mode):
        if mode not in MODES:
            raise RoomError('Choose Hidden, Visible invite-only, or Public.')
        async with self.locks[channel_id]:
            room = self.authorize(channel_id, actor)
            channel = self.channel(room)
            candidate = copy.deepcopy(room)
            if mode != 'public':
                candidate.invited.update(self.humans(channel).keys() - candidate.blocked)
            candidate.mode = mode
            await self._apply(candidate, channel)
            await self.refresh_panel(candidate)
            return candidate

    async def manage_member(self, channel_id, actor, member, action):
        async with self.locks[channel_id]:
            room = self.authorize(channel_id, actor)
            channel = self.channel(room)
            if getattr(getattr(member, 'guild', None), 'id', None) != room.guild_id:
                raise RoomError('Choose a member of this server.')
            if member.id == room.creator_id:
                raise RoomError('The room creator retains access so they can return.')
            if member.id in {room.host_id, channel.guild.me.id}:
                raise RoomError('You cannot remove yourself or the room bot.')
            if member.id == channel.guild.owner_id or member.guild_permissions.administrator:
                raise RoomError('Server administrators retain access and cannot be blocked through this panel.')
            candidate = copy.deepcopy(room)
            if action == 'disconnect':
                if member.id not in {value.id for value in channel.members}:
                    raise RoomError('That person is not in your voice room.')
                await member.move_to(None, reason='Disconnected from temporary room by its host')
                return room
            if action == 'block':
                candidate.invited.discard(member.id)
                candidate.blocked.add(member.id)
            elif action == 'revoke':
                candidate.invited.discard(member.id)
            elif action == 'unblock':
                candidate.blocked.discard(member.id)
            else:
                raise RoomError('Unknown member action.')
            await self._apply(candidate, channel)
            if action == 'block' and member.id in {value.id for value in channel.members}:
                await member.move_to(None, reason='Blocked from temporary room by its host')
            await self.refresh_panel(candidate)
            return candidate

    async def rename(self, channel_id, actor, name):
        name = name.strip()
        if not name or len(name) > 100 or any(ord(character) < 32 or ord(character) == 127 for character in name):
            raise RoomError('Choose a room name from 1 to 100 characters on a single line.')
        async with self.locks[channel_id]:
            room = self.authorize(channel_id, actor)
            await self.channel(room).edit(name=name, reason='Renamed by temporary room host')
            await self.refresh_panel(room)

    async def set_limit(self, channel_id, actor, limit):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= 99:
            raise RoomError('Choose a participant limit from 0 to 99. Use 0 for unlimited.')
        async with self.locks[channel_id]:
            room = self.authorize(channel_id, actor)
            await self.channel(room).edit(user_limit=limit, reason='Participant limit set by temporary room host')
            await self.refresh_panel(room)

    def schedule(self, channel_id, delay=None):
        previous = self.tasks.pop(channel_id, None)
        if previous:
            previous.cancel()

        async def later():
            try:
                await asyncio.sleep(self.grace if delay is None else delay)
                await self.reconcile(channel_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception('Unable to reconcile room %s; retrying', channel_id)
                self.schedule(channel_id, delay=30)
            finally:
                if self.tasks.get(channel_id) is asyncio.current_task():
                    self.tasks.pop(channel_id, None)

        self.tasks[channel_id] = asyncio.create_task(later())

    async def reconcile(self, channel_id):
        async with self.locks[channel_id]:
            room = self.store.rooms.get(channel_id)
            if room is None:
                return
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                self.store.rooms.pop(channel_id)
                self.store.save()
                return
            present = self.humans(channel)
            for ident in set(room.join_order) - present.keys():
                room.leave(ident)
            for ident in present:
                room.join(ident)
            if not channel.members:
                remaining = 60 - (time.time() - room.created_at) if room.sequence == 0 else 0
                if remaining > 0:
                    self.store.save()
                    self.schedule(channel_id, delay=remaining)
                    return
                await channel.delete(reason='Temporary voice room is empty')
                self.store.rooms.pop(channel_id)
                self.store.save()
                return
            successor = room.choose_host(present.keys())
            if successor is not None and successor != room.host_id:
                candidate = copy.deepcopy(room)
                candidate.host_id = successor
                await self._apply(candidate, channel)
                await self.refresh_panel(candidate)
                try:
                    await channel.send(
                        f'Host updated: <@{successor}>. The creator regains ownership when they return. '
                        'Room settings and invitations stay the same.',
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    log.warning('Host changed for room %s; announcement unavailable', channel_id)
            else:
                self.store.save()

    async def voice_update(self, member, before, after):
        old_id = before.channel.id if before.channel else None
        new_id = after.channel.id if after.channel else None
        if old_id == new_id:
            return
        if old_id in self.store.rooms:
            async with self.locks[old_id]:
                room = self.store.rooms.get(old_id)
                if room:
                    room.leave(member.id)
                    self.store.save()
            self.schedule(old_id)
        if new_id in self.store.rooms:
            async with self.locks[new_id]:
                room = self.store.rooms.get(new_id)
                if room and not member.bot:
                    room.join(member.id)
                    self.store.save()
            room = self.store.rooms.get(new_id)
            if room and member.id == room.creator_id:
                await self.reconcile(new_id)
            else:
                self.schedule(new_id)
        settings = self.settings.get(str(member.guild.id), {})
        if new_id and new_id == settings.get('creator_channel_id') and not member.bot:
            try:
                await self.create(member)
            except (RoomError, discord.HTTPException):
                log.exception('Could not create or return member to temporary room')
                try:
                    await after.channel.send(
                        f'<@{member.id}>, your room could not be opened. Use /room or Create a room in '
                        'the voice-controls channel; ask an admin if it happens again.',
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    pass

    async def migrate_legacy(self, path):
        path = Path(path)
        if self.store.path.exists() or not path.exists():
            return
        old = json.loads(path.read_text(encoding='utf-8'))
        for channel_id, creator_id in old.items():
            channel = self.bot.get_channel(int(channel_id))
            if channel is None:
                continue
            overwrite = channel.overwrites.get(channel.guild.default_role, discord.PermissionOverwrite())
            room = Room(channel_id=channel.id, guild_id=channel.guild.id,
                        creator_id=int(creator_id), host_id=int(creator_id),
                        mode='public' if overwrite.connect else 'visible', created_at=0)
            for target, permissions in channel.overwrites.items():
                if isinstance(target, discord.Member) and target.id not in {room.creator_id, channel.guild.me.id}:
                    if permissions.connect is True:
                        room.invited.add(target.id)
                    elif permissions.connect is False:
                        room.blocked.add(target.id)
            for member in channel.members:
                if not member.bot:
                    room.join(member.id)
            self.store.rooms[channel.id] = room
        self.store.save()

    async def setup(self, guild):
        """Idempotently configure only the bot's category, entry and launcher."""
        settings = self.settings.setdefault(str(guild.id), {})
        category = guild.get_channel(settings.get('category_id', 0))
        if not isinstance(category, discord.CategoryChannel):
            category = discord.utils.get(guild.categories, name='Temporary Channels')
        if category is None:
            category = await guild.create_category('Temporary Channels', reason='TempVCBot setup')
        template = Room(channel_id=category.id, guild_id=guild.id, creator_id=guild.me.id, host_id=guild.me.id)
        await category.edit(overwrites=build_overwrites(guild, template, self.audience_ids), reason='Hidden room defaults')
        entry_category = guild.get_channel(self.entry_category_id) if self.entry_category_id else category
        if entry_category is None:
            raise RoomError('Configured Voice Channels category is missing. Ask an admin to check ENTRY_CATEGORY_ID.')
        entry_policy = copy.deepcopy(template)
        entry_policy.mode = 'public'
        entry = guild.get_channel(settings.get('creator_channel_id', 0))
        if entry is None:
            entry = await entry_category.create_voice_channel('➕ Create VC',
                        overwrites=build_overwrites(guild, entry_policy, self.audience_ids))
        else:
            await entry.edit(category=entry_category, overwrites=build_overwrites(guild, entry_policy, self.audience_ids))
        controls = guild.get_channel(settings.get('controls_channel_id', 0))
        control_overwrites = build_overwrites(guild, entry_policy, self.audience_ids)
        for target, overwrite in control_overwrites.items():
            overwrite.send_messages = target == guild.me
            overwrite.add_reactions = target == guild.me
            overwrite.use_application_commands = True
        if controls is None:
            controls = await category.create_text_channel('voice-controls', overwrites=control_overwrites,
                        topic='Create and manage your temporary voice room. Rooms start hidden; only hosts can invite.')
        else:
            await controls.edit(overwrites=control_overwrites)
        settings.update(category_id=category.id, creator_channel_id=entry.id, controls_channel_id=controls.id)
        atomic_json(self.config_path, self.settings)
        await self.ensure_launcher(guild)
        return settings

    async def ensure_launcher(self, guild):
        settings = self.settings[str(guild.id)]
        channel = guild.get_channel(settings.get('controls_channel_id', 0))
        if channel is None:
            return await self.setup(guild)
        message = None
        if settings.get('launcher_message_id'):
            try:
                message = await channel.fetch_message(settings['launcher_message_id'])
            except discord.NotFound:
                pass
        embed = discord.Embed(title='🎙️ Your voice room', color=0x5865F2,
            description='Rooms start **hidden**. Create one or manage the room you host.\n\n'
            '**Create a room:** make your own private voice space. If you are already in voice, you will be moved.\n'
            '**Manage my room:** invite friends, change privacy, rename, set a limit or manage people.\n\n'
            'You can also join **➕ Create VC**. Only the current host can invite or change settings. '
            'Everyone with room access can write in its chat.\n\n'
            'The earliest-joined member becomes host when the host leaves; the creator regains ownership on return. '
            'Empty rooms are automatically deleted. Server administrators retain access.')
        embed.set_footer(text='Use these buttons anytime • /room also opens your controls')
        if message is None:
            message = await channel.send(embed=embed, view=self.launcher_factory(), allowed_mentions=discord.AllowedMentions.none())
            settings['launcher_message_id'] = message.id
            atomic_json(self.config_path, self.settings)
        else:
            await message.edit(embed=embed, view=self.launcher_factory(), allowed_mentions=discord.AllowedMentions.none())
        if not message.pinned:
            try:
                await message.pin(reason='Permanent temporary-room controls launcher')
            except discord.Forbidden:
                log.warning('Cannot pin launcher; its fixed bot-only channel remains available')

    async def restore(self, guild):
        for room in list(self.store.rooms.values()):
            if room.guild_id != guild.id:
                continue
            channel = self.bot.get_channel(room.channel_id)
            if channel is None:
                self.store.rooms.pop(room.channel_id)
                self.store.save()
                continue
            if hasattr(channel, 'guild') and channel.guild.id != guild.id:
                raise RoomError('Persisted room belongs to a different server; check room state.')
            await self.reconcile(room.channel_id)
            room = self.store.rooms.get(room.channel_id)
            if room:
                await channel.edit(overwrites=build_overwrites(guild, room, self.audience_ids), reason='Restore room access after restart')
                await self.refresh_panel(room)
        await self.ensure_launcher(guild)
