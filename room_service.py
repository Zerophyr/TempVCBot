"""Discord boundary for room creation, access, ownership and durable panels."""

import asyncio
import copy
import json
import logging
import time
from collections import defaultdict
from pathlib import Path

import discord

from room_state import Room, atomic_json, build_overwrites, member_access, migration_overwrites, staff_permissions

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
        self.notification_locks = defaultdict(asyncio.Lock)
        self.owner_statuses = {}
        self.tasks = {}
        self.dashboard_factory = lambda channel_id: None
        self.launcher_factory = lambda: None
        self.cleanup_controls = self._no_cleanup

    @staticmethod
    async def _no_cleanup(channel_id):
        pass

    async def forget(self, channel_id):
        self.store.rooms.pop(channel_id, None)
        self.owner_statuses.pop(channel_id, None)
        self.store.save()
        task = self.tasks.pop(channel_id, None)
        if task and task is not asyncio.current_task():
            task.cancel()
        for view in self.bot.persistent_views:
            if getattr(view, 'channel_id', None) == channel_id:
                view.stop()
        await self.cleanup_controls(channel_id)

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
        if not self.can_manage(room, actor):
            raise RoomError('Only the current host or server staff can do that. Open Manage my room to check the host.')
        return room

    @staticmethod
    def is_staff(actor):
        return not actor.bot and (actor.id == actor.guild.owner_id or staff_permissions(actor.guild_permissions))

    def can_manage(self, room, actor):
        return actor.guild.id == room.guild_id and (actor.id == room.host_id or self.is_staff(actor))

    def owned(self, member):
        return next((room for room in self.store.rooms.values()
                     if room.guild_id == member.guild.id and room.creator_id == member.id), None)

    def managed(self, member):
        voice = getattr(member, 'voice', None)
        if voice and voice.channel:
            room = self.store.rooms.get(voice.channel.id)
            if room and self.can_manage(room, member):
                return room
        room = self.owned(member)
        if room and room.host_id == member.id:
            return room
        if self.is_staff(member):
            raise RoomError('Use Find a room, choose the room, then Manage room for a staff override.')
        raise RoomError('You are not hosting a room. Join your room to regain ownership, or use Create a room.')

    @staticmethod
    def link(room):
        return f'https://discord.com/channels/{room.guild_id}/{room.channel_id}'

    @staticmethod
    def humans(channel):
        return {member.id: member for member in channel.members if not member.bot}

    @staticmethod
    def full(channel):
        return channel.user_limit > 0 and len(channel.members) >= channel.user_limit

    def accessible(self, channel_id, actor):
        room = self.store.rooms.get(channel_id)
        if room is None or getattr(getattr(actor, 'guild', None), 'id', None) != room.guild_id:
            raise RoomError('This room no longer exists in this server. Use Find a room again.')
        channel = self.channel(room)
        permissions = channel.permissions_for(actor)
        if actor.bot or not permissions.view_channel or not permissions.connect:
            raise RoomError('You need normal voice access to this room. Ask a server moderator for help.')
        return room, channel

    async def subscribe(self, channel_id, actor):
        async with self.locks[channel_id]:
            room, channel = self.accessible(channel_id, actor)
            voice = getattr(actor, 'voice', None)
            if voice and voice.channel and voice.channel.id == channel_id:
                raise RoomError('You are already in this room.')
            if not self.full(channel):
                return False
            room.waiting.add(actor.id)
            self.store.save()
            return True

    async def unsubscribe(self, channel_id, actor):
        async with self.locks[channel_id]:
            room = self.store.rooms.get(channel_id)
            if room is None or getattr(getattr(actor, 'guild', None), 'id', None) != room.guild_id:
                raise RoomError('This room has closed; its notifications have already been cancelled.')
            room.waiting.discard(actor.id)
            self.store.save()

    async def notify_available(self, channel_id, *, channel=None):
        """One-shot, opted-in DMs. Never reserve a place or move a subscriber."""
        # Serialize deliveries, but never hold the room's settings/voice-state
        # lock during network IO: people must still be able to cancel or leave.
        async with self.notification_locks[channel_id]:
            room = self.store.rooms.get(channel_id)
            # REST edit results are detached snapshots. Wait for the gateway's
            # cached state rather than trusting a possibly stale caller hint.
            channel = self.bot.get_channel(channel_id)
            if room is None or channel is None or self.full(channel):
                return
            for ident in sorted(room.waiting):
                try:
                    member = channel.guild.get_member(ident) or await channel.guild.fetch_member(ident)
                except discord.HTTPException:
                    member = None
                async with self.locks[channel_id]:
                    room = self.store.rooms.get(channel_id)
                    live_channel = self.bot.get_channel(channel_id)
                    if room is None or live_channel is None:
                        return
                    if self.full(live_channel):
                        return
                    if ident not in room.waiting:
                        continue
                    # Claim immediately before send: cancellations can remove
                    # opt-ins while another member's DM is being delivered.
                    room.waiting.discard(ident)
                    self.store.save()
                    if member is None:
                        continue
                    permissions = live_channel.permissions_for(member)
                    if member.bot or not permissions.view_channel or not permissions.connect:
                        continue
                    if member.voice and member.voice.channel and member.voice.channel.id == channel_id:
                        continue
                try:
                    await member.send(
                        f'A place is available in **{discord.utils.escape_markdown(live_channel.name)}** '
                        f'in **{discord.utils.escape_markdown(live_channel.guild.name)}**: {self.link(room)}\n'
                        'You asked for this one-time notification. A place is not reserved and may fill again. '
                        'You will not be moved automatically.', allowed_mentions=discord.AllowedMentions.none())
                except discord.HTTPException as error:
                    log.warning('Availability DM could not be delivered: HTTP %s, code %s', error.status, error.code)

    def embed(self, room, channel):
        embed = discord.Embed(title=channel.name, description='**🌐 Public**\nMembers with normal voice access can see, join and chat.', color=0x5865F2)
        embed.add_field(name='👑 Current room owner (host)', value=f'<@{room.host_id}>\nUser ID: `{room.host_id}`', inline=False)
        embed.add_field(name='Creator', value=f'<@{room.creator_id}>')
        embed.add_field(name='Participant limit', value=str(channel.user_limit or 'Unlimited'))
        embed.add_field(name='Your room', value=f'[Open voice and chat]({self.link(room)})', inline=False)
        embed.set_footer(text='Join / Notify me: everyone • Settings: host and server staff • /room reopens controls')
        return embed

    @staticmethod
    def owner_line(room):
        return f'👑 **Current room owner:** <@{room.host_id}> · User ID: `{room.host_id}`'

    async def announce(self, room, channel, content):
        try:
            await channel.send(f'{content}\n{self.owner_line(room)}',
                               allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as error:
            log.warning('Room %s announcement unavailable: HTTP %s, code %s', room.channel_id, error.status, error.code)

    async def ensure_panel(self, room, *, channel=None):
        channel = channel or self.channel(room)
        panel = None
        if room.panel_id:
            try:
                panel = await channel.fetch_message(room.panel_id)
            except discord.NotFound:
                pass
        if panel is None:
            panel = await channel.send(content=self.owner_line(room), embed=self.embed(room, channel), view=self.dashboard_factory(room.channel_id),
                                       allowed_mentions=discord.AllowedMentions.none())
            room.panel_id = panel.id
            self.store.save()
        else:
            await panel.edit(content=self.owner_line(room), embed=self.embed(room, channel), view=self.dashboard_factory(room.channel_id),
                             allowed_mentions=discord.AllowedMentions.none())
        return panel

    async def refresh_panel(self, room, *, channel=None):
        try:
            await self.ensure_panel(room, channel=channel)
        except discord.HTTPException:
            log.exception('Room %s updated, but its panel could not be refreshed', room.channel_id)
        await self.refresh_owner_status(room, channel=channel)

    async def refresh_owner_status(self, room, *, channel=None):
        # Voice-chat messages cannot be made sticky. The native channel status
        # provides an owner label independent of chat scrolling, alongside the
        # current owner in the control card and ownership announcements.
        channel = channel or self.channel(room)
        permissions = channel.permissions_for(channel.guild.me)
        if not permissions.set_voice_channel_status or not permissions.manage_channels:
            return  # A moderator may intentionally restrict the bot.
        host = channel.guild.get_member(room.host_id)
        display_name = host.display_name if host else str(room.host_id)
        status = f'👑 Owner: {display_name} · {room.host_id}'[:500]
        if self.owner_statuses.get(room.channel_id) == status:
            return
        try:
            await channel.edit(status=status, reason=f'TempVCBot current room owner: user {room.host_id}')
        except discord.HTTPException as error:
            log.warning('Room %s owner status unavailable: HTTP %s, code %s', room.channel_id, error.status, error.code)
        else:
            self.owner_statuses[room.channel_id] = status

    async def _apply(self, candidate):
        # Hosting grants bot controls, not Discord moderation powers. Do not
        # reset native moderation overwrites when ownership changes.
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
                    self.accessible(existing.channel_id, creator)
                    if creator.voice and creator.voice.channel and creator.voice.channel.id != channel.id and not self.full(channel):
                        await creator.move_to(channel)
                    await self.refresh_panel(existing)
                    return existing
                await self.forget(existing.channel_id)
            settings = self.settings.get(str(creator.guild.id), {})
            category = creator.guild.get_channel(settings.get('category_id', 0))
            if not isinstance(category, discord.CategoryChannel):
                raise RoomError('Room creation is not configured. Ask a server admin to run /setup.')
            room = Room(channel_id=0, guild_id=creator.guild.id, creator_id=creator.id, host_id=creator.id,
                        public_policy_applied=True)
            channel = await category.create_voice_channel(
                name=f"{creator.display_name}'s Room"[:100],
                overwrites=build_overwrites(creator.guild, room, self.audience_ids),
                user_limit=0,
                reason='Member created a public temporary voice room',
            )
            room.channel_id = channel.id
            self.store.rooms[channel.id] = room
            self.store.save()
            try:
                if creator.voice and creator.voice.channel:
                    self.accessible(room.channel_id, creator)
                    await creator.move_to(channel)
            finally:
                self.schedule(room.channel_id, delay=60)
            await self.refresh_panel(room)
            return room

    async def rename(self, channel_id, actor, name):
        name = name.strip()
        if not name or len(name) > 100 or any(ord(character) < 32 or ord(character) == 127 for character in name):
            raise RoomError('Choose a room name from 1 to 100 characters on a single line.')
        async with self.locks[channel_id]:
            room = self.authorize(channel_id, actor)
            channel = self.channel(room)
            old_name = channel.name
            updated = await channel.edit(name=name, reason=f'TempVCBot rename by user {actor.id}; current host {room.host_id}')
            updated = updated if isinstance(updated, discord.VoiceChannel) else channel
            await self.refresh_panel(room, channel=updated)
            if old_name != name:
                await self.announce(room, updated,
                    f'✏️ <@{actor.id}> (User ID: `{actor.id}`) renamed this room:\n'
                    f'**{discord.utils.escape_markdown(old_name)}** → **{discord.utils.escape_markdown(name)}**')

    async def set_limit(self, channel_id, actor, limit):
        async with self.locks[channel_id]:
            room = self.authorize(channel_id, actor)
            minimum = 1 if self.is_staff(actor) else 5
            if isinstance(limit, bool) or not isinstance(limit, int) or not (limit == 0 or minimum <= limit <= 99):
                raise RoomError(f'Choose {minimum}–99 participants, or 0 for unlimited.')
            channel = self.channel(room)
            old_limit = channel.user_limit
            updated = await channel.edit(user_limit=limit, reason=f'TempVCBot limit change by user {actor.id}; current host {room.host_id}')
            updated = updated if isinstance(updated, discord.VoiceChannel) else channel
            await self.refresh_panel(room, channel=updated)
            if old_limit != limit:
                await self.announce(room, updated,
                    f'🔢 <@{actor.id}> (User ID: `{actor.id}`) changed the participant limit: '
                    f'**{old_limit or "Unlimited"}** → **{limit or "Unlimited"}**.')
        await self.notify_available(channel_id)

    async def channel_update(self, channel):
        if channel.id not in self.store.rooms:
            return
        # Native changes belong to server staff; do not normalize their limit.
        await self.notify_available(channel.id, channel=channel)
        room = self.store.rooms.get(channel.id)
        if room:
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
                await self.forget(channel_id)
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
                await self.forget(channel_id)
                return
            successor = room.choose_host(present.keys())
            if successor is not None and successor != room.host_id:
                previous_host = room.host_id
                candidate = copy.deepcopy(room)
                candidate.host_id = successor
                await self._apply(candidate)
                await self.refresh_panel(candidate)
                reason = ('The creator returned.' if successor == room.creator_id else
                          'The previous host left; the earliest-joined remaining member takes over.')
                await self.announce(candidate, channel,
                    f'👑 Room ownership changed: <@{previous_host}> (`{previous_host}`) → '
                    f'<@{successor}> (`{successor}`). {reason} Room settings stay the same.')
            else:
                self.store.save()

    async def record_voice_update(self, member, before, after):
        """Record arrival order without network IO, including startup replays."""
        old_id = before.channel.id if before.channel else None
        new_id = after.channel.id if after.channel else None
        if old_id == new_id:
            return old_id, new_id
        if old_id in self.store.rooms:
            async with self.locks[old_id]:
                room = self.store.rooms.get(old_id)
                if room:
                    room.leave(member.id)
                    self.store.save()
        if new_id in self.store.rooms:
            async with self.locks[new_id]:
                room = self.store.rooms.get(new_id)
                if room and not member.bot:
                    room.join(member.id)
                    room.waiting.discard(member.id)
                    self.store.save()
        return old_id, new_id

    async def voice_update(self, member, before, after):
        old_id, new_id = await self.record_voice_update(member, before, after)
        if old_id == new_id:
            return
        if old_id in self.store.rooms:
            self.schedule(old_id)
        if new_id in self.store.rooms:
            room = self.store.rooms.get(new_id)
            if room and member.id == room.creator_id:
                await self.reconcile(new_id)
            else:
                self.schedule(new_id)
        settings = self.settings.get(str(member.guild.id), {})
        if old_id and old_id == settings.get('legacy_entry_channel_id'):
            await self.retire_entry(member.guild, member.guild.get_channel(settings.get('category_id')))
        for channel_id in {old_id, new_id} - {None}:
            if channel_id in self.store.rooms:
                await self.notify_available(channel_id)

    async def migrate_legacy(self, path):
        path = Path(path)
        if self.store.path.exists() or not path.exists():
            return
        old = json.loads(path.read_text(encoding='utf-8'))
        for channel_id, creator_id in old.items():
            channel = self.bot.get_channel(int(channel_id))
            if channel is None:
                continue
            room = Room(channel_id=channel.id, guild_id=channel.guild.id,
                        creator_id=int(creator_id), host_id=int(creator_id), created_at=0)
            for member in channel.members:
                if not member.bot:
                    room.join(member.id)
            self.store.rooms[channel.id] = room
        self.store.save()

    async def retire_entry(self, guild, category):
        """Retire the configured legacy entry without deleting chat history."""
        settings = self.settings[str(guild.id)]
        entry_id = settings.pop('creator_channel_id', None) or settings.get('legacy_entry_channel_id')
        if not entry_id:
            return
        settings['legacy_entry_channel_id'] = entry_id
        atomic_json(self.config_path, self.settings)
        entry = guild.get_channel(entry_id)
        if entry is not None:
            if not isinstance(entry, discord.VoiceChannel) or entry.id != entry_id or entry.name != '➕ Create VC':
                log.warning('Legacy entry %s no longer matches; leaving channel untouched', entry_id)
                return
            if entry.members:
                return  # Retry after its final occupant leaves.
            try:
                has_history = any([message async for message in entry.history(limit=1)])
            except discord.HTTPException:
                has_history = True  # Preserve a channel whose history cannot be checked.
            if has_history:
                if category is None:
                    raise RoomError('The managed category is missing; legacy chat has been preserved.')
                await entry.edit(name='archived-create-vc', category=category,
                                 overwrites={guild.default_role: member_access(False),
                                             **{role: member_access(False) for ident in self.audience_ids
                                                if (role := guild.get_role(ident)) is not None},
                                             guild.me: member_access()},
                                 reason='Preserve legacy entry chat while moving creation to Voice Dashboard')
                settings['archived_entry_channel_id'] = entry_id
            else:
                await entry.delete(reason='Voice Dashboard replaces the unused temporary-room entry')
        settings.pop('legacy_entry_channel_id', None)
        atomic_json(self.config_path, self.settings)

    async def setup(self, guild):
        """Idempotently configure the managed category and Voice Dashboard."""
        settings = self.settings.setdefault(str(guild.id), {})
        category = guild.get_channel(settings.get('category_id', 0))
        if not isinstance(category, discord.CategoryChannel):
            category = discord.utils.get(guild.categories, name='Temporary Channels')
        if category is None:
            category = await guild.create_category('Temporary Channels', reason='TempVCBot setup')
        template = Room(channel_id=category.id, guild_id=guild.id, creator_id=guild.me.id, host_id=guild.me.id)
        # Rooms and dashboard get explicit public overwrites. Keep an existing
        # category policy: changing it could expose old permission-synced archives.
        controls = guild.get_channel(settings.get('controls_channel_id', 0))
        control_overwrites = build_overwrites(guild, template, self.audience_ids)
        for target, overwrite in control_overwrites.items():
            overwrite.send_messages = target == guild.me
            overwrite.add_reactions = target == guild.me
            overwrite.use_application_commands = True
        if controls is None:
            controls = await category.create_text_channel('voice-dashboard', overwrites=control_overwrites,
                        topic='Create public voice rooms, find a room, or get notified when a full room has space.')
        else:
            await controls.edit(name='voice-dashboard', overwrites=control_overwrites,
                                topic='Create public voice rooms, find a room, or get notified when a full room has space.')
        settings.update(category_id=category.id, controls_channel_id=controls.id, dashboard_version=4)
        atomic_json(self.config_path, self.settings)
        await self.retire_entry(guild, category)
        await self.ensure_launcher(guild)
        return settings

    async def ensure_launcher(self, guild):
        settings = self.settings[str(guild.id)]
        channel = guild.get_channel(settings.get('controls_channel_id', 0))
        if channel is None or settings.get('dashboard_version') != 4:
            return await self.setup(guild)
        message = None
        if settings.get('launcher_message_id'):
            try:
                message = await channel.fetch_message(settings['launcher_message_id'])
            except discord.NotFound:
                pass
        embed = discord.Embed(title='🎙️ Voice Dashboard', color=0x5865F2,
            description='All rooms are **public** to members with normal voice access. New rooms have **no user limit**.\n\n'
            '**Create a room:** start a public voice room. If already in voice, you will be moved. Otherwise join within 60 seconds.\n'
            '**Find a room:** check availability. If a room is full, choose **Notify me** for a one-time DM when space opens.\n'
            '**Manage my room:** rename your room or set a size of **5–99**; **0** means unlimited.\n'
            '**Server staff:** use Find a room → Manage room to override settings. Staff can set **1–99** or unlimited.\n\n'
            'Everyone can chat and use webcam/video. Regular room hosts cannot kick or block people, or make rooms private. '
            'Admins and moderators retain their native Discord moderation powers. '
            'The earliest-joined member takes over after the host leaves; the creator regains hosting on return. '
            'Empty rooms are automatically deleted.')
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
                await self.forget(room.channel_id)
                continue
            if hasattr(channel, 'guild') and channel.guild.id != guild.id:
                raise RoomError('Persisted room belongs to a different server; check room state.')
            await self.reconcile(room.channel_id)
            async with self.locks[room.channel_id]:
                room = self.store.rooms.get(room.channel_id)
                if room is None:
                    continue
                channel = self.channel(room)
                updated = channel
                if not room.public_policy_applied:
                    updated = await channel.edit(
                        overwrites=migration_overwrites(guild, room, self.audience_ids, channel.overwrites),
                        user_limit=5 if 0 < channel.user_limit < 5 else channel.user_limit,
                        reason='One-time migration to public room access, preserving moderator changes')
                    room.public_policy_applied = True
                    room.migration_policy = None
                    self.store.save()
                await self.refresh_panel(room, channel=updated if isinstance(updated, discord.VoiceChannel) else None)
            await self.notify_available(room.channel_id)
        await self.ensure_launcher(guild)
        if self.settings[str(guild.id)].get('legacy_entry_channel_id'):
            await self.retire_entry(guild, guild.get_channel(self.settings[str(guild.id)]['category_id']))
