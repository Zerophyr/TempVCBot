"""Durable public-room ownership, notification opt-ins and permission policy."""

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import discord


def staff_permissions(permissions):
    """Native moderation authority, never a role name or a new permission grant."""
    return any(getattr(permissions, name) for name in (
        'administrator', 'manage_guild', 'manage_channels', 'move_members',
        'kick_members', 'ban_members', 'moderate_members', 'mute_members', 'deafen_members',
    ))


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


@dataclass
class Room:
    channel_id: int
    guild_id: int
    creator_id: int
    host_id: int
    waiting: set[int] = field(default_factory=set)
    join_order: dict[int, int] = field(default_factory=dict)
    sequence: int = 0
    panel_id: int | None = None
    created_at: float = field(default_factory=time.time)
    public_policy_applied: bool = False
    # Temporary provenance for removing only the former bot's access rules.
    # Retained across interrupted migrations, then discarded after success.
    migration_policy: dict | None = None

    def join(self, member_id):
        if member_id not in self.join_order:
            self.sequence += 1
            self.join_order[member_id] = self.sequence

    def leave(self, member_id):
        self.join_order.pop(member_id, None)

    def choose_host(self, present):
        eligible = set(present)
        if self.creator_id in eligible:
            return self.creator_id
        if self.host_id in eligible:
            return self.host_id
        if not eligible:
            return None
        return min(eligible, key=lambda ident: (self.join_order.get(ident, float('inf')), ident))

    def to_dict(self):
        value = asdict(self)
        value['waiting'] = sorted(self.waiting)
        if self.migration_policy is None:
            value.pop('migration_policy')
        return value

    @classmethod
    def from_dict(cls, value, *, legacy=False):
        value = dict(value)
        if legacy:
            value['public_policy_applied'] = False
            mode = value.pop('mode', 'hidden')
            if mode not in {'hidden', 'visible', 'public'}:
                raise ValueError('Invalid legacy room privacy mode')
            invited = set(map(int, value.pop('invited', [])))
            blocked = set(map(int, value.pop('blocked', [])))
            permitted = (invited | {value['creator_id'], value['host_id']}) - blocked
            value['migration_policy'] = {
                'visible': mode != 'hidden', 'public': mode == 'public',
                'members': {str(ident): ident not in blocked for ident in permitted | blocked},
            }
        value['waiting'] = set(map(int, value.get('waiting', [])))
        value['join_order'] = {int(key): int(order) for key, order in value.get('join_order', {}).items()}
        room = cls(**value)
        if min(room.channel_id, room.guild_id, room.creator_id, room.host_id, *room.waiting) <= 0:
            raise ValueError('Invalid persisted room state')
        return room


class RoomStore:
    def __init__(self, path):
        self.path = Path(path)
        self.rooms = {}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding='utf-8'))
            version = data.get('version')
            if version not in {2, 3}:
                raise ValueError('Unsupported room state version; preserve this file before migrating')
            self.rooms = {int(key): Room.from_dict(value, legacy=version == 2) for key, value in data['rooms'].items()}
            if any(key != room.channel_id for key, room in self.rooms.items()):
                raise ValueError('Room IDs do not match persisted keys')

    def save(self):
        atomic_json(self.path, {'version': 3, 'rooms': {key: room.to_dict() for key, room in self.rooms.items()}})


def member_access(allowed=True):
    return discord.PermissionOverwrite(
        view_channel=allowed, connect=allowed, speak=allowed, stream=allowed,
        use_voice_activation=allowed, send_messages=allowed,
        read_message_history=allowed, add_reactions=allowed, attach_files=allowed,
        embed_links=allowed,
    )


def build_overwrites(guild, room, audience_ids):
    audience = [role for ident in audience_ids if (role := guild.get_role(ident)) is not None]
    overwrites = {guild.default_role: member_access(not audience)}
    overwrites[guild.default_role].set_voice_channel_status = False
    for role in audience:
        overwrites[role] = member_access()
        overwrites[role].set_voice_channel_status = False
    for role in guild.roles:
        if staff_permissions(role.permissions):
            overwrites[role] = member_access()
            overwrites[role].set_voice_channel_status = True
    for ident in {room.creator_id, room.host_id}:
        target = guild.get_member(ident) or discord.Object(id=ident, type=discord.Member)
        overwrites[target] = member_access()
    overwrites[guild.me] = member_access()
    overwrites[guild.me].set_voice_channel_status = True
    return overwrites


def migration_overwrites(guild, room, audience_ids, existing):
    """Replace known legacy bot rules, preserving unrelated moderator changes.

    A value identical to a known old bot rule cannot be attributed separately
    to a moderator. Only that known baseline is removed; unknown rules and
    values differing from the baseline are retained.
    """
    policy = build_overwrites(guild, room, audience_ids)
    previous = {}
    provenance = room.migration_policy
    if provenance is not None:
        audience = [role for ident in audience_ids if (role := guild.get_role(ident)) is not None]
        everyone = member_access(provenance['visible'] and not audience)
        everyone.connect = provenance['public'] and not audience
        previous[(False, guild.default_role.id)] = everyone
        for role in audience:
            overwrite = member_access(provenance['visible'])
            overwrite.connect = provenance['public']
            previous[(False, role.id)] = overwrite
        for ident, allowed in provenance['members'].items():
            previous[(True, int(ident))] = member_access(allowed)
        previous[(True, guild.me.id)] = member_access()
    for target, current in existing.items():
        old = previous.get((not isinstance(target, discord.Role), target.id))
        merged = policy.get(target, discord.PermissionOverwrite())
        for name, value in current:
            if value is not None and (old is None or value != getattr(old, name)):
                setattr(merged, name, value)
        if not merged.is_empty():
            policy[target] = merged
    return policy
